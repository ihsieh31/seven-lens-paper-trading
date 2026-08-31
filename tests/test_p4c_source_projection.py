# mypy: ignore-errors
"""Public-entry source projection tests for P4-C authorities."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.market_data.snapshots import DailyBar, Feed, daily_bars_from_record
from seven_lens.screening.funnel import factor_input_from_source_records
from seven_lens.securities.contracts import (
    AssetClass,
    Cik,
    ListingExchange,
    SecurityId,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    build_identity_record,
)
from seven_lens.sources.adapters.alpaca import parse_assets, parse_bars
from seven_lens.sources.adapters.issuer_exchange import parse_exchange_notice
from seven_lens.sources.adapters.sec_edgar import parse_companyfacts
from seven_lens.universe.builder import (
    AssetKind,
    AssetObservation,
    IdentityView,
    asset_observation_from_records,
    identity_view_from_records,
)

_SECURITY_ID = SecurityId("11111111-1111-4111-8111-111111111111")
_SYMBOL = SecuritySymbol("TEST")
_KNOWN_AT = UtcTimestamp.from_isoformat("2026-06-01T20:00:00.000000Z")
_ACCEPTED_AT = UtcTimestamp.from_isoformat("2026-05-01T20:00:00.000000Z")
_ACCESSION = "0000000001-26-000001"


def _identity(asset_ref: SourceRef | None = None):
    asset_family = parse_assets(_asset_json(), retrieved_at=_KNOWN_AT)[0].family
    refs = (
        asset_ref
        if asset_ref is not None
        else SourceRef("identity-source", asset_family, "d" * 64),
    )
    return build_identity_record(
        security_id=_SECURITY_ID,
        symbol=_SYMBOL,
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        cik=Cik("0000000001"),
        valid_from=UtcTimestamp.from_isoformat("2025-01-01T00:00:00.000000Z"),
        available_at=UtcTimestamp.from_isoformat("2025-01-01T00:00:00.000000Z"),
        status=SecurityStatus.ACTIVE,
        source_refs=refs,
        schema_version=SchemaVersion("1.0.0"),
    )


def _asset_json() -> bytes:
    return json.dumps(
        [
            {
                "id": _SECURITY_ID.value,
                "symbol": _SYMBOL.value,
                "exchange": "NYSE",
                "asset_class": "us_equity",
                "status": "active",
                "tradable": True,
            }
        ]
    ).encode()


def _bar_record():
    payload = json.dumps(
        {
            "symbol": _SYMBOL.value,
            "bars": [
                {
                    "t": "2026-05-29T20:00:00Z",
                    "o": "99.00",
                    "h": "101.00",
                    "l": "98.00",
                    "c": "100.00",
                    "v": 1000000,
                }
            ],
        }
    ).encode()
    return parse_bars(
        payload,
        retrieved_at=_KNOWN_AT,
        requested_feed="sip",
        effective_feed="sip",
        requested_timeframe="1Day",
    )[0]


def _sec_records():
    fact_base = {
        "end": "2026-03-31",
        "fy": 2026,
        "fp": "Q1",
        "form": "10-Q",
        "filed": "2026-05-01",
        "accn": _ACCESSION,
        "frame": "CY2026Q1I",
    }
    payload = {
        "cik": 1,
        "entityName": "Test Issuer",
        "facts": {
            "us-gaap": {"Assets": {"units": {"USD": [{**fact_base, "val": 1000000000}]}}},
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [{**fact_base, "val": 10000000}]}
                }
            },
        },
    }
    return parse_companyfacts(
        json.dumps(payload).encode(),
        retrieved_at=_KNOWN_AT,
        submission_acceptance={_ACCESSION: _ACCEPTED_AT},
    )


def test_daily_bars_public_factory_binds_record_and_identity() -> None:
    record = _bar_record()
    identity = _identity()
    bars = daily_bars_from_record(
        record,
        security_id=_SECURITY_ID,
        identities=(identity,),
        known_at=_KNOWN_AT,
    )
    assert len(bars) == 1
    assert bars[0].source_ref.record_hash == record.record_hash
    assert bars[0].security_id == identity.security_id

    object.__setattr__(bars[0], "close", Decimal("1.00"))
    with pytest.raises(ValueError, match="not bound"):
        bars[0].__post_init__()


def test_direct_daily_bar_and_asset_observation_cannot_mint_authority() -> None:
    with pytest.raises(ValueError, match="historical-record factory"):
        DailyBar(
            trading_date=TradingDate.from_isoformat("2026-05-29"),
            close=Decimal("100"),
            volume=1,
            source_ref=SourceRef("bar", _bar_record().family, "b" * 64),
            feed=Feed.SIP_DELAYED,
            available_at=_KNOWN_AT,
            security_id=_SECURITY_ID,
        )
    with pytest.raises(ValueError, match="source-record factory"):
        AssetObservation(
            security_id=_SECURITY_ID,
            symbol=_SYMBOL,
            kind=AssetKind.ORDINARY_COMMON_STOCK,
            active=True,
            tradable=True,
            exchange=ListingExchange.NYSE,
            observed_at=_KNOWN_AT,
            halted=False,
        )
    with pytest.raises(ValueError, match="P4-B resolver"):
        IdentityView(record=_identity())


def test_identity_view_public_factory_rejects_ambiguous_heads() -> None:
    first = _identity()
    second = build_identity_record(
        security_id=_SECURITY_ID,
        symbol=_SYMBOL,
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        cik=Cik("0000000001"),
        valid_from=first.valid_from,
        available_at=first.available_at,
        status=SecurityStatus.ACTIVE,
        source_refs=(SourceRef("other-source", first.source_refs[0].family, "e" * 64),),
        schema_version=SchemaVersion("1.0.0"),
    )
    with pytest.raises(ValueError, match="unambiguous"):
        identity_view_from_records(
            (first, second),
            security_id=_SECURITY_ID,
            as_of=_KNOWN_AT,
            known_at=_KNOWN_AT,
        )


def test_asset_projection_requires_both_exact_authorities_and_p4b_lineage() -> None:
    asset = parse_assets(_asset_json(), retrieved_at=_KNOWN_AT)[0]
    exchange_payload = {
        "notices": [
            {
                "id": "status-test",
                "title": "Security status",
                "url": "https://www.nyse.com/notice/status-test",
                "exchange": "NYSE",
                "published_at": str(_KNOWN_AT),
                "symbol": _SYMBOL.value,
                "instrument_kind": "ordinary_common_stock",
                "halted": False,
                "observed_at": str(_KNOWN_AT),
            }
        ]
    }
    exchange = parse_exchange_notice(json.dumps(exchange_payload).encode(), retrieved_at=_KNOWN_AT)[
        0
    ]
    asset_ref = SourceRef(asset.record_id, asset.family, asset.record_hash)
    identity = _identity(asset_ref)
    observation = asset_observation_from_records(
        asset, exchange, identity=identity, known_at=_KNOWN_AT
    )
    assert observation.kind is AssetKind.ORDINARY_COMMON_STOCK
    assert observation.halted is False

    with pytest.raises(ValueError, match="does not descend"):
        asset_observation_from_records(asset, exchange, identity=_identity(), known_at=_KNOWN_AT)


def test_factor_public_factory_retains_bar_sec_and_identity_authority() -> None:
    identity = _identity()
    factor_input = factor_input_from_source_records(
        security_id=_SECURITY_ID,
        symbol=_SYMBOL,
        identity=identity,
        identities=(identity,),
        bar_record=_bar_record(),
        sec_records=_sec_records(),
        sessions=(),
        known_at=_KNOWN_AT,
    )
    assert len(factor_input.closes) == 1
    assert len(factor_input.facts) == 1
    assert factor_input.shares_outstanding.value == Decimal("10000000")
    assert all(value.security_id == _SECURITY_ID for value in factor_input.closes)
    assert all(value.security_id == _SECURITY_ID for value in factor_input.facts)
    assert factor_input.shares_outstanding.security_id == _SECURITY_ID

    object.__setattr__(factor_input.closes[0], "close", Decimal("1.00"))
    with pytest.raises(ValueError, match="session-close authority is not bound"):
        factor_input._verify_source_binding()


def test_factor_factory_rejects_bar_sec_identity_substitution() -> None:
    sec_identity = _identity()
    bar_identity = build_identity_record(
        security_id=_SECURITY_ID,
        symbol=_SYMBOL,
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        cik=Cik("0000000001"),
        valid_from=sec_identity.valid_from,
        available_at=sec_identity.available_at,
        status=SecurityStatus.ACTIVE,
        source_refs=(
            SourceRef("bar-identity-source", sec_identity.source_refs[0].family, "f" * 64),
        ),
        schema_version=SchemaVersion("1.0.0"),
    )
    with pytest.raises(ValueError, match="different identities"):
        factor_input_from_source_records(
            security_id=_SECURITY_ID,
            symbol=_SYMBOL,
            identity=sec_identity,
            identities=(bar_identity,),
            bar_record=_bar_record(),
            sec_records=_sec_records(),
            sessions=(),
            known_at=_KNOWN_AT,
        )
