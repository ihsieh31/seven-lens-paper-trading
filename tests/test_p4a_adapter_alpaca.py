# mypy: ignore-errors
"""P4-A Alpaca family adapters: assets, bars, IEX quotes, corporate actions."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.alpaca import (
    parse_assets,
    parse_bars,
    parse_corporate_actions,
    parse_iex_quote,
)
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    SourceSchemaDriftError,
    content_hash_of,
)
from seven_lens.sources.contracts import RightsStatus
from seven_lens.sources.roles import CoverageLabel, SourceRole

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_ASSETS_JSON = b"""[
  {"id": "90927a3c-0b6a-4d5a-bd31-4d45a26b7cc8", "symbol": "AAPL", "exchange": "NASDAQ",
   "asset_class": "us_equity", "status": "active", "tradable": true},
  {"id": "b0b6dd9d-8b9b-48a9-ba46-b700d5a42a43", "symbol": "TSLA", "exchange": "NASDAQ",
   "asset_class": "us_equity", "status": "active", "tradable": true}
]"""

_BARS_JSON = b"""{"symbol": "AAPL", "bars": [
    {"t": "2026-08-26T13:30:00Z", "o": "250.10", "h": "251.20", "l": "249.90",
     "c": "251.00", "v": 120000},
    {"t": "2026-08-26T13:31:00Z", "o": "251.00", "h": "251.50", "l": "250.80",
     "c": "251.20", "v": 95000}
  ], "next_page_token": null}"""

_QUOTE_JSON = b"""{"symbol": "AAPL", "bid_price": "250.98", "bid_size": 3,
   "ask_price": "251.02", "ask_size": 2, "timestamp": "2026-08-27T15:29:59.500000Z"}"""

_CA_JSON = b"""{"corporate_actions": [
    {"type": "split", "split_type": "reverse", "cusip": "037833100",
     "symbol": "AAPL", "ex_date": "2026-09-08", "ratio": "0.1"},
    {"type": "dividend", "cusip": "037833100", "ex_date": "2026-09-08"},
    {"type": "split", "split_type": "forward", "symbol": "NOV8", "ex_date": null,
     "ratio": null}
  ]}"""


def test_parse_assets_builds_authority_records_with_closed_enums() -> None:
    records = parse_assets(_ASSETS_JSON, retrieved_at=_RETRIEVED)

    assert len(records) == 2
    first = records[0]
    assert first.family.value == "ALPACA_ASSETS"
    assert first.role is SourceRole.AUTHORITY
    assert first.coverage is CoverageLabel.FULL
    assert first.rights is RightsStatus.ALLOWED
    assert first.content_hash == content_hash_of(_ASSETS_JSON)
    assert first.payload.to_dict()["symbol"] == "AAPL"
    assert first.record_id.startswith("alpaca-asset-")
    assert first.record_id != records[1].record_id


def test_parse_assets_rejects_unknown_exchange_enum_fail_closed() -> None:
    drifted = _ASSETS_JSON.replace(b'"NASDAQ"', b'"MOON_EXCHANGE"')
    with pytest.raises(SourceSchemaDriftError):
        parse_assets(drifted, retrieved_at=_RETRIEVED)


def test_parse_assets_rejects_schema_drift() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_assets(b'{"unexpected": "object"}', retrieved_at=_RETRIEVED)
    with pytest.raises(SourceSchemaDriftError):
        parse_assets(
            _ASSETS_JSON.replace(b'"tradable": true', b'"tradable": true, "fractional": true'),
            retrieved_at=_RETRIEVED,
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_assets(
            _ASSETS_JSON.replace(b'"asset_class": "us_equity"', b'"asset_class": "crypto"'),
            retrieved_at=_RETRIEVED,
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_assets(b"[]", retrieved_at=_RETRIEVED)


def test_parse_bars_records_requested_feed_and_latest_observation() -> None:
    records = parse_bars(
        _BARS_JSON,
        retrieved_at=_RETRIEVED,
        requested_feed="sip",
        effective_feed="sip",
    )

    assert len(records) == 1
    record = records[0]
    assert record.payload.to_dict()["feed"] == "sip"
    assert str(record.observation_at) == "2026-08-26T13:31:00.000000Z"


def test_parse_bars_never_silently_falls_back_across_feeds() -> None:
    from seven_lens.sources.adapters.alpaca import FeedEntitlementError

    with pytest.raises(FeedEntitlementError):
        parse_bars(
            _BARS_JSON,
            retrieved_at=_RETRIEVED,
            requested_feed="sip",
            effective_feed="iex",
        )


def test_parse_iex_quote_always_carries_limited_coverage_warning() -> None:
    records = parse_iex_quote(_QUOTE_JSON, retrieved_at=_RETRIEVED, symbol="AAPL")

    assert len(records) == 1
    record = records[0]
    assert record.coverage is CoverageLabel.LIMITED_MARKET_COVERAGE
    assert record.coverage_warning
    assert "NBBO" in record.coverage_warning
    assert record.observation_at is not None
    assert record.record_id.startswith("alpaca-iex-quote-AAPL-")


def test_parse_iex_quote_rejects_empty_and_mismatched_quotes() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_iex_quote(
            b'{"symbol": "AAPL", "bid_price": null, "ask_price": null,'
            b' "timestamp": "2026-08-27T15:29:59Z"}',
            retrieved_at=_RETRIEVED,
            symbol="AAPL",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_iex_quote(_QUOTE_JSON, retrieved_at=_RETRIEVED, symbol="MSFT")


def test_parse_corporate_actions_are_detection_only_and_never_confirmed() -> None:
    records = parse_corporate_actions(_CA_JSON, retrieved_at=_RETRIEVED)

    assert len(records) == 3
    reverse_split, dividend, incomplete = records

    assert reverse_split.payload.to_dict()["supported"] is True
    assert reverse_split.payload.to_dict()["complete"] is True
    assert reverse_split.effective_at is None
    assert reverse_split.material_claim is False
    assert reverse_split.payload.to_dict()["split_type"] == "reverse"

    assert dividend.payload.to_dict()["supported"] is False
    assert dividend.payload.to_dict()["complete"] is False
    assert dividend.effective_at is None
    assert dividend.material_claim is False

    assert incomplete.payload.to_dict()["complete"] is False
    assert incomplete.effective_at is None


def test_parse_corporate_actions_reject_malformed_entries() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_corporate_actions(
            b'{"corporate_actions": [{"cusip": "037833100"}]}', retrieved_at=_RETRIEVED
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_corporate_actions(
            _CA_JSON.replace(b'"ratio": "0.1"', b'"ratio": "-0.1"'), retrieved_at=_RETRIEVED
        )


def test_records_are_tamper_evident() -> None:
    records = parse_assets(_ASSETS_JSON, retrieved_at=_RETRIEVED)

    for record in records:
        assert isinstance(record, NormalizedSourceRecord)
        assert record.verify_integrity() is True
