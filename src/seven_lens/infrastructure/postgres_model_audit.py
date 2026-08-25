"""Dedicated PostgreSQL durability boundary for P3-E model-call attempts.

This adapter must use a dedicated connection: ``persist`` commits the audit and the
strict parsed result before returning it to the caller as output authority.  That
eliminates the crash window in which a successful network call could otherwise be
repeated before its audit row becomes durable.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

import psycopg

from seven_lens.analysis.model_audit import (
    CanonicalModelCallResult,
    ModelCallAuditRecord,
    ModelCallClaim,
    ModelCallClaimDecision,
    ModelCallClaimResult,
    ModelCallErrorCode,
    ModelCallOutcome,
    ModelCallResultKind,
    ModelCallRole,
    ModelCallStage,
    StoredModelCallAttempt,
)
from seven_lens.application.ports.model_audit import ModelCallAuditError
from seven_lens.config.provider import (
    ApiFlavor,
    ProviderKind,
    ReasoningEffective,
    ReasoningRequested,
)
from seven_lens.domain.json_values import JsonObject
from seven_lens.domain.value_objects import RunId, UtcTimestamp

_SELECT_ATTEMPT = """
SELECT call_id, run_id, input_id, context_id, stage, role, round_number,
       provider, model, api_flavor, endpoint_policy_id, route_ordinal,
       prompt_template_hash, request_envelope_hash, response_hash,
       reasoning_requested, reasoning_effective, token_counts_trusted,
       input_tokens, output_tokens, latency_ms, started_at, completed_at,
       outcome, error_code, authority_kind, authority_hash, authority_payload
FROM public.model_call_audits
WHERE call_id = %s
"""


class PostgresModelCallAuditError(ModelCallAuditError):
    """Fixed, body-free PostgreSQL audit failure safe to expose to orchestration."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class PostgresModelCallAuditRepository:
    """Atomic audit + sanitized parsed-result store with exact replay semantics."""

    def __init__(self, connection: psycopg.Connection[tuple[object, ...]]) -> None:
        if not isinstance(connection, psycopg.Connection):
            raise PostgresModelCallAuditError("model-call audit connection is invalid")
        self._connection = connection

    def claim(self, claim: ModelCallClaim) -> ModelCallClaimResult:
        if type(claim) is not ModelCallClaim:
            raise PostgresModelCallAuditError("model-call claim is invalid")
        try:
            claim.__post_init__()
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.claim_model_call_attempt("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        claim.call_id.value,
                        claim.run_id.value,
                        claim.input_id.value,
                        claim.context_id.value,
                        claim.stage.value,
                        claim.role.value,
                        claim.round_number,
                        claim.provider.value,
                        claim.model,
                        claim.api_flavor.value,
                        claim.endpoint_policy_id,
                        claim.route_ordinal,
                        claim.prompt_template_hash,
                        claim.request_envelope_hash,
                        claim.reasoning_requested.value,
                    ),
                )
                row = cursor.fetchone()
            if row is None or len(row) != 1 or type(row[0]) is not str:
                raise PostgresModelCallAuditError("model-call claim returned malformed authority")
            decision = ModelCallClaimDecision(row[0])
            self._connection.commit()
            attempt = (
                self.load(claim.call_id) if decision is ModelCallClaimDecision.REPLAY else None
            )
            return ModelCallClaimResult(decision, attempt)
        except PostgresModelCallAuditError:
            self._connection.rollback()
            raise
        except (TypeError, ValueError) as error:
            self._connection.rollback()
            raise PostgresModelCallAuditError("model-call claim is invalid") from error
        except psycopg.Error as error:
            self._connection.rollback()
            message = (
                "model-call claim identity collision"
                if error.sqlstate == "23505"
                else "model-call claim failed"
            )
            raise PostgresModelCallAuditError(message, sqlstate=error.sqlstate) from error

    def load(self, call_id: RunId) -> StoredModelCallAttempt | None:
        if type(call_id) is not RunId:
            raise PostgresModelCallAuditError("model-call audit identity is invalid")
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(_SELECT_ATTEMPT, (call_id.value,))
                row = cursor.fetchone()
            self._connection.commit()
        except psycopg.Error as error:
            self._connection.rollback()
            raise PostgresModelCallAuditError(
                "model-call audit lookup failed", sqlstate=error.sqlstate
            ) from error
        return None if row is None else _stored_attempt(row)

    def persist(
        self,
        record: ModelCallAuditRecord,
        result: CanonicalModelCallResult | None,
    ) -> bool:
        try:
            attempt = StoredModelCallAttempt(record, result)
        except (TypeError, ValueError) as error:
            raise PostgresModelCallAuditError("model-call audit authority is invalid") from error
        stored_result = attempt.result
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.register_model_call_attempt("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        record.call_id.value,
                        record.run_id.value,
                        record.input_id.value,
                        record.context_id.value,
                        record.stage.value,
                        record.role.value,
                        record.round_number,
                        record.provider.value,
                        record.model,
                        record.api_flavor.value,
                        record.endpoint_policy_id,
                        record.route_ordinal,
                        record.prompt_template_hash,
                        record.request_envelope_hash,
                        record.response_hash,
                        record.reasoning_requested.value,
                        record.reasoning_effective.value,
                        record.token_counts_trusted,
                        record.input_tokens,
                        record.output_tokens,
                        record.latency_ms,
                        record.started_at.value,
                        record.completed_at.value,
                        record.outcome.value,
                        record.error_code.value,
                        None if stored_result is None else stored_result.kind.value,
                        None if stored_result is None else stored_result.result_hash,
                        None if stored_result is None else stored_result.payload.to_json(),
                    ),
                )
                row = cursor.fetchone()
            if row is None or len(row) != 1 or type(row[0]) is not bool:
                raise PostgresModelCallAuditError(
                    "model-call audit registration returned malformed authority"
                )
            # Deliberately commit here: returning before durability would allow a
            # crash/resume duplicate provider call with no replayable authority.
            self._connection.commit()
            return row[0]
        except PostgresModelCallAuditError:
            self._connection.rollback()
            raise
        except psycopg.Error as error:
            self._connection.rollback()
            message = (
                "model-call audit identity collision"
                if error.sqlstate == "23505"
                else "model-call audit registration failed"
            )
            raise PostgresModelCallAuditError(message, sqlstate=error.sqlstate) from error


def _run_id(value: object) -> RunId:
    if type(value) is not UUID:
        raise PostgresModelCallAuditError("stored model-call audit identity is malformed")
    return RunId(value)


def _timestamp(value: object) -> UtcTimestamp:
    if type(value) is not datetime:
        raise PostgresModelCallAuditError("stored model-call audit timestamp is malformed")
    return UtcTimestamp(value)


def _stored_attempt(row: tuple[object, ...]) -> StoredModelCallAttempt:
    if len(row) != 28:
        raise PostgresModelCallAuditError("stored model-call audit row is malformed")
    try:
        record = ModelCallAuditRecord(
            call_id=_run_id(row[0]),
            run_id=_run_id(row[1]),
            input_id=_run_id(row[2]),
            context_id=_run_id(row[3]),
            stage=ModelCallStage(str(row[4])),
            role=ModelCallRole(str(row[5])),
            round_number=cast(int, row[6]),
            provider=ProviderKind(str(row[7])),
            model=cast(str, row[8]),
            api_flavor=ApiFlavor(str(row[9])),
            endpoint_policy_id=cast(str, row[10]),
            route_ordinal=cast(int, row[11]),
            prompt_template_hash=cast(str, row[12]),
            request_envelope_hash=cast(str, row[13]),
            response_hash=cast(str | None, row[14]),
            reasoning_requested=ReasoningRequested(str(row[15])),
            reasoning_effective=ReasoningEffective(str(row[16])),
            token_counts_trusted=cast(bool, row[17]),
            input_tokens=cast(int | None, row[18]),
            output_tokens=cast(int | None, row[19]),
            latency_ms=cast(int, row[20]),
            started_at=_timestamp(row[21]),
            completed_at=_timestamp(row[22]),
            outcome=ModelCallOutcome(str(row[23])),
            error_code=ModelCallErrorCode(str(row[24])),
        )
        if row[25] is None:
            result = None
        else:
            result = CanonicalModelCallResult(
                record.call_id,
                ModelCallResultKind(str(row[25])),
                cast(str, row[26]),
                JsonObject(cast(str, row[27])),
            )
        return StoredModelCallAttempt(record, result)
    except (TypeError, ValueError) as error:
        raise PostgresModelCallAuditError("stored model-call audit row is malformed") from error
