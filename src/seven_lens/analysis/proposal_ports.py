"""Capability-minimal P3-D proposal provider contract and deterministic scripted fake."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from seven_lens.analysis.contracts import AnalysisWindow
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    SanitizedProviderEnvelope,
)
from seven_lens.analysis.proposal_contracts import (
    MAX_ALLOWED_SYMBOLS,
    MAX_BUNDLE_CITATIONS,
    PortfolioProposal,
    RiskArgument,
)
from seven_lens.domain.value_objects import RunId, UtcTimestamp

_HASH = re.compile(r"^[0-9a-f]{64}$")


class ProposalProviderStage(StrEnum):
    AGGRESSIVE = "AGGRESSIVE"
    CONSERVATIVE = "CONSERVATIVE"
    NEUTRAL = "NEUTRAL"
    PORTFOLIO_MANAGER = "PORTFOLIO_MANAGER"
    PORTFOLIO_MANAGER_RETRY = "PORTFOLIO_MANAGER_RETRY"


_VIEWPOINT_STAGES: Final = frozenset(
    {
        ProposalProviderStage.AGGRESSIVE,
        ProposalProviderStage.CONSERVATIVE,
        ProposalProviderStage.NEUTRAL,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class ProposalRequest:
    """A frozen, de-identified proposal request carrying exact hashes and boundaries."""

    stage: ProposalProviderStage
    run_id: RunId
    input_id: RunId
    output_id: RunId
    context_id: RunId
    bundle_id: RunId
    bundle_hash: str
    context_hash: str
    snapshot_hash: str
    universe_hash: str
    window: AnalysisWindow
    deadline: UtcTimestamp
    created_at: UtcTimestamp
    attempt: int
    superseded_proposal_id: RunId | None
    superseded_proposal_hash: str | None
    round_number: int | None
    allowed_symbols: tuple[str, ...]
    citation_ids: tuple[str, ...]
    envelope: SanitizedProviderEnvelope

    def __post_init__(self) -> None:
        if type(self.stage) is not ProposalProviderStage:
            raise ValueError("provider request stage is invalid")
        for name in ("run_id", "input_id", "output_id", "context_id", "bundle_id"):
            if type(getattr(self, name)) is not RunId:
                raise ValueError(f"provider request {name} is invalid")
        for name in ("bundle_hash", "context_hash", "snapshot_hash", "universe_hash"):
            value = getattr(self, name)
            if type(value) is not str or _HASH.fullmatch(value) is None:
                raise ValueError(f"provider request {name} is invalid")
        if type(self.window) is not AnalysisWindow:
            raise ValueError("provider request window is invalid")
        if type(self.deadline) is not UtcTimestamp:
            raise ValueError("provider request deadline is invalid")
        if type(self.created_at) is not UtcTimestamp:
            raise ValueError("provider request creation time is invalid")
        if (
            type(self.attempt) is not int
            or self.attempt not in {1, 2}
            or (self.attempt == 2) != (self.superseded_proposal_id is not None)
            or (self.attempt == 2) != (self.superseded_proposal_hash is not None)
        ):
            raise ValueError("provider request attempt lineage is invalid")
        if (
            self.superseded_proposal_id is not None
            and type(self.superseded_proposal_id) is not RunId
        ):
            raise ValueError("provider request superseded proposal id is invalid")
        if self.superseded_proposal_hash is not None and (
            type(self.superseded_proposal_hash) is not str
            or _HASH.fullmatch(self.superseded_proposal_hash) is None
        ):
            raise ValueError("provider request superseded proposal hash is invalid")
        if self.stage in _VIEWPOINT_STAGES:
            if self.round_number not in {1, 2}:
                raise ValueError("viewpoint request requires round 1 or 2")
        elif self.round_number is not None:
            raise ValueError("portfolio manager request has no round")
        if (
            type(self.allowed_symbols) is not tuple
            or not self.allowed_symbols
            or len(self.allowed_symbols) > MAX_ALLOWED_SYMBOLS
        ):
            raise ValueError("provider request allowed symbol view is invalid")
        if (
            type(self.citation_ids) is not tuple
            or not self.citation_ids
            or len(self.citation_ids) > MAX_BUNDLE_CITATIONS
        ):
            raise ValueError("provider request citation view is invalid")
        if type(self.envelope) is not SanitizedProviderEnvelope:
            raise ValueError("proposal provider envelope is invalid")
        self.envelope.validate_integrity()
        envelope_stage, envelope_role = _envelope_identity(self.stage)
        if (
            self.envelope.stage is not envelope_stage
            or self.envelope.role is not envelope_role
            or self.envelope.round_number != self.round_number
            or self.envelope.run_id != self.run_id
            or self.envelope.input_id != self.input_id
            or self.envelope.output_id != self.output_id
            or self.envelope.context_id != self.context_id
            or self.envelope.bundle_id != self.bundle_id
            or self.envelope.packet_hash is not None
            or self.envelope.bundle_hash != self.bundle_hash
            or self.envelope.context_hash != self.context_hash
            or self.envelope.snapshot_hash != self.snapshot_hash
            or self.envelope.universe_hash != self.universe_hash
            or self.envelope.created_at != self.created_at
            or self.envelope.deadline != self.deadline
            or self.envelope.window is not self.window
            or self.envelope.attempt != self.attempt
            or self.envelope.superseded_proposal_id != self.superseded_proposal_id
            or self.envelope.superseded_proposal_hash != self.superseded_proposal_hash
            or self.envelope.symbol is not None
            or self.envelope.allowed_symbols != self.allowed_symbols
            or self.envelope.citation_ids != self.citation_ids
        ):
            raise ValueError("proposal provider envelope identity is invalid")

    def __repr__(self) -> str:
        return "ProposalRequest(<redacted>)"

    @property
    def key(self) -> str:
        round_text = "" if self.round_number is None else str(self.round_number)
        return f"{self.stage.value}:{round_text}"


def _envelope_identity(
    stage: ProposalProviderStage,
) -> tuple[EnvelopeStage, EnvelopeRole]:
    roles = {
        ProposalProviderStage.AGGRESSIVE: EnvelopeRole.AGGRESSIVE,
        ProposalProviderStage.CONSERVATIVE: EnvelopeRole.CONSERVATIVE,
        ProposalProviderStage.NEUTRAL: EnvelopeRole.NEUTRAL,
    }
    if stage in roles:
        return EnvelopeStage.RISK_DEBATE, roles[stage]
    if stage is ProposalProviderStage.PORTFOLIO_MANAGER_RETRY:
        return EnvelopeStage.PORTFOLIO_MANAGER, EnvelopeRole.PORTFOLIO_MANAGER_RETRY
    return EnvelopeStage.PORTFOLIO_MANAGER, EnvelopeRole.PORTFOLIO_MANAGER


ProposalOutput = RiskArgument | PortfolioProposal


class ProposalProvider(Protocol):
    def execute(self, request: ProposalRequest) -> ProposalOutput: ...


class ScriptedProposalProvider:
    """Deterministic fake with no network, filesystem, shell, secret, DB or broker capability."""

    def __init__(self, outputs: dict[str, ProposalOutput | BaseException]) -> None:
        if type(outputs) is not dict:
            raise ValueError("scripted outputs require an exact dict")
        allowed_keys = {
            "AGGRESSIVE:1",
            "AGGRESSIVE:2",
            "CONSERVATIVE:1",
            "CONSERVATIVE:2",
            "NEUTRAL:1",
            "NEUTRAL:2",
            "PORTFOLIO_MANAGER:",
            "PORTFOLIO_MANAGER_RETRY:",
        }
        if any(type(key) is not str or key not in allowed_keys for key in outputs):
            raise ValueError("scripted outputs contain an unknown request key")
        if any(
            type(value) not in {RiskArgument, PortfolioProposal}
            and not isinstance(value, BaseException)
            for value in outputs.values()
        ):
            raise ValueError("scripted outputs contain an invalid result type")
        self._outputs = dict(outputs)
        self._consumed: set[str] = set()
        self.calls: list[str] = []

    def execute(self, request: ProposalRequest) -> ProposalOutput:
        key = request.key
        self.calls.append(key)
        if key not in self._outputs:
            raise RuntimeError("scripted provider output is missing")
        if key in self._consumed:
            raise RuntimeError("scripted provider output was already consumed")
        self._consumed.add(key)
        result = self._outputs[key]
        if isinstance(result, BaseException):
            raise result
        return result
