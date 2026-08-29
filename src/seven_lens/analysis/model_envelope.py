"""Canonical, bounded, de-identified input for P3-E model workers.

The envelope is the complete capability boundary: a provider receives this immutable
value, never a repository, filesystem, broker, callback, or credential capability.
Free text from evidence and earlier model stages remains structurally confined to the
``untrusted_data`` and ``prior_outputs`` sections.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from seven_lens.analysis.contracts import (
    AnalysisStatus,
    AnalysisWindow,
    AnalystReport,
    AnalystRole,
    InvestmentDebateState,
    PortfolioSnapshot,
    ResearchConclusion,
    RiskRejectionFeedback,
)
from seven_lens.analysis.proposal_contracts import (
    RiskArgument,
    RiskDebateState,
    RiskViewpoint,
    derive_argument_id,
    derive_proposal_id,
    derive_proposal_run_id,
)
from seven_lens.domain.json_values import JsonValue
from seven_lens.domain.value_objects import RunId, UtcTimestamp
from seven_lens.security.sanitized_text import validate_sanitized_text

MAX_ALLOWED_SYMBOLS: Final = 27
MAX_ALLOWED_CITATIONS: Final = 864
MAX_PRIOR_OUTPUTS: Final = 16
MAX_SECTION_DEPTH: Final = 16
MAX_SECTION_NODES: Final = 4_096
MAX_ENVELOPE_NODES: Final = 8_192
MAX_MAP_MEMBERS: Final = 128
MAX_LIST_ITEMS: Final = 1_024
MAX_STRING_BYTES: Final = 8_192
MAX_KEY_BYTES: Final = 128
MAX_CANONICAL_ENVELOPE_BYTES: Final = 65_536
MAX_ESTIMATED_TOKEN_UPPER_BOUND: Final = 65_536

_HASH = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,95}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")
_TEMPLATE_ID = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,95}$")
_OUTPUT_ID_DOMAIN: Final = "seven-lens.p3e.provider-output.v1"

_PROHIBITED_KEYS: Final = frozenset(
    {
        "account",
        "account_id",
        "account_name",
        "account_number",
        "api_key",
        "authorization",
        "broker",
        "broker_order_id",
        "canonical_url",
        "callback",
        "client_order_id",
        "command",
        "credential",
        "database",
        "dsn",
        "env",
        "environment",
        "endpoint",
        "file",
        "filesystem",
        "header",
        "headers",
        "keychain",
        "name",
        "network",
        "path",
        "password",
        "raw_broker_payload",
        "raw_prompt",
        "raw_response",
        "secret",
        "secret_key",
        "secret_ref",
        "service",
        "shell",
        "token",
        "tool",
        "tool_definition",
        "tools",
        "url",
        "uri",
        "user_name",
        "username",
        "base_url",
    }
)

_PROHIBITED_KEY_TOKENS: Final = frozenset(
    {
        "account",
        "authorization",
        "bearer",
        "broker",
        "callback",
        "client",
        "command",
        "credential",
        "customer",
        "database",
        "dsn",
        "endpoint",
        "env",
        "environment",
        "file",
        "filesystem",
        "header",
        "email",
        "keychain",
        "network",
        "owner",
        "password",
        "secret",
        "service",
        "shell",
        "address",
        "phone",
        "token",
        "tool",
        "url",
        "uri",
        "username",
    }
)


class EnvelopeStage(StrEnum):
    ANALYST = "ANALYST"
    INVESTMENT_DEBATE = "INVESTMENT_DEBATE"
    RESEARCH_MANAGER = "RESEARCH_MANAGER"
    TRADER = "TRADER"
    RISK_DEBATE = "RISK_DEBATE"
    PORTFOLIO_MANAGER = "PORTFOLIO_MANAGER"


class EnvelopeRole(StrEnum):
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
    PORTFOLIO_MANAGER_RETRY = "PORTFOLIO_MANAGER_RETRY"


_STAGE_ROLE_ROUNDS: Final = {
    EnvelopeStage.ANALYST: (
        frozenset(
            {
                EnvelopeRole.TECHNICAL,
                EnvelopeRole.FUNDAMENTALS,
                EnvelopeRole.NEWS,
                EnvelopeRole.SENTIMENT,
            }
        ),
        frozenset({None}),
    ),
    EnvelopeStage.INVESTMENT_DEBATE: (
        frozenset({EnvelopeRole.BULL, EnvelopeRole.BEAR}),
        frozenset({1, 2}),
    ),
    EnvelopeStage.RESEARCH_MANAGER: (
        frozenset({EnvelopeRole.RESEARCH_MANAGER}),
        frozenset({None}),
    ),
    EnvelopeStage.TRADER: (frozenset({EnvelopeRole.TRADER}), frozenset({None})),
    EnvelopeStage.RISK_DEBATE: (
        frozenset(
            {
                EnvelopeRole.AGGRESSIVE,
                EnvelopeRole.CONSERVATIVE,
                EnvelopeRole.NEUTRAL,
            }
        ),
        frozenset({1, 2}),
    ),
    EnvelopeStage.PORTFOLIO_MANAGER: (
        frozenset(
            {
                EnvelopeRole.PORTFOLIO_MANAGER,
                EnvelopeRole.PORTFOLIO_MANAGER_RETRY,
            }
        ),
        frozenset({None}),
    ),
}

_P3C_STAGES: Final = frozenset(
    {
        EnvelopeStage.ANALYST,
        EnvelopeStage.INVESTMENT_DEBATE,
        EnvelopeStage.RESEARCH_MANAGER,
        EnvelopeStage.TRADER,
    }
)


@dataclass(frozen=True, slots=True)
class EnvelopeVersions:
    graph: str
    prompt: str
    model: str
    provider: str
    data: str
    memory: str

    def __post_init__(self) -> None:
        for field in ("graph", "prompt", "model", "provider", "data", "memory"):
            value = getattr(self, field)
            if type(value) is not str or _VERSION.fullmatch(value) is None:
                raise ValueError(f"envelope {field} version is invalid")

    def to_wire(self) -> dict[str, str]:
        return {
            "graph": self.graph,
            "prompt": self.prompt,
            "model": self.model,
            "provider": self.provider,
            "data": self.data,
            "memory": self.memory,
        }


@dataclass(frozen=True, slots=True, repr=False)
class CanonicalEnvelopeSection:
    """One immutable JSON object using the envelope's stricter resource policy."""

    _canonical: str

    def __post_init__(self) -> None:
        if type(self._canonical) is not str:
            raise ValueError("envelope section must be canonical JSON text")
        try:
            parsed = json.loads(self._canonical, parse_constant=_reject_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("envelope section must be valid finite JSON") from error
        normalized = _normalize_section(parsed)
        if type(normalized) is not dict or _canonical_json(normalized) != self._canonical:
            raise ValueError("envelope section must be an exact canonical JSON object")

    @classmethod
    def from_value(cls, value: object) -> CanonicalEnvelopeSection:
        normalized = _normalize_section(value)
        if type(normalized) is not dict:
            raise ValueError("envelope section must be an exact JSON object")
        return cls(_canonical_json(normalized))

    def to_dict(self) -> dict[str, JsonValue]:
        parsed = json.loads(self._canonical)
        if type(parsed) is not dict:  # pragma: no cover - protected by construction
            raise RuntimeError("canonical envelope section is not an object")
        return cast(dict[str, JsonValue], parsed)

    def to_json(self) -> str:
        return self._canonical

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self._canonical.encode("utf-8")).hexdigest()

    @property
    def byte_count(self) -> int:
        return len(self._canonical.encode("utf-8"))

    def __repr__(self) -> str:
        return (
            f"CanonicalEnvelopeSection(content_hash={self.content_hash!r}, bytes={self.byte_count})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class SanitizedProviderEnvelope:
    """All and only the material one isolated model invocation may observe.

    ``snapshot_hash`` is the trusted source-lineage hash; ``projected_snapshot_hash``
    is the hash of the exact de-identified snapshot section delivered to the provider.
    """

    stage: EnvelopeStage
    role: EnvelopeRole
    round_number: int | None
    run_id: RunId
    input_id: RunId
    output_id: RunId
    producer_version: str
    symbol: str | None
    attempt: int | None
    superseded_proposal_id: RunId | None
    superseded_proposal_hash: str | None
    context_id: RunId | None
    previous_context_id: RunId | None
    bundle_id: RunId | None
    packet_hash: str | None
    snapshot_hash: str
    projected_snapshot_hash: str
    context_hash: str | None
    bundle_hash: str | None
    universe_hash: str
    created_at: UtcTimestamp
    deadline: UtcTimestamp
    window: AnalysisWindow | None
    allowed_symbols: tuple[str, ...]
    citation_ids: tuple[str, ...]
    portfolio_snapshot: CanonicalEnvelopeSection
    untrusted_data: CanonicalEnvelopeSection
    prior_outputs: tuple[CanonicalEnvelopeSection, ...]
    feedback: CanonicalEnvelopeSection | None
    versions: EnvelopeVersions
    prompt_template_id: str
    prompt_template_hash: str
    envelope_hash: str

    def __post_init__(self) -> None:
        self.validate_integrity()

    @classmethod
    def build(
        cls,
        *,
        stage: EnvelopeStage,
        role: EnvelopeRole,
        round_number: int | None,
        run_id: RunId,
        input_id: RunId,
        output_id: RunId,
        producer_version: str,
        symbol: str | None,
        attempt: int | None,
        superseded_proposal_id: RunId | None,
        superseded_proposal_hash: str | None,
        context_id: RunId | None,
        previous_context_id: RunId | None,
        bundle_id: RunId | None,
        packet_hash: str | None,
        snapshot_hash: str,
        context_hash: str | None,
        bundle_hash: str | None,
        universe_hash: str,
        created_at: UtcTimestamp,
        deadline: UtcTimestamp,
        window: AnalysisWindow | None,
        allowed_symbols: tuple[str, ...],
        citation_ids: tuple[str, ...],
        portfolio_snapshot: PortfolioSnapshot,
        source_material: object,
        untrusted_data: object,
        prior_outputs: tuple[object, ...],
        feedback: object | None,
        versions: EnvelopeVersions,
        prompt_template_id: str,
        prompt_template_hash: str,
        route_versions: tuple[str, str] | None = None,
    ) -> SanitizedProviderEnvelope:
        if type(portfolio_snapshot) is not PortfolioSnapshot:
            raise ValueError("envelope portfolio snapshot is invalid")
        try:
            portfolio_snapshot.validate_integrity()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("envelope portfolio snapshot is invalid") from error
        if snapshot_hash != portfolio_snapshot.content_hash:
            raise ValueError("envelope snapshot hash is invalid")
        if type(prior_outputs) is not tuple or len(prior_outputs) > MAX_PRIOR_OUTPUTS:
            raise ValueError("envelope prior outputs exceed the bounded list")
        _validated_symbols(allowed_symbols)
        _validated_citations(citation_ids)
        _validate_source_projection(
            stage=stage,
            role=role,
            round_number=round_number,
            source_material=source_material,
            untrusted_data=untrusted_data,
            run_id=run_id,
            input_id=input_id,
            output_id=output_id,
            producer_version=producer_version,
            symbol=symbol,
            attempt=attempt,
            superseded_proposal_id=superseded_proposal_id,
            superseded_proposal_hash=superseded_proposal_hash,
            context_id=context_id,
            previous_context_id=previous_context_id,
            bundle_id=bundle_id,
            packet_hash=packet_hash,
            snapshot_hash=snapshot_hash,
            context_hash=context_hash,
            bundle_hash=bundle_hash,
            universe_hash=universe_hash,
            created_at=created_at,
            deadline=deadline,
            window=window,
            allowed_symbols=allowed_symbols,
            citation_ids=citation_ids,
            versions=versions,
            feedback=feedback,
            route_versions=route_versions,
        )
        validated_prior_outputs = _validate_prior_outputs(
            stage=stage,
            role=role,
            round_number=round_number,
            prior_outputs=prior_outputs,
            run_id=run_id,
            input_id=input_id,
            producer_version=producer_version,
            symbol=symbol,
            context_id=context_id,
            bundle_id=bundle_id,
            packet_hash=packet_hash,
            bundle_hash=bundle_hash,
            created_at=created_at,
            citation_ids=citation_ids,
            superseded_proposal_id=superseded_proposal_id,
            previous_context_id=previous_context_id,
        )
        projected_snapshot = CanonicalEnvelopeSection.from_value(
            _deidentified_snapshot(portfolio_snapshot)
        )
        untrusted = CanonicalEnvelopeSection.from_value(untrusted_data)
        prior = tuple(
            CanonicalEnvelopeSection.from_value(value) for value in validated_prior_outputs
        )
        is_retry = (
            stage is EnvelopeStage.PORTFOLIO_MANAGER
            and role is EnvelopeRole.PORTFOLIO_MANAGER_RETRY
        )
        if is_retry:
            try:
                rejection = (
                    feedback
                    if type(feedback) is RiskRejectionFeedback
                    else RiskRejectionFeedback.from_wire(feedback)
                )
                rejection.validate_integrity()
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise ValueError("envelope retry feedback is invalid") from error
            typed_feedback = CanonicalEnvelopeSection.from_value(rejection.to_wire())
        elif feedback is not None:
            raise ValueError("envelope feedback is only valid for Portfolio Manager retry")
        else:
            typed_feedback = None
        provisional = object.__new__(cls)
        material = {
            "stage": stage,
            "role": role,
            "round_number": round_number,
            "run_id": run_id,
            "input_id": input_id,
            "output_id": output_id,
            "producer_version": producer_version,
            "symbol": symbol,
            "attempt": attempt,
            "superseded_proposal_id": superseded_proposal_id,
            "superseded_proposal_hash": superseded_proposal_hash,
            "context_id": context_id,
            "previous_context_id": previous_context_id,
            "bundle_id": bundle_id,
            "packet_hash": packet_hash,
            "snapshot_hash": snapshot_hash,
            "projected_snapshot_hash": projected_snapshot.content_hash,
            "context_hash": context_hash,
            "bundle_hash": bundle_hash,
            "universe_hash": universe_hash,
            "created_at": created_at,
            "deadline": deadline,
            "window": window,
            "allowed_symbols": allowed_symbols,
            "citation_ids": citation_ids,
            "portfolio_snapshot": projected_snapshot,
            "untrusted_data": untrusted,
            "prior_outputs": prior,
            "feedback": typed_feedback,
            "versions": versions,
            "prompt_template_id": prompt_template_id,
            "prompt_template_hash": prompt_template_hash,
        }
        for field, value in material.items():
            object.__setattr__(provisional, field, value)
        material_json = _canonical_json(provisional._material_wire())
        envelope_hash = hashlib.sha256(material_json.encode("utf-8")).hexdigest()
        return cls(**material, envelope_hash=envelope_hash)  # type: ignore[arg-type]

    def validate_integrity(self) -> None:
        if type(self.stage) is not EnvelopeStage or type(self.role) is not EnvelopeRole:
            raise ValueError("envelope stage, role, and round closure is invalid")
        allowed_roles, allowed_rounds = _STAGE_ROLE_ROUNDS[self.stage]
        if self.role not in allowed_roles or self.round_number not in allowed_rounds:
            raise ValueError("envelope stage, role, and round closure is invalid")
        if self.round_number is not None and type(self.round_number) is not int:
            raise ValueError("envelope stage, role, and round closure is invalid")
        for field in ("run_id", "input_id", "output_id"):
            if type(getattr(self, field)) is not RunId:
                raise ValueError(f"envelope {field} is invalid")
        if (
            type(self.producer_version) is not str
            or _VERSION.fullmatch(self.producer_version) is None
        ):
            raise ValueError("envelope producer version is invalid")
        for field in ("context_id", "previous_context_id", "bundle_id"):
            value = getattr(self, field)
            if value is not None and type(value) is not RunId:
                raise ValueError(f"envelope {field} is invalid")
        for field in ("snapshot_hash", "projected_snapshot_hash", "universe_hash"):
            _valid_hash(getattr(self, field), field)
        for field in ("packet_hash", "context_hash", "bundle_hash"):
            value = getattr(self, field)
            if value is not None:
                _valid_hash(value, field)
        _validated_symbols(self.allowed_symbols)
        _validated_citations(self.citation_ids)
        if self.stage in _P3C_STAGES:
            if (
                self.packet_hash is None
                or type(self.symbol) is not str
                or _SYMBOL.fullmatch(self.symbol) is None
                or self.symbol not in self.allowed_symbols
                or self.attempt is not None
                or self.superseded_proposal_id is not None
                or self.superseded_proposal_hash is not None
                or self.context_id is not None
                or self.previous_context_id is not None
                or self.bundle_id is not None
                or self.context_hash is not None
                or self.bundle_hash is not None
            ):
                raise ValueError("envelope P3-C identity closure is invalid")
        elif (
            self.packet_hash is not None
            or self.symbol is not None
            or type(self.attempt) is not int
            or self.attempt not in {1, 2}
            or self.context_id is None
            or self.bundle_id is None
            or self.context_hash is None
            or self.bundle_hash is None
        ):
            raise ValueError("envelope P3-D identity closure is invalid")
        is_retry = (
            self.stage is EnvelopeStage.PORTFOLIO_MANAGER
            and self.role is EnvelopeRole.PORTFOLIO_MANAGER_RETRY
        )
        if (
            (self.attempt == 2) is not is_retry
            or is_retry is not (self.superseded_proposal_id is not None)
            or is_retry is not (self.superseded_proposal_hash is not None)
            or is_retry is not (self.previous_context_id is not None)
        ):
            raise ValueError("envelope P3-D attempt lineage is invalid")
        if (
            self.superseded_proposal_id is not None
            and type(self.superseded_proposal_id) is not RunId
        ):
            raise ValueError("envelope P3-D attempt lineage is invalid")
        if self.superseded_proposal_hash is not None:
            _valid_hash(self.superseded_proposal_hash, "superseded proposal")
        if type(self.created_at) is not UtcTimestamp or type(self.deadline) is not UtcTimestamp:
            raise ValueError("envelope timestamps are invalid")
        if self.created_at.value >= self.deadline.value:
            raise ValueError("envelope deadline must follow creation time")
        if self.window is not None and type(self.window) is not AnalysisWindow:
            raise ValueError("envelope analysis window is invalid")
        if self.stage not in _P3C_STAGES and self.window is None:
            raise ValueError("envelope P3-D analysis window is required")
        for field in ("portfolio_snapshot", "untrusted_data"):
            value = getattr(self, field)
            if type(value) is not CanonicalEnvelopeSection:
                raise ValueError(f"envelope {field} section is invalid")
            value.__post_init__()
        snapshot_wire = self.portfolio_snapshot.to_dict()
        if snapshot_wire.get("source_content_hash") != self.snapshot_hash:
            raise ValueError("envelope snapshot hash is invalid")
        if self.portfolio_snapshot.content_hash != self.projected_snapshot_hash:
            raise ValueError("envelope projected snapshot hash is invalid")
        if type(self.prior_outputs) is not tuple or len(self.prior_outputs) > MAX_PRIOR_OUTPUTS:
            raise ValueError("envelope prior outputs exceed the bounded list")
        for output in self.prior_outputs:
            if type(output) is not CanonicalEnvelopeSection:
                raise ValueError("envelope prior output section is invalid")
            output.__post_init__()
        typed_prior_outputs = _typed_prior_outputs_from_wire(
            self.stage, tuple(output.to_dict() for output in self.prior_outputs)
        )
        _validate_prior_outputs(
            stage=self.stage,
            role=self.role,
            round_number=self.round_number,
            prior_outputs=typed_prior_outputs,
            run_id=self.run_id,
            input_id=self.input_id,
            producer_version=self.producer_version,
            symbol=self.symbol,
            context_id=self.context_id,
            bundle_id=self.bundle_id,
            packet_hash=self.packet_hash,
            bundle_hash=self.bundle_hash,
            created_at=self.created_at,
            citation_ids=self.citation_ids,
            superseded_proposal_id=self.superseded_proposal_id,
            previous_context_id=self.previous_context_id,
        )
        if self.feedback is not None:
            if type(self.feedback) is not CanonicalEnvelopeSection:
                raise ValueError("envelope feedback section is invalid")
            self.feedback.__post_init__()
        if is_retry is not (self.feedback is not None):
            raise ValueError("envelope feedback is only valid for Portfolio Manager retry")
        if is_retry:
            retry_feedback = self.feedback
            if retry_feedback is None:
                raise ValueError("envelope retry feedback is required")
            feedback_id = retry_feedback.to_dict().get("rejected_proposal_id")
            if feedback_id != str(self.superseded_proposal_id):
                raise ValueError("envelope P3-D attempt lineage is invalid")
        if self.feedback is not None:
            try:
                RiskRejectionFeedback.from_wire(self.feedback.to_dict()).validate_integrity()
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise ValueError("envelope retry feedback is invalid") from error
        if type(self.versions) is not EnvelopeVersions:
            raise ValueError("envelope versions are invalid")
        self.versions.__post_init__()
        if (
            type(self.prompt_template_id) is not str
            or _TEMPLATE_ID.fullmatch(self.prompt_template_id) is None
        ):
            raise ValueError("envelope approved prompt template identity is invalid")
        _valid_hash(self.prompt_template_hash, "prompt template")
        # The prompt module owns the approved bytes; this local import avoids a module cycle.
        from seven_lens.analysis.prompt_builder import (
            APPROVED_PROMPT_TEMPLATE_HASH,
            APPROVED_PROMPT_TEMPLATE_ID,
        )

        if (
            self.prompt_template_id != APPROVED_PROMPT_TEMPLATE_ID
            or self.prompt_template_hash != APPROVED_PROMPT_TEMPLATE_HASH
        ):
            raise ValueError("envelope approved prompt template identity is invalid")
        _valid_hash(self.envelope_hash, "envelope")
        material_json = _canonical_json(self._material_wire())
        expected_hash = hashlib.sha256(material_json.encode("utf-8")).hexdigest()
        if self.envelope_hash != expected_hash:
            raise ValueError("envelope hash does not match canonical material")
        canonical = self.canonical_json
        byte_count = len(canonical.encode("utf-8"))
        if byte_count > MAX_CANONICAL_ENVELOPE_BYTES:
            raise ValueError("canonical envelope exceeds the byte cap")
        if self.estimated_token_upper_bound > MAX_ESTIMATED_TOKEN_UPPER_BOUND:
            raise ValueError("canonical envelope exceeds the token estimate cap")
        if _count_nodes(self.to_wire()) > MAX_ENVELOPE_NODES:
            raise ValueError("canonical envelope exceeds the total node cap")

    def _material_wire(self) -> dict[str, JsonValue]:
        return {
            "schema_version": "p3e-envelope.1",
            "stage": self.stage.value,
            "role": self.role.value,
            "round_number": self.round_number,
            "run_id": str(self.run_id),
            "input_id": str(self.input_id),
            "output_id": str(self.output_id),
            "producer_version": self.producer_version,
            "symbol": self.symbol,
            "attempt": self.attempt,
            "superseded_proposal_id": (
                None if self.superseded_proposal_id is None else str(self.superseded_proposal_id)
            ),
            "superseded_proposal_hash": self.superseded_proposal_hash,
            "context_id": None if self.context_id is None else str(self.context_id),
            "previous_context_id": (
                None if self.previous_context_id is None else str(self.previous_context_id)
            ),
            "bundle_id": None if self.bundle_id is None else str(self.bundle_id),
            "packet_hash": self.packet_hash,
            "snapshot_hash": self.snapshot_hash,
            "projected_snapshot_hash": self.projected_snapshot_hash,
            "context_hash": self.context_hash,
            "bundle_hash": self.bundle_hash,
            "universe_hash": self.universe_hash,
            "created_at": str(self.created_at),
            "deadline": str(self.deadline),
            "window": None if self.window is None else self.window.value,
            "allowed_symbols": list(self.allowed_symbols),
            "citation_ids": list(self.citation_ids),
            "portfolio_snapshot": self.portfolio_snapshot.to_dict(),
            "untrusted_data": self.untrusted_data.to_dict(),
            "prior_outputs": [item.to_dict() for item in self.prior_outputs],
            "feedback": None if self.feedback is None else self.feedback.to_dict(),
            "versions": cast(dict[str, JsonValue], self.versions.to_wire()),
            "prompt_template_id": self.prompt_template_id,
            "prompt_template_hash": self.prompt_template_hash,
        }

    def to_wire(self) -> dict[str, JsonValue]:
        return {**self._material_wire(), "envelope_hash": self.envelope_hash}

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.to_wire())

    @property
    def estimated_token_upper_bound(self) -> int:
        # One token per UTF-8 byte is deliberately conservative and tokenizer-independent.
        return len(self.canonical_json.encode("utf-8"))

    def __repr__(self) -> str:
        return (
            "SanitizedProviderEnvelope("
            f"stage={self.stage.value!r}, role={self.role.value!r}, "
            f"round_number={self.round_number!r}, run_id={str(self.run_id)!r}, "
            f"input_id={str(self.input_id)!r}, output_id={str(self.output_id)!r}, "
            f"envelope_hash={self.envelope_hash!r}, "
            f"bytes={len(self.canonical_json.encode('utf-8'))}, sections=[REDACTED])"
        )


def derive_provider_output_id(
    run_id: RunId,
    input_id: RunId,
    stage: EnvelopeStage,
    role: EnvelopeRole,
    round_number: int | None,
) -> RunId:
    """Derive a stable P3-C output identity from one exact logical invocation."""

    if type(run_id) is not RunId or type(input_id) is not RunId:
        raise ValueError("provider output identity material is invalid")
    if type(stage) is not EnvelopeStage or type(role) is not EnvelopeRole:
        raise ValueError("provider output identity material is invalid")
    allowed_roles, allowed_rounds = _STAGE_ROLE_ROUNDS[stage]
    if role not in allowed_roles or round_number not in allowed_rounds:
        raise ValueError("provider output identity material is invalid")
    material = b"\x00".join(
        value.encode("utf-8")
        for value in (
            _OUTPUT_ID_DOMAIN,
            str(run_id),
            str(input_id),
            stage.value,
            role.value,
            "0" if round_number is None else str(round_number),
        )
    )
    return RunId(UUID(bytes=hashlib.sha256(material).digest()[:16], version=4))


def _validate_prior_outputs(
    *,
    stage: EnvelopeStage,
    role: EnvelopeRole,
    round_number: int | None,
    prior_outputs: tuple[object, ...],
    run_id: RunId,
    input_id: RunId,
    producer_version: str,
    symbol: str | None,
    context_id: RunId | None,
    bundle_id: RunId | None,
    packet_hash: str | None,
    bundle_hash: str | None,
    created_at: UtcTimestamp,
    citation_ids: tuple[str, ...],
    superseded_proposal_id: RunId | None,
    previous_context_id: RunId | None,
) -> tuple[object, ...]:
    """Accept only the exact typed outputs reachable immediately before this stage."""

    from seven_lens.analysis.pipeline import ROLE_ORDER
    from seven_lens.analysis.ports import DebateArgument, ProviderStage
    from seven_lens.analysis.proposal_contracts import (
        DEBATE_ORDER,
        derive_proposal_run_id,
    )

    citations = set(citation_ids)

    def validate_meta(value: object, expected_run_id: RunId) -> bool:
        meta = getattr(value, "meta", None)
        if meta is None:
            return False
        try:
            meta.__post_init__()
        except (AttributeError, TypeError, ValueError):
            return False
        return bool(
            meta.run_id == expected_run_id
            and meta.created_at == created_at
            and meta.producer_version == producer_version
        )

    def validate_reports(values: tuple[object, ...]) -> bool:
        if len(values) != len(ROLE_ORDER):
            return False
        for item, expected_role in zip(values, ROLE_ORDER, strict=True):
            if type(item) is not AnalystReport:
                return False
            try:
                item.__post_init__()
            except (AttributeError, TypeError, ValueError):
                return False
            if (
                not validate_meta(item, run_id)
                or item.report_id
                != derive_provider_output_id(
                    run_id,
                    input_id,
                    EnvelopeStage.ANALYST,
                    EnvelopeRole(AnalystRole(expected_role.value).value),
                    None,
                )
                or item.input_id != input_id
                or item.role is not expected_role
                or item.symbol != symbol
                or item.status is not AnalysisStatus.VALID
                or not set(item.evidence_refs) <= citations
                or not set(item.counterevidence_refs) <= citations
            ):
                return False
        return True

    if stage is EnvelopeStage.ANALYST:
        if prior_outputs:
            raise ValueError("envelope analyst prior outputs must be empty")
        return ()

    if stage in _P3C_STAGES:
        reports = prior_outputs[:4]
        if not validate_reports(reports):
            raise ValueError("envelope P3-C prior output reports are foreign or stale")
        if stage is EnvelopeStage.INVESTMENT_DEBATE:
            expected_length = 4 if round_number == 1 else 6
            if len(prior_outputs) != expected_length:
                raise ValueError("envelope debate prior output closure is invalid")
            if round_number == 2:
                for item, expected_side in zip(
                    prior_outputs[4:],
                    (ProviderStage.BULL, ProviderStage.BEAR),
                    strict=True,
                ):
                    if type(item) is not DebateArgument:
                        raise ValueError("envelope debate prior output is not typed")
                    try:
                        item.__post_init__()
                    except (AttributeError, TypeError, ValueError) as error:
                        raise ValueError("envelope debate prior output is invalid") from error
                    if (
                        item.input_id != input_id
                        or item.packet_hash != packet_hash
                        or item.symbol != symbol
                        or item.side is not expected_side
                        or item.round_number != 1
                        or not set(item.evidence_refs) <= citations
                    ):
                        raise ValueError("envelope debate prior output is foreign or stale")
            return tuple(_prior_output_wire(item) for item in prior_outputs)

        if len(prior_outputs) < 5 or type(prior_outputs[4]) is not InvestmentDebateState:
            raise ValueError("envelope manager prior debate is not typed")
        debate = prior_outputs[4]
        try:
            debate.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("envelope manager prior debate is invalid") from error
        expected_debate_id = RunId(
            UUID(
                bytes=hashlib.sha256(f"{input_id}:debate".encode()).digest()[:16],
                version=4,
            )
        )
        if (
            not validate_meta(debate, run_id)
            or debate.debate_id != expected_debate_id
            or debate.input_id != input_id
            or debate.symbol != symbol
            or debate.round_count != 2
            or debate.complete is not True
            or not set(debate.verified_claims) <= citations
        ):
            raise ValueError("envelope manager prior debate is foreign or stale")
        if stage is EnvelopeStage.RESEARCH_MANAGER:
            if len(prior_outputs) != 5:
                raise ValueError("envelope research-manager prior output closure is invalid")
            return tuple(_prior_output_wire(item) for item in prior_outputs)

        if len(prior_outputs) != 6 or type(prior_outputs[5]) is not ResearchConclusion:
            raise ValueError("envelope trader prior conclusion is not typed")
        conclusion = prior_outputs[5]
        try:
            conclusion.__post_init__()
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("envelope trader prior conclusion is invalid") from error
        if (
            not validate_meta(conclusion, run_id)
            or conclusion.conclusion_id
            != derive_provider_output_id(
                run_id,
                input_id,
                EnvelopeStage.RESEARCH_MANAGER,
                EnvelopeRole.RESEARCH_MANAGER,
                None,
            )
            or conclusion.input_id != input_id
            or conclusion.symbol != symbol
            or conclusion.status is not AnalysisStatus.VALID
            or not set(conclusion.evidence_refs) <= citations
        ):
            raise ValueError("envelope trader prior conclusion is foreign or stale")
        return tuple(_prior_output_wire(item) for item in prior_outputs)

    if stage is EnvelopeStage.RISK_DEBATE:
        if round_number == 1:
            if prior_outputs:
                raise ValueError("envelope risk round 1 prior outputs must be empty")
            return ()
        if round_number != 2 or len(prior_outputs) != 3:
            raise ValueError("envelope risk round 2 prior output closure is invalid")
        for item, (expected_viewpoint, expected_round) in zip(
            prior_outputs, DEBATE_ORDER[:3], strict=True
        ):
            if type(item) is not RiskArgument:
                raise ValueError("envelope risk prior output is not typed")
            try:
                item.validate_integrity()
                item.validate_against_citations(citation_ids)
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError("envelope risk prior output is invalid") from error
            if (
                item.context_id != context_id
                or item.bundle_id != bundle_id
                or item.bundle_hash != bundle_hash
                or item.viewpoint is not expected_viewpoint
                or item.round_number != expected_round
                or item.producer_version != producer_version
                or not validate_meta(item, run_id)
            ):
                raise ValueError("envelope risk prior output is foreign or stale")
        return tuple(_prior_output_wire(item) for item in prior_outputs)

    if len(prior_outputs) != 1 or type(prior_outputs[0]) is not RiskDebateState:
        raise ValueError("envelope portfolio-manager prior debate is not typed")
    risk_debate = prior_outputs[0]
    try:
        risk_debate.validate_integrity()
        risk_debate.validate_citations(citation_ids)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("envelope portfolio-manager prior debate is invalid") from error
    expected_prior_context_id = (
        previous_context_id if role is EnvelopeRole.PORTFOLIO_MANAGER_RETRY else context_id
    )
    if expected_prior_context_id is None:
        raise ValueError("envelope portfolio-manager prior context is invalid")
    if (
        risk_debate.context_id != expected_prior_context_id
        or risk_debate.bundle_id != bundle_id
        or risk_debate.bundle_hash != bundle_hash
        or not validate_meta(risk_debate, derive_proposal_run_id(expected_prior_context_id))
        or (role is EnvelopeRole.PORTFOLIO_MANAGER_RETRY)
        is not (superseded_proposal_id is not None)
    ):
        raise ValueError("envelope portfolio-manager prior debate is foreign or stale")
    return (risk_debate.to_wire(),)


def _prior_output_wire(value: object) -> object:
    """Project one already-validated typed prior output to exact JSON material."""

    from seven_lens.analysis.ports import DebateArgument

    if type(value) is DebateArgument:
        argument = value
        return {
            "input_id": str(argument.input_id),
            "packet_hash": argument.packet_hash,
            "symbol": argument.symbol,
            "side": argument.side.value,
            "round_number": argument.round_number,
            "argument": argument.argument,
            "evidence_refs": list(argument.evidence_refs),
        }
    to_wire = getattr(value, "to_wire", None)
    if not callable(to_wire):
        raise ValueError("envelope prior output is not serializable")
    return to_wire()


def _typed_prior_outputs_from_wire(
    stage: EnvelopeStage, values: tuple[object, ...]
) -> tuple[object, ...]:
    """Rebuild canonical prior sections as strict contracts during integrity checks."""

    from seven_lens.analysis.ports import DebateArgument, ProviderStage

    try:
        if stage is EnvelopeStage.ANALYST:
            if values:
                raise ValueError("analyst prior outputs must be empty")
            return ()
        if stage is EnvelopeStage.INVESTMENT_DEBATE:
            if len(values) not in {4, 6}:
                raise ValueError("debate prior output count is invalid")
            reports: tuple[object, ...] = tuple(
                AnalystReport.from_wire(value) for value in values[:4]
            )
            arguments: list[DebateArgument] = []
            for value in values[4:]:
                if type(value) is not dict:
                    raise ValueError("debate prior output must be an object")
                raw = cast(dict[str, object], value)
                if set(raw) != {
                    "input_id",
                    "packet_hash",
                    "symbol",
                    "side",
                    "round_number",
                    "argument",
                    "evidence_refs",
                }:
                    raise ValueError("debate prior output fields are invalid")
                refs = raw["evidence_refs"]
                if (
                    type(raw["input_id"]) is not str
                    or type(raw["packet_hash"]) is not str
                    or type(raw["symbol"]) is not str
                    or type(raw["side"]) is not str
                    or type(raw["round_number"]) is not int
                    or type(raw["argument"]) is not str
                    or type(refs) is not list
                    or any(type(item) is not str for item in refs)
                ):
                    raise ValueError("debate prior evidence refs are invalid")
                arguments.append(
                    DebateArgument(
                        RunId(UUID(raw["input_id"])),
                        raw["packet_hash"],
                        raw["symbol"],
                        ProviderStage(raw["side"]),
                        raw["round_number"],
                        raw["argument"],
                        tuple(cast(list[str], refs)),
                    )
                )
            return (*reports, *arguments)
        if stage in {EnvelopeStage.RESEARCH_MANAGER, EnvelopeStage.TRADER}:
            expected = 5 if stage is EnvelopeStage.RESEARCH_MANAGER else 6
            if len(values) != expected:
                raise ValueError("manager prior output count is invalid")
            parsed: list[object] = [AnalystReport.from_wire(value) for value in values[:4]]
            parsed.append(InvestmentDebateState.from_wire(values[4]))
            if stage is EnvelopeStage.TRADER:
                parsed.append(ResearchConclusion.from_wire(values[5]))
            return tuple(parsed)
        if stage is EnvelopeStage.RISK_DEBATE:
            return tuple(RiskArgument.from_wire(value) for value in values)
        if stage is EnvelopeStage.PORTFOLIO_MANAGER:
            return tuple(RiskDebateState.from_wire(value) for value in values)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("envelope canonical prior outputs are invalid") from error
    raise ValueError("envelope stage is invalid")


#: Legacy package-default (Agnes) route identity for the P3-C projection
#: check; the pipeline always passes the configured route explicitly.
_LEGACY_P3C_ROUTE_VERSIONS: Final = ("agnes-2.5-flash", "agnes.1")


def _p3c_expected_versions(
    data_version: str,
    route_versions: tuple[str, str],
) -> EnvelopeVersions:
    model_version, provider_version = route_versions
    return EnvelopeVersions(
        graph="tradingagents.1",
        prompt="p3e.1",
        model=model_version,
        provider=provider_version,
        data=data_version,
        memory="none.1",
    )


def _validate_source_projection(
    *,
    stage: EnvelopeStage,
    role: EnvelopeRole,
    round_number: int | None,
    source_material: object,
    untrusted_data: object,
    run_id: RunId,
    input_id: RunId,
    output_id: RunId,
    producer_version: str,
    symbol: str | None,
    attempt: int | None,
    superseded_proposal_id: RunId | None,
    superseded_proposal_hash: str | None,
    context_id: RunId | None,
    previous_context_id: RunId | None,
    bundle_id: RunId | None,
    packet_hash: str | None,
    snapshot_hash: str,
    context_hash: str | None,
    bundle_hash: str | None,
    universe_hash: str,
    created_at: UtcTimestamp,
    deadline: UtcTimestamp,
    window: AnalysisWindow | None,
    allowed_symbols: tuple[str, ...],
    citation_ids: tuple[str, ...],
    versions: EnvelopeVersions,
    feedback: object | None,
    route_versions: tuple[str, str] | None = None,
) -> None:
    """Bind provider-visible facts to one exact validated typed source object."""

    from seven_lens.analysis.contracts import AnalysisInput
    from seven_lens.analysis.model_material import (
        evidence_packet_model_material,
        research_bundle_model_material,
    )
    from seven_lens.analysis.proposal_contracts import (
        ProposalContext,
        ResearchBundle,
        derive_context_id,
    )
    from seven_lens.sources.contracts import EvidencePacket

    try:
        if stage in _P3C_STAGES:
            if (
                type(source_material) is not tuple
                or len(source_material) != 3
                or type(source_material[0]) is not AnalysisInput
                or type(source_material[1]) is not EvidencePacket
                or type(source_material[2]) is not str
            ):
                raise ValueError("envelope P3-C source material is invalid")
            analysis_input, packet, selected_symbol = source_material
            analysis_input.validate_integrity()
            packet.validate_integrity()
            if (
                analysis_input.meta.run_id != run_id
                or analysis_input.input_id != input_id
                or analysis_input.meta.producer_version != producer_version
                or analysis_input.meta.created_at != created_at
                or previous_context_id is not None
                or analysis_input.portfolio_snapshot.content_hash != snapshot_hash
                or analysis_input.universe_hash != universe_hash
                or analysis_input.deadline != deadline
                or analysis_input.window is not window
                or (*analysis_input.holding_symbols, *analysis_input.candidate_symbols)
                != allowed_symbols
                or selected_symbol != symbol
                or selected_symbol not in analysis_input.focus_symbols
                or output_id
                != derive_provider_output_id(run_id, input_id, stage, role, round_number)
                or packet.packet_hash != packet_hash
                or packet.universe_hash != universe_hash
                or packet.portfolio_snapshot_hash != snapshot_hash
                or tuple(sorted(packet.citation_ids)) != citation_ids
                or set(analysis_input.evidence_refs) != packet.citation_ids
                or packet.as_of != analysis_input.as_of
                or packet.data_snapshot_refs != analysis_input.data_snapshot_refs
                or packet.status.value != "VERIFIED"
                or packet.freshness_status.value != "FRESH"
                or bool(packet.contradiction_claim_ids)
                or bool(packet.missing_evidence)
                or versions
                != _p3c_expected_versions(
                    packet.producer_version,
                    route_versions or _LEGACY_P3C_ROUTE_VERSIONS,
                )
                or evidence_packet_model_material(packet) != untrusted_data
            ):
                raise ValueError("envelope P3-C source material is foreign or stale")
            return
        if (
            type(source_material) is not tuple
            or len(source_material) != 2
            or type(source_material[0]) is not ResearchBundle
            or type(source_material[1]) is not ProposalContext
        ):
            raise ValueError("envelope P3-D source material is invalid")
        bundle, context = source_material
        bundle.validate_integrity()
        context.validate_integrity()
        if stage is EnvelopeStage.RISK_DEBATE:
            if round_number not in {1, 2}:
                raise ValueError("envelope P3-D risk round is invalid")
            expected_output_id = derive_argument_id(
                context.context_id,
                RiskViewpoint(role.value),
                round_number,
            )
        else:
            expected_output_id = derive_proposal_id(context.context_id)
        expected_previous_context_id = (
            None
            if context.attempt == 1
            else derive_context_id(
                bundle.bundle_id,
                1,
                bundle.portfolio_snapshot_hash,
                None,
            )
        )
        if (
            derive_proposal_run_id(context.context_id) != run_id
            or bundle.parent_input_id != input_id
            or bundle.bundle_id != bundle_id
            or bundle.bundle_hash != bundle_hash
            or bundle.universe_hash != universe_hash
            or bundle.meta.created_at != context.meta.created_at
            or bundle.meta.producer_version != context.meta.producer_version
            or bundle.first_item.graph_version != context.graph_version
            or bundle.first_item.prompt_version != context.prompt_version
            or bundle.first_item.data_version != context.data_version
            or (*bundle.holding_symbols, *bundle.candidate_symbols) != context.allowed_symbols
            or context.meta.producer_version != producer_version
            or context.meta.created_at != created_at
            or context.context_id != context_id
            or context.previous_context_id != previous_context_id
            or context.previous_context_id != expected_previous_context_id
            or context.context_hash != context_hash
            or context.bundle_id != bundle_id
            or context.bundle_hash != bundle_hash
            or context.snapshot_hash != snapshot_hash
            or context.attempt != attempt
            or context.superseded_proposal_id != superseded_proposal_id
            or context.superseded_proposal_hash != superseded_proposal_hash
            or (context.attempt == 1 and context.snapshot_hash != bundle.portfolio_snapshot_hash)
            or (context.attempt == 2 and context.snapshot.as_of.value < bundle.as_of.value)
            or bundle.deadline != deadline
            or bundle.window is not window
            or context.deadline != deadline
            or context.window is not window
            or context.universe_hash != universe_hash
            or context.allowed_symbols != allowed_symbols
            or context.citation_ids != citation_ids
            or bundle.citation_ids != citation_ids
            or output_id != expected_output_id
            or versions
            != EnvelopeVersions(
                graph=context.graph_version,
                prompt=context.prompt_version,
                model=context.model_version,
                provider=context.provider_version,
                data=context.data_version,
                memory=context.memory_version,
            )
            or feedback != (None if context.feedback is None else context.feedback.to_wire())
            or research_bundle_model_material(bundle) != untrusted_data
        ):
            raise ValueError("envelope P3-D source material is foreign or stale")
        if context_id is None:
            raise ValueError("envelope P3-D source material is invalid")
    except ValueError:
        raise
    except (AttributeError, TypeError):
        raise ValueError("envelope source material is invalid") from None


def _deidentified_snapshot(snapshot: PortfolioSnapshot) -> dict[str, JsonValue]:
    wire = snapshot.to_wire()
    source_hash = wire.pop("content_hash")
    if type(source_hash) is not str:
        raise ValueError("portfolio snapshot source hash is invalid")
    wire["source_content_hash"] = source_hash
    open_orders = cast(list[dict[str, JsonValue]], wire["open_orders"])
    for index, item in enumerate(open_orders, start=1):
        item["reference_id"] = f"open-order-{index:03d}"
    fills = cast(list[dict[str, JsonValue]], wire["same_day_fills"])
    for index, item in enumerate(fills, start=1):
        item["reference_id"] = f"same-day-fill-{index:03d}"
    return wire


def _validated_symbols(value: object) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or not value
        or len(value) > MAX_ALLOWED_SYMBOLS
        or any(type(item) is not str or _SYMBOL.fullmatch(item) is None for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("envelope allowed symbols are invalid")
    return cast(tuple[str, ...], value)


def _validated_citations(value: object) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or not value
        or len(value) > MAX_ALLOWED_CITATIONS
        or any(type(item) is not str or _REF.fullmatch(item) is None for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("envelope citation identifiers are invalid")
    return cast(tuple[str, ...], value)


def _valid_hash(value: object, field: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"envelope {field} hash is invalid")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON values are rejected")


@dataclass(slots=True)
class _Budget:
    nodes: int = 0


def _normalize_section(value: object) -> JsonValue:
    return _normalize(
        value,
        depth=0,
        active=set(),
        budget=_Budget(),
    )


def _normalize(
    value: object,
    *,
    depth: int,
    active: set[int],
    budget: _Budget,
) -> JsonValue:
    if depth > MAX_SECTION_DEPTH:
        raise ValueError("envelope section exceeds the depth cap")
    budget.nodes += 1
    if budget.nodes > MAX_SECTION_NODES:
        raise ValueError("envelope section exceeds the node cap")
    if value is None or type(value) in {bool, int}:
        return cast(JsonValue, value)
    if type(value) is str:
        _validate_text(value)
        return value
    if type(value) is float:
        raise ValueError("envelope sections reject floating-point values")
    if type(value) is dict:
        raw = cast(dict[object, object], value)
        if len(raw) > MAX_MAP_MEMBERS:
            raise ValueError("envelope section exceeds the map member cap")
        identity = _enter(value, active)
        try:
            result: dict[str, JsonValue] = {}
            for key, nested in raw.items():
                if type(key) is not str:
                    raise ValueError("envelope section keys must be exact strings")
                _validate_key(key)
                result[key] = _normalize(
                    nested,
                    depth=depth + 1,
                    active=active,
                    budget=budget,
                )
            return result
        finally:
            active.remove(identity)
    if type(value) in {list, tuple}:
        raw_sequence = cast(list[object] | tuple[object, ...], value)
        if len(raw_sequence) > MAX_LIST_ITEMS:
            raise ValueError("envelope section exceeds the list item cap")
        identity = _enter(value, active)
        try:
            return [
                _normalize(
                    item,
                    depth=depth + 1,
                    active=active,
                    budget=budget,
                )
                for item in raw_sequence
            ]
        finally:
            active.remove(identity)
    raise ValueError("envelope section contains a non-JSON-safe value")


def _validate_key(value: str) -> None:
    _validate_utf8(value, "envelope section key", MAX_KEY_BYTES)
    compatibility = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(character) == "Cf" for character in compatibility):
        raise ValueError("envelope section contains a prohibited capability or identity field")
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", compatibility.strip())
    separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", separated)
    normalized = re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")
    tokens = frozenset(part for part in normalized.split("_") if part)
    if (
        normalized in _PROHIBITED_KEYS
        or bool(tokens & _PROHIBITED_KEY_TOKENS)
        or {"api", "key"} <= tokens
        or {"user", "name"} <= tokens
        or (
            "name" in tokens
            and bool(tokens & {"first", "last", "full", "customer", "client", "owner"})
        )
    ):
        raise ValueError("envelope section contains a prohibited capability or identity field")


def _validate_text(value: str) -> None:
    try:
        validate_sanitized_text(value, "envelope string", maximum=MAX_STRING_BYTES)
    except ValueError as error:
        raise ValueError(
            "envelope strings contain prohibited identity or capability material"
        ) from error


def _validate_utf8(value: str, field: str, maximum: int) -> None:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field} contains invalid Unicode") from error
    if len(encoded) > maximum:
        raise ValueError(f"{field} exceeds its UTF-8 byte cap")


def _enter(value: object, active: set[int]) -> int:
    identity = id(value)
    if identity in active:
        raise ValueError("envelope section contains a cycle")
    active.add(identity)
    return identity


def _count_nodes(value: object) -> int:
    if type(value) is dict:
        return 1 + sum(_count_nodes(item) for item in cast(dict[object, object], value).values())
    if type(value) in {list, tuple}:
        return 1 + sum(_count_nodes(item) for item in cast(list[object], value))
    return 1
