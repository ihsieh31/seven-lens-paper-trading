"""PostgreSQL authority adapter for P3-D research bundles and proposal state.

Every write goes through the exact SECURITY DEFINER functions created by migration
``0011``; the runtime role never receives direct table-write capability.  All SQL in
this module is an inline literal carrying only ``%s`` placeholders, and every value
crosses the boundary as a bound parameter.  Contract integrity is re-verified here
before the boundary call so a malformed or drifted object fails in Python before
touching the authority database.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from seven_lens.analysis.contracts import RiskRejectionFeedback, canonical_wire_json
from seven_lens.analysis.proposal_contracts import ProposalContext, ResearchBundle
from seven_lens.application.ports.proposals import (
    LEGAL_PROPOSAL_TRANSITIONS,
    ProposalStage,
    StoredProposalResult,
    _parse_debate_payload,
    _parse_proposal_payload,
    _payload_digest,
)

_MAX_ITEM_BYTES = 262_144
_MAX_FEEDBACK_BYTES = 65_536


def _uuid(value: str) -> UUID:
    """Normalize one caller-supplied identity to a natively adapted UUID."""
    if type(value) is not str:
        raise PostgresProposalError("proposal authority identity is malformed")
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise PostgresProposalError("proposal authority identity is malformed") from error


class PostgresProposalError(RuntimeError):
    pass


class PostgresProposalStateRepository:
    """The PostgreSQL mirror of :class:`InMemoryProposalStateRepository`."""

    def __init__(self, connection: psycopg.Connection[tuple[object, ...]]) -> None:
        self._connection = connection

    def register_bundle(self, bundle: ResearchBundle) -> None:
        if type(bundle) is not ResearchBundle:
            raise PostgresProposalError("research bundle integrity is invalid")
        try:
            bundle.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise PostgresProposalError("research bundle integrity is invalid") from error
        items = [
            {"ordinal": ordinal, **item.to_wire()}
            for ordinal, item in enumerate(bundle.items, start=1)
        ]
        payload = canonical_wire_json(bundle)
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()
        if len(json.dumps(items, separators=(",", ":")).encode()) > _MAX_ITEM_BYTES:
            raise PostgresProposalError("research bundle items are outside the persisted bound")
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.register_research_bundle("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    bundle.bundle_id.value,
                    bundle.parent_input_id.value,
                    bundle.bundle_hash,
                    bundle.as_of.value,
                    bundle.window.value,
                    bundle.deadline.value,
                    bundle.universe_hash,
                    bundle.portfolio_snapshot_hash,
                    Jsonb(items),
                    payload_hash,
                    payload,
                ),
            )

    def register_context(self, context: ProposalContext) -> None:
        if type(context) is not ProposalContext:
            raise PostgresProposalError("proposal context integrity is invalid")
        try:
            context.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise PostgresProposalError("proposal context integrity is invalid") from error
        payload = canonical_wire_json(context)
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.register_proposal_context("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    context.context_id.value,
                    context.bundle_id.value,
                    context.attempt,
                    context.snapshot_hash,
                    None
                    if context.previous_context_id is None
                    else context.previous_context_id.value,
                    None
                    if context.superseded_proposal_id is None
                    else context.superseded_proposal_id.value,
                    context.superseded_proposal_hash,
                    None if context.feedback is None else context.feedback.meta.run_id.value,
                    context.context_hash,
                    payload_hash,
                    payload,
                ),
            )

    def register_feedback(self, feedback: RiskRejectionFeedback) -> None:
        if type(feedback) is not RiskRejectionFeedback:
            raise PostgresProposalError("risk rejection feedback integrity is invalid")
        try:
            feedback.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise PostgresProposalError("risk rejection feedback integrity is invalid") from error
        payload = canonical_wire_json(feedback)
        if len(payload) > _MAX_FEEDBACK_BYTES:
            raise PostgresProposalError("risk feedback payload is outside the persisted bound")
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.register_risk_feedback(%s, %s, %s, %s)",
                (
                    feedback.meta.run_id.value,
                    feedback.rejected_proposal_id.value,
                    digest,
                    payload,
                ),
            )

    def create_run(self, run_id: str, context_id: str, bundle_id: str, bundle_hash: str) -> None:
        parameters = (_uuid(run_id), _uuid(context_id), _uuid(bundle_id), bundle_hash)
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.create_proposal_run(%s, %s, %s, %s)",
                parameters,
            )

    def current_stage(self, run_id: str) -> ProposalStage:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_stage FROM public.proposal_runs WHERE run_id = %s",
                (_uuid(run_id),),
            )
            row = cursor.fetchone()
        if row is None or type(row[0]) is not str:
            raise PostgresProposalError("proposal run is unavailable")
        try:
            return ProposalStage(row[0])
        except ValueError as error:
            raise PostgresProposalError("proposal run has an unknown stage") from error

    def load(self, run_id: str, stage: ProposalStage) -> StoredProposalResult | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.proposal_stage_results WHERE run_id = %s AND stage = %s",
                (_uuid(run_id), stage.value),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        result_hash, payload = row[2], row[3]
        if type(result_hash) is not str or type(payload) is not str:
            raise PostgresProposalError("proposal stage result is malformed")
        return StoredProposalResult(run_id, stage, result_hash, payload)

    def advance(self, result: StoredProposalResult, expected_current: ProposalStage) -> bool:
        if (expected_current, result.stage) not in LEGAL_PROPOSAL_TRANSITIONS:
            raise PostgresProposalError("proposal stage transition is not legal")
        if _payload_digest(result.payload) != result.result_hash:
            raise PostgresProposalError("proposal stage result hash does not match its payload")
        try:
            if result.stage is ProposalStage.RISK_DEBATE:
                _parse_debate_payload(result.payload)
            elif result.stage is ProposalStage.PROPOSAL:
                _parse_proposal_payload(result.payload)
            elif result.payload != result.stage.value.lower():
                raise ValueError("terminal payload is not canonical")
        except (TypeError, ValueError) as error:
            raise PostgresProposalError("proposal stage payload is malformed") from error
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.advance_proposal_stage(%s, %s, %s, %s, %s)",
                (
                    _uuid(result.run_id),
                    expected_current.value,
                    result.stage.value,
                    result.result_hash,
                    result.payload,
                ),
            )
            row = cursor.fetchone()
        if row is None or type(row[0]) is not bool:
            raise PostgresProposalError("proposal stage transition returned malformed authority")
        return row[0]

    def attempt_two_exists(self, bundle_id: str, context_id: str) -> bool:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM public.proposal_contexts "
                "WHERE bundle_id = %s AND attempt = 2 AND context_id <> %s)",
                (_uuid(bundle_id), _uuid(context_id)),
            )
            row = cursor.fetchone()
        if row is None or type(row[0]) is not bool:
            raise PostgresProposalError("proposal authority query returned malformed evidence")
        return row[0]
