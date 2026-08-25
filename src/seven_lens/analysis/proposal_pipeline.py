"""Deterministic P3-D multi-symbol research aggregation and portfolio-proposal flow.

The :class:`ResearchBatchCoordinator` expands one parent :class:`AnalysisInput` into
per-symbol child inputs, reuses the accepted P3-C :class:`AnalysisPipeline` unchanged for
every child, and joins completed children into one immutable :class:`ResearchBundle` in
parent focus order.  The :class:`ProposalPipeline` runs the fixed two-round three-viewpoint
risk debate and at most one Portfolio Manager call per attempt; attempt 2 only ever replays
the persisted attempt-1 debate plus one typed-feedback retry.  No type here has approval,
quantity, target portfolio, order intent, broker-side write, network, credential or
ledger-write capability.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

from seven_lens.analysis.concurrency import run_bounded_group
from seven_lens.analysis.contracts import (
    SCHEMA_VERSION,
    AnalysisInput,
    AnalysisStatus,
    ContractMeta,
    PortfolioSnapshot,
    RiskRejectionFeedback,
    _version,
    canonical_wire_json,
)
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    EnvelopeVersions,
    SanitizedProviderEnvelope,
)
from seven_lens.analysis.model_material import research_bundle_model_material
from seven_lens.analysis.pipeline import AnalysisPipeline
from seven_lens.analysis.prompt_builder import (
    APPROVED_PROMPT_TEMPLATE_HASH,
    APPROVED_PROMPT_TEMPLATE_ID,
)
from seven_lens.analysis.proposal_contracts import (
    DEBATE_ORDER,
    PortfolioProposal,
    ProposalContext,
    ResearchBundle,
    ResearchBundleItem,
    RiskArgument,
    RiskDebateState,
    RiskViewpoint,
    build_proposal_context,
    build_research_bundle,
    build_risk_debate,
    derive_argument_id,
    derive_bundle_id,
    derive_child_input_id,
    derive_child_run_id,
    derive_context_id,
    derive_proposal_id,
    derive_proposal_run_id,
)
from seven_lens.analysis.proposal_ports import (
    ProposalProvider,
    ProposalProviderStage,
    ProposalRequest,
)
from seven_lens.application.ports.proposals import (
    LEGAL_PROPOSAL_TRANSITIONS,
    MAX_PROPOSAL_STAGE_PAYLOAD_BYTES,
    ProposalStage,
    ProposalStateRepository,
    StoredProposalResult,
    proposal_stage_order_at_least,
)
from seven_lens.domain.value_objects import RunId, UtcTimestamp
from seven_lens.sources.contracts import EvidencePacket, EvidenceStatus, FreshnessStatus

__all__ = [
    "ProposalPipeline",
    "ProposalPipelineError",
    "ProposalProducerVersions",
    "ResearchBatchCoordinator",
]

_VIEWPOINT_STAGE: dict[RiskViewpoint, ProposalProviderStage] = {
    RiskViewpoint.AGGRESSIVE: ProposalProviderStage.AGGRESSIVE,
    RiskViewpoint.CONSERVATIVE: ProposalProviderStage.CONSERVATIVE,
    RiskViewpoint.NEUTRAL: ProposalProviderStage.NEUTRAL,
}


def _strict_json_loads(payload: str) -> object:
    """Parse persisted payload text while rejecting duplicate JSON object keys."""

    if type(payload) is not str or not (
        1 <= len(payload.encode("utf-8")) <= MAX_PROPOSAL_STAGE_PAYLOAD_BYTES
    ):
        raise ValueError("persisted proposal payload is outside its byte bound")

    def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key is rejected")
            result[key] = value
        return result

    def _reject_constant(_: str) -> object:
        raise ValueError("non-finite JSON numbers are rejected")

    return json.loads(
        payload,
        object_pairs_hook=_reject_duplicates,
        parse_constant=_reject_constant,
    )


class ProposalPipelineError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProposalProducerVersions:
    """The frozen producer identity every context, debate and proposal must carry."""

    graph_version: str
    prompt_version: str
    model_version: str
    provider_version: str
    data_version: str
    memory_version: str

    def __post_init__(self) -> None:
        for name in (
            "graph_version",
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
        ):
            object.__setattr__(self, name, _version(getattr(self, name), name))


class ResearchBatchCoordinator:
    """Serial deterministic fan-out of one parent input to per-symbol P3-C pipelines."""

    def __init__(
        self,
        pipeline: AnalysisPipeline,
        versions: ProposalProducerVersions,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._versions = versions
        self._now = now or (lambda: datetime.now(UTC))

    def run(self, parent_input: AnalysisInput, packet: EvidencePacket) -> ResearchBundle:
        self._validate_parent(parent_input, packet)
        self._check_deadline(parent_input.deadline)
        items: list[ResearchBundleItem] = []
        for symbol in parent_input.focus_symbols:
            self._check_deadline(parent_input.deadline)
            child_input = self._child_input(parent_input, symbol)
            result = self._pipeline.run(child_input, packet, symbol)
            plan = result.trader_plan
            items.append(
                ResearchBundleItem(
                    symbol=symbol,
                    analysis_run_id=child_input.meta.run_id,
                    analysis_input_id=child_input.input_id,
                    packet_hash=packet.packet_hash,
                    snapshot_hash=parent_input.portfolio_snapshot.content_hash,
                    trader_plan_id=plan.plan_id,
                    trader_plan_hash=hashlib.sha256(canonical_wire_json(plan).encode()).hexdigest(),
                    trader_plan=plan,
                    evidence_refs=plan.evidence_refs,
                    producer_version=parent_input.meta.producer_version,
                    graph_version=self._versions.graph_version,
                    prompt_version=self._versions.prompt_version,
                    data_version=self._versions.data_version,
                    status=AnalysisStatus.VALID,
                )
            )
        return build_research_bundle(
            meta=ContractMeta(
                SCHEMA_VERSION,
                derive_bundle_id(parent_input.input_id),
                parent_input.meta.created_at,
                parent_input.meta.producer_version,
            ),
            parent_input_id=parent_input.input_id,
            as_of=parent_input.as_of,
            window=parent_input.window,
            deadline=parent_input.deadline,
            universe_hash=parent_input.universe_hash,
            portfolio_snapshot_hash=parent_input.portfolio_snapshot.content_hash,
            data_snapshot_refs=parent_input.data_snapshot_refs,
            holding_symbols=parent_input.holding_symbols,
            candidate_symbols=parent_input.candidate_symbols,
            items=items,
        )

    @staticmethod
    def _child_input(parent_input: AnalysisInput, symbol: str) -> AnalysisInput:
        child_meta = ContractMeta(
            SCHEMA_VERSION,
            derive_child_run_id(parent_input.input_id, symbol),
            parent_input.meta.created_at,
            parent_input.meta.producer_version,
        )
        return AnalysisInput(
            child_meta,
            derive_child_input_id(parent_input.input_id, symbol),
            parent_input.as_of,
            parent_input.window,
            parent_input.deadline,
            parent_input.portfolio_snapshot,
            parent_input.holding_symbols,
            parent_input.candidate_symbols,
            (symbol,),
            parent_input.evidence_refs,
            parent_input.data_snapshot_refs,
            parent_input.universe_hash,
        )

    @staticmethod
    def _validate_parent(parent_input: AnalysisInput, packet: EvidencePacket) -> None:
        if type(parent_input) is not AnalysisInput:
            raise ProposalPipelineError("analysis input integrity is invalid")
        try:
            parent_input.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("analysis input integrity is invalid") from error
        if type(packet) is not EvidencePacket:
            raise ProposalPipelineError("evidence packet integrity is invalid")
        try:
            packet.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("evidence packet integrity is invalid") from error
        if not parent_input.focus_symbols:
            raise ProposalPipelineError("batch research requires at least one focus symbol")
        if (
            packet.status is not EvidenceStatus.VERIFIED
            or packet.freshness_status is not FreshnessStatus.FRESH
            or packet.contradiction_claim_ids
            or packet.missing_evidence
            or packet.as_of != parent_input.as_of
        ):
            raise ProposalPipelineError("evidence packet is not verified for the batch time")
        if (
            packet.universe_hash != parent_input.universe_hash
            or packet.portfolio_snapshot_hash != parent_input.portfolio_snapshot.content_hash
            or packet.data_snapshot_refs != parent_input.data_snapshot_refs
            or set(parent_input.evidence_refs) != packet.citation_ids
        ):
            raise ProposalPipelineError("frozen batch input identity mismatch")

    def _check_deadline(self, deadline: UtcTimestamp) -> None:
        if self._now() > deadline.value:
            raise ProposalPipelineError("research batch deadline expired")


class ProposalPipeline:
    def __init__(
        self,
        provider: ProposalProvider,
        repository: ProposalStateRepository,
        versions: ProposalProducerVersions,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._versions = versions
        self._now = now or (lambda: datetime.now(UTC))

    def run(self, bundle: ResearchBundle, parent_input: AnalysisInput) -> PortfolioProposal:
        context = self._initial_context(bundle, parent_input)
        run_id = derive_proposal_run_id(context.context_id)
        self._check_deadline(bundle.deadline)
        self._repository.register_bundle(bundle)
        self._check_deadline(bundle.deadline)
        self._repository.register_context(context)
        self._check_deadline(bundle.deadline)
        self._repository.create_run(
            str(run_id), str(context.context_id), str(bundle.bundle_id), bundle.bundle_hash
        )
        return self._execute(bundle, context, run_id, inherited_debate=None)

    def retry(
        self,
        bundle: ResearchBundle,
        parent_input: AnalysisInput,
        refreshed_snapshot: PortfolioSnapshot,
        feedback: RiskRejectionFeedback,
        first_proposal: PortfolioProposal,
    ) -> PortfolioProposal:
        context_one = self._initial_context(bundle, parent_input)
        run_one = str(derive_proposal_run_id(context_one.context_id))
        persisted_first, superseded_hash = self._require_completed_first_attempt(
            context_one, feedback, first_proposal, run_one
        )
        refreshed = self._validated_refreshed_snapshot(
            bundle, parent_input, feedback, refreshed_snapshot
        )
        context_two = build_proposal_context(
            meta=self._meta(
                derive_context_id(
                    bundle.bundle_id,
                    2,
                    refreshed.content_hash,
                    persisted_first.proposal_id,
                    superseded_hash,
                ),
                parent_input,
            ),
            attempt=2,
            bundle=bundle,
            snapshot=refreshed,
            allowed_symbols=self._allowed_symbols(parent_input),
            graph_version=self._versions.graph_version,
            prompt_version=self._versions.prompt_version,
            model_version=self._versions.model_version,
            provider_version=self._versions.provider_version,
            data_version=self._versions.data_version,
            memory_version=self._versions.memory_version,
            previous_context_id=context_one.context_id,
            superseded_proposal_id=persisted_first.proposal_id,
            superseded_proposal_hash=superseded_hash,
            feedback=feedback,
        )
        # A second attempt-2 under a different refreshed snapshot is rejected
        # before any new authority row is written; the same-context replay stays
        # on its bounded idempotent path below.
        if self._repository.attempt_two_exists(str(bundle.bundle_id), str(context_two.context_id)):
            raise ProposalPipelineError("bundle already has an attempt 2 proposal")
        self._check_deadline(bundle.deadline)
        self._repository.register_feedback(feedback)
        self._check_deadline(bundle.deadline)
        self._repository.register_bundle(bundle)
        self._check_deadline(bundle.deadline)
        self._repository.register_context(context_two)
        self._check_deadline(bundle.deadline)
        run_two = derive_proposal_run_id(context_two.context_id)
        self._repository.create_run(
            str(run_two), str(context_two.context_id), str(bundle.bundle_id), bundle.bundle_hash
        )
        inherited = self._inherited_debate(bundle, context_one, run_one)
        return self._execute(
            bundle,
            context_two,
            run_two,
            inherited_debate=inherited,
            provider_stage=ProposalProviderStage.PORTFOLIO_MANAGER_RETRY,
        )

    # ------------------------------------------------------------------ internals

    def _execute(
        self,
        bundle: ResearchBundle,
        context: ProposalContext,
        run_id: RunId,
        *,
        inherited_debate: tuple[str, str] | None,
        provider_stage: ProposalProviderStage = ProposalProviderStage.PORTFOLIO_MANAGER,
    ) -> PortfolioProposal:
        current = self._repository.current_stage(str(run_id))
        if inherited_debate is None:
            if not self._at_least(current, ProposalStage.RISK_DEBATE):
                debate = self._debate_from_provider(bundle, context, run_id)
                self._persist(
                    str(run_id),
                    context,
                    ProposalStage.PLANNED,
                    ProposalStage.RISK_DEBATE,
                    canonical_wire_json(debate),
                )
        else:
            if not self._at_least(current, ProposalStage.RISK_DEBATE):
                payload, digest = inherited_debate
                if self._repository.load(str(run_id), ProposalStage.RISK_DEBATE) is not None:
                    raise ProposalPipelineError("inherited risk debate identity mismatch")
                self._persist_payload(
                    str(run_id),
                    context,
                    ProposalStage.PLANNED,
                    ProposalStage.RISK_DEBATE,
                    payload,
                    digest,
                )
        # Fresh and resumed runs share one validator: the persisted debate is fully
        # re-verified against the bundle and its owning context before any later stage.
        debate = self._load_debate(
            str(run_id),
            bundle,
            expected_context_id=context.previous_context_id or context.context_id,
        )
        current = self._repository.current_stage(str(run_id))
        if not self._at_least(current, ProposalStage.PROPOSAL):
            proposal = self._proposal_from_provider(bundle, context, debate, run_id, provider_stage)
            self._persist(
                str(run_id),
                context,
                ProposalStage.RISK_DEBATE,
                ProposalStage.PROPOSAL,
                canonical_wire_json(proposal),
            )
        # Fresh and resumed runs are symmetric with the debate path above: the
        # persisted proposal is always reloaded through the wire parser before the
        # run may advance to COMPLETE, so any value from_wire refuses never
        # becomes authority even on the fresh path.
        proposal = self._load_proposal(str(run_id), context)
        if self._repository.current_stage(str(run_id)) is ProposalStage.PROPOSAL:
            self._persist(
                str(run_id),
                context,
                ProposalStage.PROPOSAL,
                ProposalStage.COMPLETE,
                "complete",
            )
        self._load_terminal(str(run_id))
        return proposal

    def _initial_context(
        self, bundle: ResearchBundle, parent_input: AnalysisInput
    ) -> ProposalContext:
        self._validate_bundle_and_parent(bundle, parent_input)
        snapshot = parent_input.portfolio_snapshot
        context_id = derive_context_id(bundle.bundle_id, 1, snapshot.content_hash, None)
        return build_proposal_context(
            meta=self._meta(context_id, parent_input),
            attempt=1,
            bundle=bundle,
            snapshot=snapshot,
            allowed_symbols=self._allowed_symbols(parent_input),
            graph_version=self._versions.graph_version,
            prompt_version=self._versions.prompt_version,
            model_version=self._versions.model_version,
            provider_version=self._versions.provider_version,
            data_version=self._versions.data_version,
            memory_version=self._versions.memory_version,
        )

    def _meta(self, identity: RunId, parent_input: AnalysisInput) -> ContractMeta:
        return ContractMeta(
            SCHEMA_VERSION,
            identity,
            parent_input.meta.created_at,
            parent_input.meta.producer_version,
        )

    def _run_meta(self, run_id: RunId, context: ProposalContext) -> ContractMeta:
        return ContractMeta(
            SCHEMA_VERSION, run_id, context.meta.created_at, context.meta.producer_version
        )

    @staticmethod
    def _allowed_symbols(parent_input: AnalysisInput) -> tuple[str, ...]:
        return (*parent_input.holding_symbols, *parent_input.candidate_symbols)

    def _validate_bundle_and_parent(
        self, bundle: ResearchBundle, parent_input: AnalysisInput
    ) -> None:
        if type(bundle) is not ResearchBundle or type(parent_input) is not AnalysisInput:
            raise ProposalPipelineError("bundle or parent input integrity is invalid")
        try:
            bundle.validate_integrity()
            parent_input.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("bundle or parent input integrity is invalid") from error
        if (
            bundle.parent_input_id != parent_input.input_id
            or bundle.as_of != parent_input.as_of
            or bundle.window is not parent_input.window
            or bundle.deadline != parent_input.deadline
            or bundle.universe_hash != parent_input.universe_hash
            or bundle.portfolio_snapshot_hash != parent_input.portfolio_snapshot.content_hash
            or bundle.data_snapshot_refs != parent_input.data_snapshot_refs
            or bundle.holding_symbols != parent_input.holding_symbols
            or bundle.candidate_symbols != parent_input.candidate_symbols
            or bundle.meta.created_at != parent_input.meta.created_at
            or bundle.meta.schema_version != parent_input.meta.schema_version
        ):
            raise ProposalPipelineError("bundle does not bind the exact parent input")
        if tuple(bundle.focus_symbols) != tuple(parent_input.focus_symbols):
            raise ProposalPipelineError("bundle focus symbols must equal the parent focus order")
        if bundle.meta.producer_version != parent_input.meta.producer_version:
            raise ProposalPipelineError("bundle producer version does not match the parent input")
        if (
            bundle.first_item.graph_version != self._versions.graph_version
            or bundle.first_item.prompt_version != self._versions.prompt_version
            or bundle.first_item.data_version != self._versions.data_version
        ):
            raise ProposalPipelineError(
                "bundle producer versions do not match the proposal pipeline"
            )
        self._check_deadline(bundle.deadline)

    def _require_completed_first_attempt(
        self,
        context_one: ProposalContext,
        feedback: RiskRejectionFeedback,
        first_proposal: PortfolioProposal,
        run_one: str,
    ) -> tuple[PortfolioProposal, str]:
        if type(first_proposal) is not PortfolioProposal:
            raise ProposalPipelineError("first proposal integrity is invalid")
        try:
            first_proposal.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("first proposal integrity is invalid") from error
        if first_proposal.status is not AnalysisStatus.VALID:
            raise ProposalPipelineError("first proposal status cannot be retried")
        try:
            first_proposal.validate_against(context_one)
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError(
                "first proposal does not match the attempt 1 context"
            ) from error
        if type(feedback) is not RiskRejectionFeedback:
            raise ProposalPipelineError("risk rejection feedback integrity is invalid")
        try:
            feedback.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("risk rejection feedback integrity is invalid") from error
        if feedback.rejected_proposal_id != first_proposal.proposal_id:
            raise ProposalPipelineError("risk rejection feedback targets a foreign proposal")
        if feedback.reviewed_at.value <= first_proposal.meta.created_at.value:
            raise ProposalPipelineError("risk review predates the initial proposal")
        proposal_symbols = {request.symbol for request in first_proposal.requests}
        if not set(feedback.rejected_symbols) <= proposal_symbols:
            raise ProposalPipelineError("risk rejection feedback targets a foreign symbol")
        try:
            stage = self._repository.current_stage(run_one)
        except (KeyError, RuntimeError) as error:
            raise ProposalPipelineError("attempt 1 proposal run is not complete") from error
        if stage is not ProposalStage.COMPLETE:
            raise ProposalPipelineError("attempt 1 proposal run is not complete")
        self._load_terminal(run_one)
        persisted = self._load_proposal(run_one, context_one)
        stored = self._load_stored(run_one, ProposalStage.PROPOSAL)
        if persisted != first_proposal:
            raise ProposalPipelineError("first proposal does not match persisted authority")
        return persisted, stored.result_hash

    def _validated_refreshed_snapshot(
        self,
        bundle: ResearchBundle,
        parent_input: AnalysisInput,
        feedback: RiskRejectionFeedback,
        refreshed_snapshot: PortfolioSnapshot,
    ) -> PortfolioSnapshot:
        if type(refreshed_snapshot) is not PortfolioSnapshot:
            raise ProposalPipelineError("refreshed snapshot integrity is invalid")
        try:
            refreshed_snapshot.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("refreshed snapshot integrity is invalid") from error
        if refreshed_snapshot.as_of.value > self._now():
            raise ProposalPipelineError("refreshed snapshot is in the future")
        if refreshed_snapshot.as_of.value < parent_input.portfolio_snapshot.as_of.value:
            raise ProposalPipelineError("refreshed snapshot is retrograde")
        if refreshed_snapshot.as_of.value < feedback.reviewed_at.value:
            raise ProposalPipelineError("risk review must precede the refreshed snapshot")
        if refreshed_snapshot.as_of.value >= bundle.deadline.value:
            raise ProposalPipelineError("refreshed snapshot crosses the proposal deadline")
        if refreshed_snapshot.remaining_limits != feedback.remaining_limits:
            raise ProposalPipelineError("risk feedback remaining limits do not match the refresh")
        return refreshed_snapshot

    def _inherited_debate(
        self, bundle: ResearchBundle, context_one: ProposalContext, run_one: str
    ) -> tuple[str, str]:
        self._load_debate(run_one, bundle, expected_context_id=context_one.context_id)
        stored = self._load_stored(run_one, ProposalStage.RISK_DEBATE)
        return stored.payload, stored.result_hash

    def _debate_from_provider(
        self, bundle: ResearchBundle, context: ProposalContext, run_id: RunId
    ) -> RiskDebateState:
        arguments: list[RiskArgument] = []
        for round_number in (1, 2):
            prior_outputs = tuple(arguments)
            viewpoints = tuple(
                viewpoint
                for viewpoint, configured_round in DEBATE_ORDER
                if configured_round == round_number
            )
            arguments.extend(
                run_bounded_group(
                    tuple(
                        partial(
                            self._viewpoint_call,
                            bundle,
                            context,
                            run_id,
                            viewpoint,
                            round_number,
                            prior_outputs,
                        )
                        for viewpoint in viewpoints
                    ),
                    max_workers=3,
                )
            )
        debate = build_risk_debate(
            meta=self._run_meta(run_id, context),
            context_id=context.context_id,
            bundle=bundle,
            arguments=arguments,
        )
        try:
            debate.validate_integrity()
            debate.validate_citations(context.citation_ids)
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("risk debate integrity is invalid") from error
        return debate

    def _viewpoint_call(
        self,
        bundle: ResearchBundle,
        context: ProposalContext,
        run_id: RunId,
        viewpoint: RiskViewpoint,
        round_number: int,
        prior_outputs: tuple[object, ...],
    ) -> RiskArgument:
        argument_id = derive_argument_id(context.context_id, viewpoint, round_number)
        stage = _VIEWPOINT_STAGE[viewpoint]
        envelope = self._model_envelope(
            bundle=bundle,
            context=context,
            run_id=run_id,
            output_id=argument_id,
            stage=stage,
            round_number=round_number,
            prior_outputs=prior_outputs,
        )
        request = ProposalRequest(
            stage=stage,
            run_id=run_id,
            input_id=bundle.parent_input_id,
            output_id=argument_id,
            context_id=context.context_id,
            bundle_id=context.bundle_id,
            bundle_hash=context.bundle_hash,
            context_hash=context.context_hash,
            snapshot_hash=context.snapshot_hash,
            universe_hash=context.universe_hash,
            window=context.window,
            deadline=context.deadline,
            created_at=context.meta.created_at,
            attempt=context.attempt,
            superseded_proposal_id=context.superseded_proposal_id,
            superseded_proposal_hash=context.superseded_proposal_hash,
            round_number=round_number,
            allowed_symbols=context.allowed_symbols,
            citation_ids=context.citation_ids,
            envelope=envelope,
        )
        output = self._execute_request(request, context.deadline)
        if type(output) is not RiskArgument:
            raise ProposalPipelineError("proposal provider returned an invalid result type")
        try:
            output.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("risk argument integrity is invalid") from error
        expected_meta = self._run_meta(run_id, context)
        if (
            output.argument_id != argument_id
            or output.context_id != context.context_id
            or output.bundle_id != context.bundle_id
            or output.bundle_hash != context.bundle_hash
            or output.viewpoint is not viewpoint
            or output.round_number != round_number
            or output.meta != expected_meta
            or output.producer_version != context.meta.producer_version
        ):
            raise ProposalPipelineError("risk argument identity is invalid")
        try:
            output.validate_against_citations(context.citation_ids)
        except ValueError as error:
            raise ProposalPipelineError(
                "risk argument cites evidence outside the frozen bundle set"
            ) from error
        return output

    def _proposal_from_provider(
        self,
        bundle: ResearchBundle,
        context: ProposalContext,
        debate: RiskDebateState,
        run_id: RunId,
        stage: ProposalProviderStage,
    ) -> PortfolioProposal:
        proposal_id = derive_proposal_id(context.context_id)
        envelope = self._model_envelope(
            bundle=bundle,
            context=context,
            run_id=run_id,
            output_id=proposal_id,
            stage=stage,
            round_number=None,
            prior_outputs=(debate,),
        )
        request = ProposalRequest(
            stage=stage,
            run_id=run_id,
            input_id=bundle.parent_input_id,
            output_id=proposal_id,
            context_id=context.context_id,
            bundle_id=context.bundle_id,
            bundle_hash=context.bundle_hash,
            context_hash=context.context_hash,
            snapshot_hash=context.snapshot_hash,
            universe_hash=context.universe_hash,
            window=context.window,
            deadline=context.deadline,
            created_at=context.meta.created_at,
            attempt=context.attempt,
            superseded_proposal_id=context.superseded_proposal_id,
            superseded_proposal_hash=context.superseded_proposal_hash,
            round_number=None,
            allowed_symbols=context.allowed_symbols,
            citation_ids=context.citation_ids,
            envelope=envelope,
        )
        output = self._execute_request(request, context.deadline)
        if type(output) is not PortfolioProposal:
            raise ProposalPipelineError("proposal provider returned an invalid result type")
        return self._verified_proposal(output, context)

    def _model_envelope(
        self,
        *,
        bundle: ResearchBundle,
        context: ProposalContext,
        run_id: RunId,
        output_id: RunId,
        stage: ProposalProviderStage,
        round_number: int | None,
        prior_outputs: tuple[object, ...],
    ) -> SanitizedProviderEnvelope:
        if stage is ProposalProviderStage.AGGRESSIVE:
            envelope_stage, role = EnvelopeStage.RISK_DEBATE, EnvelopeRole.AGGRESSIVE
        elif stage is ProposalProviderStage.CONSERVATIVE:
            envelope_stage, role = EnvelopeStage.RISK_DEBATE, EnvelopeRole.CONSERVATIVE
        elif stage is ProposalProviderStage.NEUTRAL:
            envelope_stage, role = EnvelopeStage.RISK_DEBATE, EnvelopeRole.NEUTRAL
        elif stage is ProposalProviderStage.PORTFOLIO_MANAGER_RETRY:
            envelope_stage, role = (
                EnvelopeStage.PORTFOLIO_MANAGER,
                EnvelopeRole.PORTFOLIO_MANAGER_RETRY,
            )
        else:
            envelope_stage, role = (
                EnvelopeStage.PORTFOLIO_MANAGER,
                EnvelopeRole.PORTFOLIO_MANAGER,
            )
        return SanitizedProviderEnvelope.build(
            stage=envelope_stage,
            role=role,
            round_number=round_number,
            run_id=run_id,
            input_id=bundle.parent_input_id,
            output_id=output_id,
            producer_version=context.meta.producer_version,
            symbol=None,
            attempt=context.attempt,
            superseded_proposal_id=context.superseded_proposal_id,
            superseded_proposal_hash=context.superseded_proposal_hash,
            context_id=context.context_id,
            previous_context_id=context.previous_context_id,
            bundle_id=bundle.bundle_id,
            packet_hash=None,
            snapshot_hash=context.snapshot_hash,
            context_hash=context.context_hash,
            bundle_hash=bundle.bundle_hash,
            universe_hash=context.universe_hash,
            created_at=context.meta.created_at,
            deadline=context.deadline,
            window=context.window,
            allowed_symbols=context.allowed_symbols,
            citation_ids=context.citation_ids,
            portfolio_snapshot=context.snapshot,
            source_material=(bundle, context),
            untrusted_data=research_bundle_model_material(bundle),
            prior_outputs=prior_outputs,
            feedback=None if context.feedback is None else context.feedback.to_wire(),
            versions=EnvelopeVersions(
                graph=context.graph_version,
                prompt=context.prompt_version,
                model=context.model_version,
                provider=context.provider_version,
                data=context.data_version,
                memory=context.memory_version,
            ),
            prompt_template_id=APPROVED_PROMPT_TEMPLATE_ID,
            prompt_template_hash=APPROVED_PROMPT_TEMPLATE_HASH,
        )

    def _execute_request(self, request: ProposalRequest, deadline: UtcTimestamp) -> object:
        self._check_deadline(deadline)
        try:
            output = self._provider.execute(request)
        except Exception as error:
            raise ProposalPipelineError("proposal provider failed closed") from error
        self._check_deadline(deadline)
        return output

    def _verified_proposal(
        self, proposal: PortfolioProposal, context: ProposalContext
    ) -> PortfolioProposal:
        if type(proposal) is not PortfolioProposal:
            raise ProposalPipelineError("proposal provider returned an invalid result type")
        try:
            proposal.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("portfolio proposal integrity is invalid") from error
        if proposal.status not in {AnalysisStatus.VALID, AnalysisStatus.ABSTAIN}:
            raise ProposalPipelineError("portfolio manager returned an invalid status")
        try:
            proposal.validate_against(context)
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError(
                "proposal does not match the exact context boundary"
            ) from error
        if proposal.meta.producer_version != context.meta.producer_version:
            raise ProposalPipelineError("proposal producer version is invalid")
        return proposal

    def _load_debate(
        self, run_id: str, bundle: ResearchBundle, *, expected_context_id: RunId
    ) -> RiskDebateState:
        stored = self._load_stored(run_id, ProposalStage.RISK_DEBATE)
        try:
            debate = RiskDebateState.from_wire(_strict_json_loads(stored.payload))
        except (RecursionError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProposalPipelineError("persisted risk debate is malformed") from error
        if canonical_wire_json(debate) != stored.payload:
            raise ProposalPipelineError("persisted risk debate is not canonical")
        self._verify_debate(debate, bundle, expected_context_id=expected_context_id)
        return debate

    @staticmethod
    def _verify_debate(
        debate: RiskDebateState, bundle: ResearchBundle, *, expected_context_id: RunId
    ) -> None:
        try:
            debate.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProposalPipelineError("risk debate integrity is invalid") from error
        if debate.complete is not True or len(debate.arguments) != 6:
            raise ProposalPipelineError("risk debate is not complete")
        if debate.bundle_id != bundle.bundle_id or debate.bundle_hash != bundle.bundle_hash:
            raise ProposalPipelineError("risk debate binds a foreign bundle")
        if debate.context_id != expected_context_id:
            raise ProposalPipelineError("risk debate context lineage is invalid")
        if (
            debate.meta.run_id != derive_proposal_run_id(expected_context_id)
            or debate.meta.created_at != bundle.meta.created_at
            or debate.meta.producer_version != bundle.meta.producer_version
        ):
            raise ProposalPipelineError("risk debate producer identity is invalid")
        try:
            debate.validate_citations(bundle.citation_ids)
        except ValueError as error:
            raise ProposalPipelineError(
                "risk debate cites evidence outside the frozen bundle set"
            ) from error

    def _load_proposal(self, run_id: str, context: ProposalContext) -> PortfolioProposal:
        stored = self._load_stored(run_id, ProposalStage.PROPOSAL)
        try:
            proposal = PortfolioProposal.from_wire(_strict_json_loads(stored.payload))
        except (RecursionError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProposalPipelineError("persisted proposal is malformed") from error
        if canonical_wire_json(proposal) != stored.payload:
            raise ProposalPipelineError("persisted proposal is not canonical")
        return self._verified_proposal(proposal, context)

    def _load_terminal(self, run_id: str) -> None:
        stored = self._load_stored(run_id, ProposalStage.COMPLETE)
        if stored.payload != "complete":
            raise ProposalPipelineError("persisted terminal stage is malformed")

    def _load_stored(self, run_id: str, stage: ProposalStage) -> StoredProposalResult:
        try:
            stored = self._repository.load(run_id, stage)
        except (KeyError, RuntimeError) as error:
            raise ProposalPipelineError("persisted proposal stage is unavailable") from error
        if (
            type(stored) is not StoredProposalResult
            or stored.run_id != run_id
            or stored.stage is not stage
            or type(stored.result_hash) is not str
            or type(stored.payload) is not str
            or not 1 <= len(stored.payload.encode("utf-8")) <= MAX_PROPOSAL_STAGE_PAYLOAD_BYTES
            or hashlib.sha256(stored.payload.encode("utf-8")).hexdigest() != stored.result_hash
        ):
            raise ProposalPipelineError("persisted proposal stage identity is invalid")
        return stored

    def _at_least(self, current: ProposalStage, required: ProposalStage) -> bool:
        try:
            return proposal_stage_order_at_least(current, required)
        except ValueError as error:
            raise ProposalPipelineError(str(error)) from error

    def _check_deadline(self, deadline: UtcTimestamp) -> None:
        if self._now() > deadline.value:
            raise ProposalPipelineError("proposal deadline expired")

    def _persist(
        self,
        run_id: str,
        context: ProposalContext,
        expected: ProposalStage,
        stage: ProposalStage,
        payload: str,
    ) -> None:
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self._persist_payload(run_id, context, expected, stage, payload, digest)

    def _persist_payload(
        self,
        run_id: str,
        context: ProposalContext,
        expected: ProposalStage,
        stage: ProposalStage,
        payload: str,
        digest: str,
    ) -> None:
        if (expected, stage) not in LEGAL_PROPOSAL_TRANSITIONS:
            raise ProposalPipelineError("proposal stage transition is not legal")
        self._check_deadline(context.deadline)
        current = self._repository.current_stage(run_id)
        if current is stage:
            existing = self._repository.load(run_id, stage)
            if existing is None or existing.result_hash != digest or existing.payload != payload:
                raise ProposalPipelineError("persisted stage result identity mismatch")
            return
        if current is not expected:
            raise ProposalPipelineError("proposal stage transition is out of order")
        self._check_deadline(context.deadline)
        self._repository.advance(StoredProposalResult(run_id, stage, digest, payload), expected)
