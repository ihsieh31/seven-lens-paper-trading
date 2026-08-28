# mypy: ignore-errors
"""P4-B identity resolver: typed UNKNOWN/AMBIGUOUS/CONFLICT/STALE, time travel."""

from __future__ import annotations

from datetime import timedelta
from itertools import permutations

import pytest

from seven_lens.domain.value_objects import SchemaVersion, UtcTimestamp
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
from seven_lens.securities.identity import (
    MAX_RESOLUTION_RECORDS,
    IdentityQuery,
    IdentityResolution,
    IdentityResolutionStatus,
    resolve_identity,
)
from seven_lens.sources.roles import P4SourceFamily

_T0 = UtcTimestamp.from_isoformat("2026-01-01T00:00:00.000000Z")
_T1 = UtcTimestamp.from_isoformat("2026-02-01T00:00:00.000000Z")
_T2 = UtcTimestamp.from_isoformat("2026-03-01T00:00:00.000000Z")
_T3 = UtcTimestamp.from_isoformat("2026-04-01T00:00:00.000000Z")
_MICRO = timedelta(microseconds=1)

_SEC_A = SecurityId("11111111-1111-4111-8111-111111111111")
_SEC_B = SecurityId("22222222-2222-4222-8222-222222222222")
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64


def _shift(ts: UtcTimestamp, delta: timedelta) -> UtcTimestamp:
    return UtcTimestamp(ts.value + delta)


def _ref(record_id: str, record_hash: str = _HASH_A) -> SourceRef:
    return SourceRef(
        record_id=record_id, family=P4SourceFamily.ALPACA_ASSETS, record_hash=record_hash
    )


def _record(
    *,
    security_id: SecurityId = _SEC_A,
    symbol: str = "ACME",
    valid_from: UtcTimestamp = _T0,
    valid_to: UtcTimestamp | None = None,
    available_at: UtcTimestamp = _T0,
    cik: str | None = "0000000001",
    ref_id: str = "ref-1",
    ref_hash: str = _HASH_A,
):
    return build_identity_record(
        security_id=security_id,
        symbol=SecuritySymbol(symbol),
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        cik=None if cik is None else Cik(cik),
        valid_from=valid_from,
        valid_to=valid_to,
        available_at=available_at,
        status=SecurityStatus.ACTIVE,
        source_refs=(_ref(ref_id, ref_hash),),
        schema_version=SchemaVersion("1.0.0"),
    )


def _query(
    *,
    symbol: str | None = "ACME",
    security_id: SecurityId | None = None,
    as_of: UtcTimestamp = _T1,
    known_at: UtcTimestamp = _T3,
) -> IdentityQuery:
    return IdentityQuery(
        security_id=security_id,
        symbol=None if symbol is None else SecuritySymbol(symbol),
        as_of=as_of,
        known_at=known_at,
    )


# --- query validation ---------------------------------------------------------


def test_query_requires_an_identity_key_and_exact_types() -> None:
    with pytest.raises(ValueError, match="identity key"):
        IdentityQuery(security_id=None, symbol=None, as_of=_T1, known_at=_T3)
    with pytest.raises(ValueError, match="as_of"):
        IdentityQuery(symbol=SecuritySymbol("ACME"), as_of="2026-01-01", known_at=_T3)
    with pytest.raises(ValueError, match="known_at"):
        IdentityQuery(symbol=SecuritySymbol("ACME"), as_of=_T1, known_at=_T3.value)
    with pytest.raises(ValueError, match="symbol"):
        IdentityQuery(symbol="ACME", as_of=_T1, known_at=_T3)


def test_resolve_requires_a_bounded_exact_record_tuple() -> None:
    with pytest.raises(ValueError, match="records"):
        resolve_identity([_record()], _query())
    with pytest.raises(ValueError, match="records"):
        resolve_identity((_record(), "not-a-record"), _query())
    with pytest.raises(ValueError, match="records"):
        resolve_identity(
            tuple(
                _record(ref_id=f"ref-{i}", valid_from=_shift(_T0, timedelta(seconds=i)))
                for i in range(MAX_RESOLUTION_RECORDS + 1)
            ),
            _query(),
        )


def test_resolve_rejects_tampered_input_records() -> None:
    record = _record()
    object.__setattr__(record, "status", SecurityStatus.INACTIVE)
    with pytest.raises(ValueError, match="integrity"):
        resolve_identity((record,), _query())


# --- symbol reuse and rename --------------------------------------------------


def test_symbol_reuse_resolves_to_the_right_security_per_instant() -> None:
    old_holder = _record(
        security_id=_SEC_A, valid_from=_T0, valid_to=_T1, available_at=_T0, ref_id="ref-a"
    )
    new_holder = _record(
        security_id=_SEC_B, valid_from=_T2, valid_to=None, available_at=_T2, ref_id="ref-b"
    )
    records = (old_holder, new_holder)

    early = resolve_identity(records, _query(as_of=_shift(_T1, -_MICRO), known_at=_T3))
    assert early.status is IdentityResolutionStatus.RESOLVED
    assert early.record is old_holder

    gap = resolve_identity(records, _query(as_of=_T1, known_at=_T3))
    assert gap.status is IdentityResolutionStatus.UNKNOWN
    assert gap.record is None

    late = resolve_identity(records, _query(as_of=_T2, known_at=_T3))
    assert late.status is IdentityResolutionStatus.RESOLVED
    assert late.record is new_holder


def test_symbol_change_keeps_one_stable_security_across_time() -> None:
    before = _record(symbol="OLDTK", valid_from=_T0, valid_to=_T1, ref_id="ref-a")
    after = _record(symbol="NEWTK", valid_from=_T1, valid_to=None, ref_id="ref-b")
    records = (before, after)

    early = resolve_identity(records, _query(symbol="OLDTK", as_of=_shift(_T1, -_MICRO)))
    assert early.status is IdentityResolutionStatus.RESOLVED
    assert early.record is before

    late = resolve_identity(records, _query(symbol="NEWTK", as_of=_T1))
    assert late.status is IdentityResolutionStatus.RESOLVED
    assert late.record is after

    # The old ticker no longer resolves after the rename.
    stale_ticker = resolve_identity(records, _query(symbol="OLDTK", as_of=_T1))
    assert stale_ticker.status is IdentityResolutionStatus.UNKNOWN

    # By stable id, the same security resolves on both sides of the rename.
    by_id_early = resolve_identity(
        records, _query(symbol=None, security_id=_SEC_A, as_of=_shift(_T1, -_MICRO))
    )
    by_id_late = resolve_identity(records, _query(symbol=None, security_id=_SEC_A, as_of=_T2))
    assert by_id_early.record is before
    assert by_id_late.record is after


# --- late correction (append-only supersession) --------------------------------


def test_late_correction_supersedes_only_when_knowable() -> None:
    original = _record(
        cik="0000000001", valid_from=_T0, available_at=_T0, ref_id="ref-a", ref_hash=_HASH_A
    )
    corrected = _record(
        cik="0000000002", valid_from=_T0, available_at=_T2, ref_id="ref-b", ref_hash=_HASH_B
    )
    records = (original, corrected)

    before_correction = resolve_identity(records, _query(as_of=_T1, known_at=_T1))
    assert before_correction.status is IdentityResolutionStatus.RESOLVED
    assert before_correction.record is original

    after_correction = resolve_identity(records, _query(as_of=_T1, known_at=_T3))
    assert after_correction.status is IdentityResolutionStatus.RESOLVED
    assert after_correction.record is corrected
    assert after_correction.record.cik == Cik("0000000002")


def test_unorderable_same_instant_correction_is_a_conflict() -> None:
    first = _record(cik="0000000001", available_at=_T2, ref_id="ref-a", ref_hash=_HASH_A)
    second = _record(cik="0000000002", available_at=_T2, ref_id="ref-b", ref_hash=_HASH_B)
    resolution = resolve_identity((first, second), _query(as_of=_T3, known_at=_T3))
    assert resolution.status is IdentityResolutionStatus.CONFLICT
    assert resolution.record is None


def test_duplicate_identical_records_stay_resolved() -> None:
    record = _record()
    resolution = resolve_identity((record, record), _query())
    assert resolution.status is IdentityResolutionStatus.RESOLVED
    assert resolution.record is record


# --- typed failure modes -------------------------------------------------------


def test_no_records_is_unknown() -> None:
    resolution = resolve_identity((), _query())
    assert resolution.status is IdentityResolutionStatus.UNKNOWN


def test_future_availability_is_stale_not_unknown() -> None:
    future = _record(available_at=_T3)
    resolution = resolve_identity((future,), _query(as_of=_T1, known_at=_T2))
    assert resolution.status is IdentityResolutionStatus.STALE
    assert resolution.record is None


def test_expired_interval_with_no_successor_is_unknown() -> None:
    expired = _record(valid_from=_T0, valid_to=_T1)
    resolution = resolve_identity((expired,), _query(as_of=_T2, known_at=_T3))
    assert resolution.status is IdentityResolutionStatus.UNKNOWN


def test_overlapping_intervals_for_one_security_are_a_conflict() -> None:
    first = _record(valid_from=_T0, valid_to=_T2, ref_id="ref-a", ref_hash=_HASH_A)
    second = _record(valid_from=_T1, valid_to=None, ref_id="ref-b", ref_hash=_HASH_B)
    resolution = resolve_identity((first, second), _query(as_of=_T3, known_at=_T3))
    assert resolution.status is IdentityResolutionStatus.CONFLICT


def test_two_securities_claiming_one_symbol_at_one_instant_are_ambiguous() -> None:
    claim_a = _record(security_id=_SEC_A, valid_from=_T0, valid_to=None, ref_id="ref-a")
    claim_b = _record(
        security_id=_SEC_B,
        valid_from=_T0,
        valid_to=None,
        available_at=_T0,
        ref_id="ref-b",
        ref_hash=_HASH_B,
    )
    resolution = resolve_identity((claim_a, claim_b), _query(as_of=_T1, known_at=_T3))
    assert resolution.status is IdentityResolutionStatus.AMBIGUOUS
    assert resolution.record is None


def test_resolution_by_id_ignores_other_securities() -> None:
    record = _record(security_id=_SEC_A, ref_id="ref-a")
    other = _record(security_id=_SEC_B, ref_id="ref-b", ref_hash=_HASH_B)
    resolution = resolve_identity(
        (record, other), _query(symbol=None, security_id=_SEC_A, as_of=_T1, known_at=_T3)
    )
    assert resolution.status is IdentityResolutionStatus.RESOLVED
    assert resolution.record is record


# --- determinism and closed output ---------------------------------------------


def test_resolution_never_returns_none_and_is_order_independent() -> None:
    records = (
        _record(valid_from=_T0, valid_to=_T1, ref_id="ref-a", ref_hash=_HASH_A),
        _record(valid_from=_T1, valid_to=None, ref_id="ref-b", ref_hash=_HASH_B),
        _record(
            security_id=_SEC_B,
            valid_from=_T2,
            valid_to=None,
            available_at=_T2,
            ref_id="ref-c",
            ref_hash=_HASH_C,
        ),
    )
    query = _query(as_of=_shift(_T1, -_MICRO), known_at=_T3)
    baseline = resolve_identity(records, query)
    assert isinstance(baseline, IdentityResolution)
    for ordered in permutations(records):
        permuted = resolve_identity(ordered, query)
        assert permuted.status is baseline.status
        assert permuted.record == baseline.record


def test_resolution_is_deterministic_for_identical_inputs() -> None:
    records = (_record(),)
    query = _query()
    assert resolve_identity(records, query) == resolve_identity(records, query)


def test_as_of_boundary_microsecond_either_side_of_validity() -> None:
    bounded = _record(valid_from=_T1, valid_to=_T2)
    inside = resolve_identity((bounded,), _query(as_of=_shift(_T2, -_MICRO), known_at=_T3))
    outside = resolve_identity((bounded,), _query(as_of=_T2, known_at=_T3))
    assert inside.status is IdentityResolutionStatus.RESOLVED
    assert outside.status is IdentityResolutionStatus.UNKNOWN
