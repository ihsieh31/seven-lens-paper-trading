"""P3-D coordinator and proposal-pipeline tests: traces, deadlines, retry and authority."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from seven_lens.analysis.contracts import (
    SCHEMA_VERSION,
    AnalysisInput,
    AnalysisStatus,
    AnalystReport,
    ContractMeta,
    PortfolioRequest,
    PortfolioSnapshot,
    PositionSide,
    ProposalAction,
    ProposalReasonCode,
    ResearchConclusion,
    ResearchRating,
    RiskRejectionCode,
    RiskRejectionFeedback,
    TraderPlan,
    build_portfolio_snapshot,
    canonical_wire_json,
)
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    derive_provider_output_id,
)
from seven_lens.analysis.pipeline import AnalysisPipeline, AnalysisPipelineError
from seven_lens.analysis.ports import (
    DebateArgument,
    ProviderOutput,
    ProviderRequest,
    ProviderStage,
)
from seven_lens.analysis.proposal_contracts import (
    PortfolioProposal,
    ProposalContext,
    ResearchBundle,
    RiskArgument,
    RiskViewpoint,
    build_proposal_context,
    derive_argument_id,
    derive_bundle_id,
    derive_child_input_id,
    derive_child_run_id,
    derive_context_id,
    derive_proposal_run_id,
)
from seven_lens.analysis.proposal_pipeline import (
    ProposalPipeline,
    ProposalPipelineError,
    ProposalProducerVersions,
    ResearchBatchCoordinator,
)
from seven_lens.analysis.proposal_ports import (
    ProposalOutput,
    ProposalProvider,
    ProposalProviderStage,
    ProposalRequest,
)
from seven_lens.application.ports.analysis import (
    AnalysisStage,
    InMemoryAnalysisStateRepository,
)
from seven_lens.application.ports.proposals import (
    MAX_PROPOSAL_STAGE_ATTEMPTS,
    AnalysisRepositoryBundleVerifier,
    InMemoryProposalStateRepository,
    ProposalStage,
    StoredProposalResult,
)
from seven_lens.domain.value_objects import RunId, UtcTimestamp
from seven_lens.sources.contracts import EvidencePacket, build_evidence_packet
from test_analysis_contracts import (
    limits,
    meta,
    rid,
    timestamp,
)
from test_analysis_contracts import (
    snapshot as base_snapshot,
)
from test_p3bc_evidence_and_infrastructure import evidence_packet
from test_p3d_proposal_contracts import bundle as fixture_bundle
from test_p3d_proposal_contracts import context as fixture_context
from test_p3d_proposal_contracts import debate as fixture_debate
from test_p3d_proposal_contracts import item as fixture_item
from test_p3d_proposal_contracts import p3d_proposal as fixture_p3d_proposal
from test_p3d_proposal_contracts import parent_input as fixture_parent

PRODUCER = "p3a.1"
RESEARCH_KEYS = (
    "ANALYST:TECHNICAL:",
    "ANALYST:FUNDAMENTALS:",
    "ANALYST:NEWS:",
    "ANALYST:SENTIMENT:",
    "BULL::1",
    "BEAR::1",
    "BULL::2",
    "BEAR::2",
    "RESEARCH_MANAGER::",
    "TRADER::",
)
INITIAL_TRACE = [
    "AGGRESSIVE:1",
    "CONSERVATIVE:1",
    "NEUTRAL:1",
    "AGGRESSIVE:2",
    "CONSERVATIVE:2",
    "NEUTRAL:2",
    "PORTFOLIO_MANAGER:",
]


def assert_grouped_research_trace(calls: list[str]) -> None:
    assert len(calls) == len(RESEARCH_KEYS)
    assert set(calls[:4]) == set(RESEARCH_KEYS[:4])
    assert set(calls[4:6]) == set(RESEARCH_KEYS[4:6])
    assert set(calls[6:8]) == set(RESEARCH_KEYS[6:8])
    assert tuple(calls[8:]) == RESEARCH_KEYS[8:]


def assert_grouped_proposal_trace(calls: list[str]) -> None:
    assert len(calls) == len(INITIAL_TRACE)
    assert set(calls[:3]) == set(INITIAL_TRACE[:3])
    assert set(calls[3:6]) == set(INITIAL_TRACE[3:6])
    assert calls[6:] == INITIAL_TRACE[6:]


class FixtureBundleVerifier:
    """Explicit test double; strict verifier coverage is separate below."""

    def verify(self, bundle: ResearchBundle) -> None:
        bundle.validate_integrity()


def versions() -> ProposalProducerVersions:
    return ProposalProducerVersions(
        "graph.1", "prompt.1", "model.1", "provider.1", "data.1", "memory.1"
    )


def plan_id(symbol: str) -> RunId:
    parent_id = fixture_parent().input_id
    return derive_provider_output_id(
        derive_child_run_id(parent_id, symbol),
        derive_child_input_id(parent_id, symbol),
        EnvelopeStage.TRADER,
        EnvelopeRole.TRADER,
        None,
    )


class PerSymbolResearchProvider:
    """Deterministic P3-C fake producing valid child outputs for any requested symbol."""

    def __init__(self, parent_id: RunId, *, fail_key: tuple[str, str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._parent_id = parent_id
        self._fail = fail_key

    def execute(self, request: ProviderRequest) -> ProviderOutput:
        self.calls.append((request.symbol, request.key))
        if self._fail == (request.symbol, request.key):
            raise TimeoutError("scripted research failure")
        run_meta = ContractMeta(
            SCHEMA_VERSION,
            derive_child_run_id(self._parent_id, request.symbol),
            timestamp(),
            PRODUCER,
        )
        if request.stage is ProviderStage.ANALYST:
            if request.role is None:
                raise ValueError("analyst request requires a role")
            return AnalystReport(
                run_meta,
                request.envelope.output_id,
                request.input_id,
                request.role,
                request.symbol,
                AnalysisStatus.VALID,
                "summary",
                ("observation",),
                ("claim",),
                ("evidence.1",),
                (),
                ("missing",),
                ("risk",),
                ("catalyst",),
                ("invalidator",),
                Decimal("0.8000"),
            )
        if request.stage in {ProviderStage.BULL, ProviderStage.BEAR}:
            return DebateArgument(
                request.input_id,
                request.packet_hash,
                request.symbol,
                request.stage,
                request.round_number or 0,
                f"{request.stage.value.lower()} argument {request.round_number}",
                ("evidence.1",),
            )
        if request.stage is ProviderStage.RESEARCH_MANAGER:
            return ResearchConclusion(
                run_meta,
                request.envelope.output_id,
                request.input_id,
                request.symbol,
                ResearchRating.BUY,
                "conclusion",
                ("driver",),
                ("risk",),
                ("invalidator",),
                ("evidence.1",),
                Decimal("0.8000"),
                AnalysisStatus.VALID,
            )
        return TraderPlan(
            run_meta,
            request.envelope.output_id,
            request.input_id,
            request.symbol,
            ResearchRating.BUY,
            (ProposalReasonCode.FUNDAMENTAL,),
            ("evidence.1",),
            Decimal("100.00"),
            Decimal("110.00"),
            Decimal("90.00"),
            AnalysisStatus.VALID,
        )


def make_coordinator(
    parent: AnalysisInput,
    *,
    now: Callable[[], datetime] | None = None,
    fail_key: tuple[str, str] | None = None,
) -> tuple[ResearchBatchCoordinator, PerSymbolResearchProvider, InMemoryAnalysisStateRepository]:
    provider = PerSymbolResearchProvider(parent.input_id, fail_key=fail_key)
    repository = InMemoryAnalysisStateRepository()
    clock = now or (lambda: timestamp().value)
    pipeline = AnalysisPipeline(provider, repository, now=clock)
    coordinator = ResearchBatchCoordinator(pipeline, versions(), now=clock)
    return coordinator, provider, repository


def packet_with_refs(refs: tuple[str, ...]) -> EvidencePacket:
    base = evidence_packet()
    return build_evidence_packet(
        schema_version=base.schema_version,
        packet_id=base.packet_id,
        as_of=base.as_of,
        source_records=base.source_records,
        fragments=base.fragments,
        claims=base.claims,
        contradiction_claim_ids=base.contradiction_claim_ids,
        missing_evidence=base.missing_evidence,
        freshness_status=base.freshness_status,
        status=base.status,
        universe_hash=base.universe_hash,
        portfolio_snapshot_hash=base.portfolio_snapshot_hash,
        data_snapshot_refs=refs,
        producer_version=base.producer_version,
    )


def test_batch_joins_children_in_parent_focus_order_with_exact_calls() -> None:
    parent = fixture_parent()
    coordinator, provider, repository = make_coordinator(parent)
    built = coordinator.run(parent, evidence_packet())
    assert [symbol for symbol, _ in provider.calls[:10]] == ["MSFT"] * 10
    assert [symbol for symbol, _ in provider.calls[10:]] == ["NVDA"] * 10
    assert_grouped_research_trace([key for _, key in provider.calls[:10]])
    assert_grouped_research_trace([key for _, key in provider.calls[10:]])
    assert tuple(item.symbol for item in built.items) == ("MSFT", "NVDA")
    assert built.bundle_id == derive_bundle_id(parent.input_id)
    first = built.items[0]
    assert first.analysis_run_id == derive_child_run_id(parent.input_id, "MSFT")
    assert first.analysis_input_id == derive_child_input_id(parent.input_id, "MSFT")
    assert first.trader_plan_id == plan_id("MSFT")
    assert re.fullmatch(r"[0-9a-f]{64}", first.trader_plan_hash) is not None
    assert repository.current_stage(str(first.analysis_run_id)) is AnalysisStage.COMPLETE


@pytest.mark.parametrize(
    "items",
    [
        ("NVDA",),  # partial: only one of the two parent focus symbols
        ("MSFT", "XOM"),  # foreign: a symbol outside the parent focus set
        ("NVDA", "MSFT"),  # reordered: right symbols, wrong parent focus order
    ],
    ids=["partial", "foreign", "reordered"],
)
def test_bundle_focus_mismatch_never_reaches_proposal_authority(
    items: tuple[str, ...],
) -> None:
    parent = fixture_parent()
    provider = ProposalFakeProvider()
    pipeline, repository = make_proposal_pipeline(provider)
    if "XOM" in items:
        with pytest.raises(ValueError, match="exact universe"):
            fixture_bundle(
                items=tuple(fixture_item(symbol, 71 + i) for i, symbol in enumerate(items))
            )
        assert provider.calls == []
        return
    mismatched = fixture_bundle(
        items=tuple(fixture_item(symbol, 71 + i) for i, symbol in enumerate(items))
    )
    with pytest.raises(ProposalPipelineError, match="parent focus order"):
        pipeline.run(mismatched, parent)
    assert provider.calls == []
    context_id = derive_context_id(
        mismatched.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
    )
    with pytest.raises(KeyError):
        repository.current_stage(str(derive_proposal_run_id(context_id)))


def test_coordinator_built_bundle_passes_proposal_pipeline_unchanged() -> None:
    parent = fixture_parent()
    coordinator, _, _ = make_coordinator(parent)
    built = coordinator.run(parent, evidence_packet())
    provider = ProposalFakeProvider()
    pipeline, repository = make_proposal_pipeline(provider)
    proposal = pipeline.run(built, parent)
    assert_grouped_proposal_trace(provider.calls)
    assert proposal.status is AnalysisStatus.VALID
    run_id = str(
        derive_proposal_run_id(
            derive_context_id(built.bundle_id, 1, parent.portfolio_snapshot.content_hash, None)
        )
    )
    assert repository.current_stage(run_id) is ProposalStage.COMPLETE


def test_batch_resume_replays_completed_children_without_provider_calls() -> None:
    parent = fixture_parent()
    coordinator, _, repository = make_coordinator(parent)
    first = coordinator.run(parent, evidence_packet())
    replay_provider = PerSymbolResearchProvider(parent.input_id)
    replay_pipeline = AnalysisPipeline(replay_provider, repository, now=lambda: timestamp().value)
    replay = ResearchBatchCoordinator(
        replay_pipeline, versions(), now=lambda: timestamp().value
    ).run(parent, evidence_packet())
    assert replay == first
    assert replay_provider.calls == []


@pytest.mark.parametrize("stage", [AnalysisStage.TRADER, AnalysisStage.COMPLETE])
def test_batch_resume_rejects_stale_hash_child_authority(stage: AnalysisStage) -> None:
    parent = fixture_parent()
    coordinator, _, repository = make_coordinator(parent)
    coordinator.run(parent, evidence_packet())
    run_id = str(derive_child_run_id(parent.input_id, "MSFT"))
    stored = repository.load(run_id, stage)
    assert stored is not None
    payload = (
        "tampered"
        if stage is AnalysisStage.COMPLETE
        else stored.payload.replace('"100.00"', '"101.00"', 1)
    )
    repository._results[(run_id, stage)] = replace(stored, payload=payload)
    replay_provider = PerSymbolResearchProvider(parent.input_id)
    replay = ResearchBatchCoordinator(
        AnalysisPipeline(replay_provider, repository, now=lambda: timestamp().value),
        versions(),
        now=lambda: timestamp().value,
    )
    with pytest.raises(AnalysisPipelineError, match="result identity is invalid"):
        replay.run(parent, evidence_packet())
    assert replay_provider.calls == []


def test_inmemory_bundle_authority_requires_verified_p3c_results() -> None:
    with pytest.raises(ValueError, match="verifier is required"):
        InMemoryProposalStateRepository(now=lambda: timestamp(5).value).register_bundle(
            fixture_bundle()
        )

    parent = fixture_parent()
    coordinator, _, analysis_repository = make_coordinator(parent)
    built = coordinator.run(parent, evidence_packet())
    strict_repository = InMemoryProposalStateRepository(
        AnalysisRepositoryBundleVerifier(analysis_repository),
        now=lambda: timestamp(5).value,
    )
    strict_repository.register_bundle(built)

    child_run = str(built.items[0].analysis_run_id)
    trader = analysis_repository.load(child_run, AnalysisStage.TRADER)
    assert trader is not None
    analysis_repository._results[(child_run, AnalysisStage.TRADER)] = replace(
        trader, payload=trader.payload.replace('"100.00"', '"101.00"', 1)
    )
    with pytest.raises(ValueError, match="verified child authority"):
        InMemoryProposalStateRepository(
            AnalysisRepositoryBundleVerifier(analysis_repository),
            now=lambda: timestamp(5).value,
        ).register_bundle(built)


def test_batch_partial_failure_keeps_child_authority_and_resumes() -> None:
    parent = fixture_parent()
    coordinator, provider, repository = make_coordinator(parent, fail_key=("NVDA", "TRADER::"))
    with pytest.raises(AnalysisPipelineError, match="failed closed"):
        coordinator.run(parent, evidence_packet())
    assert (
        repository.current_stage(str(derive_child_run_id(parent.input_id, "MSFT")))
        is AnalysisStage.COMPLETE
    )
    assert (
        repository.current_stage(str(derive_child_run_id(parent.input_id, "NVDA")))
        is AnalysisStage.RESEARCH
    )
    resumed_provider = PerSymbolResearchProvider(parent.input_id)
    resumed_pipeline = AnalysisPipeline(resumed_provider, repository, now=lambda: timestamp().value)
    resumed = ResearchBatchCoordinator(
        resumed_pipeline, versions(), now=lambda: timestamp().value
    ).run(parent, evidence_packet())
    assert tuple(item.symbol for item in resumed.items) == ("MSFT", "NVDA")
    assert all(symbol == "NVDA" for symbol, _ in resumed_provider.calls)
    assert provider.calls[-1] == ("NVDA", "TRADER::")


def test_batch_rejects_expired_parent_before_any_child_authority() -> None:
    parent = fixture_parent()
    coordinator, _, repository = make_coordinator(parent, now=lambda: timestamp(16).value)
    with pytest.raises(ProposalPipelineError, match="deadline expired"):
        coordinator.run(parent, evidence_packet())
    with pytest.raises(KeyError):
        repository.current_stage(str(derive_child_run_id(parent.input_id, "MSFT")))


def test_batch_rejects_tampered_parent_and_packet_drift_before_authority() -> None:
    parent = fixture_parent()
    coordinator, _, repository = make_coordinator(parent)
    forged = replace(parent)
    object.__setattr__(forged, "focus_symbols", ("SPY",))
    with pytest.raises(ProposalPipelineError, match="input integrity"):
        coordinator.run(forged, evidence_packet())
    with pytest.raises(ProposalPipelineError, match="identity mismatch"):
        coordinator.run(parent, packet_with_refs(("other.snapshot",)))
    with pytest.raises(KeyError):
        repository.current_stage(str(derive_child_run_id(parent.input_id, "MSFT")))


def test_batch_requires_focus_and_supports_single_symbol() -> None:
    parent = fixture_parent()
    empty_focus = replace(parent, focus_symbols=())
    coordinator, _, _ = make_coordinator(empty_focus)
    with pytest.raises(ProposalPipelineError, match="at least one focus symbol"):
        coordinator.run(empty_focus, evidence_packet())
    single_parent = replace(parent, focus_symbols=("MSFT",))
    single_coordinator, _, _ = make_coordinator(single_parent)
    built = single_coordinator.run(single_parent, evidence_packet())
    assert tuple(item.symbol for item in built.items) == ("MSFT",)


class ProposalFakeProvider:
    def __init__(
        self,
        *,
        pm_status: AnalysisStatus = AnalysisStatus.VALID,
        argument_refs: tuple[str, ...] = ("evidence.1",),
    ) -> None:
        self.calls: list[str] = []
        self._pm_status = pm_status
        self._argument_refs = argument_refs

    def execute(self, request: ProposalRequest) -> ProposalOutput:
        self.calls.append(request.key)
        if request.stage in {
            ProposalProviderStage.AGGRESSIVE,
            ProposalProviderStage.CONSERVATIVE,
            ProposalProviderStage.NEUTRAL,
        }:
            if request.round_number is None:
                raise ValueError("viewpoint request requires a round number")
            viewpoint = RiskViewpoint(request.stage.value)
            return RiskArgument(
                meta=ContractMeta(SCHEMA_VERSION, request.run_id, request.created_at, PRODUCER),
                argument_id=request.output_id,
                context_id=request.context_id,
                bundle_id=request.bundle_id,
                bundle_hash=request.bundle_hash,
                viewpoint=viewpoint,
                round_number=request.round_number,
                argument=f"{viewpoint.value} round {request.round_number}",
                evidence_refs=self._argument_refs,
                producer_version=PRODUCER,
            )
        payload = (
            PortfolioRequest(
                request.allowed_symbols[-1],
                ProposalAction.OPEN,
                PositionSide.LONG,
                Decimal("0.050000"),
                Decimal("0.8000"),
                (request.citation_ids[0],),
                (ProposalReasonCode.FUNDAMENTAL,),
                ("margin compression",),
            ),
        )
        return PortfolioProposal(
            meta=ContractMeta(SCHEMA_VERSION, request.output_id, request.created_at, PRODUCER),
            proposal_id=request.output_id,
            attempt=request.attempt,
            context_id=request.context_id,
            context_hash=request.context_hash,
            bundle_id=request.bundle_id,
            bundle_hash=request.bundle_hash,
            superseded_proposal_id=request.superseded_proposal_id,
            universe_hash=request.universe_hash,
            snapshot_hash=request.snapshot_hash,
            window=request.window,
            requests=payload,
            graph_version="graph.1",
            prompt_version="prompt.1",
            model_version="model.1",
            provider_version="provider.1",
            data_version="data.1",
            memory_version="memory.1",
            expiration_at=request.deadline,
            status=self._pm_status,
        )


class ClockFlippingProposalProvider:
    """Delegate that moves the shared clock forward after N completed provider calls."""

    def __init__(self, inner: ProposalFakeProvider, clock: list[datetime], flip_after: int) -> None:
        self._inner = inner
        self._clock = clock
        self._flip_after = flip_after
        self.calls = inner.calls

    def execute(self, request: ProposalRequest) -> ProposalOutput:
        output = self._inner.execute(request)
        if len(self._inner.calls) >= self._flip_after:
            self._clock[0] = timestamp(16).value
        return output


class TamperingProposalProvider(ProposalFakeProvider):
    """Mutate one nested PortfolioRequest field after the contract object is fully built."""

    def __init__(self, field: str, value: Decimal) -> None:
        super().__init__()
        self._field = field
        self._value = value

    def execute(self, request: ProposalRequest) -> ProposalOutput:
        output = super().execute(request)
        if (
            request.stage
            in {
                ProposalProviderStage.PORTFOLIO_MANAGER,
                ProposalProviderStage.PORTFOLIO_MANAGER_RETRY,
            }
            and type(output) is PortfolioProposal
            and output.requests
        ):
            object.__setattr__(output.requests[0], self._field, self._value)
        return output


def make_proposal_pipeline(
    provider: ProposalProvider,
    *,
    now: Callable[[], datetime] | None = None,
    repository: InMemoryProposalStateRepository | None = None,
    producer_versions: ProposalProducerVersions | None = None,
) -> tuple[ProposalPipeline, InMemoryProposalStateRepository]:
    clock = now or (lambda: timestamp(5).value)
    repo = (
        repository
        if repository is not None
        else InMemoryProposalStateRepository(FixtureBundleVerifier(), now=clock)
    )
    pipeline = ProposalPipeline(provider, repo, producer_versions or versions(), now=clock)
    return pipeline, repo


def refreshed_snapshot_at(minutes: int) -> PortfolioSnapshot:
    return build_portfolio_snapshot(
        as_of=timestamp(minutes),
        nav=base_snapshot().nav,
        cash=base_snapshot().cash,
        buying_power=base_snapshot().buying_power,
        positions=base_snapshot().positions,
        open_orders=base_snapshot().open_orders,
        same_day_fills=base_snapshot().same_day_fills,
        borrow_statuses=base_snapshot().borrow_statuses,
        remaining_limits=limits(),
    )


def feedback_for(
    proposal: PortfolioProposal, reviewed_at: UtcTimestamp | None = None
) -> RiskRejectionFeedback:
    chosen_reviewed_at = timestamp(1) if reviewed_at is None else reviewed_at
    return RiskRejectionFeedback(
        meta(),
        proposal.proposal_id,
        1,
        (RiskRejectionCode.TURNOVER,),
        tuple(request.symbol for request in proposal.requests),
        limits(),
        "c" * 64,
        chosen_reviewed_at,
    )


def proposal_payload_hash(proposal: PortfolioProposal) -> str:
    return hashlib.sha256(canonical_wire_json(proposal).encode()).hexdigest()


def registered_proposal_run(
    repository: InMemoryProposalStateRepository | None = None,
) -> tuple[
    InMemoryProposalStateRepository,
    ResearchBundle,
    ProposalContext,
    str,
]:
    chosen = repository or InMemoryProposalStateRepository(
        FixtureBundleVerifier(), now=lambda: timestamp(5).value
    )
    built = fixture_bundle()
    ctx = fixture_context()
    chosen.register_bundle(built)
    chosen.register_context(ctx)
    run_id = str(derive_proposal_run_id(ctx.context_id))
    chosen.create_run(run_id, str(ctx.context_id), str(built.bundle_id), built.bundle_hash)
    return chosen, built, ctx, run_id


def stored_result(run_id: str, stage: ProposalStage, payload: str) -> StoredProposalResult:
    return StoredProposalResult(
        run_id, stage, hashlib.sha256(payload.encode()).hexdigest(), payload
    )


def test_initial_flow_exact_call_trace_and_completion() -> None:
    provider = ProposalFakeProvider()
    pipeline, repository = make_proposal_pipeline(provider)
    parent = fixture_parent()
    bundle = fixture_bundle()
    proposal = pipeline.run(bundle, parent)
    assert_grouped_proposal_trace(provider.calls)
    assert proposal.status is AnalysisStatus.VALID
    assert proposal.attempt == 1
    context_id = derive_context_id(
        bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
    )
    run_id = str(derive_proposal_run_id(context_id))
    assert repository.current_stage(run_id) is ProposalStage.COMPLETE
    replay_provider = ProposalFakeProvider()
    replay_pipeline, _ = make_proposal_pipeline(replay_provider, repository=repository)
    assert replay_pipeline.run(bundle, parent) == proposal
    assert replay_provider.calls == []


def test_deadline_crossing_before_pm_keeps_only_debate_authority() -> None:
    clock = [timestamp().value]
    fake = ProposalFakeProvider()
    provider = ClockFlippingProposalProvider(fake, clock, flip_after=6)
    pipeline, repository = make_proposal_pipeline(provider, now=lambda: clock[0])
    parent = fixture_parent()
    bundle = fixture_bundle()
    with pytest.raises(ProposalPipelineError, match="deadline expired"):
        pipeline.run(bundle, parent)
    context_id = derive_context_id(
        bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
    )
    run_id = str(derive_proposal_run_id(context_id))
    # A deadline crossing before persistence leaves zero new authority behind.
    assert repository.current_stage(run_id) is ProposalStage.PLANNED
    assert repository.load(run_id, ProposalStage.RISK_DEBATE) is None


def test_deadline_flip_during_persist_leaves_zero_authority() -> None:
    parent = fixture_parent()
    built = fixture_bundle()
    context = fixture_context()
    clock = [parent.deadline.value - timedelta(microseconds=1)]

    class FlippingRepository(InMemoryProposalStateRepository):
        armed = False

        def current_stage(self, run_id: str) -> ProposalStage:
            current = super().current_stage(run_id)
            if self.armed:
                clock[0] = parent.deadline.value + timedelta(microseconds=1)
            return current

    repository = FlippingRepository(FixtureBundleVerifier(), now=lambda: clock[0])
    repository.register_bundle(built)
    repository.register_context(context)
    run_id = str(derive_proposal_run_id(context.context_id))
    repository.create_run(run_id, str(context.context_id), str(built.bundle_id), built.bundle_hash)
    pipeline = ProposalPipeline(
        ProposalFakeProvider(), repository, versions(), now=lambda: clock[0]
    )
    repository.armed = True
    debate_payload = canonical_wire_json(fixture_debate(context))
    with pytest.raises(ProposalPipelineError, match="deadline expired"):
        pipeline._persist(
            run_id,
            context,
            ProposalStage.PLANNED,
            ProposalStage.RISK_DEBATE,
            debate_payload,
        )
    repository.armed = False
    assert repository.current_stage(run_id) is ProposalStage.PLANNED
    assert repository.load(run_id, ProposalStage.RISK_DEBATE) is None


def test_expired_context_leaves_no_authority() -> None:
    provider = ProposalFakeProvider()
    pipeline, repository = make_proposal_pipeline(provider, now=lambda: timestamp(16).value)
    parent = fixture_parent()
    bundle = fixture_bundle()
    with pytest.raises(ProposalPipelineError, match="deadline expired"):
        pipeline.run(bundle, parent)
    assert provider.calls == []
    context_id = derive_context_id(
        bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
    )
    with pytest.raises(KeyError):
        repository.current_stage(str(derive_proposal_run_id(context_id)))


def test_deadline_microsecond_boundary_is_exact() -> None:
    parent = fixture_parent()
    bundle = fixture_bundle()
    deadline = parent.deadline.value

    def clock_at(moment: datetime) -> Callable[[], datetime]:
        return lambda: moment

    # The rule is "expired iff now > deadline": one microsecond before and exactly
    # at the deadline both stay valid and complete; one microsecond after expires
    # with zero run authority and zero provider calls.
    for now, completes in (
        (deadline - timedelta(microseconds=1), True),
        (deadline, True),
        (deadline + timedelta(microseconds=1), False),
    ):
        provider = ProposalFakeProvider()
        pipeline, repository = make_proposal_pipeline(provider, now=clock_at(now))
        if completes:
            proposal = pipeline.run(bundle, parent)
            assert proposal.status is AnalysisStatus.VALID
            assert_grouped_proposal_trace(provider.calls)
            run_id = str(
                derive_proposal_run_id(
                    derive_context_id(
                        bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
                    )
                )
            )
            assert repository.current_stage(run_id) is ProposalStage.COMPLETE
        else:
            with pytest.raises(ProposalPipelineError, match="deadline expired"):
                pipeline.run(bundle, parent)
            assert provider.calls == []
            context_id = derive_context_id(
                bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
            )
            with pytest.raises(KeyError):
                repository.current_stage(str(derive_proposal_run_id(context_id)))


def test_late_argument_return_is_never_persisted() -> None:
    clock = [timestamp().value]
    fake = ProposalFakeProvider()
    provider = ClockFlippingProposalProvider(fake, clock, flip_after=1)
    pipeline, repository = make_proposal_pipeline(provider, now=lambda: clock[0])
    parent = fixture_parent()
    bundle = fixture_bundle()
    with pytest.raises(ProposalPipelineError, match="deadline expired"):
        pipeline.run(bundle, parent)
    context_id = derive_context_id(
        bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
    )
    run_id = str(derive_proposal_run_id(context_id))
    assert repository.current_stage(run_id) is ProposalStage.PLANNED
    assert repository.load(run_id, ProposalStage.RISK_DEBATE) is None


def test_foreign_argument_citation_never_persists_partial_debate() -> None:
    provider = ProposalFakeProvider(argument_refs=("foreign.evidence",))
    pipeline, repository = make_proposal_pipeline(provider)
    parent = fixture_parent()
    bundle = fixture_bundle()
    with pytest.raises(ProposalPipelineError, match="frozen bundle set"):
        pipeline.run(bundle, parent)
    assert "AGGRESSIVE:1" in provider.calls
    assert set(provider.calls) <= {"AGGRESSIVE:1", "CONSERVATIVE:1", "NEUTRAL:1"}
    context_id = derive_context_id(
        bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
    )
    run_id = str(derive_proposal_run_id(context_id))
    assert repository.current_stage(run_id) is ProposalStage.PLANNED


def test_viewpoint_identity_drift_is_rejected_before_persist() -> None:
    class DriftedRoundProvider(ProposalFakeProvider):
        def execute(self, request: ProposalRequest) -> ProposalOutput:
            if request.key == "CONSERVATIVE:1":
                # A contract-valid round-2 answer served to a round-1 request: the
                # pipeline, not the contract, must reject the identity drift.
                return super().execute(
                    replace(
                        request,
                        round_number=2,
                        output_id=derive_argument_id(
                            request.context_id, RiskViewpoint.CONSERVATIVE, 2
                        ),
                    )
                )
            return super().execute(request)

    provider = DriftedRoundProvider()
    pipeline, _ = make_proposal_pipeline(provider)
    with pytest.raises(ProposalPipelineError, match="failed closed"):
        pipeline.run(fixture_bundle(), fixture_parent())
    assert set(provider.calls) <= {"AGGRESSIVE:1", "NEUTRAL:1"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_weight", Decimal("0.500000")),
        ("confidence", Decimal("0.1000")),
        ("target_weight", Decimal("-0.000000")),
    ],
    ids=["over-range-weight", "low-confidence-open", "negative-zero-weight"],
)
def test_pm_request_tampering_is_rejected_before_proposal_authority(
    field: str, value: Decimal
) -> None:
    provider = TamperingProposalProvider(field, value)
    pipeline, repository = make_proposal_pipeline(provider)
    parent = fixture_parent()
    bundle = fixture_bundle()
    with pytest.raises(ProposalPipelineError, match="proposal integrity is invalid"):
        pipeline.run(bundle, parent)
    # The full debate plus exactly one Portfolio Manager call happened; nothing later.
    assert_grouped_proposal_trace(provider.calls)
    context_id = derive_context_id(
        bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None
    )
    run_id = str(derive_proposal_run_id(context_id))
    assert repository.current_stage(run_id) is ProposalStage.RISK_DEBATE
    assert repository.load(run_id, ProposalStage.PROPOSAL) is None

    # Second layer: even a value that somehow reached persistence would be refused by
    # the wire parser on reload, so it can never advance to COMPLETE.
    wire = json.loads(canonical_wire_json(fixture_p3d_proposal()))
    wire["requests"][0][field] = str(value)
    with pytest.raises(ValueError):
        PortfolioProposal.from_wire(wire)


def test_retry_flow_makes_exactly_one_extra_call_and_supersedes() -> None:
    provider = ProposalFakeProvider()
    pipeline, repository = make_proposal_pipeline(provider)
    parent = fixture_parent()
    bundle = fixture_bundle()
    first = pipeline.run(bundle, parent)

    retry_provider = ProposalFakeProvider()
    retry_pipeline, _ = make_proposal_pipeline(retry_provider, repository=repository)
    refreshed = refreshed_snapshot_at(2)
    second = retry_pipeline.retry(
        bundle, parent, refreshed, feedback_for(first, reviewed_at=timestamp(1)), first
    )
    assert retry_provider.calls == ["PORTFOLIO_MANAGER_RETRY:"]
    assert second.attempt == 2
    assert second.superseded_proposal_id == first.proposal_id
    assert second.snapshot_hash == refreshed.content_hash

    first_run = str(
        derive_proposal_run_id(
            derive_context_id(bundle.bundle_id, 1, parent.portfolio_snapshot.content_hash, None)
        )
    )
    second_run = str(
        derive_proposal_run_id(
            derive_context_id(
                bundle.bundle_id,
                2,
                refreshed.content_hash,
                first.proposal_id,
                proposal_payload_hash(first),
            )
        )
    )
    assert repository.current_stage(first_run) is ProposalStage.COMPLETE
    assert repository.current_stage(second_run) is ProposalStage.COMPLETE

    same_replay_provider = ProposalFakeProvider()
    same_replay, _ = make_proposal_pipeline(same_replay_provider, repository=repository)
    assert (
        same_replay.retry(
            bundle, parent, refreshed, feedback_for(first, reviewed_at=timestamp(1)), first
        )
        == second
    )
    assert same_replay_provider.calls == []


def test_retry_requires_completed_first_attempt() -> None:
    pipeline, _ = make_proposal_pipeline(ProposalFakeProvider())
    parent = fixture_parent()
    bundle = fixture_bundle()
    # A well-formed attempt-1 proposal on the derived context, but its proposal run
    # authority was never created: the retry must fail closed either way.
    from test_p3d_proposal_contracts import context as fixture_context

    ghost = fixture_p3d_proposal(fixture_context())
    with pytest.raises(ProposalPipelineError, match="attempt 1 proposal run is not complete"):
        pipeline.retry(bundle, parent, refreshed_snapshot_at(2), feedback_for(ghost), ghost)


def test_retry_rejects_review_predating_initial_proposal_with_zero_authority() -> None:
    provider = ProposalFakeProvider()
    pipeline, repository = make_proposal_pipeline(provider)
    parent = fixture_parent()
    bundle = fixture_bundle()
    first = pipeline.run(bundle, parent)

    refreshed = refreshed_snapshot_at(2)
    for reviewed_at in (timestamp(0), timestamp(-30)):
        retry_provider = ProposalFakeProvider()
        retry_pipeline, _ = make_proposal_pipeline(retry_provider, repository=repository)
        with pytest.raises(ProposalPipelineError, match="predates the initial proposal"):
            retry_pipeline.retry(
                bundle, parent, refreshed, feedback_for(first, reviewed_at=reviewed_at), first
            )
        assert retry_provider.calls == []
    second_run = str(
        derive_proposal_run_id(
            derive_context_id(
                bundle.bundle_id,
                2,
                refreshed.content_hash,
                first.proposal_id,
                proposal_payload_hash(first),
            )
        )
    )
    with pytest.raises(KeyError):
        repository.current_stage(second_run)


def test_retry_rejects_foreign_feedback_and_invalid_refresh_timeline() -> None:
    provider = ProposalFakeProvider()
    pipeline, _ = make_proposal_pipeline(provider)
    parent = fixture_parent()
    bundle = fixture_bundle()
    first = pipeline.run(bundle, parent)
    foreign = RiskRejectionFeedback(
        meta(), rid(99), 1, (RiskRejectionCode.CASH,), (), limits(), "c" * 64, timestamp(1)
    )
    with pytest.raises(ProposalPipelineError, match="foreign proposal"):
        pipeline.retry(bundle, parent, refreshed_snapshot_at(2), foreign, first)
    with pytest.raises(ProposalPipelineError, match="retrograde"):
        pipeline.retry(bundle, parent, refreshed_snapshot_at(-5), feedback_for(first), first)
    with pytest.raises(ProposalPipelineError, match="future"):
        pipeline.retry(bundle, parent, refreshed_snapshot_at(30), feedback_for(first), first)
    with pytest.raises(ProposalPipelineError, match="precede"):
        pipeline.retry(
            bundle,
            parent,
            refreshed_snapshot_at(2),
            feedback_for(first, reviewed_at=timestamp(3)),
            first,
        )


def test_second_attempt_two_never_becomes_authority() -> None:
    provider = ProposalFakeProvider()
    pipeline, repository = make_proposal_pipeline(provider)
    parent = fixture_parent()
    bundle = fixture_bundle()
    first = pipeline.run(bundle, parent)

    retry_one_provider = ProposalFakeProvider()
    retry_one, _ = make_proposal_pipeline(retry_one_provider, repository=repository)
    refreshed_one = refreshed_snapshot_at(2)
    retry_one.retry(
        bundle, parent, refreshed_one, feedback_for(first, reviewed_at=timestamp(1)), first
    )

    retry_two_provider = ProposalFakeProvider()
    retry_two, retry_two_repository = make_proposal_pipeline(
        retry_two_provider, repository=repository
    )
    refreshed_two = refreshed_snapshot_at(3)
    # The fast-fail gate fires before any new authority row is created, so the
    # doomed third attempt leaves zero context, run or debate rows behind.
    with pytest.raises(ProposalPipelineError, match="already has an attempt 2"):
        retry_two.retry(
            bundle,
            parent,
            refreshed_two,
            feedback_for(first, reviewed_at=timestamp(1)),
            first,
        )
    third_run = str(
        derive_proposal_run_id(
            derive_context_id(
                bundle.bundle_id,
                2,
                refreshed_two.content_hash,
                first.proposal_id,
                proposal_payload_hash(first),
            )
        )
    )
    with pytest.raises(KeyError):
        retry_two_repository.current_stage(third_run)
    assert retry_two_repository.load(third_run, ProposalStage.RISK_DEBATE) is None
    assert retry_two_repository.load(third_run, ProposalStage.PROPOSAL) is None
    assert (
        repository.current_stage(
            str(
                derive_proposal_run_id(
                    derive_context_id(
                        bundle.bundle_id,
                        2,
                        refreshed_one.content_hash,
                        first.proposal_id,
                        proposal_payload_hash(first),
                    )
                )
            )
        )
        is ProposalStage.COMPLETE
    )


def test_proposal_stage_whitelist_sinks_and_budget() -> None:
    repository, _, ctx, run_id = registered_proposal_run()
    debate_payload = canonical_wire_json(fixture_debate(ctx))
    debate_result = stored_result(run_id, ProposalStage.RISK_DEBATE, debate_payload)
    for expected, stage in (
        (ProposalStage.PLANNED, ProposalStage.PROPOSAL),
        (ProposalStage.PLANNED, ProposalStage.COMPLETE),
        (ProposalStage.RISK_DEBATE, ProposalStage.RISK_DEBATE),
        (ProposalStage.PROPOSAL, ProposalStage.RISK_DEBATE),
    ):
        with pytest.raises(ValueError, match="not legal"):
            repository.advance(StoredProposalResult(run_id, stage, "c" * 64, "{}"), expected)
    assert repository.advance(debate_result, ProposalStage.PLANNED) is True
    assert repository.advance(debate_result, ProposalStage.PLANNED) is False
    with pytest.raises(ValueError, match="hash does not match"):
        repository.advance(
            StoredProposalResult(run_id, ProposalStage.RISK_DEBATE, "d" * 64, debate_payload),
            ProposalStage.PLANNED,
        )
    with pytest.raises(ValueError, match="not legal"):
        repository.advance(
            stored_result(run_id, ProposalStage.COMPLETE, "complete"),
            ProposalStage.RISK_DEBATE,
        )
    assert (
        repository.advance(
            stored_result(run_id, ProposalStage.INVALID, "invalid"),
            ProposalStage.RISK_DEBATE,
        )
        is True
    )
    with pytest.raises(ValueError, match="not legal"):
        repository.advance(
            stored_result(run_id, ProposalStage.INVALID, "invalid"),
            ProposalStage.INVALID,
        )
    with pytest.raises(ValueError, match="not legal"):
        repository.advance(
            StoredProposalResult(run_id, ProposalStage.RISK_DEBATE, "0" * 64, "{}"),
            ProposalStage.INVALID,
        )
    budget_repository, _, budget_context, budget_run = registered_proposal_run()
    result = stored_result(
        budget_run,
        ProposalStage.RISK_DEBATE,
        canonical_wire_json(fixture_debate(budget_context)),
    )
    assert budget_repository.advance(result, ProposalStage.PLANNED) is True
    for _ in range(MAX_PROPOSAL_STAGE_ATTEMPTS - 1):
        assert budget_repository.advance(result, ProposalStage.PLANNED) is False
    with pytest.raises(ValueError, match="budget"):
        budget_repository.advance(result, ProposalStage.PLANNED)


def test_repository_rejects_unregistered_run_and_payload_hash_drift() -> None:
    repository = InMemoryProposalStateRepository(
        FixtureBundleVerifier(), now=lambda: timestamp(5).value
    )
    with pytest.raises(ValueError, match="unregistered authority"):
        repository.create_run(str(rid(90)), str(rid(91)), str(rid(92)), "a" * 64)

    repository, _, ctx, run_id = registered_proposal_run(repository)
    payload = canonical_wire_json(fixture_debate(ctx))
    with pytest.raises(ValueError, match="hash does not match"):
        repository.advance(
            StoredProposalResult(run_id, ProposalStage.RISK_DEBATE, "a" * 64, payload),
            ProposalStage.PLANNED,
        )
    assert repository.current_stage(run_id) is ProposalStage.PLANNED


def test_malformed_proposal_payload_is_rejected_without_authority() -> None:
    repository, _, ctx, run_id = registered_proposal_run()
    debate_payload = canonical_wire_json(fixture_debate(ctx))
    assert (
        repository.advance(
            StoredProposalResult(
                run_id,
                ProposalStage.RISK_DEBATE,
                hashlib.sha256(debate_payload.encode()).hexdigest(),
                debate_payload,
            ),
            ProposalStage.PLANNED,
        )
        is True
    )
    with pytest.raises(ValueError, match="malformed"):
        repository.advance(
            stored_result(run_id, ProposalStage.PROPOSAL, "not-json"),
            ProposalStage.RISK_DEBATE,
        )


def test_duplicate_json_object_keys_are_rejected_at_both_persist_layers() -> None:
    repository, built, ctx, run_id = registered_proposal_run()
    valid_debate = canonical_wire_json(fixture_debate(ctx))
    debate_id = str(fixture_debate(ctx).debate_id)
    dup_debate = valid_debate.replace(
        f'"debate_id":"{debate_id}"',
        f'"debate_id":"{debate_id}","debate_id":"{debate_id}"',
        1,
    )
    valid_proposal = canonical_wire_json(fixture_p3d_proposal(ctx))
    proposal_id = str(fixture_p3d_proposal(ctx).proposal_id)
    dup_proposal = valid_proposal.replace(
        f'"proposal_id":"{proposal_id}"',
        f'"proposal_id":"{proposal_id}","proposal_id":"{proposal_id}"',
        1,
    )

    # Repository registration layer: a duplicate-key payload never registers.
    with pytest.raises(ValueError, match="malformed"):
        repository.advance(
            stored_result(run_id, ProposalStage.RISK_DEBATE, dup_debate),
            ProposalStage.PLANNED,
        )
    assert (
        repository.advance(
            stored_result(run_id, ProposalStage.RISK_DEBATE, valid_debate),
            ProposalStage.PLANNED,
        )
        is True
    )
    with pytest.raises(ValueError, match="malformed"):
        repository.advance(
            stored_result(run_id, ProposalStage.PROPOSAL, dup_proposal),
            ProposalStage.RISK_DEBATE,
        )

    # Pipeline loader layer: even a stored duplicate-key payload whose digest
    # matches is refused by every reload path.
    seeded = InMemoryProposalStateRepository(
        FixtureBundleVerifier(), now=lambda: timestamp(5).value
    )
    seeded._results[(run_id, ProposalStage.RISK_DEBATE)] = StoredProposalResult(
        run_id,
        ProposalStage.RISK_DEBATE,
        hashlib.sha256(dup_debate.encode()).hexdigest(),
        dup_debate,
    )
    seeded._results[(run_id, ProposalStage.PROPOSAL)] = StoredProposalResult(
        run_id,
        ProposalStage.PROPOSAL,
        hashlib.sha256(dup_proposal.encode()).hexdigest(),
        dup_proposal,
    )
    pipeline, _ = make_proposal_pipeline(ProposalFakeProvider(), repository=seeded)
    with pytest.raises(ProposalPipelineError, match="malformed"):
        pipeline._load_debate(run_id, built, expected_context_id=ctx.context_id)
    with pytest.raises(ProposalPipelineError, match="malformed"):
        pipeline._inherited_debate(built, ctx, run_id)
    with pytest.raises(ProposalPipelineError, match="malformed"):
        pipeline._load_proposal(run_id, ctx)


def test_excessively_nested_json_is_rejected_without_recursion_escape() -> None:
    repository, built, ctx, run_id = registered_proposal_run()
    nested_payload = "[" * 10_000 + "]" * 10_000
    nested_result = StoredProposalResult(
        run_id,
        ProposalStage.RISK_DEBATE,
        hashlib.sha256(nested_payload.encode()).hexdigest(),
        nested_payload,
    )
    repository._results[(run_id, ProposalStage.RISK_DEBATE)] = nested_result
    pipeline, _ = make_proposal_pipeline(ProposalFakeProvider(), repository=repository)

    with pytest.raises(ProposalPipelineError, match="malformed"):
        pipeline._load_debate(run_id, built, expected_context_id=ctx.context_id)


def test_complete_replay_revalidates_terminal_and_canonical_stage_bytes() -> None:
    built = fixture_bundle()
    parent = fixture_parent()
    context_one = fixture_context()
    run_id = str(derive_proposal_run_id(context_one.context_id))

    first_pipeline, first_repository = make_proposal_pipeline(ProposalFakeProvider())
    first_pipeline.run(built, parent)
    terminal = first_repository._results.pop((run_id, ProposalStage.COMPLETE))
    with pytest.raises(ProposalPipelineError, match="stage identity"):
        first_pipeline.run(built, parent)
    first_repository._results[(run_id, ProposalStage.COMPLETE)] = terminal

    debate = first_repository._results[(run_id, ProposalStage.RISK_DEBATE)]
    noncanonical = json.dumps(json.loads(debate.payload), indent=2)
    first_repository._results[(run_id, ProposalStage.RISK_DEBATE)] = StoredProposalResult(
        run_id,
        ProposalStage.RISK_DEBATE,
        hashlib.sha256(noncanonical.encode()).hexdigest(),
        noncanonical,
    )
    with pytest.raises(ProposalPipelineError, match="not canonical"):
        first_pipeline.run(built, parent)


def test_complete_replay_contains_deep_proposal_json_and_feedback_requires_stage_bytes() -> None:
    built = fixture_bundle()
    parent = fixture_parent()
    context_one = fixture_context()
    run_id = str(derive_proposal_run_id(context_one.context_id))
    pipeline, repository = make_proposal_pipeline(ProposalFakeProvider())
    proposal = pipeline.run(built, parent)
    proposal_result = repository._results[(run_id, ProposalStage.PROPOSAL)]

    nested_payload = "[" * 10_000 + "]" * 10_000
    repository._results[(run_id, ProposalStage.PROPOSAL)] = StoredProposalResult(
        run_id,
        ProposalStage.PROPOSAL,
        hashlib.sha256(nested_payload.encode()).hexdigest(),
        nested_payload,
    )
    with pytest.raises(ProposalPipelineError, match="malformed"):
        pipeline.run(built, parent)

    repository._results.pop((run_id, ProposalStage.PROPOSAL))
    with pytest.raises(ValueError, match="completed proposal authority"):
        repository.register_feedback(feedback_for(proposal))
    repository._results[(run_id, ProposalStage.PROPOSAL)] = proposal_result


def test_feedback_registration_is_idempotent_and_collision_free() -> None:
    provider = ProposalFakeProvider()
    pipeline, repository = make_proposal_pipeline(provider)
    first = pipeline.run(fixture_bundle(), fixture_parent())
    feedback = feedback_for(first)
    repository.register_feedback(feedback)
    repository.register_feedback(feedback)
    with pytest.raises(ValueError, match="identity collision"):
        repository.register_feedback(
            RiskRejectionFeedback(
                feedback.meta,
                feedback.rejected_proposal_id,
                1,
                (RiskRejectionCode.CASH,),
                (),
                limits(),
                "c" * 64,
                timestamp(1),
            )
        )


@pytest.mark.parametrize("reviewed_at", [timestamp(0), timestamp(2)], ids=["retrograde", "future"])
def test_feedback_registration_rejects_impossible_timeline_without_authority(
    reviewed_at: UtcTimestamp,
) -> None:
    repository = InMemoryProposalStateRepository(
        FixtureBundleVerifier(),
        now=lambda: timestamp(1).value,
    )
    pipeline, repository = make_proposal_pipeline(ProposalFakeProvider(), repository=repository)
    proposal = pipeline.run(fixture_bundle(), fixture_parent())
    feedback = feedback_for(proposal, reviewed_at=reviewed_at)

    with pytest.raises(ValueError, match="timeline"):
        repository.register_feedback(feedback)
    assert repository._feedback == {}


def test_scripted_provider_fails_closed_on_missing_and_reused_outputs() -> None:
    from seven_lens.analysis.proposal_ports import ScriptedProposalProvider

    parent = fixture_parent()
    bundle = fixture_bundle()
    context = fixture_context()
    context_id = context.context_id
    run_id = derive_proposal_run_id(context_id)
    aggressive_argument = RiskArgument(
        meta=ContractMeta(SCHEMA_VERSION, run_id, timestamp(), PRODUCER),
        argument_id=derive_argument_id(context_id, RiskViewpoint.AGGRESSIVE, 1),
        context_id=context_id,
        bundle_id=bundle.bundle_id,
        bundle_hash=bundle.bundle_hash,
        viewpoint=RiskViewpoint.AGGRESSIVE,
        round_number=1,
        argument="aggressive",
        evidence_refs=("evidence.1",),
        producer_version=PRODUCER,
    )
    provider = ScriptedProposalProvider({"AGGRESSIVE:1": aggressive_argument})

    def request(round_number: int, viewpoint: RiskViewpoint) -> ProposalRequest:
        stage = {
            RiskViewpoint.AGGRESSIVE: ProposalProviderStage.AGGRESSIVE,
            RiskViewpoint.CONSERVATIVE: ProposalProviderStage.CONSERVATIVE,
            RiskViewpoint.NEUTRAL: ProposalProviderStage.NEUTRAL,
        }[viewpoint]
        output_id = derive_argument_id(context_id, viewpoint, round_number)
        pipeline, _ = make_proposal_pipeline(provider)
        envelope = pipeline._model_envelope(
            bundle=bundle,
            context=context,
            run_id=run_id,
            output_id=output_id,
            stage=stage,
            round_number=round_number,
            prior_outputs=(),
        )
        return ProposalRequest(
            stage=stage,
            run_id=run_id,
            input_id=bundle.parent_input_id,
            output_id=output_id,
            context_id=context_id,
            bundle_id=bundle.bundle_id,
            bundle_hash=bundle.bundle_hash,
            context_hash=context.context_hash,
            snapshot_hash=parent.portfolio_snapshot.content_hash,
            universe_hash=parent.universe_hash,
            window=parent.window,
            deadline=parent.deadline,
            created_at=parent.meta.created_at,
            attempt=1,
            superseded_proposal_id=None,
            superseded_proposal_hash=None,
            round_number=round_number,
            allowed_symbols=parent.holding_symbols + parent.candidate_symbols,
            citation_ids=bundle.citation_ids,
            envelope=envelope,
        )

    aggressive = request(1, RiskViewpoint.AGGRESSIVE)
    assert provider.execute(aggressive) is aggressive_argument
    assert provider.calls == ["AGGRESSIVE:1"]
    with pytest.raises(RuntimeError, match="already consumed"):
        provider.execute(aggressive)
    with pytest.raises(RuntimeError, match="missing"):
        provider.execute(request(1, RiskViewpoint.CONSERVATIVE))


def test_inmemory_bundle_and_context_registration_lineage() -> None:
    from test_p3d_proposal_contracts import rejection

    repository = InMemoryProposalStateRepository(
        FixtureBundleVerifier(), now=lambda: timestamp(5).value
    )
    parent = fixture_parent()
    built = fixture_bundle()
    first_ctx = fixture_context()
    with pytest.raises(ValueError, match="unknown research bundle"):
        repository.register_context(first_ctx)

    repository.register_bundle(built)
    repository.register_bundle(built)
    forged = object.__new__(ResearchBundle)
    for name in ResearchBundle.__slots__:
        object.__setattr__(forged, name, getattr(built, name))
    object.__setattr__(forged, "bundle_hash", "0" * 64)
    with pytest.raises(ValueError, match="integrity is invalid"):
        repository.register_bundle(forged)

    repository.register_context(first_ctx)
    repository.register_context(first_ctx)
    foreign_allowed = replace(first_ctx)
    object.__setattr__(foreign_allowed, "allowed_symbols", (*first_ctx.allowed_symbols, "XOM"))
    object.__setattr__(foreign_allowed, "context_hash", foreign_allowed.compute_hash())
    foreign_allowed.validate_integrity()
    with pytest.raises(ValueError, match="frozen research bundle"):
        repository.register_context(foreign_allowed)
    with pytest.raises(ValueError, match="attempt 1 context"):
        repository.register_context(fixture_context(snapshot=refreshed_snapshot_at(2)))
    with pytest.raises(ValueError, match="attempt 2 context"):
        repository.register_context(
            fixture_context(
                attempt=2,
                superseded_proposal_id=rid(11),
                previous_context_id=first_ctx.context_id,
                feedback=rejection(rid(11)),
            )
        )

    proposal = fixture_p3d_proposal(first_ctx)
    run_one = str(derive_proposal_run_id(first_ctx.context_id))
    repository.create_run(
        run_one, str(first_ctx.context_id), str(built.bundle_id), built.bundle_hash
    )
    debate_payload = canonical_wire_json(fixture_debate(first_ctx))
    repository.advance(
        StoredProposalResult(
            run_one,
            ProposalStage.RISK_DEBATE,
            hashlib.sha256(debate_payload.encode()).hexdigest(),
            debate_payload,
        ),
        ProposalStage.PLANNED,
    )
    proposal_payload = canonical_wire_json(proposal)
    repository.advance(
        StoredProposalResult(
            run_one,
            ProposalStage.PROPOSAL,
            hashlib.sha256(proposal_payload.encode()).hexdigest(),
            proposal_payload,
        ),
        ProposalStage.RISK_DEBATE,
    )
    feedback = rejection(proposal.proposal_id)
    with pytest.raises(ValueError, match="completed proposal authority"):
        repository.register_feedback(feedback)
    repository._feedback[str(feedback.meta.run_id)] = (
        feedback,
        canonical_wire_json(feedback),
    )
    premature_context = fixture_context(
        attempt=2,
        superseded_proposal_id=proposal.proposal_id,
        superseded_proposal_hash=proposal_payload_hash(proposal),
        previous_context_id=first_ctx.context_id,
        feedback=feedback,
    )
    with pytest.raises(ValueError, match="completed attempt 1"):
        repository.register_context(premature_context)
    repository._feedback.clear()
    repository.advance(
        StoredProposalResult(
            run_one,
            ProposalStage.COMPLETE,
            hashlib.sha256(b"complete").hexdigest(),
            "complete",
        ),
        ProposalStage.PROPOSAL,
    )
    repository.register_feedback(feedback)
    drifted_feedback = replace(feedback, rejection_codes=(RiskRejectionCode.CASH,))
    drifted_context = fixture_context(
        attempt=2,
        superseded_proposal_id=proposal.proposal_id,
        superseded_proposal_hash=proposal_payload_hash(proposal),
        previous_context_id=first_ctx.context_id,
        feedback=drifted_feedback,
    )
    with pytest.raises(ValueError, match="registered authority"):
        repository.register_context(drifted_context)
    second_ctx = fixture_context(
        attempt=2,
        superseded_proposal_id=proposal.proposal_id,
        superseded_proposal_hash=proposal_payload_hash(proposal),
        previous_context_id=first_ctx.context_id,
        feedback=feedback,
    )
    repository.register_context(second_ctx)
    with pytest.raises(ValueError, match="frozen bundle universe"):
        build_proposal_context(
            meta=second_ctx.meta,
            attempt=2,
            bundle=built,
            snapshot=second_ctx.snapshot,
            allowed_symbols=(*parent.holding_symbols, *parent.candidate_symbols, "SPY"),
            graph_version="graph.1",
            prompt_version="prompt.1",
            model_version="model.1",
            provider_version="provider.1",
            data_version="data.1",
            memory_version="memory.1",
            previous_context_id=first_ctx.context_id,
            superseded_proposal_id=proposal.proposal_id,
            superseded_proposal_hash=proposal_payload_hash(proposal),
            feedback=feedback,
        )


def test_p3d_modules_have_no_network_broker_or_execution_capability() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).parents[1]
    modules = (
        root / "src/seven_lens/analysis/proposal_contracts.py",
        root / "src/seven_lens/analysis/proposal_pipeline.py",
        root / "src/seven_lens/analysis/proposal_ports.py",
        root / "src/seven_lens/application/ports/proposals.py",
    )
    forbidden_roots = {
        "alpaca",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "psycopg",
        "pydantic",
        "langgraph",
        "openai",
        "tavily",
    }
    forbidden_targets = {"seven_lens.execution", "seven_lens.infrastructure"}
    forbidden_markers = ("OrderIntent", "TargetPortfolio")
    for path in modules:
        source = path.read_text(encoding="utf-8")
        targets: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    targets.update((alias.name, alias.name.split(".")[0]))
            elif isinstance(node, ast.ImportFrom):
                prefix = "seven_lens." if node.level > 0 else ""
                if node.module:
                    root_name = f"{prefix}{node.module}"
                    targets.update((root_name, root_name.split(".")[0]))
        assert not targets & forbidden_roots, path
        assert not targets & forbidden_targets, path
        for marker in forbidden_markers:
            assert marker.lower() not in source.lower(), (path, marker)
