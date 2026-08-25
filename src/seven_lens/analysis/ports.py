"""Capability-minimal provider contract and deterministic scripted fake."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seven_lens.analysis.contracts import AnalystReport, AnalystRole, ResearchConclusion, TraderPlan
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    SanitizedProviderEnvelope,
    derive_provider_output_id,
)
from seven_lens.domain.value_objects import RunId, UtcTimestamp
from seven_lens.security.sanitized_text import validate_sanitized_text

_HASH = re.compile(r"^[0-9a-f]{64}$")


class ProviderStage(StrEnum):
    ANALYST = "ANALYST"
    BULL = "BULL"
    BEAR = "BEAR"
    RESEARCH_MANAGER = "RESEARCH_MANAGER"
    TRADER = "TRADER"


@dataclass(frozen=True, slots=True, repr=False)
class ProviderRequest:
    stage: ProviderStage
    run_id: RunId
    input_id: RunId
    packet_hash: str
    snapshot_hash: str
    symbol: str
    deadline: UtcTimestamp
    evidence_refs: tuple[str, ...]
    envelope: SanitizedProviderEnvelope
    role: AnalystRole | None = None
    round_number: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.stage) is not ProviderStage
            or type(self.run_id) is not RunId
            or type(self.input_id) is not RunId
        ):
            raise ValueError("provider request identity is invalid")
        if type(self.packet_hash) is not str or _HASH.fullmatch(self.packet_hash) is None:
            raise ValueError("provider packet hash is invalid")
        if type(self.snapshot_hash) is not str or _HASH.fullmatch(self.snapshot_hash) is None:
            raise ValueError("provider snapshot hash is invalid")
        if type(self.symbol) is not str or not self.symbol:
            raise ValueError("provider symbol is invalid")
        if type(self.deadline) is not UtcTimestamp:
            raise ValueError("provider deadline is invalid")
        if type(self.evidence_refs) is not tuple or len(self.evidence_refs) > 64:
            raise ValueError("provider evidence view is invalid")
        if self.stage is ProviderStage.ANALYST:
            if type(self.role) is not AnalystRole or self.round_number is not None:
                raise ValueError("analyst request requires one exact role")
        elif self.stage in {ProviderStage.BULL, ProviderStage.BEAR}:
            if self.role is not None or self.round_number not in {1, 2}:
                raise ValueError("debate request requires round 1 or 2")
        elif self.role is not None or self.round_number is not None:
            raise ValueError("manager/trader request has no role or round")
        if type(self.envelope) is not SanitizedProviderEnvelope:
            raise ValueError("provider request envelope is invalid")
        self.envelope.validate_integrity()
        envelope_stage, envelope_role = _envelope_identity(self.stage, self.role)
        if (
            self.envelope.stage is not envelope_stage
            or self.envelope.role is not envelope_role
            or self.envelope.round_number != self.round_number
            or self.envelope.run_id != self.run_id
            or self.envelope.input_id != self.input_id
            or self.envelope.output_id
            != derive_provider_output_id(
                self.run_id,
                self.input_id,
                envelope_stage,
                envelope_role,
                self.round_number,
            )
            or self.envelope.packet_hash != self.packet_hash
            or self.envelope.snapshot_hash != self.snapshot_hash
            or self.envelope.symbol != self.symbol
            or self.envelope.deadline != self.deadline
            or self.symbol not in self.envelope.allowed_symbols
            or self.envelope.citation_ids != tuple(sorted(self.evidence_refs))
        ):
            raise ValueError("provider request envelope identity is invalid")

    def __repr__(self) -> str:
        return "ProviderRequest(<redacted>)"

    @property
    def key(self) -> str:
        role = "" if self.role is None else self.role.value
        round_text = "" if self.round_number is None else str(self.round_number)
        return f"{self.stage.value}:{role}:{round_text}"


def _envelope_identity(
    stage: ProviderStage,
    role: AnalystRole | None,
) -> tuple[EnvelopeStage, EnvelopeRole]:
    if stage is ProviderStage.ANALYST:
        if type(role) is not AnalystRole:
            raise ValueError("analyst request requires one exact role")
        return EnvelopeStage.ANALYST, EnvelopeRole(role.value)
    if stage is ProviderStage.BULL:
        return EnvelopeStage.INVESTMENT_DEBATE, EnvelopeRole.BULL
    if stage is ProviderStage.BEAR:
        return EnvelopeStage.INVESTMENT_DEBATE, EnvelopeRole.BEAR
    if stage is ProviderStage.RESEARCH_MANAGER:
        return EnvelopeStage.RESEARCH_MANAGER, EnvelopeRole.RESEARCH_MANAGER
    return EnvelopeStage.TRADER, EnvelopeRole.TRADER


@dataclass(frozen=True, slots=True)
class DebateArgument:
    input_id: RunId
    packet_hash: str
    symbol: str
    side: ProviderStage
    round_number: int
    argument: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.side not in {ProviderStage.BULL, ProviderStage.BEAR}:
            raise ValueError("debate side is invalid")
        if self.round_number not in {1, 2}:
            raise ValueError("debate round is invalid")
        validate_sanitized_text(self.argument, "debate argument", maximum=2_048)
        if type(self.evidence_refs) is not tuple or not self.evidence_refs:
            raise ValueError("debate argument requires evidence")


ProviderOutput = AnalystReport | DebateArgument | ResearchConclusion | TraderPlan


class AnalysisProvider(Protocol):
    def execute(self, request: ProviderRequest) -> ProviderOutput: ...


class ScriptedAnalysisProvider:
    """Deterministic fake with no network, secret, shell or filesystem capability."""

    def __init__(self, outputs: dict[str, ProviderOutput | BaseException]) -> None:
        if type(outputs) is not dict:
            raise ValueError("scripted outputs require an exact dict")
        self._outputs = dict(outputs)
        self.calls: list[str] = []

    def execute(self, request: ProviderRequest) -> ProviderOutput:
        self.calls.append(request.key)
        if request.key not in self._outputs:
            raise RuntimeError("scripted provider output is missing")
        result = self._outputs[request.key]
        if isinstance(result, BaseException):
            raise result
        return result
