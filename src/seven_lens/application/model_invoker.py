"""Strict single-route model invocation with durable audit-before-authority."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final, NoReturn, cast

from seven_lens.analysis.contracts import (
    AnalystReport,
    AnalystRole,
    ProposalAction,
    ResearchConclusion,
    TraderPlan,
)
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
    derive_model_call_id,
)
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    SanitizedProviderEnvelope,
)
from seven_lens.analysis.ports import DebateArgument, ProviderStage
from seven_lens.analysis.prompt_builder import (
    OutputContract,
    build_model_prompt,
)
from seven_lens.analysis.proposal_contracts import (
    PortfolioProposal,
    RiskArgument,
    RiskViewpoint,
)
from seven_lens.application.ports.model_audit import ModelCallAuditPort
from seven_lens.application.ports.model_transport import (
    JsonMessageRole,
    JsonModelMessage,
    JsonModelRequest,
    JsonModelResponse,
    JsonModelTransport,
    ModelTransportError,
    ModelTransportErrorCode,
)
from seven_lens.config.analysis_provider import AnalysisProviderConfig
from seven_lens.config.provider import (
    ApiFlavor,
    ProviderKind,
    ReasoningEffective,
    ReasoningRequested,
)
from seven_lens.domain.json_values import MAX_SERIALIZED_BYTES, JsonObject
from seven_lens.domain.value_objects import RunId, UtcTimestamp

ModelOutput = (
    AnalystReport
    | DebateArgument
    | ResearchConclusion
    | TraderPlan
    | RiskArgument
    | PortfolioProposal
)

P3E_PROMPT_VERSION: Final = "p3e.1"

_ERROR_MESSAGES: Final = {
    ModelTransportErrorCode.CONFIG: "model call configuration is invalid",
    ModelTransportErrorCode.AUTH: "model provider authentication failed",
    ModelTransportErrorCode.PERMANENT: "model provider rejected the request",
    ModelTransportErrorCode.RATE_LIMIT: "model provider rate limit was reached",
    ModelTransportErrorCode.TRANSIENT: "model provider transport failed",
    ModelTransportErrorCode.TIMEOUT: "model provider request timed out",
    ModelTransportErrorCode.PROTOCOL: "model provider protocol response is invalid",
    ModelTransportErrorCode.SCHEMA: "model provider output schema is invalid",
    ModelTransportErrorCode.OVERSIZE: "model provider output exceeds the fixed limit",
    ModelTransportErrorCode.DEADLINE: "model provider request timed out",
    ModelTransportErrorCode.AUDIT: "model call audit failed",
}

_STAGE_MAP: Final = {stage: ModelCallStage(stage.value) for stage in EnvelopeStage}
_ROLE_MAP: Final = {
    role: (
        ModelCallRole.PORTFOLIO_MANAGER
        if role is EnvelopeRole.PORTFOLIO_MANAGER_RETRY
        else ModelCallRole(role.value)
    )
    for role in EnvelopeRole
}
_CONTRACT_KIND: Final = {
    OutputContract.ANALYST_REPORT: ModelCallResultKind.ANALYST_REPORT,
    OutputContract.DEBATE_ARGUMENT: ModelCallResultKind.DEBATE_ARGUMENT,
    OutputContract.RESEARCH_CONCLUSION: ModelCallResultKind.RESEARCH_CONCLUSION,
    OutputContract.TRADER_PLAN: ModelCallResultKind.TRADER_PLAN,
    OutputContract.RISK_ARGUMENT: ModelCallResultKind.RISK_ARGUMENT,
    OutputContract.PORTFOLIO_PROPOSAL: ModelCallResultKind.PORTFOLIO_PROPOSAL,
}
_STAGE_CONTRACT: Final = {
    EnvelopeStage.ANALYST: OutputContract.ANALYST_REPORT,
    EnvelopeStage.INVESTMENT_DEBATE: OutputContract.DEBATE_ARGUMENT,
    EnvelopeStage.RESEARCH_MANAGER: OutputContract.RESEARCH_CONCLUSION,
    EnvelopeStage.TRADER: OutputContract.TRADER_PLAN,
    EnvelopeStage.RISK_DEBATE: OutputContract.RISK_ARGUMENT,
    EnvelopeStage.PORTFOLIO_MANAGER: OutputContract.PORTFOLIO_PROPOSAL,
}
_TRANSPORT_AUDIT_CODE: Final = {
    ModelTransportErrorCode.CONFIG: ModelCallErrorCode.CONFIG,
    ModelTransportErrorCode.AUTH: ModelCallErrorCode.AUTH,
    ModelTransportErrorCode.PERMANENT: ModelCallErrorCode.PERMANENT,
    ModelTransportErrorCode.RATE_LIMIT: ModelCallErrorCode.RATE_LIMIT,
    ModelTransportErrorCode.TRANSIENT: ModelCallErrorCode.TRANSIENT,
    ModelTransportErrorCode.TIMEOUT: ModelCallErrorCode.TIMEOUT,
    ModelTransportErrorCode.PROTOCOL: ModelCallErrorCode.PROTOCOL,
    ModelTransportErrorCode.SCHEMA: ModelCallErrorCode.SCHEMA,
    ModelTransportErrorCode.OVERSIZE: ModelCallErrorCode.OVERSIZE,
}
_AUDIT_TRANSPORT_CODE: Final = {
    **{value: key for key, value in _TRANSPORT_AUDIT_CODE.items()},
    # Historical audit rows may contain DEADLINE; replay them under the closed TIMEOUT taxonomy.
    ModelCallErrorCode.DEADLINE: ModelTransportErrorCode.TIMEOUT,
}


class ModelInvocationError(RuntimeError):
    """Fixed, non-disclosing invocation failure."""

    __slots__ = ("code",)

    def __init__(self, code: ModelTransportErrorCode) -> None:
        if type(code) is not ModelTransportErrorCode:
            raise ValueError("model invocation error code is invalid")
        normalized = (
            ModelTransportErrorCode.TIMEOUT if code is ModelTransportErrorCode.DEADLINE else code
        )
        self.code = normalized
        super().__init__(_ERROR_MESSAGES[normalized])

    def __repr__(self) -> str:
        return f"ModelInvocationError(code={self.code.value!r})"


class AuditedModelInvoker:
    """One exact configured-route attempt; no fallback, retry, or unaudited output."""

    def __init__(
        self,
        *,
        config: AnalysisProviderConfig,
        transport: JsonModelTransport,
        audit: ModelCallAuditPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(config) is not AnalysisProviderConfig:
            raise ValueError("model invoker configuration is invalid")
        config.__post_init__()
        if not hasattr(transport, "execute") or not all(
            hasattr(audit, method) for method in ("load", "claim", "persist")
        ):
            raise ValueError("model invoker capabilities are invalid")
        self._config = config
        self._transport = transport
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))

    def claim_for(
        self,
        envelope: SanitizedProviderEnvelope,
        output_contract: OutputContract,
    ) -> ModelCallClaim:
        """Build the deterministic pre-network claim without revealing prompt text."""

        self._validate_request(envelope, output_contract)
        stage = _STAGE_MAP[envelope.stage]
        role = _ROLE_MAP[envelope.role]
        round_number = 0 if envelope.round_number is None else envelope.round_number
        context_id = envelope.context_id or envelope.input_id
        call_id = derive_model_call_id(
            envelope.input_id,
            context_id,
            stage,
            role,
            round_number,
            1,
        )
        return ModelCallClaim(
            call_id=call_id,
            run_id=envelope.run_id,
            input_id=envelope.input_id,
            context_id=context_id,
            stage=stage,
            role=role,
            round_number=round_number,
            provider=ProviderKind(self._config.route_provider_kind),
            model=self._config.model_id,
            api_flavor=ApiFlavor(self._config.api_flavor),
            endpoint_policy_id=self._config.route_policy_id,
            route_ordinal=1,
            prompt_template_hash=envelope.prompt_template_hash,
            request_envelope_hash=envelope.envelope_hash,
            reasoning_requested=ReasoningRequested.MAX,
        )

    def invoke(
        self,
        envelope: SanitizedProviderEnvelope,
        output_contract: OutputContract,
    ) -> ModelOutput:
        """Return only a strict parsed result whose audit+result commit succeeded."""

        self._validate_request(envelope, output_contract)
        try:
            claim = self.claim_for(envelope, output_contract)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA) from None

        loaded = self._audit_load(claim.call_id)
        if loaded is not None:
            return self._replay(loaded, claim, envelope, output_contract)

        try:
            claim_result = self._audit.claim(claim)
        except Exception:
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None
        if type(claim_result) is not ModelCallClaimResult:
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT)
        try:
            claim_result.__post_init__()
        except (AttributeError, TypeError, ValueError):
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None
        if claim_result.decision is ModelCallClaimDecision.REPLAY:
            assert claim_result.attempt is not None
            return self._replay(
                claim_result.attempt,
                claim,
                envelope,
                output_contract,
            )
        if claim_result.decision is not ModelCallClaimDecision.CLAIMED:
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT)

        started_at = self._timestamp()
        try:
            validate_configured_route(envelope, self._config)
        except ModelInvocationError:
            completed_at = self._completed(started_at)
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.SCHEMA,
            )
            raise
        if started_at.value > envelope.deadline.value:
            completed_at = _after(started_at)
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.TIMEOUT,
            )
            raise ModelInvocationError(ModelTransportErrorCode.TIMEOUT)
        try:
            prompt = build_model_prompt(envelope, output_contract)
        except (AttributeError, KeyError, TypeError, ValueError):
            completed_at = self._completed(started_at)
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.SCHEMA,
            )
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA) from None
        request = JsonModelRequest(
            claim.call_id,
            (
                JsonModelMessage(JsonMessageRole.SYSTEM, prompt.system_text),
                JsonModelMessage(JsonMessageRole.DEVELOPER, prompt.developer_text),
                JsonModelMessage(JsonMessageRole.USER, prompt.user_text),
            ),
            envelope.deadline,
            self._config.max_output_tokens,
        )
        try:
            response = self._transport.execute(request)
        except ModelTransportError as error:
            completed_at = self._completed(started_at)
            if error.code is ModelTransportErrorCode.AUDIT:
                raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                _TRANSPORT_AUDIT_CODE[error.code],
            )
            raise ModelInvocationError(error.code) from None
        except Exception:
            completed_at = self._completed(started_at)
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.TRANSIENT,
            )
            raise ModelInvocationError(ModelTransportErrorCode.TRANSIENT) from None

        completed_at = self._completed(started_at)
        if type(response) is not JsonModelResponse:
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.PROTOCOL,
            )
            raise ModelInvocationError(ModelTransportErrorCode.PROTOCOL)
        try:
            response.__post_init__()
        except (AttributeError, TypeError, ValueError):
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.PROTOCOL,
            )
            raise ModelInvocationError(ModelTransportErrorCode.PROTOCOL) from None
        if response.model_id != self._config.model_id:
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.PROTOCOL,
                response=response,
            )
            raise ModelInvocationError(ModelTransportErrorCode.PROTOCOL)
        if completed_at.value > envelope.deadline.value:
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.TIMEOUT,
                response=response,
            )
            raise ModelInvocationError(ModelTransportErrorCode.TIMEOUT)
        if len(response.content.encode("utf-8")) > MAX_SERIALIZED_BYTES:
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.OVERSIZE,
                response=response,
            )
            raise ModelInvocationError(ModelTransportErrorCode.OVERSIZE)
        try:
            output = _parse_output(response.content, output_contract)
            _validate_output(output, envelope)
            result = CanonicalModelCallResult.from_contract(
                claim.call_id,
                _CONTRACT_KIND[output_contract],
                output,
            )
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._persist_failure(
                claim,
                started_at,
                completed_at,
                ModelCallErrorCode.SCHEMA,
                response=response,
            )
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA) from None

        try:
            record = self._record(
                claim,
                started_at,
                completed_at,
                outcome=ModelCallOutcome.SUCCESS,
                error_code=ModelCallErrorCode.NONE,
                response=response,
            )
        except (AttributeError, TypeError, ValueError):
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None
        self._audit_persist(record, result)
        return output

    def _validate_request(
        self,
        envelope: SanitizedProviderEnvelope,
        output_contract: OutputContract,
    ) -> None:
        if type(envelope) is not SanitizedProviderEnvelope:
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)
        try:
            envelope.validate_integrity()
            validate_configured_route(envelope, self._config)
        except (AttributeError, TypeError, ValueError):
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA) from None
        if type(output_contract) is not OutputContract:
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)
        if _STAGE_CONTRACT[envelope.stage] is not output_contract:
            raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)

    def _audit_load(self, call_id: RunId) -> StoredModelCallAttempt | None:
        try:
            loaded = self._audit.load(call_id)
        except Exception:
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None
        if loaded is not None and type(loaded) is not StoredModelCallAttempt:
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT)
        return loaded

    def _replay(
        self,
        attempt: StoredModelCallAttempt,
        claim: ModelCallClaim,
        envelope: SanitizedProviderEnvelope,
        output_contract: OutputContract,
    ) -> ModelOutput:
        try:
            attempt.__post_init__()
            if attempt.record.to_claim() != claim:
                raise ValueError("model-call claim collision")
        except (AttributeError, TypeError, ValueError):
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None
        if self._timestamp().value > envelope.deadline.value:
            raise ModelInvocationError(ModelTransportErrorCode.TIMEOUT)
        if attempt.record.outcome is ModelCallOutcome.FAILURE:
            code = _AUDIT_TRANSPORT_CODE.get(attempt.record.error_code)
            if code is None:
                raise ModelInvocationError(ModelTransportErrorCode.AUDIT)
            raise ModelInvocationError(code)
        if attempt.result is None or attempt.result.kind is not _CONTRACT_KIND[output_contract]:
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT)
        try:
            output = _parse_output(attempt.result.payload.to_json(), output_contract)
            _validate_output(output, envelope)
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None
        return output

    def _persist_failure(
        self,
        claim: ModelCallClaim,
        started_at: UtcTimestamp,
        completed_at: UtcTimestamp,
        error_code: ModelCallErrorCode,
        *,
        response: JsonModelResponse | None = None,
    ) -> None:
        try:
            record = self._record(
                claim,
                started_at,
                completed_at,
                outcome=ModelCallOutcome.FAILURE,
                error_code=error_code,
                response=response,
            )
        except (AttributeError, TypeError, ValueError):
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None
        self._audit_persist(record, None)

    def _record(
        self,
        claim: ModelCallClaim,
        started_at: UtcTimestamp,
        completed_at: UtcTimestamp,
        *,
        outcome: ModelCallOutcome,
        error_code: ModelCallErrorCode,
        response: JsonModelResponse | None,
    ) -> ModelCallAuditRecord:
        latency_ms = int((completed_at.value - started_at.value).total_seconds() * 1_000)
        token_counts_fit = (
            response is not None
            and max(
                response.prompt_tokens,
                response.completion_tokens,
            )
            <= 1_000_000
        )
        return ModelCallAuditRecord(
            call_id=claim.call_id,
            run_id=claim.run_id,
            input_id=claim.input_id,
            context_id=claim.context_id,
            stage=claim.stage,
            role=claim.role,
            round_number=claim.round_number,
            provider=claim.provider,
            model=claim.model,
            api_flavor=claim.api_flavor,
            endpoint_policy_id=claim.endpoint_policy_id,
            route_ordinal=claim.route_ordinal,
            prompt_template_hash=claim.prompt_template_hash,
            request_envelope_hash=claim.request_envelope_hash,
            response_hash=None if response is None else response.response_hash,
            reasoning_requested=claim.reasoning_requested,
            reasoning_effective=ReasoningEffective.UNKNOWN,
            token_counts_trusted=token_counts_fit,
            input_tokens=(
                response.prompt_tokens if token_counts_fit and response is not None else None
            ),
            output_tokens=(
                response.completion_tokens if token_counts_fit and response is not None else None
            ),
            latency_ms=latency_ms,
            started_at=started_at,
            completed_at=completed_at,
            outcome=outcome,
            error_code=error_code,
        )

    def _audit_persist(
        self,
        record: ModelCallAuditRecord,
        result: CanonicalModelCallResult | None,
    ) -> None:
        try:
            persisted = self._audit.persist(record, result)
            if type(persisted) is not bool:
                raise ValueError("model-call audit result is invalid")
            if not persisted:
                existing = self._audit.load(record.call_id)
                expected = StoredModelCallAttempt(record, result)
                if existing != expected:
                    raise ValueError("model-call audit collision")
        except Exception:
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None

    def _timestamp(self) -> UtcTimestamp:
        try:
            return UtcTimestamp(self._clock())
        except Exception:
            raise ModelInvocationError(ModelTransportErrorCode.AUDIT) from None

    def _completed(self, started_at: UtcTimestamp) -> UtcTimestamp:
        completed = self._timestamp()
        return completed if completed.value > started_at.value else _after(started_at)


def validate_configured_route(
    envelope: SanitizedProviderEnvelope,
    config: AnalysisProviderConfig,
) -> None:
    """Reject any envelope not bound to the exact configured analysis route."""

    if (
        type(envelope) is not SanitizedProviderEnvelope
        or type(config) is not AnalysisProviderConfig
        or envelope.versions.prompt != P3E_PROMPT_VERSION
        or envelope.versions.provider != config.route_provider_version
        or envelope.versions.model != config.route_model_version
    ):
        raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)


def validate_agnes_route(
    envelope: SanitizedProviderEnvelope,
    configured_model_id: str | None = None,
) -> None:
    """Deprecated legacy wrapper binding the package-default Agnes route."""

    from seven_lens.config.analysis_provider import package_default_analysis_provider_config

    config = package_default_analysis_provider_config()
    if configured_model_id is not None and configured_model_id != config.model_id:
        raise ModelInvocationError(ModelTransportErrorCode.SCHEMA)
    validate_configured_route(envelope, config)


def _after(timestamp: UtcTimestamp) -> UtcTimestamp:
    return UtcTimestamp(timestamp.value + timedelta(microseconds=1))


def _parse_output(content: str, output_contract: OutputContract) -> ModelOutput:
    if type(content) is not str:
        raise ValueError("model output must be exact JSON text")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key is rejected")
            result[key] = value
        return result

    def reject_constant(_: str) -> NoReturn:
        raise ValueError("non-finite JSON values are rejected")

    value = json.loads(
        content,
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )
    if type(value) is not dict:
        raise ValueError("model output must be one JSON object")
    JsonObject.from_value(value)
    if output_contract is OutputContract.ANALYST_REPORT:
        return AnalystReport.from_wire(value)
    if output_contract is OutputContract.DEBATE_ARGUMENT:
        return _debate_argument_from_wire(value)
    if output_contract is OutputContract.RESEARCH_CONCLUSION:
        return ResearchConclusion.from_wire(value)
    if output_contract is OutputContract.TRADER_PLAN:
        return TraderPlan.from_wire(value)
    if output_contract is OutputContract.RISK_ARGUMENT:
        return RiskArgument.from_wire(value)
    if output_contract is OutputContract.PORTFOLIO_PROPOSAL:
        return PortfolioProposal.from_wire(value)
    raise ValueError("model output contract is invalid")


def _debate_argument_from_wire(value: dict[str, object]) -> DebateArgument:
    fields = {
        "input_id",
        "packet_hash",
        "symbol",
        "side",
        "round_number",
        "argument",
        "evidence_refs",
    }
    if set(value) != fields:
        raise ValueError("debate argument fields are invalid")
    refs = value["evidence_refs"]
    if (
        type(value["input_id"]) is not str
        or type(value["packet_hash"]) is not str
        or len(value["packet_hash"]) != 64
        or any(character not in "0123456789abcdef" for character in value["packet_hash"])
        or type(value["symbol"]) is not str
        or not 1 <= len(value["symbol"]) <= 10
        or value["symbol"] != value["symbol"].upper()
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for character in value["symbol"]
        )
        or type(value["side"]) is not str
        or value["side"] not in {"BULL", "BEAR"}
        or type(value["round_number"]) is not int
        or value["round_number"] not in {1, 2}
        or type(refs) is not list
        or not refs
        or len(refs) > 64
        or any(type(ref) is not str or not 1 <= len(ref.encode()) <= 96 for ref in refs)
        or len(refs) != len(set(refs))
    ):
        raise ValueError("debate argument evidence is invalid")
    return DebateArgument(
        input_id=RunId.from_string(value["input_id"]),
        packet_hash=value["packet_hash"],
        symbol=value["symbol"],
        side=ProviderStage(value["side"]),
        round_number=value["round_number"],
        argument=cast(str, value["argument"]),
        evidence_refs=tuple(cast(list[str], refs)),
    )


def _validate_output(output: ModelOutput, envelope: SanitizedProviderEnvelope) -> None:
    citations = frozenset(envelope.citation_ids)
    if type(output) is AnalystReport:
        expected_role = AnalystRole(envelope.role.value)
        if (
            output.meta.run_id != envelope.run_id
            or output.meta.created_at != envelope.created_at
            or output.meta.producer_version != envelope.producer_version
            or output.report_id != envelope.output_id
            or output.input_id != envelope.input_id
            or output.role is not expected_role
            or output.symbol != envelope.symbol
            or not set(output.evidence_refs) <= citations
            or not set(output.counterevidence_refs) <= citations
        ):
            raise ValueError("analyst output identity or evidence is invalid")
        return
    if type(output) is DebateArgument:
        expected_side = ProviderStage(envelope.role.value)
        if (
            output.input_id != envelope.input_id
            or output.packet_hash != envelope.packet_hash
            or output.symbol != envelope.symbol
            or output.side is not expected_side
            or output.round_number != envelope.round_number
            or not set(output.evidence_refs) <= citations
        ):
            raise ValueError("debate output identity or evidence is invalid")
        return
    if type(output) is ResearchConclusion:
        if (
            output.meta.run_id != envelope.run_id
            or output.meta.created_at != envelope.created_at
            or output.meta.producer_version != envelope.producer_version
            or output.conclusion_id != envelope.output_id
            or output.input_id != envelope.input_id
            or output.symbol != envelope.symbol
            or not set(output.evidence_refs) <= citations
        ):
            raise ValueError("research output identity or evidence is invalid")
        return
    if type(output) is TraderPlan:
        if (
            output.meta.run_id != envelope.run_id
            or output.meta.created_at != envelope.created_at
            or output.meta.producer_version != envelope.producer_version
            or output.plan_id != envelope.output_id
            or output.input_id != envelope.input_id
            or output.symbol != envelope.symbol
            or not set(output.evidence_refs) <= citations
        ):
            raise ValueError("trader output identity or evidence is invalid")
        return
    if type(output) is RiskArgument:
        expected_viewpoint = RiskViewpoint(envelope.role.value)
        if (
            output.meta.run_id != envelope.run_id
            or output.meta.created_at != envelope.created_at
            or output.meta.producer_version != envelope.producer_version
            or output.argument_id != envelope.output_id
            or output.context_id != envelope.context_id
            or output.bundle_id != envelope.bundle_id
            or output.bundle_hash != envelope.bundle_hash
            or output.viewpoint is not expected_viewpoint
            or output.round_number != envelope.round_number
            or output.producer_version != envelope.producer_version
            or not set(output.evidence_refs) <= citations
        ):
            raise ValueError("risk output identity or evidence is invalid")
        return
    if type(output) is PortfolioProposal:
        expected_attempt = envelope.attempt
        if expected_attempt not in {1, 2}:
            raise ValueError("portfolio output attempt identity is invalid")
        request_citations = {
            citation for request in output.requests for citation in request.evidence_refs
        }
        if (
            output.meta.run_id != envelope.output_id
            or output.meta.created_at != envelope.created_at
            or output.meta.producer_version != envelope.producer_version
            or output.proposal_id != envelope.output_id
            or output.context_id != envelope.context_id
            or output.bundle_id != envelope.bundle_id
            or output.bundle_hash != envelope.bundle_hash
            or output.context_hash != envelope.context_hash
            or output.snapshot_hash != envelope.snapshot_hash
            or output.universe_hash != envelope.universe_hash
            or output.window is not envelope.window
            or output.attempt != expected_attempt
            or output.graph_version != envelope.versions.graph
            or output.prompt_version != envelope.versions.prompt
            or output.model_version != envelope.versions.model
            or output.provider_version != envelope.versions.provider
            or output.data_version != envelope.versions.data
            or output.memory_version != envelope.versions.memory
            or output.expiration_at.value > envelope.deadline.value
            or any(request.symbol not in envelope.allowed_symbols for request in output.requests)
            or not request_citations <= citations
        ):
            raise ValueError("portfolio output identity or evidence is invalid")
        if envelope.window is None:
            raise ValueError("portfolio output analysis window is invalid")
        if envelope.window.value == "EMERGENCY" and any(
            request.action in {ProposalAction.OPEN, ProposalAction.INCREASE}
            for request in output.requests
        ):
            raise ValueError("portfolio emergency output cannot add exposure")
        if output.superseded_proposal_id != envelope.superseded_proposal_id:
            raise ValueError("portfolio output retry lineage is invalid")
        return
    raise ValueError("model output type is invalid")
