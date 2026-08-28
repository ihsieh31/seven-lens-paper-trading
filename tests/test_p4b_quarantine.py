# mypy: ignore-errors
"""P4-B unified quarantine query: one evaluator, three caller seams.

The same authoritative evaluator answers candidate creation, P4 Risk approval,
and future submit-time recheck with canonical-identical decisions.  Unknown
identity, symbol drift, stale master, future sources, conflicting identities,
and any unresolved split state all fail closed; nothing ever defaults to
ELIGIBLE on uncertainty.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.securities.contracts import (
    AssetClass,
    ListingExchange,
    SecurityId,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
    build_identity_record,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionState,
    CorporateActionType,
    IllegalTransitionError,
    SplitRatio,
    build_corporate_action_record,
)
from seven_lens.securities.quarantine import (
    ConfirmationOutcome,
    EventEvidence,
    QuarantineOutcome,
    QuarantinePurpose,
    QuarantineQuery,
    QuarantineReason,
    SourceObservation,
    evaluate_confirmation,
    evaluate_quarantine,
    master_version_for,
)
from seven_lens.sources.roles import P4SourceFamily

_T_DECL = UtcTimestamp(datetime(2026, 1, 5, 14, 30, 0, tzinfo=UTC))
_T_AVAIL = UtcTimestamp(datetime(2026, 1, 5, 15, 0, 0, tzinfo=UTC))
_T_BLOCK = UtcTimestamp(datetime(2026, 1, 5, 15, 5, 0, tzinfo=UTC))
_T_CONFIRM = UtcTimestamp(datetime(2026, 1, 6, 12, 0, 0, tzinfo=UTC))
_T_WITHDRAW = UtcTimestamp(datetime(2026, 1, 8, 9, 0, 0, tzinfo=UTC))
_T_EFFECTIVE = UtcTimestamp(datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC))
_T_DECISION = UtcTimestamp(datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC))
_EX_DATE = TradingDate.from_isoformat("2026-02-01")
_SCHEMA = SchemaVersion("1.0.0")
_SEC = SecurityId("0d96f15b-8b11-4f84-8c2c-6f6f6f6f6f6f")
_OTHER_SEC = SecurityId("1e96f15b-8b11-4f84-8c2c-6f6f6f6f6f6f")
_ALPACA_HASH = "b" * 64
_OFFICIAL_HASH = "c" * 64

_IDENTITY = build_identity_record(
    security_id=_SEC,
    symbol=SecuritySymbol("TEST"),
    exchange=ListingExchange.NASDAQ,
    asset_class=AssetClass.US_EQUITY,
    valid_from=UtcTimestamp(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
    available_at=UtcTimestamp(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
    status=SecurityStatus.ACTIVE,
    source_refs=(
        SourceRef(
            record_id="alpaca-asset-1",
            family=P4SourceFamily.ALPACA_ASSETS,
            record_hash="d" * 64,
        ),
    ),
    schema_version=_SCHEMA,
)
_MASTER_VERSION = master_version_for(_IDENTITY)

_ALPACA_REF = SourceRef(
    record_id="alpaca-ca-1",
    family=P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
    record_hash=_ALPACA_HASH,
)
_OFFICIAL_REF = SourceRef(
    record_id="official-sec-edgar-1",
    family=P4SourceFamily.SEC_EDGAR,
    record_hash=_OFFICIAL_HASH,
)


def _event(state: CorporateActionState, available_at: UtcTimestamp, event_id: str = "evt-1"):
    return build_corporate_action_record(
        event_id=event_id,
        security_id=_SEC,
        security_identity_hash=_IDENTITY.identity_hash,
        action_type=CorporateActionType.FORWARD_SPLIT,
        ratio=SplitRatio.from_fraction(numerator=3, denominator=2),
        declared_at=_T_DECL,
        ex_date=_EX_DATE,
        effective_date=_EX_DATE,
        available_at=available_at,
        state=state,
        source_refs=(_ALPACA_REF,),
        schema_version=_SCHEMA,
    )


def _row(
    state: CorporateActionState,
    available_at: UtcTimestamp,
    refs: tuple[SourceRef, ...] = (_ALPACA_REF,),
):
    return build_corporate_action_record(
        event_id="evt-1",
        security_id=_SEC,
        security_identity_hash=_IDENTITY.identity_hash,
        action_type=CorporateActionType.FORWARD_SPLIT,
        ratio=SplitRatio.from_fraction(numerator=3, denominator=2),
        declared_at=_T_DECL,
        ex_date=_EX_DATE,
        effective_date=_EX_DATE,
        available_at=available_at,
        state=state,
        source_refs=refs,
        schema_version=_SCHEMA,
    )


def _obs(source_ref: SourceRef, available_at: UtcTimestamp, **overrides: object):
    values: dict[str, object] = {
        "source_ref": source_ref,
        "available_at": available_at,
        "withdrawn": False,
        "auditable": True,
        "claimed_type": CorporateActionType.FORWARD_SPLIT,
        "claimed_ratio": SplitRatio.from_fraction(numerator=3, denominator=2),
        "claimed_ex_date": _EX_DATE,
        "claimed_effective_date": _EX_DATE,
    }
    values.update(overrides)
    return SourceObservation(**values)


def _query(**overrides: object) -> QuarantineQuery:
    values: dict[str, object] = {
        "purpose": QuarantinePurpose.CANDIDATE_CREATION,
        "security_id": _SEC,
        "symbol_as_of": SecuritySymbol("TEST"),
        "decision_at": _T_DECISION,
        "master_version": _MASTER_VERSION,
    }
    values.update(overrides)
    return QuarantineQuery(**values)


def _evaluate(query=None, identity_records=(_IDENTITY,), event_lineages=()):
    return evaluate_quarantine(
        query=query if query is not None else _query(),
        identity_records=identity_records,
        event_lineages=event_lineages,
    )


# --- closed enums and version token ------------------------------------------


def test_outcome_and_purpose_enums_are_closed() -> None:
    assert set(QuarantineOutcome) == {
        QuarantineOutcome.ELIGIBLE,
        QuarantineOutcome.ENTRY_BLOCKED,
        QuarantineOutcome.REVIEW_REQUIRED,
    }
    assert set(QuarantinePurpose) == {
        QuarantinePurpose.CANDIDATE_CREATION,
        QuarantinePurpose.RISK_APPROVAL,
        QuarantinePurpose.SUBMIT_RECHECK,
    }


def test_master_version_token_is_derived_from_identity() -> None:
    expected = f"{_IDENTITY.producer_version}:{_IDENTITY.identity_hash}"
    assert expected == _MASTER_VERSION


# --- eligible baseline --------------------------------------------------------


def test_clean_identity_without_events_is_eligible() -> None:
    decision = _evaluate()
    assert decision.outcome is QuarantineOutcome.ELIGIBLE
    assert decision.reasons == ()
    assert decision.event_ids == ()
    assert decision.source_refs == _IDENTITY.source_refs
    assert decision.security_id == _SEC
    assert decision.symbol_as_of == SecuritySymbol("TEST")
    assert decision.master_version == _MASTER_VERSION
    assert decision.decision_at == _T_DECISION


def test_official_observation_without_complete_claims_cannot_confirm() -> None:
    event = _event(CorporateActionState.DETECTED, _T_AVAIL)
    evaluation = evaluate_confirmation(
        event=event,
        identity=_IDENTITY,
        observations=(
            _obs(_ALPACA_REF, _T_AVAIL),
            _obs(
                _OFFICIAL_REF,
                _T_AVAIL,
                claimed_type=None,
                claimed_ratio=None,
                claimed_ex_date=None,
                claimed_effective_date=None,
            ),
        ),
        decision_at=_T_DECISION,
    )

    assert evaluation.outcome is ConfirmationOutcome.ENTRY_BLOCKED
    assert evaluation.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.FORMAL_CONFIRMATION_MISSING,
    )


# --- three seams, one evaluator -----------------------------------------------


def test_three_purposes_yield_canonical_identical_decisions() -> None:
    detected = (_row(CorporateActionState.DETECTED, _T_AVAIL),)
    evidence = EventEvidence(lineage=detected, observations=(_obs(_ALPACA_REF, _T_AVAIL),))
    decisions = [
        _evaluate(
            query=_query(purpose=purpose),
            event_lineages=(evidence,),
        )
        for purpose in QuarantinePurpose
    ]
    first = decisions[0]
    for decision in decisions[1:]:
        assert decision.outcome is first.outcome
        assert decision.reasons == first.reasons
        assert decision.event_ids == first.event_ids
        assert decision.source_refs == first.source_refs
        assert decision.decision_hash == first.decision_hash


# --- identity-layer fail-closed findings ---------------------------------------


def test_unknown_security_fails_closed() -> None:
    decision = _evaluate(identity_records=())
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert decision.reasons == (QuarantineReason.UNKNOWN_SECURITY,)


def test_ambiguous_symbol_claims_fail_closed() -> None:
    rival = build_identity_record(
        security_id=_OTHER_SEC,
        symbol=SecuritySymbol("TEST"),
        exchange=ListingExchange.NYSE,
        asset_class=AssetClass.US_EQUITY,
        valid_from=UtcTimestamp(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
        available_at=UtcTimestamp(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
        status=SecurityStatus.ACTIVE,
        source_refs=(
            SourceRef(
                record_id="alpaca-asset-9",
                family=P4SourceFamily.ALPACA_ASSETS,
                record_hash="9" * 64,
            ),
        ),
        schema_version=_SCHEMA,
    )
    decision = _evaluate(identity_records=(_IDENTITY, rival))
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert QuarantineReason.AMBIGUOUS_IDENTITY in decision.reasons


def test_symbol_as_of_mismatch_fails_closed() -> None:
    decision = _evaluate(query=_query(symbol_as_of=SecuritySymbol("OLDTICK")))
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SYMBOL_AS_OF_MISMATCH in decision.reasons


def test_stale_master_version_fails_closed() -> None:
    decision = _evaluate(query=_query(master_version="stale-master-token"))
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert QuarantineReason.STALE_SECURITY_MASTER in decision.reasons


def test_future_identity_availability_maps_to_source_not_yet_available() -> None:
    future = build_identity_record(
        security_id=_SEC,
        symbol=SecuritySymbol("TEST"),
        exchange=ListingExchange.NASDAQ,
        asset_class=AssetClass.US_EQUITY,
        valid_from=UtcTimestamp(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
        available_at=UtcTimestamp(datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)),
        status=SecurityStatus.ACTIVE,
        source_refs=(
            SourceRef(
                record_id="alpaca-asset-1",
                family=P4SourceFamily.ALPACA_ASSETS,
                record_hash="d" * 64,
            ),
        ),
        schema_version=_SCHEMA,
    )
    decision = _evaluate(identity_records=(future,))
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SOURCE_NOT_YET_AVAILABLE in decision.reasons


def test_overlapping_identity_intervals_fail_closed() -> None:
    overlapping = build_identity_record(
        security_id=_SEC,
        symbol=SecuritySymbol("TEST"),
        exchange=ListingExchange.NASDAQ,
        asset_class=AssetClass.US_EQUITY,
        valid_from=UtcTimestamp(datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)),
        available_at=UtcTimestamp(datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)),
        status=SecurityStatus.ACTIVE,
        source_refs=(
            SourceRef(
                record_id="alpaca-asset-2",
                family=P4SourceFamily.ALPACA_ASSETS,
                record_hash="e" * 64,
            ),
        ),
        schema_version=_SCHEMA,
    )
    decision = _evaluate(identity_records=(_IDENTITY, overlapping))
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert QuarantineReason.IDENTITY_INTERVAL_CONFLICT in decision.reasons


# --- event-layer decisions -------------------------------------------------------


def test_detected_event_blocks_entry() -> None:
    evidence = EventEvidence(
        lineage=(_row(CorporateActionState.DETECTED, _T_AVAIL),),
        observations=(_obs(_ALPACA_REF, _T_AVAIL),),
    )
    decision = _evaluate(event_lineages=(evidence,))
    assert decision.outcome is QuarantineOutcome.ENTRY_BLOCKED
    assert decision.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.FORMAL_CONFIRMATION_MISSING,
    )
    assert decision.event_ids == ("evt-1",)


def test_confirmed_event_still_blocks_entry_until_reconciled() -> None:
    lineage = (
        _row(CorporateActionState.DETECTED, _T_AVAIL),
        _row(CorporateActionState.ENTRY_BLOCKED, _T_BLOCK),
        _row(CorporateActionState.CONFIRMED, _T_CONFIRM, refs=(_OFFICIAL_REF,)),
    )
    evidence = EventEvidence(
        lineage=lineage,
        observations=(
            _obs(_ALPACA_REF, _T_AVAIL),
            _obs(_OFFICIAL_REF, _T_CONFIRM),
        ),
    )
    decision = _evaluate(event_lineages=(evidence,))
    assert decision.outcome is QuarantineOutcome.ENTRY_BLOCKED
    assert decision.reasons == (QuarantineReason.SPLIT_DETECTED,)


def test_withdrawal_after_confirmation_forces_review() -> None:
    lineage = (
        _row(CorporateActionState.DETECTED, _T_AVAIL),
        _row(CorporateActionState.ENTRY_BLOCKED, _T_BLOCK),
        _row(CorporateActionState.CONFIRMED, _T_CONFIRM, refs=(_OFFICIAL_REF,)),
        _row(CorporateActionState.REVIEW_REQUIRED, _T_WITHDRAW, refs=(_OFFICIAL_REF,)),
    )
    evidence = EventEvidence(
        lineage=lineage,
        observations=(
            _obs(_ALPACA_REF, _T_AVAIL),
            _obs(_OFFICIAL_REF, _T_CONFIRM, withdrawn=True),
        ),
    )
    decision = _evaluate(event_lineages=(evidence,))
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert decision.reasons == (
        QuarantineReason.SPLIT_DETECTED,
        QuarantineReason.SOURCE_WITHDRAWN_OR_CORRECTED,
    )


def test_effective_event_forces_review() -> None:
    lineage = (
        _row(CorporateActionState.DETECTED, _T_AVAIL),
        _row(CorporateActionState.ENTRY_BLOCKED, _T_BLOCK),
        _row(CorporateActionState.CONFIRMED, _T_CONFIRM, refs=(_OFFICIAL_REF,)),
        _row(CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION, _T_EFFECTIVE),
    )
    evidence = EventEvidence(
        lineage=lineage,
        observations=(
            _obs(_ALPACA_REF, _T_AVAIL),
            _obs(_OFFICIAL_REF, _T_CONFIRM),
        ),
    )
    decision = _evaluate(
        query=_query(decision_at=UtcTimestamp(datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC))),
        event_lineages=(evidence,),
    )
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert QuarantineReason.EFFECTIVE_OR_LATE_EVENT_REVIEW in decision.reasons


def test_event_not_yet_known_at_decision_time_is_ignored() -> None:
    evidence = EventEvidence(
        lineage=(
            _row(
                CorporateActionState.DETECTED,
                UtcTimestamp(datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)),
            ),
        ),
        observations=(_obs(_ALPACA_REF, UtcTimestamp(datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC))),),
    )
    decision = _evaluate(event_lineages=(evidence,))
    assert decision.outcome is QuarantineOutcome.ELIGIBLE


def test_point_in_time_replay_changes_with_knowledge_cutoff() -> None:
    lineage = (
        _row(CorporateActionState.DETECTED, _T_AVAIL),
        _row(CorporateActionState.ENTRY_BLOCKED, _T_BLOCK),
        _row(CorporateActionState.CONFIRMED, _T_CONFIRM, refs=(_OFFICIAL_REF,)),
        _row(CorporateActionState.REVIEW_REQUIRED, _T_WITHDRAW, refs=(_OFFICIAL_REF,)),
    )
    before_withdrawal = EventEvidence(
        lineage=lineage,
        observations=(
            _obs(_ALPACA_REF, _T_AVAIL),
            _obs(_OFFICIAL_REF, _T_CONFIRM, withdrawn=False),
        ),
    )
    early = _evaluate(
        query=_query(decision_at=UtcTimestamp(datetime(2026, 1, 7, 0, 0, 0, tzinfo=UTC))),
        event_lineages=(before_withdrawal,),
    )
    assert early.outcome is QuarantineOutcome.ENTRY_BLOCKED
    assert early.reasons == (QuarantineReason.SPLIT_DETECTED,)

    after_withdrawal = EventEvidence(
        lineage=lineage,
        observations=(
            _obs(_ALPACA_REF, _T_AVAIL),
            _obs(_OFFICIAL_REF, _T_CONFIRM, withdrawn=True),
        ),
    )
    late = _evaluate(
        query=_query(decision_at=UtcTimestamp(datetime(2026, 1, 9, 0, 0, 0, tzinfo=UTC))),
        event_lineages=(after_withdrawal,),
    )
    assert late.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SOURCE_WITHDRAWN_OR_CORRECTED in late.reasons


def test_events_for_other_securities_are_ignored() -> None:
    other_event = build_corporate_action_record(
        event_id="evt-other",
        security_id=_OTHER_SEC,
        security_identity_hash="f" * 64,
        action_type=CorporateActionType.REVERSE_SPLIT,
        ratio=SplitRatio.from_fraction(numerator=1, denominator=10),
        declared_at=_T_DECL,
        ex_date=_EX_DATE,
        effective_date=_EX_DATE,
        available_at=_T_AVAIL,
        state=CorporateActionState.DETECTED,
        source_refs=(
            SourceRef(
                record_id="alpaca-ca-9",
                family=P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
                record_hash="9" * 64,
            ),
        ),
        schema_version=_SCHEMA,
    )
    evidence = EventEvidence(
        lineage=(other_event,),
        observations=(_obs(other_event.source_refs[0], _T_AVAIL),),
    )
    decision = _evaluate(event_lineages=(evidence,))
    assert decision.outcome is QuarantineOutcome.ELIGIBLE


def test_future_observation_fails_closed() -> None:
    future_obs = _obs(_OFFICIAL_REF, UtcTimestamp(datetime(2026, 1, 12, 0, 0, 0, tzinfo=UTC)))
    evidence = EventEvidence(
        lineage=(
            _row(CorporateActionState.DETECTED, _T_AVAIL),
            _row(CorporateActionState.ENTRY_BLOCKED, _T_BLOCK),
        ),
        observations=(_obs(_ALPACA_REF, _T_AVAIL), future_obs),
    )
    decision = _evaluate(event_lineages=(evidence,))
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED
    assert QuarantineReason.SOURCE_NOT_YET_AVAILABLE in decision.reasons


def test_head_confirmed_but_evidence_regressed_forces_review() -> None:
    lineage = (
        _row(CorporateActionState.DETECTED, _T_AVAIL),
        _row(CorporateActionState.ENTRY_BLOCKED, _T_BLOCK),
        _row(CorporateActionState.CONFIRMED, _T_CONFIRM, refs=(_OFFICIAL_REF,)),
    )
    evidence = EventEvidence(
        lineage=lineage,
        observations=(_obs(_ALPACA_REF, _T_AVAIL),),
    )
    decision = _evaluate(event_lineages=(evidence,))
    assert decision.outcome is QuarantineOutcome.REVIEW_REQUIRED


# --- fail-closed input contract --------------------------------------------------


def test_illegal_lineage_is_rejected_at_evidence_construction() -> None:
    with pytest.raises(IllegalTransitionError):
        EventEvidence(
            lineage=(
                _row(CorporateActionState.DETECTED, _T_AVAIL),
                _row(CorporateActionState.CONFIRMED, _T_CONFIRM),
            ),
            observations=(_obs(_ALPACA_REF, _T_AVAIL),),
        )


def test_tampered_lineage_row_is_rejected_not_eligible() -> None:
    row = _row(CorporateActionState.DETECTED, _T_AVAIL)
    object.__setattr__(row, "ratio", SplitRatio.from_fraction(numerator=9, denominator=1))
    with pytest.raises(ValueError, match="hash"):
        EventEvidence(lineage=(row,), observations=(_obs(_ALPACA_REF, _T_AVAIL),))


def test_evidence_observations_must_cover_detection_sources() -> None:
    with pytest.raises(ValueError, match="cover"):
        EventEvidence(
            lineage=(_row(CorporateActionState.DETECTED, _T_AVAIL),),
            observations=(_obs(_OFFICIAL_REF, _T_CONFIRM),),
        )


def test_event_lineages_are_bounded() -> None:
    evidence = EventEvidence(
        lineage=(_row(CorporateActionState.DETECTED, _T_AVAIL),),
        observations=(_obs(_ALPACA_REF, _T_AVAIL),),
    )
    with pytest.raises(ValueError, match="tuple"):
        _evaluate(event_lineages=[evidence])
    with pytest.raises(ValueError, match="tuple"):
        _evaluate(event_lineages=tuple(evidence for _ in range(17)))


def test_query_fields_are_exact_typed() -> None:
    with pytest.raises(ValueError, match="purpose"):
        _query(purpose="CANDIDATE_CREATION")
    with pytest.raises(ValueError, match="security_id"):
        _query(security_id=_SEC.value)
    with pytest.raises(ValueError, match="symbol_as_of"):
        _query(symbol_as_of="TEST")
    with pytest.raises(ValueError, match="decision_at"):
        _query(decision_at="2026-01-10T00:00:00.000000Z")
    with pytest.raises(ValueError, match="master_version"):
        _query(master_version="")
    with pytest.raises(ValueError, match="master_version"):
        _query(master_version=123)


# --- decision auditability --------------------------------------------------------


def test_decision_hash_is_deterministic_and_excludes_purpose() -> None:
    candidate = _evaluate(query=_query(purpose=QuarantinePurpose.CANDIDATE_CREATION))
    risk = _evaluate(query=_query(purpose=QuarantinePurpose.RISK_APPROVAL))
    assert candidate.decision_hash == risk.decision_hash
    assert candidate.decision_hash == _evaluate().decision_hash


def test_decision_wire_carries_required_audit_fields() -> None:
    evidence = EventEvidence(
        lineage=(_row(CorporateActionState.DETECTED, _T_AVAIL),),
        observations=(_obs(_ALPACA_REF, _T_AVAIL),),
    )
    decision = _evaluate(event_lineages=(evidence,))
    wire = decision.wire()
    assert set(wire) >= {
        "security_id",
        "symbol_as_of",
        "master_version",
        "decision_at",
        "outcome",
        "reasons",
        "event_ids",
        "source_refs",
        "producer_version",
    }
    assert wire["outcome"] == "ENTRY_BLOCKED"
    assert decision.verify_integrity() is True


def test_decision_lineage_includes_event_evidence_refs() -> None:
    evidence = EventEvidence(
        lineage=(
            _row(CorporateActionState.DETECTED, _T_AVAIL),
            _row(CorporateActionState.ENTRY_BLOCKED, _T_BLOCK, refs=(_OFFICIAL_REF,)),
        ),
        observations=(_obs(_ALPACA_REF, _T_AVAIL), _obs(_OFFICIAL_REF, _T_CONFIRM)),
    )
    decision = _evaluate(event_lineages=(evidence,))
    record_ids = {ref.record_id for ref in decision.source_refs}
    assert record_ids == {"alpaca-asset-1", "alpaca-ca-1", "official-sec-edgar-1"}
