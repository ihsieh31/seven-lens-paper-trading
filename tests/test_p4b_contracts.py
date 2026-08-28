# mypy: ignore-errors
"""P4-B identity contracts: pure validators and point-in-time time-travel tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from seven_lens.domain.value_objects import SchemaVersion, UtcTimestamp
from seven_lens.securities.contracts import (
    MAX_SOURCE_REFS,
    AssetClass,
    Cik,
    Cusip,
    Isin,
    ListingExchange,
    SecurityId,
    SecurityIdentityRecord,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    ValidityInterval,
    build_identity_record,
    intervals_overlap,
    producer_version,
)
from seven_lens.sources.roles import P4SourceFamily

_T0 = UtcTimestamp.from_isoformat("2026-01-01T00:00:00.000000Z")
_T1 = UtcTimestamp.from_isoformat("2026-02-01T00:00:00.000000Z")
_T2 = UtcTimestamp.from_isoformat("2026-03-01T00:00:00.000000Z")
_T3 = UtcTimestamp.from_isoformat("2026-04-01T00:00:00.000000Z")
_MICRO = timedelta(microseconds=1)


def _shift(ts: UtcTimestamp, delta: timedelta) -> UtcTimestamp:
    return UtcTimestamp(ts.value + delta)


_ASSET_ID = "0d96f15b-8b11-4f84-8c2c-6f6f6f6f6f6f"
_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _source_ref(record_id: str = "alpaca-asset-x", record_hash: str = _HASH_A) -> SourceRef:
    return SourceRef(
        record_id=record_id,
        family=P4SourceFamily.ALPACA_ASSETS,
        record_hash=record_hash,
    )


def _identity_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "security_id": SecurityId(_ASSET_ID),
        "symbol": SecuritySymbol("AAPL"),
        "exchange": ListingExchange.NASDAQ,
        "asset_class": AssetClass.US_EQUITY,
        "cik": Cik("0000320193"),
        "cusip": Cusip("037833100"),
        "isin": Isin("US0378331005"),
        "valid_from": _T1,
        "valid_to": None,
        "available_at": _T0,
        "status": SecurityStatus.ACTIVE,
        "source_refs": (_source_ref(),),
        "schema_version": SchemaVersion("1.0.0"),
    }
    values.update(overrides)
    return values


# --- closed enums -----------------------------------------------------------


def test_enums_are_closed_and_bounded() -> None:
    assert list(AssetClass) == [AssetClass.US_EQUITY]
    assert list(ListingExchange) == [
        ListingExchange.AMEX,
        ListingExchange.ARCA,
        ListingExchange.BATS,
        ListingExchange.NASDAQ,
        ListingExchange.NYSE,
    ]
    assert list(SecurityStatus) == [SecurityStatus.ACTIVE, SecurityStatus.INACTIVE]


# --- exact identifiers ------------------------------------------------------


def test_security_id_requires_canonical_lowercase_provider_identity() -> None:
    assert SecurityId(_ASSET_ID).value == _ASSET_ID
    with pytest.raises(ValueError, match="security id"):
        SecurityId("0D96F15B-8B11-4F84-8C2C-6F6F6F6F6F6F")
    with pytest.raises(ValueError, match="security id"):
        SecurityId("short")
    with pytest.raises(ValueError, match="security id"):
        SecurityId("x" * 65)
    with pytest.raises(ValueError, match="security id"):
        SecurityId(123)  # type: ignore[arg-type]


def test_symbol_uses_the_canonical_broker_form() -> None:
    assert SecuritySymbol("AAPL").value == "AAPL"
    assert SecuritySymbol("BRK.B").value == "BRK.B"
    assert SecuritySymbol("A" * 10).value == "A" * 10
    with pytest.raises(ValueError, match="symbol"):
        SecuritySymbol("aapl")
    with pytest.raises(ValueError, match="symbol"):
        SecuritySymbol("A" * 11)
    with pytest.raises(ValueError, match="symbol"):
        SecuritySymbol(".AAPL")
    with pytest.raises(ValueError, match="symbol"):
        SecuritySymbol("")


def test_cik_cusip_isin_validate_exact_shapes() -> None:
    assert Cik("0000320193").value == "0000320193"
    with pytest.raises(ValueError, match="cik"):
        Cik("320193")
    with pytest.raises(ValueError, match="cik"):
        Cik("00003201930")

    assert Cusip("037833100").value == "037833100"
    with pytest.raises(ValueError, match="cusip"):
        Cusip("03783310")
    with pytest.raises(ValueError, match="cusip"):
        Cusip("0378331005")

    assert Isin("US0378331005").value == "US0378331005"
    with pytest.raises(ValueError, match="isin"):
        Isin("us0378331005")
    with pytest.raises(ValueError, match="isin"):
        Isin("US037833100")


# --- valid/available intervals ----------------------------------------------


def test_validity_interval_is_half_open() -> None:
    interval = ValidityInterval(valid_from=_T1, valid_to=_T2)
    assert interval.contains(_T1) is True
    assert interval.contains(_shift(_T1, _MICRO)) is True
    assert interval.contains(_shift(_T2, -_MICRO)) is True
    assert interval.contains(_T2) is False
    assert interval.contains(_shift(_T2, _MICRO)) is False
    assert interval.contains(_shift(_T1, -_MICRO)) is False


def test_validity_interval_open_end_never_expires() -> None:
    interval = ValidityInterval(valid_from=_T1, valid_to=None)
    assert interval.contains(_T1) is True
    assert interval.contains(UtcTimestamp.from_isoformat("2999-12-31T23:59:59.999999Z")) is True
    assert interval.contains(_shift(_T1, -_MICRO)) is False


def test_validity_interval_rejects_inverted_or_degenerate_bounds() -> None:
    with pytest.raises(ValueError, match="interval"):
        ValidityInterval(valid_from=_T2, valid_to=_T1)
    with pytest.raises(ValueError, match="interval"):
        ValidityInterval(valid_from=_T1, valid_to=_T1)
    with pytest.raises(ValueError, match="interval"):
        ValidityInterval(valid_from="2026-01-01", valid_to=None)  # type: ignore[arg-type]


def test_intervals_overlap_detects_any_shared_instant() -> None:
    first = ValidityInterval(valid_from=_T1, valid_to=_T2)
    assert intervals_overlap(first, ValidityInterval(valid_from=_T1, valid_to=_T2)) is True
    assert intervals_overlap(first, ValidityInterval(valid_from=_T2, valid_to=_T3)) is False
    assert intervals_overlap(first, ValidityInterval(valid_from=_T0, valid_to=_T1)) is False
    assert (
        intervals_overlap(first, ValidityInterval(valid_from=_shift(_T2, -_MICRO), valid_to=None))
        is True
    )
    assert intervals_overlap(first, ValidityInterval(valid_from=_T0, valid_to=None)) is True


# --- source refs -------------------------------------------------------------


def test_source_ref_requires_hash_bound_lineage() -> None:
    ref = _source_ref()
    assert ref.record_id == "alpaca-asset-x"
    assert ref.family is P4SourceFamily.ALPACA_ASSETS
    with pytest.raises(ValueError, match="record hash"):
        SourceRef(record_id="r", family=P4SourceFamily.SEC_EDGAR, record_hash="ZZZ")
    with pytest.raises(ValueError, match="record id"):
        SourceRef(record_id="", family=P4SourceFamily.SEC_EDGAR, record_hash=_HASH_A)
    with pytest.raises(ValueError, match="family"):
        SourceRef(record_id="r", family="SEC_EDGAR", record_hash=_HASH_A)  # type: ignore[arg-type]


# --- SecurityIdentityRecord ---------------------------------------------------


def test_identity_record_builds_with_derived_hash() -> None:
    record = build_identity_record(**_identity_values())
    assert record.identity_hash == record.compute_hash()
    assert record.verify_integrity() is True
    assert record.producer_version == producer_version()


def test_identity_record_wire_is_canonical_and_content_bound() -> None:
    first = build_identity_record(**_identity_values())
    second = build_identity_record(**_identity_values())
    assert first.wire() == second.wire()
    assert first.identity_hash == second.identity_hash

    changed = build_identity_record(**_identity_values(symbol=SecuritySymbol("MSFT")))
    assert changed.identity_hash != first.identity_hash

    late = build_identity_record(**_identity_values(available_at=_T2))
    assert late.identity_hash != first.identity_hash


def test_identity_record_rejects_wrong_hash_and_tamper() -> None:
    with pytest.raises(ValueError, match="identity hash"):
        SecurityIdentityRecord(**_identity_values(identity_hash="f" * 64))  # type: ignore[arg-type]

    record = build_identity_record(**_identity_values())
    object.__setattr__(record, "symbol", SecuritySymbol("MSFT"))
    with pytest.raises(ValueError, match="identity hash"):
        record.verify_integrity()


def test_identity_record_requires_exact_types() -> None:
    with pytest.raises(ValueError, match="security_id"):
        build_identity_record(**_identity_values(security_id=_ASSET_ID))
    with pytest.raises(ValueError, match="symbol"):
        build_identity_record(**_identity_values(symbol="AAPL"))
    with pytest.raises(ValueError, match="exchange"):
        build_identity_record(**_identity_values(exchange="NASDAQ"))
    with pytest.raises(ValueError, match="asset_class"):
        build_identity_record(**_identity_values(asset_class="us_equity"))
    with pytest.raises(ValueError, match="status"):
        build_identity_record(**_identity_values(status="active"))
    with pytest.raises(ValueError, match="valid_from"):
        build_identity_record(**_identity_values(valid_from=str(_T1)))
    with pytest.raises(ValueError, match="available_at"):
        build_identity_record(**_identity_values(available_at=str(_T0)))
    with pytest.raises(ValueError, match="schema_version"):
        build_identity_record(**_identity_values(schema_version="1.0.0"))


def test_identity_record_rejects_bad_intervals_and_refs() -> None:
    with pytest.raises(ValueError, match="interval"):
        build_identity_record(**_identity_values(valid_from=_T2, valid_to=_T1))
    with pytest.raises(ValueError, match="source_refs"):
        build_identity_record(**_identity_values(source_refs=()))
    with pytest.raises(ValueError, match="source_refs"):
        build_identity_record(
            **_identity_values(
                source_refs=tuple(
                    _source_ref(record_id=f"r{i}", record_hash=_HASH_A)
                    for i in range(MAX_SOURCE_REFS + 1)
                )
            )
        )
    with pytest.raises(ValueError, match="source_refs"):
        build_identity_record(
            **_identity_values(source_refs=(_source_ref(), _source_ref(record_hash=_HASH_B)))
        )
    with pytest.raises(ValueError, match="source_refs"):
        build_identity_record(**_identity_values(source_refs=[_source_ref()]))


def test_identity_record_optional_identifiers_may_be_absent_but_never_guessed() -> None:
    record = build_identity_record(**_identity_values(cik=None, cusip=None, isin=None))
    assert record.cik is None
    assert record.cusip is None
    assert record.isin is None
    assert record.verify_integrity() is True
    with pytest.raises(ValueError, match="cik"):
        build_identity_record(**_identity_values(cik="320193"))


# --- time travel -------------------------------------------------------------


def test_future_record_is_invisible_at_a_historical_cutoff() -> None:
    record = build_identity_record(**_identity_values(valid_from=_T0, available_at=_T2))
    assert record.known_at(_T2) is True
    assert record.known_at(_shift(_T2, -_MICRO)) is False
    assert record.known_at(_T1) is False


def test_valid_at_uses_real_world_validity_not_system_knowledge() -> None:
    record = build_identity_record(**_identity_values(valid_from=_T1, valid_to=_T2))
    assert record.valid_at(_T1) is True
    assert record.valid_at(_shift(_T2, -_MICRO)) is True
    assert record.valid_at(_T2) is False
    assert record.valid_at(_shift(_T1, -_MICRO)) is False


def test_as_of_query_requires_both_visibility_and_validity() -> None:
    # Learned late (available_at=T2) about a real-world interval [T0, T2):
    record = build_identity_record(
        **_identity_values(valid_from=_T0, valid_to=_T2, available_at=_T2)
    )
    # Historical as-of before the system could know: never answer from it.
    assert record.answers_as_of(as_of=_T1, known_at=_T1) is False
    # Same real-world instant, queried with present knowledge: valid.
    assert record.answers_as_of(as_of=_T1, known_at=_T2) is True
    # Real-world instant outside validity: never valid regardless of knowledge.
    assert record.answers_as_of(as_of=_T3, known_at=_T3) is False


def test_symbol_reuse_and_rename_are_both_representable() -> None:
    # Two different securities share one ticker across time (reuse).
    old_holder = build_identity_record(
        **_identity_values(
            security_id=SecurityId("11111111-1111-4111-8111-111111111111"),
            symbol=SecuritySymbol("ACME"),
            valid_from=_T0,
            valid_to=_T1,
            available_at=_T0,
        )
    )
    new_holder = build_identity_record(
        **_identity_values(
            security_id=SecurityId("22222222-2222-4222-8222-222222222222"),
            symbol=SecuritySymbol("ACME"),
            valid_from=_T2,
            valid_to=None,
            available_at=_T2,
        )
    )
    assert old_holder.security_id != new_holder.security_id
    assert old_holder.symbol == new_holder.symbol

    # One stable security changes its ticker (rename).
    before_rename = build_identity_record(
        **_identity_values(symbol=SecuritySymbol("OLDTK"), valid_from=_T0, valid_to=_T1)
    )
    after_rename = build_identity_record(
        **_identity_values(symbol=SecuritySymbol("NEWTK"), valid_from=_T1, valid_to=None)
    )
    assert before_rename.security_id == after_rename.security_id
    assert before_rename.symbol != after_rename.symbol


def test_wire_round_trips_point_in_time_fields_without_merging_them() -> None:
    record = build_identity_record(**_identity_values(valid_from=_T1, valid_to=_T2))
    wire = record.wire()
    assert wire["valid_from"] == str(_T1)
    assert wire["valid_to"] == str(_T2)
    assert wire["available_at"] == str(_T0)
    assert wire["valid_from"] != wire["available_at"]
    assert wire["security_id"] == _ASSET_ID
    assert wire["symbol"] == "AAPL"
    assert wire["producer_version"] == producer_version()
    assert wire["schema_version"] == "1.0.0"
    assert wire["source_refs"] == [
        {"record_id": "alpaca-asset-x", "family": "ALPACA_ASSETS", "record_hash": _HASH_A}
    ]
