# mypy: ignore-errors
"""P4-A normalized source record contract: hashing, tamper, and role-consistency tests."""

from __future__ import annotations

import dataclasses

import pytest

from seven_lens.domain.json_values import JsonObject
from seven_lens.domain.value_objects import SchemaVersion, UtcTimestamp
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    ProviderTimestampError,
    SourceSchemaDriftError,
    parse_provider_timestamp,
    provider_utc_date,
    strict_json_loads,
)
from seven_lens.sources.adapters.records import (
    _build_normalized_record as build_normalized_record,
)
from seven_lens.sources.adapters.records import (
    build_normalized_record as public_build_normalized_record,
)
from seven_lens.sources.contracts import RightsStatus
from seven_lens.sources.roles import CoverageLabel, P4SourceFamily, SourceRole, p4_manifest_registry

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")
_OBSERVED = UtcTimestamp.from_isoformat("2026-08-27T15:29:59.000000Z")


def _base_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "record_id": "alpaca-asset-test-0001",
        "family": P4SourceFamily.ALPACA_ASSETS,
        "endpoint_id": "asset_detail",
        "schema_version": SchemaVersion("1.0.0"),
        "content_hash": "a" * 64,
        "retrieved_at": _RETRIEVED,
        "payload": {"symbol": "AAPL", "status": "active"},
        "material_claim": False,
    }
    values.update(overrides)
    return values


def test_record_derives_role_coverage_rights_from_registry() -> None:
    record = build_normalized_record(**_base_values())

    policy = p4_manifest_registry().policy(P4SourceFamily.ALPACA_ASSETS)
    assert record.role is policy.role
    assert record.coverage is policy.coverage
    assert record.rights is policy.rights
    assert record.record_hash == record.compute_hash()
    assert len(record.record_hash) == 64


def test_public_record_builder_rejects_caller_authored_source_authority() -> None:
    with pytest.raises(ValueError, match="adapter-only"):
        public_build_normalized_record(**_base_values())


def test_record_authority_rejects_equality_spoofing() -> None:
    class EqualToEverything:
        def __eq__(self, other: object) -> bool:
            del other
            return True

    record = build_normalized_record(**_base_values())
    values = {
        field: getattr(record, field)
        for field in (
            "record_id",
            "family",
            "endpoint_id",
            "schema_version",
            "content_hash",
            "record_hash",
            "retrieved_at",
            "payload",
            "material_claim",
            "observation_at",
            "published_at",
            "available_at",
            "effective_at",
            "vintage",
            "supersedes_content_hash",
            "coverage_warning",
        )
    }
    with pytest.raises(ValueError, match="adapter or trusted readback"):
        NormalizedSourceRecord(**values, _authority=EqualToEverything())


def test_role_coverage_rights_are_derived_and_unforgeable() -> None:
    record = build_normalized_record(**_base_values(record_id="alpaca-asset-test-0002"))

    policy = p4_manifest_registry().policy(P4SourceFamily.ALPACA_ASSETS)
    assert record.role is policy.role
    assert record.rights is policy.rights
    with pytest.raises(AttributeError):
        object.__setattr__(record, "role", SourceRole.DISCOVERY)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        object.__setattr__(record, "rights", RightsStatus.UNKNOWN)  # type: ignore[attr-defined]


def test_iex_record_requires_limited_coverage_warning() -> None:
    policy_values = _base_values(
        record_id="alpaca-iex-quote-0001",
        family=P4SourceFamily.ALPACA_IEX_QUOTES,
        endpoint_id="latest_quote",
        payload={"symbol": "AAPL", "bid_price": "250.01", "ask_price": "250.02"},
        observation_at=_OBSERVED,
    )
    with pytest.raises(ValueError):
        build_normalized_record(**{**policy_values, "coverage_warning": None})

    record = build_normalized_record(
        **{**policy_values, "coverage_warning": "IEX feed; not full NBBO"}
    )
    assert record.coverage is CoverageLabel.LIMITED_MARKET_COVERAGE
    assert record.coverage_warning == "IEX feed; not full NBBO"


def test_discovery_records_cannot_be_material() -> None:
    with pytest.raises(ValueError):
        build_normalized_record(
            **_base_values(
                record_id="tavily-result-0001",
                family=P4SourceFamily.TAVILY,
                endpoint_id="tavily_search",
                payload={"title": "headline", "url": "https://example.com/a"},
                material_claim=True,
            )
        )


def test_supplement_records_cannot_be_material() -> None:
    with pytest.raises(ValueError):
        build_normalized_record(
            **_base_values(
                record_id="yfinance-chart-0001",
                family=P4SourceFamily.YFINANCE,
                endpoint_id="yahoo_chart",
                payload={"symbol": "AAPL", "price": "250.00"},
                material_claim=True,
            )
        )


def test_unknown_timestamps_stay_none_and_are_not_guessed() -> None:
    record = build_normalized_record(**_base_values())

    assert record.observation_at is None
    assert record.published_at is None
    assert record.available_at is None
    assert record.effective_at is None
    assert record.vintage is None
    assert record.supersedes_content_hash is None


def test_timestamp_ordering_is_enforced() -> None:
    later = UtcTimestamp.from_isoformat("2026-08-27T16:30:00.000000Z")
    with pytest.raises(ValueError):
        build_normalized_record(**_base_values(observation_at=later))


def test_record_hash_is_domain_separated_and_tamper_detected() -> None:
    record = build_normalized_record(**_base_values())

    tampered = object.__new__(NormalizedSourceRecord)
    for field_item in dataclasses.fields(NormalizedSourceRecord):
        object.__setattr__(tampered, field_item.name, getattr(record, field_item.name))
    tampered_payload = JsonObject.from_value({"symbol": "MSFT", "status": "active"})
    object.__setattr__(tampered, "payload", tampered_payload)
    with pytest.raises(ValueError, match="record_hash"):
        tampered.verify_integrity()


def test_wire_round_trip_via_canonical_payload() -> None:
    record = build_normalized_record(**_base_values())

    wire = record.wire()
    assert wire["family"] == "ALPACA_ASSETS"
    assert wire["record_id"] == "alpaca-asset-test-0001"
    assert "record_hash" not in wire
    assert record.compute_hash() == record.record_hash
    assert record.verify_integrity() is True


def test_strict_json_rejects_duplicate_keys_and_nonfinite() -> None:
    with pytest.raises(SourceSchemaDriftError):
        strict_json_loads(b'{"a": 1, "a": 2}')
    with pytest.raises(SourceSchemaDriftError):
        strict_json_loads(b'{"a": NaN}')
    with pytest.raises(SourceSchemaDriftError):
        strict_json_loads(b"[1, 2, 3"[:3])  # truncated
    with pytest.raises(SourceSchemaDriftError):
        strict_json_loads(b'"scalar"')
    assert strict_json_loads(b'{"a": 1}') == {"a": 1}


def test_strict_json_maps_parser_recursion_to_bounded_schema_drift() -> None:
    deeply_nested = b"[" * 10_000 + b"0" + b"]" * 10_000

    with pytest.raises(SourceSchemaDriftError):
        strict_json_loads(deeply_nested)


def test_record_endpoint_must_belong_to_its_source_family() -> None:
    with pytest.raises(ValueError, match="endpoint_id"):
        build_normalized_record(
            **_base_values(
                endpoint_id="tavily_search",
                record_id="sec-endpoint-mismatch-0001",
            )
        )


def test_parse_provider_timestamp_accepts_bounded_canonical_variants() -> None:
    canonical = parse_provider_timestamp("2026-08-27T15:30:00.123456Z")
    assert canonical == UtcTimestamp(_RETRIEVED.value.replace(microsecond=123456))
    no_fraction = parse_provider_timestamp("2026-08-27T15:30:00Z")
    assert str(no_fraction) == "2026-08-27T15:30:00.000000Z"
    compact = parse_provider_timestamp("20260827153000")
    assert str(compact) == "2026-08-27T15:30:00.000000Z"
    with pytest.raises(ProviderTimestampError):
        parse_provider_timestamp("2026-08-27 15:30:00 +0000")
    with pytest.raises(ProviderTimestampError):
        parse_provider_timestamp("not-a-time")
    with pytest.raises(ProviderTimestampError):
        parse_provider_timestamp("2026-13-40T00:00:00Z")


def test_provider_utc_date_converts_date_only_semantics() -> None:
    stamp = provider_utc_date("2026-01-15")
    assert str(stamp) == "2026-01-15T00:00:00.000000Z"
    with pytest.raises(ProviderTimestampError):
        provider_utc_date("2026-02-30")
