"""Persistence-neutral P3-D proposal stage authority port.

The proposal state machine is independent of the P3-C analysis machine and of migration
``0010``: ``PLANNED -> RISK_DEBATE -> PROPOSAL -> COMPLETE`` with every non-terminal stage
allowed to fail closed into ``INVALID`` or ``EXPIRED``.  COMPLETE, INVALID and EXPIRED are
sinks.  Both repository layers share the single adjacency whitelist and retry budget below,
and both independently enforce bundle, context and proposal lineage when authority is first
recorded.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol

from seven_lens.analysis.contracts import (
    AnalysisStatus,
    RiskRejectionFeedback,
    TraderPlan,
    canonical_wire_json,
)
from seven_lens.analysis.proposal_contracts import (
    PortfolioProposal,
    ProposalContext,
    ResearchBundle,
    RiskDebateState,
    derive_proposal_run_id,
)
from seven_lens.application.ports.analysis import (
    AnalysisStage,
    AnalysisStateRepository,
    StoredStageResult,
)
from seven_lens.domain.value_objects import RunId, UtcTimestamp


class ProposalStage(StrEnum):
    PLANNED = "PLANNED"
    RISK_DEBATE = "RISK_DEBATE"
    PROPOSAL = "PROPOSAL"
    COMPLETE = "COMPLETE"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"


# The single authoritative adjacency whitelist shared by every repository layer.
LEGAL_PROPOSAL_TRANSITIONS: frozenset[tuple[ProposalStage, ProposalStage]] = frozenset(
    {
        (ProposalStage.PLANNED, ProposalStage.RISK_DEBATE),
        (ProposalStage.RISK_DEBATE, ProposalStage.PROPOSAL),
        (ProposalStage.PROPOSAL, ProposalStage.COMPLETE),
        (ProposalStage.PLANNED, ProposalStage.INVALID),
        (ProposalStage.RISK_DEBATE, ProposalStage.INVALID),
        (ProposalStage.PROPOSAL, ProposalStage.INVALID),
        (ProposalStage.PLANNED, ProposalStage.EXPIRED),
        (ProposalStage.RISK_DEBATE, ProposalStage.EXPIRED),
        (ProposalStage.PROPOSAL, ProposalStage.EXPIRED),
    }
)

MAX_PROPOSAL_STAGE_ATTEMPTS: Final = 8
MAX_PROPOSAL_STAGE_PAYLOAD_BYTES: Final = 262_144
_HASH = re.compile(r"^[0-9a-f]{64}$")


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


def _payload_digest(payload: str) -> str:
    if type(payload) is not str:
        raise ValueError("persisted proposal payload must be exact text")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_debate_payload(payload: str) -> RiskDebateState:
    try:
        debate = RiskDebateState.from_wire(_strict_json_loads(payload))
        debate.validate_integrity()
    except (AttributeError, RecursionError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("persisted risk debate payload is malformed") from error
    if canonical_wire_json(debate) != payload:
        raise ValueError("persisted risk debate payload is not canonical")
    return debate


def _parse_proposal_payload(payload: str) -> PortfolioProposal:
    try:
        proposal = PortfolioProposal.from_wire(_strict_json_loads(payload))
        proposal.validate_integrity()
    except (AttributeError, RecursionError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("persisted proposal payload is malformed") from error
    if canonical_wire_json(proposal) != payload:
        raise ValueError("persisted proposal payload is not canonical")
    return proposal


_STAGE_ORDER: Final = (
    ProposalStage.PLANNED,
    ProposalStage.RISK_DEBATE,
    ProposalStage.PROPOSAL,
    ProposalStage.COMPLETE,
)


def proposal_stage_order_at_least(current: ProposalStage, required: ProposalStage) -> bool:
    """Shared monotonic ordering used by fresh and resumed proposal runs."""
    if current in {ProposalStage.INVALID, ProposalStage.EXPIRED}:
        raise ValueError("terminal proposal run cannot resume")
    return _STAGE_ORDER.index(current) >= _STAGE_ORDER.index(required)


@dataclass(frozen=True, slots=True)
class StoredProposalResult:
    run_id: str
    stage: ProposalStage
    result_hash: str
    payload: str


class ProposalStateRepository(Protocol):
    def register_bundle(self, bundle: ResearchBundle) -> None: ...
    def register_context(self, context: ProposalContext) -> None: ...
    def create_run(
        self, run_id: str, context_id: str, bundle_id: str, bundle_hash: str
    ) -> None: ...
    def current_stage(self, run_id: str) -> ProposalStage: ...
    def load(self, run_id: str, stage: ProposalStage) -> StoredProposalResult | None: ...
    def advance(self, result: StoredProposalResult, expected_current: ProposalStage) -> bool: ...
    def register_feedback(self, feedback: RiskRejectionFeedback) -> None: ...
    def attempt_two_exists(self, bundle_id: str, context_id: str) -> bool: ...


class BundleAuthorityVerifier(Protocol):
    def verify(self, bundle: ResearchBundle) -> None: ...


class AnalysisRepositoryBundleVerifier:
    """Reproduce P3-C COMPLETE and canonical TraderPlan evidence before registration."""

    def __init__(self, repository: AnalysisStateRepository) -> None:
        self._repository = repository

    def verify(self, bundle: ResearchBundle) -> None:
        try:
            for item in bundle.items:
                run_id = str(item.analysis_run_id)
                if self._repository.current_stage(run_id) is not AnalysisStage.COMPLETE:
                    raise ValueError("child analysis run is not complete")
                if self._repository.run_identity(run_id) != (
                    str(item.analysis_input_id),
                    item.packet_hash,
                    item.snapshot_hash,
                ):
                    raise ValueError("child analysis identity is foreign")
                terminal = self._repository.load(run_id, AnalysisStage.COMPLETE)
                trader = self._repository.load(run_id, AnalysisStage.TRADER)
                if terminal is None or trader is None:
                    raise ValueError("child analysis authority is incomplete")
                if (
                    type(terminal) is not StoredStageResult
                    or terminal.run_id != run_id
                    or terminal.stage is not AnalysisStage.COMPLETE
                    or terminal.payload != "complete"
                    or _payload_digest(terminal.payload) != terminal.result_hash
                    or type(trader) is not StoredStageResult
                    or trader.run_id != run_id
                    or trader.stage is not AnalysisStage.TRADER
                    or _payload_digest(trader.payload) != trader.result_hash
                    or trader.result_hash != item.trader_plan_hash
                ):
                    raise ValueError("child analysis payload identity is invalid")
                plan = TraderPlan.from_wire(_strict_json_loads(trader.payload))
                if canonical_wire_json(plan) != trader.payload:
                    raise ValueError("child TraderPlan is not canonical")
                if (
                    plan.meta.run_id != item.analysis_run_id
                    or plan.input_id != item.analysis_input_id
                    or plan.plan_id != item.trader_plan_id
                    or plan.symbol != item.symbol
                    or plan.status is not AnalysisStatus.VALID
                    or plan.evidence_refs != item.evidence_refs
                    or plan.meta.producer_version != item.producer_version
                ):
                    raise ValueError("child TraderPlan authority is foreign")
        except (
            AttributeError,
            KeyError,
            RecursionError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            raise ValueError("research bundle lacks verified child authority") from error


class InMemoryProposalStateRepository:
    """In-memory mirror of the PostgreSQL proposal authority, including lineage rules."""

    def __init__(
        self,
        bundle_verifier: BundleAuthorityVerifier | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._bundle_verifier = bundle_verifier
        self._now = now or (lambda: datetime.now(UTC))
        self._runs: dict[str, ProposalStage] = {}
        self._identities: dict[str, tuple[str, str, str]] = {}
        self._run_by_context: dict[str, str] = {}
        self._results: dict[tuple[str, ProposalStage], StoredProposalResult] = {}
        self._attempts: dict[tuple[str, ProposalStage], int] = {}
        self._proposals: dict[str, tuple[int, str | None, str, str, str, str]] = {}
        self._proposal_contracts: dict[str, PortfolioProposal] = {}
        self._proposal_by_context: dict[str, str] = {}
        self._superseded: set[str] = set()
        self._attempt_one_bundles: set[str] = set()
        self._bundles: dict[str, ResearchBundle] = {}
        self._bundle_by_parent: dict[str, str] = {}
        self._bundle_by_child_run: dict[str, str] = {}
        self._bundle_by_child_input: dict[str, str] = {}
        self._contexts: dict[str, ProposalContext] = {}
        self._attempt_one_contexts: set[str] = set()
        self._attempt_two_context_by_bundle: dict[str, str] = {}
        self._feedback: dict[str, tuple[RiskRejectionFeedback, str]] = {}

    def register_bundle(self, bundle: ResearchBundle) -> None:
        if type(bundle) is not ResearchBundle:
            raise ValueError("research bundle integrity is invalid")
        try:
            bundle.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("research bundle integrity is invalid") from error
        self._check_authority_deadline(bundle.deadline)
        if self._bundle_verifier is None:
            raise ValueError("research bundle verifier is required")
        self._bundle_verifier.verify(bundle)
        frozen = ResearchBundle.from_wire(bundle.to_wire())
        bundle_id = str(bundle.bundle_id)
        existing = self._bundles.get(bundle_id)
        if existing is not None and existing != frozen:
            raise ValueError("research bundle identity collision")
        parent_id = str(bundle.parent_input_id)
        if self._bundle_by_parent.get(parent_id, bundle_id) != bundle_id:
            raise ValueError("parent input already has a research bundle")
        for item in bundle.items:
            if self._bundle_by_child_run.get(str(item.analysis_run_id), bundle_id) != bundle_id:
                raise ValueError("child analysis run is already bound to another bundle")
            if self._bundle_by_child_input.get(str(item.analysis_input_id), bundle_id) != bundle_id:
                raise ValueError("child analysis input is already bound to another bundle")
        self._bundles.setdefault(bundle_id, frozen)
        self._bundle_by_parent.setdefault(parent_id, bundle_id)
        for item in bundle.items:
            self._bundle_by_child_run.setdefault(str(item.analysis_run_id), bundle_id)
            self._bundle_by_child_input.setdefault(str(item.analysis_input_id), bundle_id)

    def register_context(self, context: ProposalContext) -> None:
        if type(context) is not ProposalContext:
            raise ValueError("proposal context integrity is invalid")
        try:
            context.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("proposal context integrity is invalid") from error
        self._check_authority_deadline(context.deadline)
        bundle = self._bundles.get(str(context.bundle_id))
        if bundle is None or bundle.bundle_hash != context.bundle_hash:
            raise ValueError("proposal context binds an unknown research bundle")
        expected_allowed_symbols = (*bundle.holding_symbols, *bundle.candidate_symbols)
        if (
            context.window is not bundle.window
            or context.deadline != bundle.deadline
            or context.universe_hash != bundle.universe_hash
            or context.allowed_symbols != expected_allowed_symbols
            or context.citation_ids != bundle.citation_ids
            or context.meta.schema_version != bundle.meta.schema_version
            or context.meta.created_at != bundle.meta.created_at
            or context.meta.producer_version != bundle.meta.producer_version
            or context.graph_version != bundle.first_item.graph_version
            or context.prompt_version != bundle.first_item.prompt_version
            or context.data_version != bundle.first_item.data_version
        ):
            raise ValueError("proposal context does not match the frozen research bundle")
        if context.attempt == 1 and context.snapshot_hash != bundle.portfolio_snapshot_hash:
            raise ValueError("attempt 1 context snapshot is not the frozen bundle snapshot")
        frozen = ProposalContext.from_wire(context.to_wire())
        context_id = str(context.context_id)
        existing = self._contexts.get(context_id)
        if existing is not None:
            if existing != frozen:
                raise ValueError("proposal context identity collision")
            return
        if context.attempt == 1:
            if str(context.bundle_id) in self._attempt_one_contexts:
                raise ValueError("bundle already has an attempt 1 context")
        else:
            previous_id = (
                "" if context.previous_context_id is None else str(context.previous_context_id)
            )
            previous = self._contexts.get(previous_id)
            if previous is None or previous.bundle_id != context.bundle_id or previous.attempt != 1:
                raise ValueError("attempt 2 context requires the attempt 1 context")
            superseded_id = (
                ""
                if context.superseded_proposal_id is None
                else str(context.superseded_proposal_id)
            )
            superseded = self._proposals.get(superseded_id)
            if (
                superseded is None
                or superseded[0] != 1
                or superseded[3] != str(context.bundle_id)
                or superseded[5] != context.superseded_proposal_hash
            ):
                raise ValueError("attempt 2 context supersedes an unknown proposal")
            if not self._proposal_authority_is_complete(superseded):
                raise ValueError("attempt 2 context requires a completed attempt 1 proposal")
            feedback_id = "" if context.feedback is None else str(context.feedback.meta.run_id)
            feedback = self._feedback.get(feedback_id)
            if feedback is None or str(feedback[0].rejected_proposal_id) != superseded_id:
                raise ValueError("attempt 2 context requires registered risk feedback")
            if context.feedback != feedback[0]:
                raise ValueError("attempt 2 context feedback is not the registered authority")
            if context.snapshot.remaining_limits != feedback[0].remaining_limits:
                raise ValueError("attempt 2 context limits do not match risk feedback")
            prior_attempt_two = self._attempt_two_context_by_bundle.get(str(context.bundle_id))
            if prior_attempt_two is not None and prior_attempt_two != context_id:
                raise ValueError("bundle already has an attempt 2 context")
        self._contexts[context_id] = frozen
        if context.attempt == 1:
            self._attempt_one_contexts.add(str(context.bundle_id))
        else:
            self._attempt_two_context_by_bundle[str(context.bundle_id)] = context_id

    def create_run(self, run_id: str, context_id: str, bundle_id: str, bundle_hash: str) -> None:
        context = self._contexts.get(context_id)
        bundle = self._bundles.get(bundle_id)
        if (
            context is None
            or bundle is None
            or str(context.bundle_id) != bundle_id
            or context.bundle_hash != bundle_hash
            or bundle.bundle_hash != bundle_hash
        ):
            raise ValueError("proposal run binds unregistered authority")
        self._check_authority_deadline(context.deadline)
        try:
            expected_run = str(derive_proposal_run_id(RunId.from_string(context_id)))
        except (TypeError, ValueError) as error:
            raise ValueError("proposal run identity is malformed") from error
        if run_id != expected_run:
            raise ValueError("proposal run identity does not match its context")
        identity = (context_id, bundle_id, bundle_hash)
        existing = self._identities.get(run_id)
        if existing is not None and existing != identity:
            raise ValueError("proposal run identity collision")
        existing_run = self._run_by_context.get(context_id)
        if existing_run is not None and existing_run != run_id:
            raise ValueError("proposal context already has an authority run")
        self._identities.setdefault(run_id, identity)
        self._run_by_context.setdefault(context_id, run_id)
        self._runs.setdefault(run_id, ProposalStage.PLANNED)

    def current_stage(self, run_id: str) -> ProposalStage:
        return self._runs[run_id]

    def load(self, run_id: str, stage: ProposalStage) -> StoredProposalResult | None:
        return self._results.get((run_id, stage))

    def attempt_count(self, run_id: str, stage: ProposalStage) -> int:
        return self._attempts.get((run_id, stage), 0)

    def register_feedback(self, feedback: RiskRejectionFeedback) -> None:
        if type(feedback) is not RiskRejectionFeedback:
            raise ValueError("risk rejection feedback integrity is invalid")
        try:
            feedback.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("risk rejection feedback integrity is invalid") from error
        feedback_id = str(feedback.meta.run_id)
        rendered = canonical_wire_json(feedback)
        proposal = self._proposal_contracts.get(str(feedback.rejected_proposal_id))
        if proposal is None or proposal.attempt != 1 or proposal.status is not AnalysisStatus.VALID:
            raise ValueError("risk rejection feedback targets unknown proposal authority")
        proposal_record = self._proposals.get(str(feedback.rejected_proposal_id))
        if proposal_record is None or not self._proposal_authority_is_complete(proposal_record):
            raise ValueError("risk rejection feedback requires a completed proposal authority")
        context = self._contexts.get(proposal_record[4])
        now = self._now()
        if (
            context is None
            or feedback.reviewed_at.value <= context.meta.created_at.value
            or feedback.reviewed_at.value > now
            or feedback.reviewed_at.value >= context.deadline.value
            or now > context.deadline.value
        ):
            raise ValueError("risk rejection feedback timeline is invalid")
        if not set(feedback.rejected_symbols) <= {request.symbol for request in proposal.requests}:
            raise ValueError("risk rejection feedback targets a foreign symbol")
        existing = self._feedback.get(feedback_id)
        if existing is not None:
            if existing[1] != rendered:
                raise ValueError("risk feedback identity collision")
            return
        frozen = RiskRejectionFeedback.from_wire(feedback.to_wire())
        self._feedback.setdefault(feedback_id, (frozen, rendered))

    def _proposal_authority_is_complete(
        self, proposal: tuple[int, str | None, str, str, str, str]
    ) -> bool:
        context_id = proposal[4]
        run_id = self._run_by_context.get(context_id)
        if run_id is None or self._runs.get(run_id) is not ProposalStage.COMPLETE:
            return False
        proposal_id = self._proposal_by_context.get(context_id)
        proposal_contract = (
            None if proposal_id is None else self._proposal_contracts.get(proposal_id)
        )
        persisted = self._results.get((run_id, ProposalStage.PROPOSAL))
        terminal = self._results.get((run_id, ProposalStage.COMPLETE))
        if not (
            proposal_id is not None
            and type(proposal_contract) is PortfolioProposal
            and type(persisted) is StoredProposalResult
            and persisted.run_id == run_id
            and persisted.stage is ProposalStage.PROPOSAL
            and persisted.result_hash == proposal[5]
            and _payload_digest(persisted.payload) == persisted.result_hash
            and str(proposal_contract.proposal_id) == proposal_id
            and str(proposal_contract.context_id) == context_id
            and type(terminal) is StoredProposalResult
            and terminal.run_id == run_id
            and terminal.stage is ProposalStage.COMPLETE
            and terminal.payload == "complete"
            and _payload_digest(terminal.payload) == terminal.result_hash
        ):
            return False
        try:
            return _parse_proposal_payload(persisted.payload) == proposal_contract
        except ValueError:
            return False

    def attempt_two_exists(self, bundle_id: str, context_id: str) -> bool:
        """True when this bundle already has an attempt-2 proposal under a
        different context; the same-context replay stays on its idempotent path."""
        known_context = self._attempt_two_context_by_bundle.get(bundle_id)
        return known_context is not None and known_context != context_id

    def advance(self, result: StoredProposalResult, expected_current: ProposalStage) -> bool:
        if (expected_current, result.stage) not in LEGAL_PROPOSAL_TRANSITIONS:
            raise ValueError("proposal stage transition is not legal")
        contract = self._validate_stage_result(result)
        if result.stage not in {ProposalStage.INVALID, ProposalStage.EXPIRED}:
            identity = self._identities.get(result.run_id)
            context = None if identity is None else self._contexts.get(identity[0])
            if context is None:
                raise ValueError("proposal stage result binds unavailable authority")
            self._check_authority_deadline(context.deadline)
        key = (result.run_id, result.stage)
        existing = self._results.get(key)
        if existing is not None:
            if existing != result:
                raise ValueError("proposal stage retry changed immutable output")
            attempt = self._attempts.get(key, 1) + 1
            if attempt > MAX_PROPOSAL_STAGE_ATTEMPTS:
                raise ValueError("proposal stage retry budget is exhausted")
            self._attempts[key] = attempt
            return False
        if self._runs.get(result.run_id) is not expected_current:
            raise ValueError("proposal stage transition is out of order")
        if result.stage is ProposalStage.PROPOSAL:
            if type(contract) is not PortfolioProposal:
                raise ValueError("persisted proposal payload is malformed")
            self._register_proposal(contract, result.result_hash)
        self._results[key] = result
        self._runs[result.run_id] = result.stage
        self._attempts[key] = 1
        return True

    def _check_authority_deadline(self, deadline: UtcTimestamp) -> None:
        if type(deadline) is not UtcTimestamp or self._now() > deadline.value:
            raise ValueError("proposal authority deadline expired")

    def _validate_stage_result(
        self, result: StoredProposalResult
    ) -> RiskDebateState | PortfolioProposal | None:
        if type(result) is not StoredProposalResult:
            raise ValueError("proposal stage result requires an exact contract")
        if type(result.result_hash) is not str or _HASH.fullmatch(result.result_hash) is None:
            raise ValueError("proposal stage result hash is malformed")
        if _payload_digest(result.payload) != result.result_hash:
            raise ValueError("proposal stage result hash does not match its payload")
        identity = self._identities.get(result.run_id)
        if identity is None:
            raise ValueError("proposal stage result binds an unknown run")
        context = self._contexts.get(identity[0])
        bundle = self._bundles.get(identity[1])
        if context is None or bundle is None or bundle.bundle_hash != identity[2]:
            raise ValueError("proposal stage result binds unavailable authority")
        if result.stage is ProposalStage.RISK_DEBATE:
            debate = _parse_debate_payload(result.payload)
            expected_context = context.previous_context_id or context.context_id
            if (
                debate.context_id != expected_context
                or debate.bundle_id != bundle.bundle_id
                or debate.bundle_hash != bundle.bundle_hash
                or debate.meta.run_id != derive_proposal_run_id(expected_context)
                or debate.meta.created_at != bundle.meta.created_at
                or debate.meta.producer_version != bundle.meta.producer_version
                or debate.complete is not True
            ):
                raise ValueError("persisted risk debate binds foreign authority")
            try:
                debate.validate_citations(bundle.citation_ids)
            except ValueError as error:
                raise ValueError("persisted risk debate cites foreign evidence") from error
            return debate
        if result.stage is ProposalStage.PROPOSAL:
            proposal = _parse_proposal_payload(result.payload)
            try:
                proposal.validate_against(context)
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError("persisted proposal binds foreign authority") from error
            if (
                proposal.status not in {AnalysisStatus.VALID, AnalysisStatus.ABSTAIN}
                or proposal.meta.producer_version != context.meta.producer_version
            ):
                raise ValueError("persisted proposal status or producer is invalid")
            return proposal
        expected_payload = result.stage.value.lower()
        if result.payload != expected_payload:
            raise ValueError("terminal proposal stage payload is not canonical")
        return None

    def _register_proposal(self, proposal: PortfolioProposal, proposal_hash: str) -> None:
        proposal_id = str(proposal.proposal_id)
        attempt = proposal.attempt
        superseded = (
            None
            if proposal.superseded_proposal_id is None
            else str(proposal.superseded_proposal_id)
        )
        bundle_id = str(proposal.bundle_id)
        context_id = str(proposal.context_id)
        status = proposal.status.value
        if proposal_id in self._proposals:
            raise ValueError("portfolio proposal identity collision")
        existing_for_context = self._proposal_by_context.get(context_id)
        if existing_for_context is not None and existing_for_context != proposal_id:
            raise ValueError("proposal context already has a portfolio proposal")
        if attempt == 1:
            if superseded is not None:
                raise ValueError("attempt 1 proposal must not supersede another proposal")
            if bundle_id in self._attempt_one_bundles:
                raise ValueError("bundle already has an attempt 1 proposal")
        else:
            if type(superseded) is not str:
                raise ValueError("attempt 2 proposal requires a superseded proposal id")
            lineage = self._proposals.get(superseded)
            if lineage is None:
                raise ValueError("attempt 2 proposal supersedes an unknown proposal")
            if lineage[0] != 1 or lineage[3] != bundle_id:
                raise ValueError("attempt 2 proposal supersedes a foreign bundle proposal")
            if superseded in self._superseded:
                raise ValueError("proposal lineage collision")
            context = self._contexts.get(context_id)
            if (
                context is None
                or context.superseded_proposal_hash != self._proposals[superseded][5]
            ):
                raise ValueError("proposal lineage hash collision")
        if attempt == 1:
            self._attempt_one_bundles.add(bundle_id)
        else:
            if superseded is None:
                raise ValueError("attempt 2 proposal requires a superseded proposal id")
            self._superseded.add(superseded)
        self._proposals[proposal_id] = (
            attempt,
            superseded,
            status,
            bundle_id,
            context_id,
            proposal_hash,
        )
        self._proposal_contracts[proposal_id] = proposal
        self._proposal_by_context[context_id] = proposal_id
