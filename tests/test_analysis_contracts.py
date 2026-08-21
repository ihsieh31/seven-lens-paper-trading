from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from seven_lens.analysis.contracts import (
    SCHEMA_VERSION,
    AnalysisInput,
    AnalysisStatus,
    AnalysisWindow,
    AnalystReport,
    AnalystRole,
    BorrowAvailability,
    BorrowStatus,
    ContractMeta,
    InvestmentDebateState,
    OpenOrderSummary,
    PortfolioPosition,
    PortfolioProposal,
    PortfolioRequest,
    PortfolioSnapshot,
    PositionSide,
    ProposalAction,
    ProposalReasonCode,
    RemainingLimits,
    ResearchConclusion,
    ResearchRating,
    RiskDebateState,
    RiskRejectionCode,
    RiskRejectionFeedback,
    SameDayExitReason,
    SameDayFillSummary,
    TraderPlan,
    build_analysis_input,
    build_portfolio_snapshot,
    canonical_wire_json,
)
from seven_lens.domain.value_objects import RunId, UtcTimestamp

FIXTURE = Path(__file__).parent / "fixtures" / "p3a_contracts" / "golden_bundle.json"


def rid(number: int) -> RunId:
    return RunId.from_string(f"00000000-0000-4000-8000-{number:012d}")


def timestamp(minutes: int = 0) -> UtcTimestamp:
    return UtcTimestamp(datetime(2026, 8, 21, 14, 30, tzinfo=UTC) + timedelta(minutes=minutes))


def meta() -> ContractMeta:
    return ContractMeta(SCHEMA_VERSION, rid(1), timestamp(), "p3a.1")


def limits() -> RemainingLimits:
    return RemainingLimits(
        13,
        Decimal("0.650000"),
        Decimal("0.150000"),
        Decimal("0.800000"),
        Decimal("0.050000"),
        Decimal("0.550000"),
        Decimal("0.100000"),
        Decimal("0.300000"),
    )


def snapshot() -> PortfolioSnapshot:
    positions = (
        PortfolioPosition(
            "AAPL",
            PositionSide.LONG,
            Decimal("10.000000"),
            Decimal("0.100000"),
            Decimal("200.00"),
            Decimal("210.00"),
            Decimal("2100.00"),
            Decimal("100.00"),
            Decimal("0.00"),
            timestamp(-1440),
            False,
        ),
        PortfolioPosition(
            "TSLA",
            PositionSide.SHORT,
            Decimal("5.000000"),
            Decimal("-0.050000"),
            Decimal("300.00"),
            Decimal("290.00"),
            Decimal("1450.00"),
            Decimal("50.00"),
            Decimal("-10.00"),
            timestamp(-60),
            True,
        ),
    )
    return build_portfolio_snapshot(
        as_of=timestamp(),
        nav=Decimal("100000.00"),
        cash=Decimal("35000.00"),
        buying_power=Decimal("50000.00"),
        positions=positions,
        open_orders=(OpenOrderSummary("open.1", "AAPL", PositionSide.LONG, Decimal("2.000000")),),
        same_day_fills=(
            SameDayFillSummary(
                "fill.1",
                "TSLA",
                PositionSide.SHORT,
                Decimal("5.000000"),
                Decimal("300.00"),
                timestamp(-60),
            ),
        ),
        borrow_statuses=(
            BorrowStatus("TSLA", BorrowAvailability.AVAILABLE, Decimal("100.000000")),
        ),
        remaining_limits=limits(),
    )


CANDIDATES = (
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOG",
    "NFLX",
    "AMD",
    "INTC",
    "IBM",
    "ORCL",
    "CRM",
    "ADBE",
)


def analysis_input(window: AnalysisWindow = AnalysisWindow.PRIMARY) -> AnalysisInput:
    candidates: tuple[str, ...] = CANDIDATES if window is AnalysisWindow.PRIMARY else CANDIDATES[:5]
    focus = ("AAPL", "TSLA", *candidates)
    deadline = timestamp(15)
    if window is AnalysisWindow.EMERGENCY:
        candidates = ()
        focus = ("TSLA",)
        deadline = timestamp(3)
    return build_analysis_input(
        meta=meta(),
        input_id=rid(2),
        as_of=timestamp(),
        window=window,
        deadline=deadline,
        portfolio_snapshot=snapshot(),
        holding_symbols=("AAPL", "TSLA"),
        candidate_symbols=candidates,
        focus_symbols=focus,
        evidence_refs=("evidence.1",),
        data_snapshot_refs=("market.1",),
    )


def requests() -> tuple[PortfolioRequest, ...]:
    return (
        PortfolioRequest(
            "MSFT",
            ProposalAction.OPEN,
            PositionSide.LONG,
            Decimal("0.050000"),
            Decimal("0.8000"),
            ("evidence.1",),
            (ProposalReasonCode.FUNDAMENTAL,),
            ("margin compression",),
        ),
        PortfolioRequest(
            "NVDA",
            ProposalAction.OPEN,
            PositionSide.SHORT,
            Decimal("-0.030000"),
            Decimal("0.7500"),
            ("evidence.2",),
            (ProposalReasonCode.VALUATION,),
            ("growth acceleration",),
        ),
        PortfolioRequest(
            "AAPL",
            ProposalAction.REDUCE,
            PositionSide.LONG,
            Decimal("0.060000"),
            Decimal("0.7000"),
            ("evidence.3",),
            (ProposalReasonCode.REBALANCE,),
            ("new product cycle",),
        ),
        PortfolioRequest(
            "TSLA",
            ProposalAction.CLOSE,
            PositionSide.FLAT,
            Decimal("0.000000"),
            Decimal("0.9000"),
            ("evidence.4",),
            (ProposalReasonCode.NEWS,),
            ("event disproved",),
            SameDayExitReason.MATERIAL_NEW_EVENT,
        ),
        PortfolioRequest(
            "AMZN",
            ProposalAction.HOLD,
            PositionSide.FLAT,
            Decimal("0.000000"),
            Decimal("0.6499"),
            ("evidence.5",),
            (ProposalReasonCode.SENTIMENT,),
            (),
        ),
    )


def proposal(attempt: int = 1) -> PortfolioProposal:
    inp = analysis_input()
    result = PortfolioProposal(
        meta(),
        rid(10 + attempt),
        attempt,
        None if attempt == 1 else rid(11),
        inp.input_id,
        inp.universe_hash,
        inp.portfolio_snapshot.content_hash,
        inp.window,
        requests(),
        "graph.1",
        "prompt.1",
        "model.1",
        "provider.1",
        "data.1",
        "memory.1",
        inp.deadline,
        AnalysisStatus.VALID,
    )
    result.validate_against(inp)
    return result


def rejection() -> RiskRejectionFeedback:
    return RiskRejectionFeedback(
        meta(),
        proposal().proposal_id,
        1,
        (RiskRejectionCode.TURNOVER, RiskRejectionCode.SINGLE_NAME),
        ("MSFT",),
        limits(),
        "a" * 64,
        timestamp(1),
    )


def report(status: AnalysisStatus) -> AnalystReport:
    confidence = Decimal("0.8000") if status is AnalysisStatus.VALID else Decimal("0.0000")
    claims = ("revenue accelerated",) if status is AnalysisStatus.VALID else ()
    return AnalystReport(
        meta(),
        rid(20),
        rid(2),
        AnalystRole.FUNDAMENTALS,
        "MSFT",
        status,
        "bounded summary",
        ("observation",),
        claims,
        ("evidence.1",),
        (),
        ("missing item",),
        ("risk",),
        ("catalyst",),
        ("invalidator",),
        confidence,
    )


def all_contracts() -> tuple[object, ...]:
    return (
        meta(),
        limits(),
        snapshot(),
        analysis_input(),
        analysis_input(AnalysisWindow.SECONDARY),
        analysis_input(AnalysisWindow.EMERGENCY),
        report(AnalysisStatus.VALID),
        report(AnalysisStatus.INVALID),
        report(AnalysisStatus.ABSTAIN),
        InvestmentDebateState(
            meta(),
            rid(30),
            rid(2),
            "MSFT",
            ("bull",),
            ("bear",),
            ("verified",),
            ("disputed",),
            ("conflict",),
            2,
            True,
        ),
        ResearchConclusion(
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
            Decimal("0.8000"),
            AnalysisStatus.VALID,
        ),
        TraderPlan(
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
        ),
        RiskDebateState(
            meta(),
            rid(33),
            rid(2),
            ("aggressive",),
            ("conservative",),
            ("neutral",),
            ("conflict",),
            2,
            True,
        ),
        *requests(),
        proposal(),
        rejection(),
        proposal(2),
    )


def golden_bundle() -> dict[str, object]:
    return {
        "primary": analysis_input().to_wire(),
        "secondary": analysis_input(AnalysisWindow.SECONDARY).to_wire(),
        "emergency": analysis_input(AnalysisWindow.EMERGENCY).to_wire(),
        "first_proposal": proposal().to_wire(),
        "risk_rejection": rejection().to_wire(),
        "second_proposal": proposal(2).to_wire(),
        "invalid_report": report(AnalysisStatus.INVALID).to_wire(),
        "abstain_report": report(AnalysisStatus.ABSTAIN).to_wire(),
        "request_cases": [item.to_wire() for item in requests()],
    }


@pytest.mark.parametrize("contract", all_contracts())
def test_exact_wire_round_trip(contract: object) -> None:
    wire = contract.to_wire()  # type: ignore[attr-defined]
    decoded = cast(Any, type(contract)).from_wire(wire)
    assert decoded == contract
    assert json.loads(canonical_wire_json(contract)) == wire


def test_golden_bundle_is_exact() -> None:
    assert json.loads(FIXTURE.read_text()) == golden_bundle()


def test_sequences_are_snapshotted_and_contracts_are_frozen() -> None:
    candidates = list(CANDIDATES)
    inp = build_analysis_input(
        meta=meta(),
        input_id=rid(2),
        as_of=timestamp(),
        window=AnalysisWindow.PRIMARY,
        deadline=timestamp(15),
        portfolio_snapshot=snapshot(),
        holding_symbols=["AAPL", "TSLA"],
        candidate_symbols=candidates,
        focus_symbols=["AAPL"],
        evidence_refs=["evidence.1"],
        data_snapshot_refs=["market.1"],
    )
    candidates.clear()
    assert inp.candidate_symbols == CANDIDATES
    with pytest.raises(FrozenInstanceError):
        inp.window = AnalysisWindow.EMERGENCY  # type: ignore[misc]


def test_snapshot_hash_is_recomputed_and_proposal_boundary_is_executable() -> None:
    inp = analysis_input()
    assert snapshot().content_hash == snapshot().compute_content_hash()
    proposal().validate_against(inp)
    wire = snapshot().to_wire()
    wire["cash"] = "35000.01"
    with pytest.raises(ValueError, match="content_hash"):
        type(snapshot()).from_wire(wire)
