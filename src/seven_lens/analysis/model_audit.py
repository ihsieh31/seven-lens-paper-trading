"""Strict, payload-free metadata for authoritative P3-E model-call auditing."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID

from seven_lens.analysis.contracts import (
    AnalystReport,
    ResearchConclusion,
    TraderPlan,
)
from seven_lens.analysis.ports import DebateArgument, ProviderStage
from seven_lens.analysis.proposal_contracts import PortfolioProposal, RiskArgument
from seven_lens.config.provider import (
    ApiFlavor,
    ProviderKind,
    ReasoningEffective,
    ReasoningRequested,
)
from seven_lens.domain.json_values import JsonObject
from seven_lens.domain.value_objects import RunId, UtcTimestamp

__all__ = [
    "CanonicalModelCallResult",
    "ModelCallAuditRecord",
    "ModelCallClaim",
    "ModelCallClaimDecision",
    "ModelCallClaimResult",
    "ModelCallErrorCode",
    "ModelCallOutcome",
    "ModelCallResultKind",
    "ModelCallRole",
    "ModelCallStage",
    "ReasoningEffective",
    "ReasoningRequested",
    "StoredModelCallAttempt",
    "derive_model_call_id",
]

_CALL_ID_DOMAIN: Final = "seven-lens.p3e.model-call.v1"
_HASH_LENGTH: Final = 64
_MAX_LATENCY_MS: Final = 900_000
_MAX_TOKENS: Final = 1_000_000


class ModelCallStage(StrEnum):
    ANALYST = "ANALYST"
    INVESTMENT_DEBATE = "INVESTMENT_DEBATE"
    RESEARCH_MANAGER = "RESEARCH_MANAGER"
    TRADER = "TRADER"
    RISK_DEBATE = "RISK_DEBATE"
    PORTFOLIO_MANAGER = "PORTFOLIO_MANAGER"


class ModelCallRole(StrEnum):
    TECHNICAL = "TECHNICAL"
    FUNDAMENTALS = "FUNDAMENTALS"
    NEWS = "NEWS"
    SENTIMENT = "SENTIMENT"
    BULL = "BULL"
    BEAR = "BEAR"
    RESEARCH_MANAGER = "RESEARCH_MANAGER"
    TRADER = "TRADER"
    AGGRESSIVE = "AGGRESSIVE"
    CONSERVATIVE = "CONSERVATIVE"
    NEUTRAL = "NEUTRAL"
    PORTFOLIO_MANAGER = "PORTFOLIO_MANAGER"


class ModelCallOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class ModelCallErrorCode(StrEnum):
    NONE = "NONE"
    CONFIG = "CONFIG"
    AUTH = "AUTH"
    PERMANENT = "PERMANENT"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT = "TRANSIENT"
    TIMEOUT = "TIMEOUT"
    PROTOCOL = "PROTOCOL"
    SCHEMA = "SCHEMA"
    OVERSIZE = "OVERSIZE"
    DEADLINE = "DEADLINE"


class ModelCallClaimDecision(StrEnum):
    CLAIMED = "CLAIMED"
    IN_PROGRESS = "IN_PROGRESS"
    REPLAY = "REPLAY"


class ModelCallResultKind(StrEnum):
    ANALYST_REPORT = "ANALYST_REPORT"
    DEBATE_ARGUMENT = "DEBATE_ARGUMENT"
    RESEARCH_CONCLUSION = "RESEARCH_CONCLUSION"
    TRADER_PLAN = "TRADER_PLAN"
    RISK_ARGUMENT = "RISK_ARGUMENT"
    PORTFOLIO_PROPOSAL = "PORTFOLIO_PROPOSAL"


_STAGE_ROLE_ROUNDS: Final = {
    ModelCallStage.ANALYST: (
        frozenset(
            {
                ModelCallRole.TECHNICAL,
                ModelCallRole.FUNDAMENTALS,
                ModelCallRole.NEWS,
                ModelCallRole.SENTIMENT,
            }
        ),
        frozenset({0}),
    ),
    ModelCallStage.INVESTMENT_DEBATE: (
        frozenset({ModelCallRole.BULL, ModelCallRole.BEAR}),
        frozenset({1, 2}),
    ),
    ModelCallStage.RESEARCH_MANAGER: (
        frozenset({ModelCallRole.RESEARCH_MANAGER}),
        frozenset({0}),
    ),
    ModelCallStage.TRADER: (
        frozenset({ModelCallRole.TRADER}),
        frozenset({0}),
    ),
    ModelCallStage.RISK_DEBATE: (
        frozenset(
            {
                ModelCallRole.AGGRESSIVE,
                ModelCallRole.CONSERVATIVE,
                ModelCallRole.NEUTRAL,
            }
        ),
        frozenset({1, 2}),
    ),
    ModelCallStage.PORTFOLIO_MANAGER: (
        frozenset({ModelCallRole.PORTFOLIO_MANAGER}),
        frozenset({0}),
    ),
}

_STAGE_RESULT_KIND: Final = {
    ModelCallStage.ANALYST: ModelCallResultKind.ANALYST_REPORT,
    ModelCallStage.INVESTMENT_DEBATE: ModelCallResultKind.DEBATE_ARGUMENT,
    ModelCallStage.RESEARCH_MANAGER: ModelCallResultKind.RESEARCH_CONCLUSION,
    ModelCallStage.TRADER: ModelCallResultKind.TRADER_PLAN,
    ModelCallStage.RISK_DEBATE: ModelCallResultKind.RISK_ARGUMENT,
    ModelCallStage.PORTFOLIO_MANAGER: ModelCallResultKind.PORTFOLIO_PROPOSAL,
}

_RESULT_PARSERS: Final = {
    ModelCallResultKind.ANALYST_REPORT: AnalystReport.from_wire,
    ModelCallResultKind.DEBATE_ARGUMENT: lambda value: _parse_debate_argument(value),
    ModelCallResultKind.RESEARCH_CONCLUSION: ResearchConclusion.from_wire,
    ModelCallResultKind.TRADER_PLAN: TraderPlan.from_wire,
    ModelCallResultKind.RISK_ARGUMENT: RiskArgument.from_wire,
    ModelCallResultKind.PORTFOLIO_PROPOSAL: PortfolioProposal.from_wire,
}

_RESULT_TYPES: Final = {
    ModelCallResultKind.ANALYST_REPORT: AnalystReport,
    ModelCallResultKind.DEBATE_ARGUMENT: DebateArgument,
    ModelCallResultKind.RESEARCH_CONCLUSION: ResearchConclusion,
    ModelCallResultKind.TRADER_PLAN: TraderPlan,
    ModelCallResultKind.RISK_ARGUMENT: RiskArgument,
    ModelCallResultKind.PORTFOLIO_PROPOSAL: PortfolioProposal,
}


def _parse_debate_argument(value: object) -> DebateArgument:
    expected = {
        "input_id",
        "packet_hash",
        "symbol",
        "side",
        "round_number",
        "argument",
        "evidence_refs",
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("model-call debate argument fields are invalid")
    item = value
    packet_hash = item["packet_hash"]
    _hash(packet_hash, "debate packet")
    symbol = item["symbol"]
    if (
        type(symbol) is not str
        or not 1 <= len(symbol) <= 10
        or symbol != symbol.upper()
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for character in symbol)
    ):
        raise ValueError("model-call debate argument symbol is invalid")
    refs = item["evidence_refs"]
    if (
        type(refs) is not list
        or not refs
        or len(refs) > 64
        or any(type(ref) is not str or not 1 <= len(ref.encode()) <= 96 for ref in refs)
        or len(refs) != len(set(refs))
    ):
        raise ValueError("model-call debate argument evidence is invalid")
    if type(item["side"]) is not str or item["side"] not in {"BULL", "BEAR"}:
        raise ValueError("model-call debate argument side is invalid")
    if type(item["round_number"]) is not int or item["round_number"] not in {1, 2}:
        raise ValueError("model-call debate argument round is invalid")
    return DebateArgument(
        RunId.from_string(item["input_id"]),
        packet_hash,
        symbol,
        ProviderStage(item["side"]),
        item["round_number"],
        item["argument"],
        tuple(refs),
    )


class _WireContract(Protocol):
    def to_wire(self) -> object: ...


def _contract_wire(value: object) -> object:
    if type(value) is DebateArgument:
        return {
            "input_id": str(value.input_id),
            "packet_hash": value.packet_hash,
            "symbol": value.symbol,
            "side": value.side.value,
            "round_number": value.round_number,
            "argument": value.argument,
            "evidence_refs": list(value.evidence_refs),
        }
    return cast(_WireContract, value).to_wire()


def _hash(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if (
        type(value) is not str
        or len(value) != _HASH_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"model-call audit {field} hash is invalid")
    return value


def _bounded_integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
    optional: bool = False,
) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"model-call audit {field} is invalid")
    return value


def derive_model_call_id(
    input_id: RunId,
    context_id: RunId,
    stage: ModelCallStage,
    role: ModelCallRole,
    round_number: int,
    route_ordinal: int,
) -> RunId:
    """Derive the sole identity for one logical primary/fallback provider attempt."""
    if type(input_id) is not RunId or type(context_id) is not RunId:
        raise ValueError("model-call audit identity material is invalid")
    if type(stage) is not ModelCallStage or type(role) is not ModelCallRole:
        raise ValueError("model-call audit identity material is invalid")
    _bounded_integer(round_number, "round number", minimum=0, maximum=2)
    _bounded_integer(route_ordinal, "route ordinal", minimum=1, maximum=1)
    material = b"\x00".join(
        value.encode("utf-8")
        for value in (
            _CALL_ID_DOMAIN,
            str(input_id),
            str(context_id),
            stage.value,
            role.value,
            str(round_number),
            str(route_ordinal),
        )
    )
    return RunId(UUID(bytes=hashlib.sha256(material).digest()[:16], version=4))


@dataclass(frozen=True, slots=True)
class ModelCallClaim:
    """Durable pre-network intent; an unclosed claim is never automatically retried."""

    call_id: RunId
    run_id: RunId
    input_id: RunId
    context_id: RunId
    stage: ModelCallStage
    role: ModelCallRole
    round_number: int
    provider: ProviderKind
    model: str
    api_flavor: ApiFlavor
    endpoint_policy_id: str
    route_ordinal: int
    prompt_template_hash: str
    request_envelope_hash: str
    reasoning_requested: ReasoningRequested

    def __post_init__(self) -> None:
        for field in ("call_id", "run_id", "input_id", "context_id"):
            if type(getattr(self, field)) is not RunId:
                raise ValueError(f"model-call claim {field} identity is invalid")
        if type(self.stage) is not ModelCallStage or type(self.role) is not ModelCallRole:
            raise ValueError("model-call claim stage role round closure is invalid")
        _bounded_integer(self.round_number, "round number", minimum=0, maximum=2)
        allowed_roles, allowed_rounds = _STAGE_ROLE_ROUNDS[self.stage]
        if self.role not in allowed_roles or self.round_number not in allowed_rounds:
            raise ValueError("model-call claim stage role round closure is invalid")
        _bounded_integer(self.route_ordinal, "route ordinal", minimum=1, maximum=1)
        if self.call_id != derive_model_call_id(
            self.input_id,
            self.context_id,
            self.stage,
            self.role,
            self.round_number,
            self.route_ordinal,
        ):
            raise ValueError("model-call claim identity is invalid")
        if type(self.provider) is not ProviderKind or self.provider is not ProviderKind.AGNES:
            raise ValueError("model-call claim provider is invalid")
        if self.model != "agnes-2.5-flash":
            raise ValueError("model-call claim model is invalid")
        if (
            type(self.api_flavor) is not ApiFlavor
            or self.api_flavor is not ApiFlavor.CHAT_COMPLETIONS
        ):
            raise ValueError("model-call claim API flavor is invalid")
        if self.endpoint_policy_id != "p3e-agnes-2.5-flash-only-v1":
            raise ValueError("model-call claim endpoint policy is invalid")
        _hash(self.prompt_template_hash, "claim prompt template")
        _hash(self.request_envelope_hash, "claim request envelope")
        if (
            type(self.reasoning_requested) is not ReasoningRequested
            or self.reasoning_requested is not ReasoningRequested.MAX
        ):
            raise ValueError("model-call claim requested reasoning is invalid")


@dataclass(frozen=True, slots=True)
class ModelCallClaimResult:
    decision: ModelCallClaimDecision
    attempt: StoredModelCallAttempt | None

    def __post_init__(self) -> None:
        if type(self.decision) is not ModelCallClaimDecision:
            raise ValueError("model-call claim decision is invalid")
        if (self.decision is ModelCallClaimDecision.REPLAY) != (self.attempt is not None):
            raise ValueError("model-call claim replay authority is invalid")


@dataclass(frozen=True, slots=True)
class ModelCallAuditRecord:
    """One append-only attempt record; it intentionally cannot carry prompt/body data."""

    call_id: RunId
    run_id: RunId
    input_id: RunId
    context_id: RunId
    stage: ModelCallStage
    role: ModelCallRole
    round_number: int
    provider: ProviderKind
    model: str
    api_flavor: ApiFlavor
    endpoint_policy_id: str
    route_ordinal: int
    prompt_template_hash: str
    request_envelope_hash: str
    response_hash: str | None
    reasoning_requested: ReasoningRequested
    reasoning_effective: ReasoningEffective
    token_counts_trusted: bool
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    started_at: UtcTimestamp
    completed_at: UtcTimestamp
    outcome: ModelCallOutcome
    error_code: ModelCallErrorCode

    def __post_init__(self) -> None:
        for field in ("call_id", "run_id", "input_id", "context_id"):
            if type(getattr(self, field)) is not RunId:
                raise ValueError(f"model-call audit {field} identity is invalid")
        if type(self.stage) is not ModelCallStage or type(self.role) is not ModelCallRole:
            raise ValueError("model-call audit stage role round closure is invalid")
        _bounded_integer(self.round_number, "round number", minimum=0, maximum=2)
        allowed_roles, allowed_rounds = _STAGE_ROLE_ROUNDS[self.stage]
        if self.role not in allowed_roles or self.round_number not in allowed_rounds:
            raise ValueError("model-call audit stage role round closure is invalid")
        _bounded_integer(self.route_ordinal, "route ordinal", minimum=1, maximum=1)
        expected_call_id = derive_model_call_id(
            self.input_id,
            self.context_id,
            self.stage,
            self.role,
            self.round_number,
            self.route_ordinal,
        )
        if self.call_id != expected_call_id:
            raise ValueError("model-call audit call identity is invalid")
        if type(self.provider) is not ProviderKind or self.provider is not ProviderKind.AGNES:
            raise ValueError("model-call audit provider is invalid")
        if self.model != "agnes-2.5-flash":
            raise ValueError("model-call audit model is invalid")
        if (
            type(self.api_flavor) is not ApiFlavor
            or self.api_flavor is not ApiFlavor.CHAT_COMPLETIONS
        ):
            raise ValueError("model-call audit API flavor is invalid")
        if self.endpoint_policy_id != "p3e-agnes-2.5-flash-only-v1":
            raise ValueError("model-call audit endpoint policy is invalid")
        _hash(self.prompt_template_hash, "prompt template")
        _hash(self.request_envelope_hash, "request envelope")
        _hash(self.response_hash, "response", optional=True)
        if type(self.reasoning_requested) is not ReasoningRequested:
            raise ValueError("model-call audit requested reasoning is invalid")
        if type(self.reasoning_effective) is not ReasoningEffective:
            raise ValueError("model-call audit effective reasoning is invalid")
        if type(self.token_counts_trusted) is not bool:
            raise ValueError("model-call audit trusted token marker is invalid")
        _bounded_integer(
            self.input_tokens,
            "input token count",
            minimum=0,
            maximum=_MAX_TOKENS,
            optional=True,
        )
        _bounded_integer(
            self.output_tokens,
            "output token count",
            minimum=0,
            maximum=_MAX_TOKENS,
            optional=True,
        )
        tokens_present = self.input_tokens is not None and self.output_tokens is not None
        if self.token_counts_trusted is not tokens_present:
            raise ValueError("model-call audit trusted token counts are inconsistent")
        _bounded_integer(self.latency_ms, "latency", minimum=0, maximum=_MAX_LATENCY_MS)
        if type(self.started_at) is not UtcTimestamp or type(self.completed_at) is not UtcTimestamp:
            raise ValueError("model-call audit timestamps are invalid")
        if self.completed_at.value <= self.started_at.value:
            raise ValueError("model-call audit timestamps are invalid")
        measured_ms = (self.completed_at.value - self.started_at.value).total_seconds() * 1_000
        if abs(measured_ms - self.latency_ms) > 1:
            raise ValueError("model-call audit latency does not match its timestamps")
        if (
            type(self.outcome) is not ModelCallOutcome
            or type(self.error_code) is not ModelCallErrorCode
        ):
            raise ValueError("model-call audit outcome is invalid")
        if self.outcome is ModelCallOutcome.SUCCESS:
            if self.error_code is not ModelCallErrorCode.NONE:
                raise ValueError("model-call audit outcome is inconsistent")
            if self.response_hash is None:
                raise ValueError("model-call audit success requires a response hash")
        elif self.error_code is ModelCallErrorCode.NONE:
            raise ValueError("model-call audit outcome is inconsistent")

    def to_metadata(self) -> dict[str, object]:
        """Return exact bounded metadata for diagnostics and repository adaptation."""
        return {
            "call_id": str(self.call_id),
            "run_id": str(self.run_id),
            "input_id": str(self.input_id),
            "context_id": str(self.context_id),
            "stage": self.stage.value,
            "role": self.role.value,
            "round_number": self.round_number,
            "provider": self.provider.value,
            "model": self.model,
            "api_flavor": self.api_flavor.value,
            "endpoint_policy_id": self.endpoint_policy_id,
            "route_ordinal": self.route_ordinal,
            "prompt_template_hash": self.prompt_template_hash,
            "request_envelope_hash": self.request_envelope_hash,
            "response_hash": self.response_hash,
            "reasoning_requested": self.reasoning_requested.value,
            "reasoning_effective": self.reasoning_effective.value,
            "token_counts_trusted": self.token_counts_trusted,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "started_at": str(self.started_at),
            "completed_at": str(self.completed_at),
            "outcome": self.outcome.value,
            "error_code": self.error_code.value,
        }

    def to_claim(self) -> ModelCallClaim:
        return ModelCallClaim(
            self.call_id,
            self.run_id,
            self.input_id,
            self.context_id,
            self.stage,
            self.role,
            self.round_number,
            self.provider,
            self.model,
            self.api_flavor,
            self.endpoint_policy_id,
            self.route_ordinal,
            self.prompt_template_hash,
            self.request_envelope_hash,
            self.reasoning_requested,
        )


@dataclass(frozen=True, slots=True)
class CanonicalModelCallResult:
    """A strict domain output, never the provider's raw response or prompt."""

    call_id: RunId
    kind: ModelCallResultKind
    result_hash: str
    payload: JsonObject

    def __post_init__(self) -> None:
        if type(self.call_id) is not RunId:
            raise ValueError("model-call result call identity is invalid")
        if type(self.kind) is not ModelCallResultKind:
            raise ValueError("model-call result kind is invalid")
        _hash(self.result_hash, "result")
        if type(self.payload) is not JsonObject:
            raise ValueError("model-call result payload is invalid")
        canonical = self.payload.to_json()
        if hashlib.sha256(canonical.encode()).hexdigest() != self.result_hash:
            raise ValueError("model-call result hash does not match canonical payload")
        parsed = _RESULT_PARSERS[self.kind](self.payload.to_dict())
        if JsonObject.from_value(_contract_wire(parsed)) != self.payload:
            raise ValueError("model-call result payload is not the exact parsed contract")

    @classmethod
    def from_contract(
        cls,
        call_id: RunId,
        kind: ModelCallResultKind,
        value: object,
    ) -> CanonicalModelCallResult:
        if type(kind) is not ModelCallResultKind:
            raise ValueError("model-call result kind is invalid")
        expected_type = _RESULT_TYPES[kind]
        if type(value) is not expected_type:
            raise ValueError("model-call result contract type is invalid")
        payload = JsonObject.from_value(_contract_wire(value))
        return cls(
            call_id,
            kind,
            hashlib.sha256(payload.to_json().encode()).hexdigest(),
            payload,
        )


@dataclass(frozen=True, slots=True)
class StoredModelCallAttempt:
    """Atomic durable attempt returned for network-free crash/resume replay."""

    record: ModelCallAuditRecord
    result: CanonicalModelCallResult | None

    def __post_init__(self) -> None:
        if type(self.record) is not ModelCallAuditRecord:
            raise ValueError("stored model-call audit record is invalid")
        if self.record.outcome is ModelCallOutcome.SUCCESS:
            if type(self.result) is not CanonicalModelCallResult:
                raise ValueError("successful model-call audit requires canonical result authority")
            if (
                self.result.call_id != self.record.call_id
                or self.result.kind is not _STAGE_RESULT_KIND[self.record.stage]
            ):
                raise ValueError("model-call audit result identity is invalid")
        elif self.result is not None:
            raise ValueError("failed model-call audit cannot carry result authority")
