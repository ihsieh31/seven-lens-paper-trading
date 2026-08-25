"""P3-D proposal-contract tests: identity derivation, bounds, tamper and wire rules."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from typing import cast

import pytest

from seven_lens.analysis.contracts import (
    SCHEMA_VERSION,
    AnalysisInput,
    AnalysisStatus,
    AnalysisWindow,
    ContractMeta,
    PortfolioRequest,
    PortfolioSnapshot,
    PositionSide,
    ProposalAction,
    ProposalReasonCode,
    ResearchRating,
    RiskRejectionCode,
    RiskRejectionFeedback,
    TraderPlan,
    build_portfolio_snapshot,
    canonical_wire_json,
)
from seven_lens.analysis.proposal_contracts import (
    PortfolioProposal,
    ProposalContext,
    ResearchBundle,
    ResearchBundleItem,
    RiskArgument,
    RiskDebateState,
    RiskViewpoint,
    assert_child_identity,
    build_portfolio_proposal,
    build_proposal_context,
    build_research_bundle,
    build_risk_debate,
    derive_argument_id,
    derive_bundle_id,
    derive_child_input_id,
    derive_child_run_id,
    derive_context_id,
    derive_debate_id,
    derive_proposal_id,
    derive_proposal_run_id,
)
from seven_lens.domain.value_objects import RunId, UtcTimestamp
from test_analysis_contracts import (
    analysis_input,
    limits,
    meta,
    rid,
    timestamp,
)
from test_analysis_contracts import (
    snapshot as base_snapshot,
)
from test_p3bc_evidence_and_infrastructure import evidence_packet

DEBATE_ORDER = (
    (RiskViewpoint.AGGRESSIVE, 1),
    (RiskViewpoint.CONSERVATIVE, 1),
    (RiskViewpoint.NEUTRAL, 1),
    (RiskViewpoint.AGGRESSIVE, 2),
    (RiskViewpoint.CONSERVATIVE, 2),
    (RiskViewpoint.NEUTRAL, 2),
)


def parent_input(window: AnalysisWindow = AnalysisWindow.PRIMARY) -> AnalysisInput:
    base = analysis_input(window)
    if window is AnalysisWindow.EMERGENCY:
        return base
    return replace(base, focus_symbols=("MSFT", "NVDA"))


def item(symbol: str, plan_number: int, parent: AnalysisInput | None = None) -> ResearchBundleItem:
    chosen = parent or parent_input()
    analysis_run_id = derive_child_run_id(chosen.input_id, symbol)
    analysis_input_id = derive_child_input_id(chosen.input_id, symbol)
    trader_plan = TraderPlan(
        ContractMeta(
            SCHEMA_VERSION,
            analysis_run_id,
            chosen.meta.created_at,
            chosen.meta.producer_version,
        ),
        rid(plan_number),
        analysis_input_id,
        symbol,
        ResearchRating.BUY,
        (ProposalReasonCode.FUNDAMENTAL,),
        ("evidence.1",),
        Decimal("100.00"),
        Decimal("110.00"),
        Decimal("90.00"),
        AnalysisStatus.VALID,
    )
    return ResearchBundleItem(
        symbol=symbol,
        analysis_run_id=analysis_run_id,
        analysis_input_id=analysis_input_id,
        packet_hash=evidence_packet().packet_hash,
        snapshot_hash=chosen.portfolio_snapshot.content_hash,
        trader_plan_id=rid(plan_number),
        trader_plan_hash=hashlib.sha256(canonical_wire_json(trader_plan).encode()).hexdigest(),
        trader_plan=trader_plan,
        evidence_refs=("evidence.1",),
        producer_version="p3a.1",
        graph_version="graph.1",
        prompt_version="prompt.1",
        data_version="data.1",
        status=AnalysisStatus.VALID,
    )


def bundle(
    parent: AnalysisInput | None = None,
    items: tuple[ResearchBundleItem, ...] | None = None,
) -> ResearchBundle:
    chosen = parent or parent_input()
    return build_research_bundle(
        meta=ContractMeta(SCHEMA_VERSION, derive_bundle_id(chosen.input_id), timestamp(), "p3a.1"),
        parent_input_id=chosen.input_id,
        as_of=chosen.as_of,
        window=chosen.window,
        deadline=chosen.deadline,
        universe_hash=chosen.universe_hash,
        portfolio_snapshot_hash=chosen.portfolio_snapshot.content_hash,
        data_snapshot_refs=chosen.data_snapshot_refs,
        holding_symbols=chosen.holding_symbols,
        candidate_symbols=chosen.candidate_symbols,
        items=items if items is not None else (item("MSFT", 71), item("NVDA", 72)),
    )


def refreshed_snapshot() -> PortfolioSnapshot:
    return build_portfolio_snapshot(
        as_of=timestamp(2),
        nav=base_snapshot().nav,
        cash=base_snapshot().cash,
        buying_power=base_snapshot().buying_power,
        positions=base_snapshot().positions,
        open_orders=base_snapshot().open_orders,
        same_day_fills=base_snapshot().same_day_fills,
        borrow_statuses=base_snapshot().borrow_statuses,
        remaining_limits=limits(),
    )


def context(attempt: int = 1, **overrides: object) -> ProposalContext:
    parent = parent_input()
    built_bundle = cast("ResearchBundle | None", overrides.pop("bundle", None)) or bundle()
    snapshot = cast("PortfolioSnapshot | None", overrides.pop("snapshot", None)) or (
        refreshed_snapshot() if attempt == 2 else parent.portfolio_snapshot
    )
    feedback = cast("RiskRejectionFeedback | None", overrides.pop("feedback", None))
    superseded = cast("RunId | None", overrides.pop("superseded_proposal_id", None))
    superseded_hash = cast(
        "str | None",
        overrides.pop(
            "superseded_proposal_hash",
            "d" * 64 if superseded is not None else None,
        ),
    )
    previous = cast("RunId | None", overrides.pop("previous_context_id", None))
    graph_version = cast("str", overrides.pop("graph_version", "graph.1"))
    identity = derive_context_id(
        built_bundle.bundle_id,
        attempt,
        snapshot.content_hash,
        superseded,
        superseded_hash,
    )
    return build_proposal_context(
        meta=ContractMeta(SCHEMA_VERSION, identity, timestamp(), "p3a.1"),
        attempt=attempt,
        bundle=built_bundle,
        snapshot=snapshot,
        allowed_symbols=(*parent.holding_symbols, *parent.candidate_symbols),
        graph_version=graph_version,
        prompt_version="prompt.1",
        model_version="model.1",
        provider_version="provider.1",
        data_version="data.1",
        memory_version="memory.1",
        previous_context_id=previous,
        superseded_proposal_id=superseded,
        superseded_proposal_hash=superseded_hash,
        feedback=feedback,
    )


def argument(ctx: ProposalContext, viewpoint: RiskViewpoint, round_number: int) -> RiskArgument:
    run_id = derive_proposal_run_id(ctx.context_id)
    return RiskArgument(
        meta=ContractMeta(SCHEMA_VERSION, run_id, timestamp(), "p3a.1"),
        argument_id=derive_argument_id(ctx.context_id, viewpoint, round_number),
        context_id=ctx.context_id,
        bundle_id=ctx.bundle_id,
        bundle_hash=ctx.bundle_hash,
        viewpoint=viewpoint,
        round_number=round_number,
        argument=f"{viewpoint.value} round {round_number}",
        evidence_refs=("evidence.1",),
        producer_version="p3a.1",
    )


def debate(ctx: ProposalContext) -> RiskDebateState:
    return build_risk_debate(
        meta=ContractMeta(
            SCHEMA_VERSION, derive_proposal_run_id(ctx.context_id), timestamp(), "p3a.1"
        ),
        context_id=ctx.context_id,
        bundle=bundle(),
        arguments=tuple(argument(ctx, viewpoint, number) for viewpoint, number in DEBATE_ORDER),
    )


def open_request(symbol: str, citation: str) -> PortfolioRequest:
    return PortfolioRequest(
        symbol,
        ProposalAction.OPEN,
        PositionSide.LONG,
        Decimal("0.050000"),
        Decimal("0.8000"),
        (citation,),
        (ProposalReasonCode.FUNDAMENTAL,),
        ("margin compression",),
    )


def p3d_proposal(ctx: ProposalContext | None = None) -> PortfolioProposal:
    chosen = ctx or context()
    return build_portfolio_proposal(
        context=chosen,
        requests=(open_request("MSFT", chosen.citation_ids[0]),),
        expiration_at=chosen.deadline,
        status=AnalysisStatus.VALID,
    )


def rejection(target: RunId, reviewed_at: UtcTimestamp | None = None) -> RiskRejectionFeedback:
    chosen_reviewed_at = timestamp(1) if reviewed_at is None else reviewed_at
    return RiskRejectionFeedback(
        meta(),
        target,
        1,
        (RiskRejectionCode.TURNOVER,),
        ("MSFT",),
        limits(),
        "c" * 64,
        chosen_reviewed_at,
    )


@pytest.mark.parametrize(
    "contract",
    [
        item("MSFT", 71),
        bundle(),
        context(),
        argument(context(), RiskViewpoint.AGGRESSIVE, 1),
        debate(context()),
        p3d_proposal(),
    ],
)
def test_exact_wire_round_trip(contract: object) -> None:
    wire = contract.to_wire()  # type: ignore[attr-defined]
    decoded = type(contract).from_wire(wire)  # type: ignore[attr-defined]
    assert decoded == contract


def test_child_identity_derivation_matches_golden_vectors_and_domains() -> None:
    parent = parent_input()
    assert str(derive_child_run_id(parent.input_id, "MSFT")) == (
        "3446b407-a014-43bb-9a67-4f62933dfd7e"
    )
    assert str(derive_child_input_id(parent.input_id, "MSFT")) == (
        "f1d0f99d-45b0-4c34-a077-155a5b15cd2f"
    )
    assert str(derive_child_run_id(parent.input_id, "NVDA")) == (
        "268c656b-48ea-4f88-bfdc-189eb80fdb40"
    )
    assert str(derive_bundle_id(parent.input_id)) == "a823f3d2-47f9-4aa8-90eb-fe280c02ce7e"
    ctx_one = derive_context_id(derive_bundle_id(parent.input_id), 1, "a" * 64, None)
    assert str(ctx_one) == "2b299a5a-4352-413e-9453-9ce698ee4464"
    assert str(
        derive_context_id(derive_bundle_id(parent.input_id), 2, "a" * 64, rid(11), "d" * 64)
    ) == ("2e192423-b0ad-46a9-9a32-a704845754ac")
    assert str(derive_debate_id(ctx_one)) == "559bc0a5-0be6-49aa-9805-632b047df1cb"
    assert str(derive_argument_id(ctx_one, RiskViewpoint.AGGRESSIVE, 1)) == (
        "b41d2f80-a62e-427a-a8e1-03229d9b72fc"
    )
    assert str(derive_proposal_id(ctx_one)) == "1e0dbc88-1733-4df8-a73a-508bb83b4001"
    assert str(derive_proposal_run_id(ctx_one)) == "ac6b8a43-debe-4900-8934-1ad75979e7fb"
    assert derive_child_run_id(parent.input_id, "MSFT") != derive_child_input_id(
        parent.input_id, "MSFT"
    )
    assert derive_child_run_id(parent.input_id, "MSFT") != derive_child_run_id(rid(3), "MSFT")


def test_bundle_items_cover_focus_symbols_exactly_and_derive_citations() -> None:
    built = bundle()
    assert tuple(x.symbol for x in built.items) == ("MSFT", "NVDA")
    assert built.focus_symbols == ("MSFT", "NVDA")
    assert built.citation_ids == ("evidence.1",)
    assert built.bundle_hash == built.compute_hash()
    assert built.meta.run_id == built.bundle_id
    assert_child_identity(built.parent_input_id, built.items[0])
    bundle().validate_integrity()
    context().validate_integrity()
    debate(context()).validate_integrity()


@pytest.mark.parametrize(
    "symbols",
    [("MSFT", "MSFT"), ()],
)
def test_bundle_rejects_duplicate_and_empty_items(symbols: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        bundle(items=tuple(item(symbol, 71 + index) for index, symbol in enumerate(symbols)))


def test_bundle_focus_count_bound_is_enforced() -> None:
    symbols = [f"S{index}" for index in range(28)]
    with pytest.raises(ValueError, match="bound"):
        bundle(items=tuple(item(symbol, 90 + index) for index, symbol in enumerate(symbols)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("packet_hash", "e" * 64),
        ("snapshot_hash", "f" * 64),
        ("producer_version", "rogue.1"),
    ],
)
def test_bundle_rejects_item_disagreement_after_first(field: str, value: str) -> None:
    base = item("NVDA", 72)
    if field == "producer_version":
        plan = replace(
            base.trader_plan,
            meta=replace(base.trader_plan.meta, producer_version=value),
        )
        drifted = replace(
            base,
            producer_version=value,
            trader_plan=plan,
            trader_plan_hash=hashlib.sha256(canonical_wire_json(plan).encode()).hexdigest(),
        )
    else:
        drifted = replace(base, **{field: value})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        bundle(items=(item("MSFT", 71), drifted))


def test_bundle_post_construction_mutation_is_frozen_or_detected() -> None:
    built = bundle()
    with pytest.raises(FrozenInstanceError):
        built.focus_symbols = ("NVDA",)  # type: ignore[misc]
    forged = object.__new__(ResearchBundle)
    for name in ResearchBundle.__slots__:
        object.__setattr__(forged, name, getattr(built, name))
    object.__setattr__(forged, "portfolio_snapshot_hash", "0" * 64)
    with pytest.raises(ValueError):
        forged.validate_integrity()

    forged_identity = object.__new__(ResearchBundle)
    for name in ResearchBundle.__slots__:
        object.__setattr__(forged_identity, name, getattr(built, name))
    object.__setattr__(forged_identity, "bundle_id", rid(99))
    object.__setattr__(
        forged_identity,
        "meta",
        replace(built.meta, run_id=rid(99)),
    )
    object.__setattr__(forged_identity, "bundle_hash", forged_identity.compute_hash())
    with pytest.raises(ValueError, match="deterministically derived"):
        forged_identity.validate_integrity()


def test_context_identity_must_match_deterministic_lineage() -> None:
    built = context()
    forged = object.__new__(ProposalContext)
    for name in ProposalContext.__slots__:
        object.__setattr__(forged, name, getattr(built, name))
    object.__setattr__(forged, "context_id", rid(99))
    object.__setattr__(forged, "meta", replace(built.meta, run_id=rid(99)))
    object.__setattr__(forged, "context_hash", forged.compute_hash())
    with pytest.raises(ValueError, match="deterministically derived"):
        forged.validate_integrity()


def test_context_attempt_lineage_rules_fail_closed() -> None:
    first = context(attempt=1)
    assert first.previous_context_id is None and first.feedback is None
    with pytest.raises(ValueError, match="attempt 1"):
        context(
            attempt=1,
            superseded_proposal_id=rid(11),
            previous_context_id=rid(12),
            feedback=rejection(rid(11)),
        )
    with pytest.raises(ValueError, match="attempt 2 context requires"):
        context(attempt=2, superseded_proposal_id=rid(11))
    second = context(
        attempt=2,
        superseded_proposal_id=rid(11),
        previous_context_id=first.context_id,
        feedback=rejection(rid(11)),
    )
    assert second.superseded_proposal_id == rid(11)
    assert second.feedback is not None
    assert second.feedback.rejected_proposal_id == rid(11)


def test_context_feedback_must_target_the_superseded_proposal() -> None:
    with pytest.raises(ValueError, match="target the superseded"):
        context(
            attempt=2,
            superseded_proposal_id=rid(11),
            previous_context_id=rid(12),
            feedback=rejection(rid(99)),
        )


@pytest.mark.parametrize("reviewed_at", [timestamp(0), timestamp(-30)], ids=["equal", "earlier"])
def test_attempt_two_review_must_strictly_follow_initial_context_creation(
    reviewed_at: UtcTimestamp,
) -> None:
    # The context meta is created at timestamp(0), the same anchor the pipeline uses for
    # the initial context and proposal: a review at or before it violates retry causality.
    with pytest.raises(ValueError, match="follow the initial context creation"):
        context(
            attempt=2,
            superseded_proposal_id=rid(11),
            previous_context_id=rid(12),
            feedback=rejection(rid(11), reviewed_at=reviewed_at),
        )


def test_context_timeline_requires_review_before_refreshed_snapshot() -> None:
    shifted = build_portfolio_snapshot(
        as_of=timestamp(2),
        nav=base_snapshot().nav,
        cash=base_snapshot().cash,
        buying_power=base_snapshot().buying_power,
        positions=base_snapshot().positions,
        open_orders=base_snapshot().open_orders,
        same_day_fills=base_snapshot().same_day_fills,
        borrow_statuses=base_snapshot().borrow_statuses,
        remaining_limits=limits(),
    )
    with pytest.raises(ValueError, match="review must not follow"):
        context(
            attempt=2,
            snapshot=shifted,
            superseded_proposal_id=rid(11),
            previous_context_id=rid(12),
            feedback=rejection(rid(11), reviewed_at=timestamp(30)),
        )


def test_risk_argument_rejects_wrong_derivation_and_foreign_citations() -> None:
    ctx = context()
    valid = argument(ctx, RiskViewpoint.NEUTRAL, 2)
    valid.validate_against_citations(ctx.citation_ids)
    foreign = replace(valid, evidence_refs=("outside.evidence",))
    with pytest.raises(ValueError, match="frozen bundle set"):
        foreign.validate_against_citations(ctx.citation_ids)
    with pytest.raises(ValueError, match="deterministic derivation"):
        replace(valid, argument_id=rid(50))


def test_debate_requires_six_fixed_order_arguments_exactly_once() -> None:
    ctx = context()
    full = debate(ctx)
    assert [x.viewpoint for x in full.arguments] == [v for v, _ in DEBATE_ORDER]
    with pytest.raises(ValueError, match="zero or exactly six"):
        build_risk_debate(
            meta=full.meta,
            context_id=ctx.context_id,
            bundle=bundle(),
            arguments=full.arguments[:5],
        )
    swapped = list(full.arguments)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(ValueError, match="order is fixed"):
        build_risk_debate(
            meta=full.meta,
            context_id=ctx.context_id,
            bundle=bundle(),
            arguments=tuple(swapped),
        )
    foreign = replace(full.arguments[0], bundle_id=rid(77))
    with pytest.raises(ValueError, match="argument identity is invalid"):
        build_risk_debate(
            meta=full.meta,
            context_id=ctx.context_id,
            bundle=bundle(),
            arguments=(foreign, *full.arguments[1:]),
        )
    incomplete = build_risk_debate(
        meta=full.meta, context_id=ctx.context_id, bundle=bundle(), arguments=()
    )
    assert incomplete.complete is False


def test_evolved_proposal_validates_against_exact_context_boundary() -> None:
    ctx = context()
    proposal = p3d_proposal(ctx)
    proposal.validate_against(ctx)
    assert proposal.proposal_id == derive_proposal_id(ctx.context_id)
    assert proposal.attempt == 1 and proposal.superseded_proposal_id is None


def test_evolved_proposal_rejects_drifted_context_versions() -> None:
    ctx = context()
    drifted = context(graph_version="graph.2")
    assert drifted.context_id == ctx.context_id
    assert drifted.context_hash != ctx.context_hash
    with pytest.raises(ValueError, match="context boundary"):
        p3d_proposal(ctx).validate_against(drifted)


def test_evolved_proposal_rejects_foreign_symbol_citation_and_deadline() -> None:
    ctx = context()
    foreign_symbol = build_portfolio_proposal(
        context=ctx,
        requests=(open_request("XOM", ctx.citation_ids[0]),),
        expiration_at=ctx.deadline,
        status=AnalysisStatus.VALID,
    )
    with pytest.raises(ValueError, match="allowed context universe"):
        foreign_symbol.validate_against(ctx)
    foreign_citation = build_portfolio_proposal(
        context=ctx,
        requests=(
            PortfolioRequest(
                "MSFT",
                ProposalAction.OPEN,
                PositionSide.LONG,
                Decimal("0.020000"),
                Decimal("0.8000"),
                ("foreign.evidence",),
                (ProposalReasonCode.FUNDAMENTAL,),
                (),
            ),
        ),
        expiration_at=ctx.deadline,
        status=AnalysisStatus.VALID,
    )
    with pytest.raises(ValueError, match="frozen bundle set"):
        foreign_citation.validate_against(ctx)
    late = build_portfolio_proposal(
        context=ctx,
        requests=(),
        expiration_at=timestamp(16),
        status=AnalysisStatus.ABSTAIN,
    )
    with pytest.raises(ValueError, match="expiration cannot exceed"):
        late.validate_against(ctx)


def test_attempt_two_supersedes_exactly_once() -> None:
    first_ctx = context(attempt=1)
    first = p3d_proposal(first_ctx)
    second_ctx = context(
        attempt=2,
        superseded_proposal_id=first.proposal_id,
        previous_context_id=first_ctx.context_id,
        feedback=rejection(first.proposal_id),
    )
    second = build_portfolio_proposal(
        context=second_ctx,
        requests=(),
        expiration_at=second_ctx.deadline,
        status=AnalysisStatus.ABSTAIN,
    )
    second.validate_against(second_ctx)
    assert second.attempt == 2
    assert second.superseded_proposal_id == first.proposal_id
    with pytest.raises(ValueError, match="attempt 1 has no superseded"):
        PortfolioProposal(
            second.meta,
            second.proposal_id,
            1,
            second.context_id,
            second.context_hash,
            second.bundle_id,
            second.bundle_hash,
            first.proposal_id,
            second.universe_hash,
            second.snapshot_hash,
            second.window,
            (),
            "graph.1",
            "prompt.1",
            "model.1",
            "provider.1",
            "data.1",
            "memory.1",
            second.expiration_at,
            AnalysisStatus.ABSTAIN,
        )


def test_wire_parser_rejects_unknown_fields_bool_attempts_and_bad_decimals() -> None:
    wire = p3d_proposal().to_wire()
    unknown = dict(wire)
    unknown["extra"] = "nope"
    with pytest.raises(ValueError, match="exact contract fields"):
        PortfolioProposal.from_wire(unknown)
    missing = {k: v for k, v in wire.items() if k != "attempt"}
    with pytest.raises(ValueError, match="exact contract fields"):
        PortfolioProposal.from_wire(missing)
    bool_attempt = dict(wire)
    bool_attempt["attempt"] = True
    with pytest.raises(ValueError, match="exact bounded integer"):
        PortfolioProposal.from_wire(bool_attempt)
    negative_zero = json.loads(json.dumps(wire))
    negative_zero["requests"][0]["target_weight"] = "-0.000000"
    with pytest.raises(ValueError, match=r"negative zero|canonical"):
        PortfolioProposal.from_wire(negative_zero)
    wrong_scale = json.loads(json.dumps(wire))
    wrong_scale["requests"][0]["confidence"] = "0.80000"
    with pytest.raises(ValueError, match="decimal places"):
        PortfolioProposal.from_wire(wrong_scale)


def test_emergency_context_blocks_open_requests_through_validate_against() -> None:
    parent = parent_input(AnalysisWindow.EMERGENCY)
    built_bundle = bundle(parent=parent, items=(item("TSLA", 73, parent),))
    identity = derive_context_id(
        built_bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
    )
    emergency_context = build_proposal_context(
        meta=ContractMeta(SCHEMA_VERSION, identity, timestamp(), "p3a.1"),
        attempt=1,
        bundle=built_bundle,
        snapshot=parent.portfolio_snapshot,
        allowed_symbols=parent.holding_symbols,
        graph_version="graph.1",
        prompt_version="prompt.1",
        model_version="model.1",
        provider_version="provider.1",
        data_version="data.1",
        memory_version="memory.1",
    )
    aggressive = build_portfolio_proposal(
        context=emergency_context,
        requests=(
            PortfolioRequest(
                "TSLA",
                ProposalAction.OPEN,
                PositionSide.SHORT,
                Decimal("-0.010000"),
                Decimal("0.9000"),
                (built_bundle.citation_ids[0],),
                (ProposalReasonCode.NEWS,),
                (),
            ),
        ),
        expiration_at=parent.deadline,
        status=AnalysisStatus.VALID,
    )
    with pytest.raises(ValueError, match="emergency proposal cannot open"):
        aggressive.validate_against(emergency_context)


def test_low_confidence_non_hold_request_is_rejected_by_shared_contract() -> None:
    with pytest.raises(ValueError, match="HOLD only"):
        PortfolioRequest(
            "MSFT",
            ProposalAction.OPEN,
            PositionSide.LONG,
            Decimal("0.010000"),
            Decimal("0.6000"),
            ("evidence.1",),
            (ProposalReasonCode.TECHNICAL,),
            (),
        )


def test_bundle_citation_union_is_always_deterministically_sorted() -> None:
    built = bundle()
    multi = replace(built.items[0], evidence_refs=("evidence.9", "evidence.2"))
    merged = bundle(items=(multi, built.items[1]))
    assert merged.citation_ids == ("evidence.1", "evidence.2", "evidence.9")
