# mypy: ignore-errors
"""P4-A issuer IR and exchange official adapter tests."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.issuer_exchange import parse_exchange_notice, parse_issuer_press
from seven_lens.sources.adapters.records import SourceSchemaDriftError
from seven_lens.sources.roles import SourceRole

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_ISSUER_JSON = b"""{"press_releases": [
    {"id": "ir-2026-0001", "title": "Seven Labs announces reverse split",
     "url": "https://www.sevenlabs.example/ir/2026/0001",
     "published_at": "2026-08-26T20:15:00Z"}
  ]}"""

_EXCHANGE_JSON = b"""{"notices": [
    {"id": "nyse-2026-0042", "title": "Trading halt pending announcement",
     "url": "https://www.nyse.com/notice/2026/0042",
     "exchange": "NYSE", "published_at": "2026-08-27T13:05:00Z"}
  ]}"""

_TYPED_EXCHANGE_JSON = b"""{"notices": [
    {"id": "nyse-2026-0043", "title": "Security status",
     "url": "https://www.nyse.com/notice/2026/0043", "exchange": "NYSE",
     "published_at": "2026-08-27T13:05:00Z", "symbol": "TEST",
     "instrument_kind": "ordinary_common_stock", "halted": false,
     "observed_at": "2026-08-27T13:04:59Z"}
  ]}"""


def test_parse_issuer_press_builds_confirmation_records() -> None:
    records = parse_issuer_press(_ISSUER_JSON, retrieved_at=_RETRIEVED, issuer_id="sevenlabs")

    assert len(records) == 1
    record = records[0]
    assert record.role is SourceRole.CONFIRMATION
    assert record.material_claim is False
    assert str(record.published_at) == "2026-08-26T20:15:00.000000Z"
    assert record.record_id.startswith("issuer-ir-sevenlabs-")
    assert record.payload.to_dict()["url"] == "https://www.sevenlabs.example/ir/2026/0001"


def test_parse_issuer_press_requires_publish_time_and_canonical_url() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_issuer_press(
            _ISSUER_JSON.replace(b'\n     "published_at": "2026-08-26T20:15:00Z"}', b"}"),
            retrieved_at=_RETRIEVED,
            issuer_id="sevenlabs",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_issuer_press(
            _ISSUER_JSON.replace(b"https://www.sevenlabs.example", b"http://www.sevenlabs.example"),
            retrieved_at=_RETRIEVED,
            issuer_id="sevenlabs",
        )


def test_parse_exchange_notice_pins_registered_exchanges() -> None:
    records = parse_exchange_notice(_EXCHANGE_JSON, retrieved_at=_RETRIEVED)

    assert len(records) == 1
    record = records[0]
    assert record.role is SourceRole.AUTHORITY
    assert record.payload.to_dict()["exchange"] == "NYSE"
    assert record.published_at is not None


def test_parse_exchange_notice_rejects_unregistered_exchange() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_exchange_notice(_EXCHANGE_JSON.replace(b'"NYSE"', b'"MOON"'), retrieved_at=_RETRIEVED)


def test_parse_exchange_notice_preserves_typed_instrument_and_halt_authority() -> None:
    record = parse_exchange_notice(_TYPED_EXCHANGE_JSON, retrieved_at=_RETRIEVED)[0]
    payload = record.payload.to_dict()
    assert payload["symbol"] == "TEST"
    assert payload["instrument_kind"] == "ordinary_common_stock"
    assert payload["halted"] is False
    assert str(record.observation_at) == "2026-08-27T13:04:59.000000Z"
    assert record.available_at == _RETRIEVED


def test_parse_exchange_notice_rejects_partial_or_unknown_typed_status() -> None:
    with pytest.raises(SourceSchemaDriftError, match="present together"):
        parse_exchange_notice(
            _TYPED_EXCHANGE_JSON.replace(b'     "instrument_kind": "ordinary_common_stock",', b""),
            retrieved_at=_RETRIEVED,
        )
    with pytest.raises(SourceSchemaDriftError, match="closed enum"):
        parse_exchange_notice(
            _TYPED_EXCHANGE_JSON.replace(b'"ordinary_common_stock"', b'"mystery"'),
            retrieved_at=_RETRIEVED,
        )


def test_notice_url_accepts_explicit_443_and_rejects_other_ports() -> None:
    accepted = _ISSUER_JSON.replace(
        b"https://www.sevenlabs.example/ir/2026/0001",
        b"https://www.sevenlabs.example:443/ir/2026/0001",
    )
    records = parse_issuer_press(accepted, retrieved_at=_RETRIEVED, issuer_id="sevenlabs")
    assert records[0].payload.to_dict()["url"] == "https://www.sevenlabs.example:443/ir/2026/0001"

    with pytest.raises(SourceSchemaDriftError):
        parse_issuer_press(
            _ISSUER_JSON.replace(
                b"https://www.sevenlabs.example/ir/2026/0001",
                b"https://www.sevenlabs.example:8080/ir/2026/0001",
            ),
            retrieved_at=_RETRIEVED,
            issuer_id="sevenlabs",
        )
