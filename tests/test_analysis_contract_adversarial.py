from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import pytest

from seven_lens.analysis.contracts import (
    AnalysisInput,
    AnalysisStatus,
    AnalysisWindow,
    AnalystReport,
    AnalystRole,
    BorrowAvailability,
    BorrowStatus,
    InvestmentDebateState,
    PortfolioProposal,
    PortfolioRequest,
    PositionSide,
    ProposalAction,
    ProposalReasonCode,
    ResearchConclusion,
    ResearchRating,
    RiskDebateState,
    RiskRejectionFeedback,
    SameDayExitReason,
    TraderPlan,
)
from seven_lens.domain.value_objects import UtcTimestamp
from test_analysis_contracts import (
    CANDIDATES,
    analysis_input,
    meta,
    proposal,
    rejection,
    report,
    requests,
    rid,
    snapshot,
    timestamp,
)


@pytest.mark.parametrize("bad", [True, False, 1.0, math.nan, math.inf, "1"])
def test_exact_integer_rejects_bool_float_and_strings(bad: object) -> None:
    wire = proposal().to_wire()
    wire["attempt"] = bad  # type: ignore[assignment]
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)


@pytest.mark.parametrize(
    "bad", [0.1, math.nan, math.inf, "1e-1", " 0.100000", "00.100000", "-0.000000", "0.10"]
)
def test_decimal_wire_is_canonical_and_never_float(bad: object) -> None:
    wire = requests()[0].to_wire()
    wire["target_weight"] = bad  # type: ignore[assignment]
    with pytest.raises(ValueError):
        PortfolioRequest.from_wire(wire)


def test_negative_zero_is_rejected_at_typed_constructor() -> None:
    with pytest.raises(ValueError, match="negative zero"):
        replace(requests()[4], target_weight=Decimal("-0.000000"))


@pytest.mark.parametrize(
    "bad", [None, "00000000-0000-0000-0000-000000000000", "00000000-0000-4000-8000-00000000000A"]
)
def test_ids_reject_nil_noncanonical_and_wrong_type(bad: object) -> None:
    wire = proposal().to_wire()
    wire["proposal_id"] = bad  # type: ignore[assignment]
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-08-21T14:30:00Z",
        "2026-08-21T14:30:00.000000+00:00",
        "2026-08-21T14:30:00.000000+08:00",
    ],
)
def test_timestamp_wire_requires_exact_canonical_utc(bad: str) -> None:
    wire = proposal().to_wire()
    wire["expiration_at"] = bad
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)
    with pytest.raises(ValueError):
        UtcTimestamp(datetime(2026, 8, 21, 14, 30))
    with pytest.raises(ValueError):
        UtcTimestamp(datetime(2026, 8, 21, 14, 30, tzinfo=timezone(timedelta(hours=8))))


def test_unknown_missing_enum_casing_and_enum_subclass_fail_closed() -> None:
    wire = proposal().to_wire()
    wire["credential"] = "marker"
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)
    wire = proposal().to_wire()
    del wire["window"]
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)
    wire = proposal().to_wire()
    wire["window"] = "primary"
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)

    class FakeWindow(str):
        pass

    wire = proposal().to_wire()
    wire["window"] = FakeWindow("PRIMARY")
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)


def test_duplicate_and_overlap_invariants() -> None:
    wire = analysis_input().to_wire()
    wire["candidate_symbols"] = ["MSFT", "MSFT"]
    with pytest.raises(ValueError, match="duplicates"):
        AnalysisInput.from_wire(wire)
    wire = analysis_input().to_wire()
    wire["candidate_symbols"] = ["AAPL"]
    with pytest.raises(ValueError, match="overlap"):
        AnalysisInput.from_wire(wire)
    wire = requests()[0].to_wire()
    wire["evidence_refs"] = ["evidence.1", "evidence.1"]
    with pytest.raises(ValueError, match="duplicates"):
        PortfolioRequest.from_wire(wire)


def _input_wire(
    window: AnalysisWindow, candidates: list[str], deadline_minutes: int
) -> dict[str, object]:
    wire = cast(dict[str, object], analysis_input(window).to_wire())
    wire["candidate_symbols"] = candidates
    wire["focus_symbols"] = ["AAPL"]
    wire["deadline"] = str(timestamp(deadline_minutes))
    # Hash mismatch comes after the intended count/deadline checks.
    return wire


def test_candidate_and_deadline_window_bounds() -> None:
    with pytest.raises(ValueError):
        AnalysisInput.from_wire(_input_wire(AnalysisWindow.PRIMARY, [*CANDIDATES, "QCOM"], 15))
    with pytest.raises(ValueError, match="candidate count"):
        AnalysisInput.from_wire(_input_wire(AnalysisWindow.SECONDARY, list(CANDIDATES[:6]), 15))
    with pytest.raises(ValueError, match="zero candidates"):
        AnalysisInput.from_wire(_input_wire(AnalysisWindow.EMERGENCY, ["MSFT"], 3))
    with pytest.raises(ValueError, match="fifteen minutes"):
        AnalysisInput.from_wire(_input_wire(AnalysisWindow.PRIMARY, list(CANDIDATES), 16))
    with pytest.raises(ValueError, match="three minutes"):
        AnalysisInput.from_wire(_input_wire(AnalysisWindow.EMERGENCY, [], 4))


@pytest.mark.parametrize(
    ("action", "side", "weight"),
    [
        (ProposalAction.OPEN, PositionSide.LONG, "-0.010000"),
        (ProposalAction.OPEN, PositionSide.SHORT, "0.010000"),
        (ProposalAction.HOLD, PositionSide.FLAT, "0.010000"),
        (ProposalAction.CLOSE, PositionSide.FLAT, "0.010000"),
        (ProposalAction.OPEN, PositionSide.LONG, "0.000000"),
    ],
)
def test_action_side_signed_weight_contradictions(
    action: ProposalAction, side: PositionSide, weight: str
) -> None:
    wire = requests()[0].to_wire()
    wire.update(action=action.value, side=side.value, target_weight=weight)
    with pytest.raises(ValueError):
        PortfolioRequest.from_wire(wire)


def test_low_confidence_non_hold_is_rejected() -> None:
    wire = requests()[0].to_wire()
    wire["confidence"] = "0.6499"
    with pytest.raises(ValueError, match="HOLD"):
        PortfolioRequest.from_wire(wire)


def test_attempt_supersedes_and_feedback_round_bounds() -> None:
    wire = proposal().to_wire()
    wire["superseded_proposal_id"] = str(rid(99))
    with pytest.raises(ValueError, match="attempt 1"):
        PortfolioProposal.from_wire(wire)
    wire = proposal(2).to_wire()
    wire["superseded_proposal_id"] = None
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)
    feedback = rejection().to_wire()
    feedback["review_round"] = 2
    with pytest.raises(ValueError):
        RiskRejectionFeedback.from_wire(feedback)
    feedback = rejection().to_wire()
    feedback["rejection_codes"] = []
    with pytest.raises(ValueError):
        RiskRejectionFeedback.from_wire(feedback)


@pytest.mark.parametrize(
    "forbidden",
    ["account_id", "broker_order_id", "authorization", "credential", "raw_broker_payload"],
)
def test_deidentified_contracts_reject_forbidden_fields(forbidden: str) -> None:
    marker = "SECRET-MARKER-DO-NOT-ECHO"
    wire = snapshot().to_wire()
    wire[forbidden] = marker
    with pytest.raises(ValueError) as caught:
        type(snapshot()).from_wire(wire)
    assert marker not in str(caught.value)


@pytest.mark.parametrize(
    "material",
    ["Authorization: Bearer abc", "account_id=123", "api_key=abc", "credential dump"],
)
def test_deidentified_contracts_reject_sensitive_text_material(material: str) -> None:
    wire = requests()[0].to_wire()
    wire["invalidators"] = [material]
    with pytest.raises(ValueError, match="prohibited"):
        PortfolioRequest.from_wire(wire)


def test_resource_boundaries_cycle_deep_wide_oversize_nul_and_non_echo() -> None:
    marker = "X" * 3000
    wire = requests()[0].to_wire()
    wire["invalidators"] = [marker]
    with pytest.raises(ValueError) as caught:
        PortfolioRequest.from_wire(wire)
    assert marker not in str(caught.value)
    wire = requests()[0].to_wire()
    wire["invalidators"] = ["bad\x00text"]
    with pytest.raises(ValueError):
        PortfolioRequest.from_wire(wire)
    cycle: dict[str, object] = {}
    cycle["cycle"] = cycle
    with pytest.raises(ValueError, match="cycle"):
        PortfolioRequest.from_wire(cycle)
    deep: object = "x"
    for _ in range(40):
        deep = [deep]
    wire = requests()[0].to_wire()
    wire["invalidators"] = deep  # type: ignore[assignment]
    with pytest.raises(ValueError, match="depth"):
        PortfolioRequest.from_wire(wire)
    wire = requests()[0].to_wire()
    wire["extra"] = {str(i): i for i in range(300)}
    with pytest.raises(ValueError, match="members"):
        PortfolioRequest.from_wire(wire)
    wire = requests()[0].to_wire()
    wire["invalidators"] = ["x" * 2000] * 40
    with pytest.raises(ValueError):
        PortfolioRequest.from_wire(wire)


def test_debate_rounds_are_bounded_and_complete_only_at_two() -> None:
    with pytest.raises(ValueError):
        replace(
            InvestmentDebateState.from_wire(
                {
                    "meta": meta().to_wire(),
                    "debate_id": str(rid(30)),
                    "input_id": str(rid(2)),
                    "symbol": "MSFT",
                    "bull_arguments": ["bull"],
                    "bear_arguments": ["bear"],
                    "verified_claims": [],
                    "disputed_claims": [],
                    "unresolved_conflicts": [],
                    "round_count": 2,
                    "complete": True,
                }
            ),
            round_count=3,
        )
    with pytest.raises(ValueError):
        RiskDebateState(meta(), rid(31), rid(2), ("a",), ("c",), ("n",), (), 1, True)


def test_proposal_exact_input_boundary_rejects_out_of_universe_and_emergency_open() -> None:
    out = replace(proposal(), requests=(replace(requests()[0], symbol="QCOM"),))
    with pytest.raises(ValueError, match="outside"):
        out.validate_against(analysis_input())
    emergency = analysis_input(AnalysisWindow.EMERGENCY)
    open_request = replace(requests()[0], symbol="AAPL")
    bad = replace(
        proposal(),
        analysis_input_id=emergency.input_id,
        universe_hash=emergency.universe_hash,
        snapshot_hash=emergency.portfolio_snapshot.content_hash,
        window=AnalysisWindow.EMERGENCY,
        requests=(open_request,),
        expiration_at=emergency.deadline,
    )
    with pytest.raises(ValueError, match="emergency"):
        bad.validate_against(emergency)


@pytest.mark.parametrize("index", [0, 4])
def test_same_day_exit_reason_is_rejected_for_open_and_hold(index: int) -> None:
    with pytest.raises(ValueError, match="REDUCE or CLOSE"):
        replace(requests()[index], same_day_exit_reason=SameDayExitReason.MATERIAL_NEW_EVENT)
    wire = requests()[index].to_wire()
    wire["same_day_exit_reason"] = SameDayExitReason.MATERIAL_NEW_EVENT.value
    with pytest.raises(ValueError, match="REDUCE or CLOSE"):
        PortfolioRequest.from_wire(wire)


def test_validate_against_rejects_identity_snapshot_window_and_late_expiration() -> None:
    inp = analysis_input()
    base = proposal()
    with pytest.raises(ValueError, match="boundary"):
        replace(base, analysis_input_id=rid(99)).validate_against(inp)
    with pytest.raises(ValueError, match="boundary"):
        replace(base, universe_hash="a" * 64).validate_against(inp)
    with pytest.raises(ValueError, match="boundary"):
        replace(base, snapshot_hash="b" * 64).validate_against(inp)
    with pytest.raises(ValueError, match="boundary"):
        replace(base, window=AnalysisWindow.SECONDARY).validate_against(inp)
    with pytest.raises(ValueError, match="deadline"):
        replace(base, expiration_at=timestamp(16)).validate_against(inp)


def test_proposal_status_request_contradictions() -> None:
    with pytest.raises(ValueError, match="must not contain requests"):
        replace(proposal(), status=AnalysisStatus.INVALID)
    wire = proposal().to_wire()
    wire["status"] = AnalysisStatus.INVALID.value
    with pytest.raises(ValueError, match="must not contain requests"):
        PortfolioProposal.from_wire(wire)
    with pytest.raises(ValueError, match="requires at least one request"):
        replace(proposal(), status=AnalysisStatus.VALID, requests=())
    wire = proposal().to_wire()
    wire["requests"] = []
    with pytest.raises(ValueError, match="requires at least one request"):
        PortfolioProposal.from_wire(wire)


def test_unavailable_or_unknown_borrow_requires_zero_located_quantity() -> None:
    for availability in (BorrowAvailability.UNAVAILABLE, BorrowAvailability.UNKNOWN):
        with pytest.raises(ValueError, match="zero located_quantity"):
            BorrowStatus("TSLA", availability, Decimal("1.000000"))
    borrows = cast(list[object], snapshot().to_wire()["borrow_statuses"])
    borrow_wire = cast(dict[str, object], borrows[0])
    borrow_wire["availability"] = BorrowAvailability.UNAVAILABLE.value
    with pytest.raises(ValueError, match="zero located_quantity"):
        BorrowStatus.from_wire(borrow_wire)


def _plan() -> TraderPlan:
    return TraderPlan(
        meta(),
        rid(32),
        rid(2),
        "MSFT",
        ResearchRating.BUY,
        (ProposalReasonCode.FUNDAMENTAL,),
        ("evidence.1",),
        Decimal("100.00"),
        Decimal("110.00"),
        Decimal("90.00"),
        AnalysisStatus.VALID,
    )


def test_trader_plan_entry_band_low_above_high_is_rejected() -> None:
    with pytest.raises(ValueError, match="entry band low must not exceed high"):
        replace(_plan(), entry_band_low=Decimal("110.01"))
    wire = _plan().to_wire()
    wire["entry_band_low"] = "200.00"
    with pytest.raises(ValueError, match="entry band low must not exceed high"):
        TraderPlan.from_wire(wire)


def test_normal_window_focus_outside_holdings_and_candidates_is_rejected() -> None:
    with pytest.raises(ValueError, match="focus symbols must belong"):
        replace(analysis_input(), focus_symbols=("AAPL", "QCOM"))
    wire = analysis_input().to_wire()
    wire["focus_symbols"] = ["AAPL", "QCOM"]
    with pytest.raises(ValueError, match="focus symbols must belong"):
        AnalysisInput.from_wire(wire)


def _conclusion(status: AnalysisStatus) -> ResearchConclusion:
    confidence = Decimal("0.8000") if status is AnalysisStatus.VALID else Decimal("0.0000")
    return ResearchConclusion(
        meta(),
        rid(31),
        rid(2),
        "MSFT",
        ResearchRating.BUY,
        "conclusion",
        ("driver",),
        ("risk",),
        ("invalidator",),
        ("evidence.1",),
        confidence,
        status,
    )


def test_analyst_report_and_conclusion_status_confidence_rules() -> None:
    with pytest.raises(ValueError, match="requires material claims"):
        replace(report(AnalysisStatus.VALID), material_claims=())
    wire = report(AnalysisStatus.VALID).to_wire()
    wire["material_claims"] = []
    with pytest.raises(ValueError, match="material claims"):
        AnalystReport.from_wire(wire)
    for status in (AnalysisStatus.INVALID, AnalysisStatus.ABSTAIN):
        with pytest.raises(ValueError, match="confidence must be zero"):
            replace(report(status), confidence=Decimal("0.5000"))
    wire = report(AnalysisStatus.INVALID).to_wire()
    wire["confidence"] = "0.5000"
    with pytest.raises(ValueError, match="confidence must be zero"):
        AnalystReport.from_wire(wire)
    with pytest.raises(ValueError, match="conclusion confidence must be zero"):
        replace(_conclusion(AnalysisStatus.INVALID), confidence=Decimal("0.8000"))
    wire = _conclusion(AnalysisStatus.ABSTAIN).to_wire()
    wire["confidence"] = "0.5000"
    with pytest.raises(ValueError, match="conclusion confidence must be zero"):
        ResearchConclusion.from_wire(wire)


def _investment_debate_wire() -> dict[str, object]:
    return {
        "meta": meta().to_wire(),
        "debate_id": str(rid(30)),
        "input_id": str(rid(2)),
        "symbol": "MSFT",
        "bull_arguments": ["bull"],
        "bear_arguments": ["bear"],
        "verified_claims": [],
        "disputed_claims": [],
        "unresolved_conflicts": [],
        "round_count": 1,
        "complete": False,
    }


def _risk_debate_wire() -> dict[str, object]:
    return {
        "meta": meta().to_wire(),
        "debate_id": str(rid(33)),
        "input_id": str(rid(2)),
        "aggressive_arguments": ["aggressive"],
        "conservative_arguments": ["conservative"],
        "neutral_arguments": ["neutral"],
        "unresolved_conflicts": [],
        "round_count": 1,
        "complete": False,
    }


def test_started_debate_requires_all_viewpoints() -> None:
    with pytest.raises(ValueError, match="bull and bear"):
        InvestmentDebateState(meta(), rid(30), rid(2), "MSFT", (), ("bear",), (), (), (), 1, False)
    wire = _investment_debate_wire()
    wire["bear_arguments"] = []
    with pytest.raises(ValueError, match="bull and bear"):
        InvestmentDebateState.from_wire(wire)
    with pytest.raises(ValueError, match="all three viewpoints"):
        RiskDebateState(meta(), rid(33), rid(2), ("aggressive",), (), ("neutral",), (), 1, False)
    wire = _risk_debate_wire()
    wire["neutral_arguments"] = []
    with pytest.raises(ValueError, match="all three viewpoints"):
        RiskDebateState.from_wire(wire)


def test_emergency_proposal_increase_is_rejected_by_boundary() -> None:
    emergency = analysis_input(AnalysisWindow.EMERGENCY)
    bad = replace(
        proposal(),
        analysis_input_id=emergency.input_id,
        universe_hash=emergency.universe_hash,
        snapshot_hash=emergency.portfolio_snapshot.content_hash,
        window=AnalysisWindow.EMERGENCY,
        requests=(replace(requests()[2], action=ProposalAction.INCREASE),),
        expiration_at=emergency.deadline,
    )
    with pytest.raises(ValueError, match="emergency"):
        bad.validate_against(emergency)


def test_ctor_rejects_cross_enum_reason_code_confusion() -> None:
    confused = cast(tuple[ProposalReasonCode, ...], (AnalystRole.NEWS,))
    with pytest.raises(ValueError, match="reason_codes requires exact enum values"):
        replace(requests()[0], reason_codes=confused)


def test_negative_zero_is_rejected_across_decimal_constructors() -> None:
    with pytest.raises(ValueError, match="negative zero"):
        BorrowStatus("TSLA", BorrowAvailability.AVAILABLE, Decimal("-0.000000"))
    with pytest.raises(ValueError, match="negative zero"):
        replace(snapshot(), cash=Decimal("-0.00"))
    with pytest.raises(ValueError, match="negative zero"):
        replace(snapshot(), buying_power=Decimal("-0.00"))
    with pytest.raises(ValueError, match="negative zero"):
        replace(report(AnalysisStatus.VALID), confidence=Decimal("-0.0000"))
    with pytest.raises(ValueError, match="negative zero"):
        replace(_conclusion(AnalysisStatus.VALID), confidence=Decimal("-0.0000"))
    with pytest.raises(ValueError, match="negative zero"):
        replace(requests()[0], confidence=Decimal("-0.0000"))
