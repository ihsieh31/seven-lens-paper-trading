# mypy: ignore-errors
"""P4-B corporate-action records and the closed split state machine.

Only FORWARD_SPLIT and REVERSE_SPLIT exist here.  Ratios are exact positive
rationals, never floats.  Every transition re-verifies the previous head, the
immutable event facts (identity version, ratio, dates, type), and decision-time
monotonicity; illegal transitions are rejected at the record level, not only
in a service layer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.securities.contracts import SecurityId, SourceRef
from seven_lens.securities.corporate_actions import (
    CorporateActionRecord,
    CorporateActionState,
    CorporateActionType,
    IllegalTransitionError,
    SplitRatio,
    allowed_transitions,
    build_corporate_action_record,
    is_legal_transition,
    is_terminal,
    parse_action_type,
    validate_lineage,
    validate_transition,
)
from seven_lens.sources.roles import P4SourceFamily

_T_DECL = UtcTimestamp(datetime(2026, 1, 5, 14, 30, 0, tzinfo=UTC))
_T_AVAIL = UtcTimestamp(datetime(2026, 1, 5, 15, 0, 0, tzinfo=UTC))
_T_BLOCK = UtcTimestamp(datetime(2026, 1, 5, 15, 5, 0, tzinfo=UTC))
_T_CONFIRM = UtcTimestamp(datetime(2026, 1, 5, 16, 0, 0, tzinfo=UTC))
_T_EFFECTIVE = UtcTimestamp(datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC))
_EX_DATE = TradingDate.from_isoformat("2026-02-01")
_EFFECTIVE_DATE = TradingDate.from_isoformat("2026-02-01")
_SEC = SecurityId("0d96f15b-8b11-4f84-8c2c-6f6f6f6f6f6f")
_IDENTITY_HASH = "a" * 64
_SOURCE_HASH = "b" * 64
_OTHER_HASH = "c" * 64
_SCHEMA = SchemaVersion("1.0.0")


def _ref(record_id: str = "alpaca-ca-1", record_hash: str = _SOURCE_HASH) -> SourceRef:
    return SourceRef(
        record_id=record_id,
        family=P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
        record_hash=record_hash,
    )


def _record(**overrides: object) -> CorporateActionRecord:
    values: dict[str, object] = {
        "event_id": "evt-split-0001",
        "security_id": _SEC,
        "security_identity_hash": _IDENTITY_HASH,
        "action_type": CorporateActionType.FORWARD_SPLIT,
        "ratio": SplitRatio.from_fraction(numerator=3, denominator=2),
        "declared_at": _T_DECL,
        "ex_date": _EX_DATE,
        "effective_date": _EFFECTIVE_DATE,
        "available_at": _T_AVAIL,
        "state": CorporateActionState.DETECTED,
        "source_refs": (_ref(),),
        "schema_version": _SCHEMA,
    }
    values.update(overrides)
    return build_corporate_action_record(**values)


def _transition(previous: CorporateActionRecord, state: CorporateActionState, at: UtcTimestamp):
    return _record(
        event_id=previous.event_id,
        state=state,
        available_at=at,
        source_refs=(_ref(record_id="sec-edgar-filing-1", record_hash=_OTHER_HASH),),
    )


# --- ratio -----------------------------------------------------------------


def test_ratio_from_decimal_text_is_exact_and_normalized() -> None:
    assert SplitRatio.from_decimal_text("1.5") == SplitRatio.from_fraction(
        numerator=3, denominator=2
    )
    assert SplitRatio.from_decimal_text("2") == SplitRatio.from_fraction(numerator=2, denominator=1)
    assert SplitRatio.from_decimal_text("0.5") == SplitRatio.from_fraction(
        numerator=1, denominator=2
    )


def test_ratio_extreme_values_stay_exact() -> None:
    tiny = SplitRatio.from_decimal_text("0.000001")
    assert (tiny.numerator, tiny.denominator) == (1, 1_000_000)
    huge = SplitRatio.from_fraction(numerator=1_000_000, denominator=1)
    assert (huge.numerator, huge.denominator) == (1_000_000, 1)
    normalized = SplitRatio.from_fraction(numerator=2, denominator=4)
    assert (normalized.numerator, normalized.denominator) == (1, 2)


def test_ratio_rejects_floats_everywhere() -> None:
    with pytest.raises(ValueError, match="never a float"):
        SplitRatio.from_fraction(numerator=1.5, denominator=1)
    with pytest.raises(ValueError, match="never a float"):
        SplitRatio.from_fraction(numerator=3, denominator=2.0)
    with pytest.raises(ValueError, match="never a float"):
        SplitRatio.from_decimal(1.5)
    with pytest.raises(ValueError, match="string"):
        SplitRatio.from_decimal_text(1.5)


def test_ratio_rejects_zero_negative_nan_infinity() -> None:
    for text in ("0", "-2.5", "NaN", "Infinity", "-Infinity", "-0.0"):
        with pytest.raises(ValueError):
            SplitRatio.from_decimal_text(text)
    with pytest.raises(ValueError):
        SplitRatio.from_fraction(numerator=0, denominator=1)
    with pytest.raises(ValueError):
        SplitRatio.from_fraction(numerator=1, denominator=-1)


def test_ratio_rejects_unnormalized_or_bool_direct_construction() -> None:
    with pytest.raises(ValueError, match="normalized"):
        SplitRatio(numerator=2, denominator=4)
    with pytest.raises(ValueError, match="int"):
        SplitRatio(numerator=True, denominator=1)
    with pytest.raises(ValueError, match="positive"):
        SplitRatio(numerator=0, denominator=1)


def test_ratio_wire_is_exact_rational() -> None:
    ratio = SplitRatio.from_decimal_text("1.5")
    assert ratio.wire() == {"numerator": 3, "denominator": 2}


# --- action type -----------------------------------------------------------


def test_action_type_is_closed_to_splits() -> None:
    assert set(CorporateActionType) == {
        CorporateActionType.FORWARD_SPLIT,
        CorporateActionType.REVERSE_SPLIT,
    }


def test_parse_action_type_rejects_unsupported_actions() -> None:
    assert parse_action_type("forward_split") is CorporateActionType.FORWARD_SPLIT
    assert parse_action_type("reverse_split") is CorporateActionType.REVERSE_SPLIT
    for bad in ("dividend", "merger", "FORWARD_SPLIT", "", 123, None):
        with pytest.raises(ValueError):
            parse_action_type(bad)


# --- state machine table ---------------------------------------------------


def test_transition_table_is_exactly_the_closed_machine() -> None:
    d = CorporateActionState.DETECTED
    b = CorporateActionState.ENTRY_BLOCKED
    c = CorporateActionState.CONFIRMED
    r = CorporateActionState.REVIEW_REQUIRED
    e = CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION
    assert allowed_transitions() == frozenset({(d, b), (b, c), (d, r), (b, r), (c, r), (c, e)})


def test_illegal_transitions_are_rejected_at_the_table() -> None:
    states = tuple(CorporateActionState)
    for from_state in states:
        for to_state in states:
            expected = (from_state, to_state) in allowed_transitions()
            assert is_legal_transition(from_state=from_state, to_state=to_state) is expected
    assert not is_legal_transition(
        from_state=CorporateActionState.DETECTED, to_state=CorporateActionState.CONFIRMED
    )
    assert not is_legal_transition(
        from_state=CorporateActionState.REVIEW_REQUIRED,
        to_state=CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION,
    )


def test_terminal_states_have_no_exits() -> None:
    assert is_terminal(CorporateActionState.REVIEW_REQUIRED)
    assert is_terminal(CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION)
    for state in (
        CorporateActionState.DETECTED,
        CorporateActionState.ENTRY_BLOCKED,
        CorporateActionState.CONFIRMED,
    ):
        assert not is_terminal(state)


# --- record construction ---------------------------------------------------


def test_build_detected_record_derives_and_verifies_hash() -> None:
    record = _record()
    assert record.verify_integrity() is True
    wire = record.wire()
    assert wire["event_id"] == "evt-split-0001"
    assert wire["security_id"] == _SEC.value
    assert wire["security_identity_hash"] == _IDENTITY_HASH
    assert wire["action_type"] == "forward_split"
    assert wire["ratio"] == {"numerator": 3, "denominator": 2}
    assert wire["ex_date"] == "2026-02-01"
    assert wire["effective_date"] == "2026-02-01"
    assert wire["state"] == "detected"
    assert record.record_hash == record.compute_hash()


def test_post_construction_tamper_breaks_integrity() -> None:
    record = _record()
    object.__setattr__(record, "ratio", SplitRatio.from_fraction(numerator=2, denominator=1))
    with pytest.raises(ValueError, match="hash"):
        record.verify_integrity()


def test_record_rejects_inexact_or_wrong_types() -> None:
    with pytest.raises(ValueError, match="ratio"):
        _record(ratio=1.5)
    with pytest.raises(ValueError, match="state"):
        _record(state="detected")
    with pytest.raises(ValueError, match="declared_at"):
        _record(declared_at="2026-01-05T14:30:00.000000Z")
    with pytest.raises(ValueError, match="ex_date"):
        _record(ex_date=_T_DECL)
    with pytest.raises(ValueError, match="action_type"):
        _record(action_type="forward_split")


def test_record_rejects_bad_event_or_identity_identifiers() -> None:
    with pytest.raises(ValueError, match="event id"):
        _record(event_id="has space")
    with pytest.raises(ValueError, match="event id"):
        _record(event_id="")
    with pytest.raises(ValueError, match="identity hash"):
        _record(security_identity_hash="xyz")


def test_record_rejects_disordered_dates() -> None:
    with pytest.raises(ValueError, match="ex date"):
        _record(ex_date=TradingDate.from_isoformat("2026-01-04"))
    with pytest.raises(ValueError, match="effective date"):
        _record(effective_date=TradingDate.from_isoformat("2026-01-31"))


def test_record_rejects_availability_before_declaration() -> None:
    with pytest.raises(ValueError, match="available_at"):
        _record(available_at=UtcTimestamp(datetime(2026, 1, 5, 14, 0, 0, tzinfo=UTC)))


def test_record_source_refs_are_bounded_and_unique() -> None:
    with pytest.raises(ValueError, match="source_refs"):
        _record(source_refs=())
    with pytest.raises(ValueError, match="source_refs"):
        _record(source_refs=tuple(_ref(record_id=f"ref-{i}") for i in range(17)))
    with pytest.raises(ValueError, match="unique"):
        _record(source_refs=(_ref(), _ref()))


# --- transition validation -------------------------------------------------


def test_legal_transition_detected_to_entry_blocked() -> None:
    head = _record()
    blocked = _transition(head, CorporateActionState.ENTRY_BLOCKED, _T_BLOCK)
    validate_transition(head, blocked)
    assert blocked.state is CorporateActionState.ENTRY_BLOCKED


def test_illegal_transition_detected_to_confirmed_raises() -> None:
    head = _record()
    confirmed = _transition(head, CorporateActionState.CONFIRMED, _T_CONFIRM)
    with pytest.raises(IllegalTransitionError, match="illegal"):
        validate_transition(head, confirmed)


def test_transition_rejects_immutable_fact_drift() -> None:
    head = _record()
    drifted = _transition(head, CorporateActionState.ENTRY_BLOCKED, _T_BLOCK)
    object.__setattr__(drifted, "ratio", SplitRatio.from_fraction(numerator=2, denominator=1))
    object.__setattr__(drifted, "record_hash", drifted.compute_hash())
    with pytest.raises(IllegalTransitionError, match="ratio"):
        validate_transition(head, drifted)
    retyped = _record(
        event_id=head.event_id,
        action_type=CorporateActionType.REVERSE_SPLIT,
        state=CorporateActionState.ENTRY_BLOCKED,
        available_at=_T_BLOCK,
    )
    with pytest.raises(IllegalTransitionError, match="action_type"):
        validate_transition(head, retyped)


def test_transition_rejects_identity_version_drift() -> None:
    head = _record()
    drifted = _record(
        event_id=head.event_id,
        security_identity_hash=_OTHER_HASH,
        state=CorporateActionState.ENTRY_BLOCKED,
        available_at=_T_BLOCK,
    )
    with pytest.raises(IllegalTransitionError, match="security_identity_hash"):
        validate_transition(head, drifted)


def test_transition_rejects_decision_time_regression() -> None:
    head = _record()
    regressed = _transition(
        head,
        CorporateActionState.ENTRY_BLOCKED,
        UtcTimestamp(datetime(2026, 1, 5, 14, 45, 0, tzinfo=UTC)),
    )
    with pytest.raises(IllegalTransitionError, match="decision time"):
        validate_transition(head, regressed)


def test_transition_rejects_tampered_previous_head() -> None:
    head = _record()
    blocked = _transition(head, CorporateActionState.ENTRY_BLOCKED, _T_BLOCK)
    object.__setattr__(head, "state", CorporateActionState.CONFIRMED)
    with pytest.raises(ValueError, match="hash"):
        validate_transition(head, blocked)


def test_transition_rejects_cross_event_rows() -> None:
    head = _record()
    other = _record(
        event_id="evt-split-0002", state=CorporateActionState.ENTRY_BLOCKED, available_at=_T_BLOCK
    )
    with pytest.raises(IllegalTransitionError, match="one corporate-action event"):
        validate_transition(head, other)


# --- lineage ---------------------------------------------------------------


def test_full_lineage_reaches_effective_pending_reconciliation() -> None:
    detected = _record()
    blocked = _transition(detected, CorporateActionState.ENTRY_BLOCKED, _T_BLOCK)
    confirmed = _transition(blocked, CorporateActionState.CONFIRMED, _T_CONFIRM)
    effective = _transition(
        confirmed, CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION, _T_EFFECTIVE
    )
    head = validate_lineage((detected, blocked, confirmed, effective))
    assert head is effective
    assert head.state is CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION


def test_withdrawal_after_confirmation_forces_review() -> None:
    detected = _record()
    blocked = _transition(detected, CorporateActionState.ENTRY_BLOCKED, _T_BLOCK)
    confirmed = _transition(blocked, CorporateActionState.CONFIRMED, _T_CONFIRM)
    review = _transition(confirmed, CorporateActionState.REVIEW_REQUIRED, _T_EFFECTIVE)
    assert validate_lineage((detected, blocked, confirmed, review)) is review


def test_lineage_must_begin_at_detected() -> None:
    detected = _record()
    blocked = _transition(detected, CorporateActionState.ENTRY_BLOCKED, _T_BLOCK)
    with pytest.raises(IllegalTransitionError, match="DETECTED"):
        validate_lineage((blocked,))


def test_lineage_rejects_out_of_order_rows() -> None:
    detected = _record()
    confirmed = _transition(detected, CorporateActionState.CONFIRMED, _T_CONFIRM)
    blocked = _transition(detected, CorporateActionState.ENTRY_BLOCKED, _T_BLOCK)
    with pytest.raises(IllegalTransitionError):
        validate_lineage((detected, confirmed, blocked))


def test_lineage_is_bounded() -> None:
    with pytest.raises(ValueError, match="tuple"):
        validate_lineage(())
    with pytest.raises(ValueError, match="tuple"):
        validate_lineage([_record()])


def test_hash_is_deterministic_and_separates_time_axes() -> None:
    base = _record()
    assert base.record_hash == _record().record_hash
    shifted = _record(declared_at=UtcTimestamp(datetime(2026, 1, 5, 14, 30, 0, 1, tzinfo=UTC)))
    assert shifted.record_hash != base.record_hash
    wire = base.wire()
    assert set(wire) >= {
        "event_id",
        "security_id",
        "security_identity_hash",
        "action_type",
        "ratio",
        "declared_at",
        "ex_date",
        "effective_date",
        "available_at",
        "state",
        "source_refs",
        "schema_version",
        "producer_version",
    }
    assert wire["declared_at"] != wire["available_at"]
