"""Immutable, bounded P3-D research-bundle and portfolio-proposal contracts.

These contracts aggregate per-symbol P3-C results into one ``ResearchBundle``, bind a
``ProposalContext`` to that bundle plus the exact sanitized portfolio snapshot, and carry the
evolved ``PortfolioProposal`` that supersedes the P3-A sketch by binding context and bundle
identity.  They describe requests only: no approval, sizing, quantity, target portfolio,
order intent, broker-side write, network, credential, or ledger-write capability exists on
any type in this module.

All decimal wire values reuse the P3-A fixed-scale canonical rules.  Every identity that a
pipeline derives (child run/input, bundle, context, debate, argument, proposal, proposal run)
comes from one domain-tagged helper so no caller can mix identifiers across bundles.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, Self, cast
from uuid import UUID

from seven_lens.analysis.contracts import (
    SCHEMA_VERSION,
    AnalysisStatus,
    AnalysisWindow,
    ContractMeta,
    PortfolioRequest,
    PortfolioSnapshot,
    RiskRejectionFeedback,
    TraderPlan,
    _exact_enum,
    _hash,
    _integer,
    _mapping,
    _refs,
    _require_type,
    _run_id,
    _sequence,
    _symbol,
    _text,
    _timestamp,
    _version,
    canonical_wire_json,
)
from seven_lens.domain.json_values import JsonObject, JsonValue
from seven_lens.domain.value_objects import RunId, UtcTimestamp

__all__ = [
    "MAX_ALLOWED_SYMBOLS",
    "MAX_BUNDLE_CITATIONS",
    "MAX_FOCUS_SYMBOLS",
    "PortfolioProposal",
    "ProposalContext",
    "ResearchBundle",
    "ResearchBundleItem",
    "RiskArgument",
    "RiskDebateState",
    "RiskViewpoint",
    "assert_child_identity",
    "build_portfolio_proposal",
    "build_proposal_context",
    "build_research_bundle",
    "build_risk_debate",
    "derive_argument_id",
    "derive_bundle_id",
    "derive_child_input_id",
    "derive_child_run_id",
    "derive_context_id",
    "derive_debate_id",
    "derive_proposal_id",
    "derive_proposal_run_id",
]

MAX_FOCUS_SYMBOLS: Final = 27
MAX_ALLOWED_SYMBOLS: Final = 27
MAX_BUNDLE_CITATIONS: Final = 864
MAX_ITEM_EVIDENCE_REFS: Final = 32

_CHILD_RUN_DOMAIN: Final = "seven-lens.p3d.child-run.v1"
_CHILD_INPUT_DOMAIN: Final = "seven-lens.p3d.child-input.v1"
_BUNDLE_DOMAIN: Final = "seven-lens.p3d.bundle.v1"
_CONTEXT_DOMAIN: Final = "seven-lens.p3d.context.v1"
_DEBATE_DOMAIN: Final = "seven-lens.p3d.debate.v1"
_ARGUMENT_DOMAIN: Final = "seven-lens.p3d.risk-argument.v1"
_PROPOSAL_DOMAIN: Final = "seven-lens.p3d.proposal.v1"
_PROPOSAL_RUN_DOMAIN: Final = "seven-lens.p3d.proposal-run.v1"


def _derive_run_id(domain: str, *parts: str) -> RunId:
    """Derive one identity from a domain and separately encoded, NUL-delimited parts."""
    material = b"\x00".join(value.encode("utf-8", errors="strict") for value in (domain, *parts))
    return RunId(UUID(bytes=hashlib.sha256(material).digest()[:16], version=4))


def derive_child_run_id(parent_input_id: RunId, symbol: str) -> RunId:
    """Derive the P3-C child run identity for one focus symbol of one parent input."""
    return _derive_run_id(_CHILD_RUN_DOMAIN, str(parent_input_id), _symbol(symbol))


def derive_child_input_id(parent_input_id: RunId, symbol: str) -> RunId:
    """Derive the child analysis-input identity; the domain tag differs from the run tag."""
    return _derive_run_id(_CHILD_INPUT_DOMAIN, str(parent_input_id), _symbol(symbol))


def derive_bundle_id(parent_input_id: RunId) -> RunId:
    return _derive_run_id(_BUNDLE_DOMAIN, str(parent_input_id))


def derive_context_id(
    bundle_id: RunId,
    attempt: int,
    snapshot_hash: str,
    superseded_proposal_id: RunId | None,
    superseded_proposal_hash: str | None = None,
) -> RunId:
    _integer(attempt, "attempt", minimum=1, maximum=2)
    _hash(snapshot_hash, "snapshot_hash")
    if (superseded_proposal_id is None) != (superseded_proposal_hash is None):
        raise ValueError("superseded proposal id and hash must appear together")
    if attempt == 1 and superseded_proposal_id is not None:
        raise ValueError("attempt 1 context must not carry superseded proposal lineage")
    if attempt == 2 and superseded_proposal_id is None:
        raise ValueError("attempt 2 context requires superseded proposal lineage")
    lineage_id = "" if superseded_proposal_id is None else str(superseded_proposal_id)
    if superseded_proposal_hash is not None:
        _hash(superseded_proposal_hash, "superseded_proposal_hash")
    lineage_hash = "" if superseded_proposal_hash is None else superseded_proposal_hash
    return _derive_run_id(
        _CONTEXT_DOMAIN,
        str(bundle_id),
        str(attempt),
        snapshot_hash,
        lineage_id,
        lineage_hash,
    )


def derive_debate_id(context_id: RunId) -> RunId:
    return _derive_run_id(_DEBATE_DOMAIN, str(context_id))


def derive_argument_id(context_id: RunId, viewpoint: RiskViewpoint, round_number: int) -> RunId:
    _integer(round_number, "round_number", minimum=1, maximum=2)
    return _derive_run_id(_ARGUMENT_DOMAIN, str(context_id), viewpoint.value, str(round_number))


def derive_proposal_id(context_id: RunId) -> RunId:
    return _derive_run_id(_PROPOSAL_DOMAIN, str(context_id))


def derive_proposal_run_id(context_id: RunId) -> RunId:
    return _derive_run_id(_PROPOSAL_RUN_DOMAIN, str(context_id))


def assert_child_identity(parent_input_id: RunId, item: ResearchBundleItem) -> None:
    """Prove a bundle item carries exactly the derived child identities for its symbol."""
    expected_run = derive_child_run_id(parent_input_id, item.symbol)
    expected_input = derive_child_input_id(parent_input_id, item.symbol)
    if item.analysis_run_id != expected_run or item.analysis_input_id != expected_input:
        raise ValueError("bundle item child identity does not match deterministic derivation")


class RiskViewpoint(StrEnum):
    AGGRESSIVE = "AGGRESSIVE"
    CONSERVATIVE = "CONSERVATIVE"
    NEUTRAL = "NEUTRAL"


DEBATE_ORDER: Final = (
    (RiskViewpoint.AGGRESSIVE, 1),
    (RiskViewpoint.CONSERVATIVE, 1),
    (RiskViewpoint.NEUTRAL, 1),
    (RiskViewpoint.AGGRESSIVE, 2),
    (RiskViewpoint.CONSERVATIVE, 2),
    (RiskViewpoint.NEUTRAL, 2),
)


@dataclass(frozen=True, slots=True)
class ResearchBundleItem:
    """One per-symbol P3-C outcome frozen into a multi-symbol research bundle."""

    symbol: str
    analysis_run_id: RunId
    analysis_input_id: RunId
    packet_hash: str
    snapshot_hash: str
    trader_plan_id: RunId
    trader_plan_hash: str
    trader_plan: TraderPlan
    evidence_refs: tuple[str, ...]
    producer_version: str
    graph_version: str
    prompt_version: str
    data_version: str
    status: AnalysisStatus

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "symbol",
            "analysis_run_id",
            "analysis_input_id",
            "packet_hash",
            "snapshot_hash",
            "trader_plan_id",
            "trader_plan_hash",
            "trader_plan",
            "evidence_refs",
            "producer_version",
            "graph_version",
            "prompt_version",
            "data_version",
            "status",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _require_type(self.analysis_run_id, RunId, "analysis_run_id")
        _require_type(self.analysis_input_id, RunId, "analysis_input_id")
        _hash(self.packet_hash, "packet_hash")
        _hash(self.snapshot_hash, "snapshot_hash")
        _require_type(self.trader_plan_id, RunId, "trader_plan_id")
        _hash(self.trader_plan_hash, "trader_plan_hash")
        _require_type(self.trader_plan, TraderPlan, "trader_plan")
        self.trader_plan.__post_init__()
        if (
            self.trader_plan.plan_id != self.trader_plan_id
            or self.trader_plan.input_id != self.analysis_input_id
            or self.trader_plan.symbol != self.symbol
            or self.trader_plan.meta.run_id != self.analysis_run_id
            or self.trader_plan.meta.producer_version != self.producer_version
            or self.trader_plan.status is not AnalysisStatus.VALID
            or hashlib.sha256(canonical_wire_json(self.trader_plan).encode()).hexdigest()
            != self.trader_plan_hash
        ):
            raise ValueError("bundle item TraderPlan material does not match its frozen identity")
        object.__setattr__(
            self,
            "evidence_refs",
            _refs(self.evidence_refs, "evidence_refs", maximum=MAX_ITEM_EVIDENCE_REFS),
        )
        for name in ("producer_version", "graph_version", "prompt_version", "data_version"):
            object.__setattr__(self, name, _version(getattr(self, name), name))
        _require_type(self.status, AnalysisStatus, "status")
        if self.status is not AnalysisStatus.VALID:
            raise ValueError("bundle item requires a VALID child TraderPlan status")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "symbol": self.symbol,
            "analysis_run_id": str(self.analysis_run_id),
            "analysis_input_id": str(self.analysis_input_id),
            "packet_hash": self.packet_hash,
            "snapshot_hash": self.snapshot_hash,
            "trader_plan_id": str(self.trader_plan_id),
            "trader_plan_hash": self.trader_plan_hash,
            "trader_plan": self.trader_plan.to_wire(),
            "evidence_refs": list(self.evidence_refs),
            "producer_version": self.producer_version,
            "graph_version": self.graph_version,
            "prompt_version": self.prompt_version,
            "data_version": self.data_version,
            "status": self.status.value,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            _symbol(r["symbol"]),
            _run_id(r["analysis_run_id"]),
            _run_id(r["analysis_input_id"]),
            _hash(r["packet_hash"], "packet_hash"),
            _hash(r["snapshot_hash"], "snapshot_hash"),
            _run_id(r["trader_plan_id"]),
            _hash(r["trader_plan_hash"], "trader_plan_hash"),
            TraderPlan.from_wire(r["trader_plan"]),
            _refs(r["evidence_refs"], "evidence_refs", maximum=MAX_ITEM_EVIDENCE_REFS),
            _version(r["producer_version"], "producer_version"),
            _version(r["graph_version"], "graph_version"),
            _version(r["prompt_version"], "prompt_version"),
            _version(r["data_version"], "data_version"),
            cast(AnalysisStatus, _exact_enum(r["status"], AnalysisStatus, "status")),
        )


@dataclass(frozen=True, slots=True)
class ResearchBundle:
    """The immutable multi-symbol aggregate of completed per-symbol P3-C runs."""

    meta: ContractMeta
    bundle_id: RunId
    parent_input_id: RunId
    as_of: UtcTimestamp
    window: AnalysisWindow
    deadline: UtcTimestamp
    universe_hash: str
    portfolio_snapshot_hash: str
    data_snapshot_refs: tuple[str, ...]
    holding_symbols: tuple[str, ...]
    candidate_symbols: tuple[str, ...]
    focus_symbols: tuple[str, ...]
    items: tuple[ResearchBundleItem, ...]
    citation_ids: tuple[str, ...]
    bundle_hash: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "bundle_id",
            "parent_input_id",
            "as_of",
            "window",
            "deadline",
            "universe_hash",
            "portfolio_snapshot_hash",
            "data_snapshot_refs",
            "holding_symbols",
            "candidate_symbols",
            "focus_symbols",
            "items",
            "citation_ids",
            "bundle_hash",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        self.meta.__post_init__()
        _require_type(self.bundle_id, RunId, "bundle_id")
        _require_type(self.parent_input_id, RunId, "parent_input_id")
        if self.bundle_id != derive_bundle_id(self.parent_input_id):
            raise ValueError("bundle_id must be deterministically derived from parent_input_id")
        _require_type(self.as_of, UtcTimestamp, "as_of")
        _require_type(self.window, AnalysisWindow, "window")
        _require_type(self.deadline, UtcTimestamp, "deadline")
        _hash(self.universe_hash, "universe_hash")
        _hash(self.portfolio_snapshot_hash, "portfolio_snapshot_hash")
        object.__setattr__(
            self, "data_snapshot_refs", _refs(self.data_snapshot_refs, "data_snapshot_refs")
        )
        for name, maximum in (("holding_symbols", 15), ("candidate_symbols", 12)):
            object.__setattr__(
                self,
                name,
                cast(
                    tuple[str, ...],
                    _sequence(getattr(self, name), name, _symbol, maximum=maximum),
                ),
            )
        if set(self.holding_symbols) & set(self.candidate_symbols):
            raise ValueError("bundle holding and candidate symbols must not overlap")
        expected_universe_hash = hashlib.sha256(
            JsonObject.from_value(
                {
                    "holdings": list(self.holding_symbols),
                    "candidates": list(self.candidate_symbols),
                }
            )
            .to_json()
            .encode()
        ).hexdigest()
        if self.universe_hash != expected_universe_hash:
            raise ValueError("bundle universe symbols do not match universe_hash")
        object.__setattr__(
            self,
            "focus_symbols",
            cast(
                tuple[str, ...],
                _sequence(self.focus_symbols, "focus_symbols", _symbol, maximum=MAX_FOCUS_SYMBOLS),
            ),
        )
        if not self.focus_symbols:
            raise ValueError("bundle requires at least one focus symbol")
        if not set(self.focus_symbols) <= {
            *self.holding_symbols,
            *self.candidate_symbols,
        }:
            raise ValueError("bundle focus symbols must belong to the exact universe")
        if (
            type(self.items) not in {list, tuple}
            or len(self.items) != len(self.focus_symbols)
            or any(type(item) is not ResearchBundleItem for item in self.items)
        ):
            raise ValueError("bundle items must match focus symbols one to one")
        object.__setattr__(self, "items", tuple(self.items))
        for position, item in enumerate(self.items):
            item.__post_init__()
            if item.symbol != self.focus_symbols[position]:
                raise ValueError("bundle item order must exactly follow parent focus order")
            assert_child_identity(self.parent_input_id, item)
        first = self.items[0]
        for name in (
            "packet_hash",
            "snapshot_hash",
            "producer_version",
            "graph_version",
            "prompt_version",
            "data_version",
        ):
            value = getattr(first, name)
            if any(getattr(item, name) != value for item in self.items):
                raise ValueError(f"bundle items disagree on {name}")
        if self.meta.run_id != self.bundle_id:
            raise ValueError("bundle meta run identity must equal bundle_id")
        if self.meta.producer_version != first.producer_version:
            raise ValueError("bundle producer version must equal item producer version")
        if self.first_item.snapshot_hash != self.portfolio_snapshot_hash:
            raise ValueError("bundle snapshot hash must equal item snapshot hash")
        supplied_citations = _refs(self.citation_ids, "citation_ids", maximum=MAX_BUNDLE_CITATIONS)
        derived_citations = tuple(
            sorted({ref for item in self.items for ref in item.evidence_refs})
        )
        if supplied_citations != derived_citations:
            raise ValueError("bundle citation union must exactly match its ordered items")
        object.__setattr__(self, "citation_ids", derived_citations)
        if not derived_citations or len(derived_citations) > MAX_BUNDLE_CITATIONS:
            raise ValueError("bundle citation union is outside its bound")
        _hash(self.bundle_hash, "bundle_hash")
        if self.bundle_hash != self.compute_hash():
            raise ValueError("bundle_hash does not match frozen bundle content")
        if self.deadline.value <= self.as_of.value:
            raise ValueError("bundle deadline must be after as_of")

    @property
    def first_item(self) -> ResearchBundleItem:
        return self.items[0]

    def validate_integrity(self) -> None:
        """Re-run nested item and aggregate bundle invariants on an already-built bundle."""
        self.__post_init__()

    def _content_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "bundle_id": str(self.bundle_id),
            "parent_input_id": str(self.parent_input_id),
            "as_of": str(self.as_of),
            "window": self.window.value,
            "deadline": str(self.deadline),
            "universe_hash": self.universe_hash,
            "portfolio_snapshot_hash": self.portfolio_snapshot_hash,
            "data_snapshot_refs": list(self.data_snapshot_refs),
            "holding_symbols": list(self.holding_symbols),
            "candidate_symbols": list(self.candidate_symbols),
            "focus_symbols": list(self.focus_symbols),
            "items": [item.to_wire() for item in self.items],
            "citation_ids": list(self.citation_ids),
        }

    def compute_hash(self) -> str:
        return hashlib.sha256(
            JsonObject.from_value(self._content_wire()).to_json().encode()
        ).hexdigest()

    def to_wire(self) -> dict[str, JsonValue]:
        return {**self._content_wire(), "bundle_hash": self.bundle_hash}

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["bundle_id"]),
            _run_id(r["parent_input_id"]),
            _timestamp(r["as_of"]),
            cast(AnalysisWindow, _exact_enum(r["window"], AnalysisWindow, "window")),
            _timestamp(r["deadline"]),
            _hash(r["universe_hash"], "universe_hash"),
            _hash(r["portfolio_snapshot_hash"], "portfolio_snapshot_hash"),
            _refs(r["data_snapshot_refs"], "data_snapshot_refs"),
            cast(
                tuple[str, ...],
                _sequence(r["holding_symbols"], "holding_symbols", _symbol, maximum=15),
            ),
            cast(
                tuple[str, ...],
                _sequence(r["candidate_symbols"], "candidate_symbols", _symbol, maximum=12),
            ),
            cast(
                tuple[str, ...],
                _sequence(r["focus_symbols"], "focus_symbols", _symbol, maximum=MAX_FOCUS_SYMBOLS),
            ),
            cast(
                tuple[ResearchBundleItem, ...],
                _sequence(
                    r["items"],
                    "items",
                    ResearchBundleItem.from_wire,
                    maximum=MAX_FOCUS_SYMBOLS,
                    unique=False,
                ),
            ),
            _refs(r["citation_ids"], "citation_ids", maximum=MAX_BUNDLE_CITATIONS),
            _hash(r["bundle_hash"], "bundle_hash"),
        )


def build_research_bundle(
    *,
    meta: ContractMeta,
    parent_input_id: RunId,
    as_of: UtcTimestamp,
    window: AnalysisWindow,
    deadline: UtcTimestamp,
    universe_hash: str,
    portfolio_snapshot_hash: str,
    data_snapshot_refs: Sequence[str],
    holding_symbols: Sequence[str],
    candidate_symbols: Sequence[str],
    items: Sequence[ResearchBundleItem],
) -> ResearchBundle:
    """Build a bundle while deriving, never trusting, bundle_id, citations and bundle_hash."""
    provisional = object.__new__(ResearchBundle)
    items = tuple(items)
    citations = tuple(sorted({ref for entry in items for ref in entry.evidence_refs}))
    for name, value in {
        "meta": meta,
        "bundle_id": derive_bundle_id(parent_input_id),
        "parent_input_id": parent_input_id,
        "as_of": as_of,
        "window": window,
        "deadline": deadline,
        "universe_hash": universe_hash,
        "portfolio_snapshot_hash": portfolio_snapshot_hash,
        "data_snapshot_refs": tuple(data_snapshot_refs),
        "holding_symbols": tuple(holding_symbols),
        "candidate_symbols": tuple(candidate_symbols),
        "focus_symbols": tuple(item.symbol for item in items),
        "items": items,
        "citation_ids": citations,
        "bundle_hash": "0" * 64,
    }.items():
        object.__setattr__(provisional, name, value)
    return ResearchBundle(
        meta,
        derive_bundle_id(parent_input_id),
        parent_input_id,
        as_of,
        window,
        deadline,
        universe_hash,
        portfolio_snapshot_hash,
        tuple(data_snapshot_refs),
        tuple(holding_symbols),
        tuple(candidate_symbols),
        tuple(item.symbol for item in items),
        items,
        citations,
        provisional.compute_hash(),
    )


@dataclass(frozen=True, slots=True)
class ProposalContext:
    """The frozen decision context binding a bundle, snapshot, universe and attempt lineage."""

    meta: ContractMeta
    context_id: RunId
    attempt: int
    bundle_id: RunId
    bundle_hash: str
    snapshot: PortfolioSnapshot
    snapshot_hash: str
    window: AnalysisWindow
    deadline: UtcTimestamp
    universe_hash: str
    allowed_symbols: tuple[str, ...]
    citation_ids: tuple[str, ...]
    graph_version: str
    prompt_version: str
    model_version: str
    provider_version: str
    data_version: str
    memory_version: str
    previous_context_id: RunId | None
    superseded_proposal_id: RunId | None
    superseded_proposal_hash: str | None
    feedback: RiskRejectionFeedback | None
    context_hash: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "context_id",
            "attempt",
            "bundle_id",
            "bundle_hash",
            "snapshot",
            "snapshot_hash",
            "window",
            "deadline",
            "universe_hash",
            "allowed_symbols",
            "citation_ids",
            "graph_version",
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
            "previous_context_id",
            "superseded_proposal_id",
            "superseded_proposal_hash",
            "feedback",
            "context_hash",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        self.meta.__post_init__()
        _require_type(self.context_id, RunId, "context_id")
        _integer(self.attempt, "attempt", minimum=1, maximum=2)
        _require_type(self.bundle_id, RunId, "bundle_id")
        _hash(self.bundle_hash, "bundle_hash")
        _require_type(self.snapshot, PortfolioSnapshot, "snapshot")
        self.snapshot.validate_integrity()
        _hash(self.snapshot_hash, "snapshot_hash")
        if self.snapshot_hash != self.snapshot.content_hash:
            raise ValueError("context snapshot hash must equal the sanitized snapshot hash")
        _require_type(self.window, AnalysisWindow, "window")
        _require_type(self.deadline, UtcTimestamp, "deadline")
        _hash(self.universe_hash, "universe_hash")
        object.__setattr__(
            self,
            "allowed_symbols",
            cast(
                tuple[str, ...],
                _sequence(
                    self.allowed_symbols, "allowed_symbols", _symbol, maximum=MAX_ALLOWED_SYMBOLS
                ),
            ),
        )
        if not self.allowed_symbols:
            raise ValueError("context requires at least one allowed symbol")
        object.__setattr__(
            self,
            "citation_ids",
            _refs(self.citation_ids, "citation_ids", maximum=MAX_BUNDLE_CITATIONS),
        )
        if not self.citation_ids:
            raise ValueError("context requires the frozen bundle citation set")
        for name in (
            "graph_version",
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
        ):
            object.__setattr__(self, name, _version(getattr(self, name), name))
        if self.previous_context_id is not None:
            _require_type(self.previous_context_id, RunId, "previous_context_id")
        if self.superseded_proposal_id is not None:
            _require_type(self.superseded_proposal_id, RunId, "superseded_proposal_id")
        if self.superseded_proposal_hash is not None:
            _hash(self.superseded_proposal_hash, "superseded_proposal_hash")
        if self.feedback is not None:
            _require_type(self.feedback, RiskRejectionFeedback, "feedback")
            self.feedback.validate_integrity()
        if self.attempt == 1:
            if (
                self.previous_context_id is not None
                or self.superseded_proposal_id is not None
                or self.superseded_proposal_hash is not None
                or self.feedback is not None
            ):
                raise ValueError("attempt 1 context must not carry retry lineage")
        else:
            if (
                self.previous_context_id is None
                or self.superseded_proposal_id is None
                or self.superseded_proposal_hash is None
                or self.feedback is None
            ):
                raise ValueError("attempt 2 context requires previous context and feedback")
            if self.feedback.rejected_proposal_id != self.superseded_proposal_id:
                raise ValueError("context feedback must target the superseded proposal")
            if self.feedback.reviewed_at.value > self.snapshot.as_of.value:
                raise ValueError("risk review must not follow the refreshed snapshot")
            if self.feedback.reviewed_at.value <= self.meta.created_at.value:
                raise ValueError("risk review must follow the initial context creation")
        if self.meta.run_id != self.context_id:
            raise ValueError("context meta run identity must equal context_id")
        if self.context_id != derive_context_id(
            self.bundle_id,
            self.attempt,
            self.snapshot_hash,
            self.superseded_proposal_id,
            self.superseded_proposal_hash,
        ):
            raise ValueError("context_id must be deterministically derived from frozen lineage")
        if self.deadline.value <= self.snapshot.as_of.value:
            raise ValueError("context deadline must be after the snapshot as_of")
        _hash(self.context_hash, "context_hash")
        if self.context_hash != self.compute_hash():
            raise ValueError("context_hash does not match frozen context content")

    def validate_integrity(self) -> None:
        """Re-run nested snapshot and aggregate context invariants at an authority boundary."""
        self.__post_init__()

    def _content_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "context_id": str(self.context_id),
            "attempt": self.attempt,
            "bundle_id": str(self.bundle_id),
            "bundle_hash": self.bundle_hash,
            "snapshot": self.snapshot.to_wire(),
            "snapshot_hash": self.snapshot_hash,
            "window": self.window.value,
            "deadline": str(self.deadline),
            "universe_hash": self.universe_hash,
            "allowed_symbols": list(self.allowed_symbols),
            "citation_ids": list(self.citation_ids),
            "graph_version": self.graph_version,
            "prompt_version": self.prompt_version,
            "model_version": self.model_version,
            "provider_version": self.provider_version,
            "data_version": self.data_version,
            "memory_version": self.memory_version,
            "previous_context_id": None
            if self.previous_context_id is None
            else str(self.previous_context_id),
            "superseded_proposal_id": None
            if self.superseded_proposal_id is None
            else str(self.superseded_proposal_id),
            "superseded_proposal_hash": self.superseded_proposal_hash,
            "feedback": None if self.feedback is None else self.feedback.to_wire(),
        }

    def compute_hash(self) -> str:
        return hashlib.sha256(
            JsonObject.from_value(self._content_wire()).to_json().encode()
        ).hexdigest()

    def to_wire(self) -> dict[str, JsonValue]:
        return {**self._content_wire(), "context_hash": self.context_hash}

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["context_id"]),
            _integer(r["attempt"], "attempt", minimum=1, maximum=2),
            _run_id(r["bundle_id"]),
            _hash(r["bundle_hash"], "bundle_hash"),
            PortfolioSnapshot.from_wire(r["snapshot"]),
            _hash(r["snapshot_hash"], "snapshot_hash"),
            cast(AnalysisWindow, _exact_enum(r["window"], AnalysisWindow, "window")),
            _timestamp(r["deadline"]),
            _hash(r["universe_hash"], "universe_hash"),
            cast(
                tuple[str, ...],
                _sequence(
                    r["allowed_symbols"], "allowed_symbols", _symbol, maximum=MAX_ALLOWED_SYMBOLS
                ),
            ),
            _refs(r["citation_ids"], "citation_ids", maximum=MAX_BUNDLE_CITATIONS),
            _version(r["graph_version"], "graph_version"),
            _version(r["prompt_version"], "prompt_version"),
            _version(r["model_version"], "model_version"),
            _version(r["provider_version"], "provider_version"),
            _version(r["data_version"], "data_version"),
            _version(r["memory_version"], "memory_version"),
            None if r["previous_context_id"] is None else _run_id(r["previous_context_id"]),
            None if r["superseded_proposal_id"] is None else _run_id(r["superseded_proposal_id"]),
            None
            if r["superseded_proposal_hash"] is None
            else _hash(r["superseded_proposal_hash"], "superseded_proposal_hash"),
            None if r["feedback"] is None else RiskRejectionFeedback.from_wire(r["feedback"]),
            _hash(r["context_hash"], "context_hash"),
        )


def build_proposal_context(
    *,
    meta: ContractMeta,
    attempt: int,
    bundle: ResearchBundle,
    snapshot: PortfolioSnapshot,
    allowed_symbols: Sequence[str],
    graph_version: str,
    prompt_version: str,
    model_version: str,
    provider_version: str,
    data_version: str,
    memory_version: str,
    previous_context_id: RunId | None = None,
    superseded_proposal_id: RunId | None = None,
    superseded_proposal_hash: str | None = None,
    feedback: RiskRejectionFeedback | None = None,
) -> ProposalContext:
    """Build a context while deriving, never trusting, context_id and context_hash."""
    provisional = object.__new__(ProposalContext)
    bundle.validate_integrity()
    snapshot.validate_integrity()
    exact_allowed_symbols = (*bundle.holding_symbols, *bundle.candidate_symbols)
    if tuple(allowed_symbols) != exact_allowed_symbols:
        raise ValueError("context allowed symbols must equal the frozen bundle universe")
    context_id = derive_context_id(
        bundle.bundle_id,
        attempt,
        snapshot.content_hash,
        superseded_proposal_id,
        superseded_proposal_hash,
    )
    for name, value in {
        "meta": meta,
        "context_id": context_id,
        "attempt": attempt,
        "bundle_id": bundle.bundle_id,
        "bundle_hash": bundle.bundle_hash,
        "snapshot": snapshot,
        "snapshot_hash": snapshot.content_hash,
        "window": bundle.window,
        "deadline": bundle.deadline,
        "universe_hash": bundle.universe_hash,
        "allowed_symbols": exact_allowed_symbols,
        "citation_ids": bundle.citation_ids,
        "graph_version": graph_version,
        "prompt_version": prompt_version,
        "model_version": model_version,
        "provider_version": provider_version,
        "data_version": data_version,
        "memory_version": memory_version,
        "previous_context_id": previous_context_id,
        "superseded_proposal_id": superseded_proposal_id,
        "superseded_proposal_hash": superseded_proposal_hash,
        "feedback": feedback,
        "context_hash": "0" * 64,
    }.items():
        object.__setattr__(provisional, name, value)
    return ProposalContext(
        meta,
        context_id,
        attempt,
        bundle.bundle_id,
        bundle.bundle_hash,
        snapshot,
        snapshot.content_hash,
        bundle.window,
        bundle.deadline,
        bundle.universe_hash,
        exact_allowed_symbols,
        bundle.citation_ids,
        graph_version,
        prompt_version,
        model_version,
        provider_version,
        data_version,
        memory_version,
        previous_context_id,
        superseded_proposal_id,
        superseded_proposal_hash,
        feedback,
        provisional.compute_hash(),
    )


@dataclass(frozen=True, slots=True)
class RiskArgument:
    """One bounded, evidence-closed viewpoint argument inside the fixed risk debate."""

    meta: ContractMeta
    argument_id: RunId
    context_id: RunId
    bundle_id: RunId
    bundle_hash: str
    viewpoint: RiskViewpoint
    round_number: int
    argument: str
    evidence_refs: tuple[str, ...]
    producer_version: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "argument_id",
            "context_id",
            "bundle_id",
            "bundle_hash",
            "viewpoint",
            "round_number",
            "argument",
            "evidence_refs",
            "producer_version",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        self.meta.__post_init__()
        _require_type(self.argument_id, RunId, "argument_id")
        _require_type(self.context_id, RunId, "context_id")
        _require_type(self.bundle_id, RunId, "bundle_id")
        _hash(self.bundle_hash, "bundle_hash")
        _require_type(self.viewpoint, RiskViewpoint, "viewpoint")
        _integer(self.round_number, "round_number", minimum=1, maximum=2)
        object.__setattr__(self, "argument", _text(self.argument, "argument", maximum=2_048))
        object.__setattr__(
            self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs", nonempty=True)
        )
        object.__setattr__(
            self, "producer_version", _version(self.producer_version, "producer_version")
        )
        if self.argument_id != derive_argument_id(
            self.context_id, self.viewpoint, self.round_number
        ):
            raise ValueError("risk argument identity does not match deterministic derivation")

    def validate_integrity(self) -> None:
        """Re-run metadata and argument invariants at an authority boundary."""
        self.__post_init__()

    def validate_against_citations(self, citation_ids: Sequence[str]) -> None:
        """Prove every cited fragment belongs to the frozen bundle citation set."""
        frozen = set(citation_ids)
        if not set(self.evidence_refs) <= frozen:
            raise ValueError("risk argument cites evidence outside the frozen bundle set")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "argument_id": str(self.argument_id),
            "context_id": str(self.context_id),
            "bundle_id": str(self.bundle_id),
            "bundle_hash": self.bundle_hash,
            "viewpoint": self.viewpoint.value,
            "round_number": self.round_number,
            "argument": self.argument,
            "evidence_refs": list(self.evidence_refs),
            "producer_version": self.producer_version,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["argument_id"]),
            _run_id(r["context_id"]),
            _run_id(r["bundle_id"]),
            _hash(r["bundle_hash"], "bundle_hash"),
            cast(RiskViewpoint, _exact_enum(r["viewpoint"], RiskViewpoint, "viewpoint")),
            _integer(r["round_number"], "round_number", minimum=1, maximum=2),
            _text(r["argument"], "argument", maximum=2_048),
            _refs(r["evidence_refs"], "evidence_refs", nonempty=True),
            _version(r["producer_version"], "producer_version"),
        )


@dataclass(frozen=True, slots=True)
class RiskDebateState:
    """Exactly six fixed-order risk arguments; anything partial is never an authority."""

    meta: ContractMeta
    debate_id: RunId
    context_id: RunId
    bundle_id: RunId
    bundle_hash: str
    arguments: tuple[RiskArgument, ...]
    complete: bool
    debate_hash: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "debate_id",
            "context_id",
            "bundle_id",
            "bundle_hash",
            "arguments",
            "complete",
            "debate_hash",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        self.meta.__post_init__()
        _require_type(self.debate_id, RunId, "debate_id")
        _require_type(self.context_id, RunId, "context_id")
        _require_type(self.bundle_id, RunId, "bundle_id")
        _hash(self.bundle_hash, "bundle_hash")
        if (
            type(self.arguments) not in {list, tuple}
            or len(self.arguments) not in {0, 6}
            or any(type(item) is not RiskArgument for item in self.arguments)
        ):
            raise ValueError("risk debate requires zero or exactly six complete arguments")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        if self.debate_id != derive_debate_id(self.context_id):
            raise ValueError("debate identity does not match deterministic derivation")
        if type(self.complete) is not bool:
            raise ValueError("complete must be an exact bool")
        if self.complete != (len(self.arguments) == 6):
            raise ValueError("debate completion requires exactly six arguments")
        for position, item in enumerate(self.arguments):
            item.validate_integrity()
            viewpoint, round_number = DEBATE_ORDER[position]
            if item.viewpoint is not viewpoint or item.round_number != round_number:
                raise ValueError("risk debate argument order is fixed by viewpoint and round")
            if (
                item.context_id != self.context_id
                or item.bundle_id != self.bundle_id
                or item.bundle_hash != self.bundle_hash
                or item.meta != self.meta
                or item.producer_version != self.meta.producer_version
            ):
                raise ValueError("risk debate argument identity is invalid")
        if len(self.arguments) == 6 and len({item.argument_id for item in self.arguments}) != 6:
            raise ValueError("risk debate arguments must be distinct")
        _hash(self.debate_hash, "debate_hash")
        if self.debate_hash != self.compute_hash():
            raise ValueError("debate_hash does not match frozen debate content")

    def validate_integrity(self) -> None:
        """Re-run every nested argument and aggregate debate invariant."""
        self.__post_init__()

    def validate_citations(self, citation_ids: Sequence[str]) -> None:
        """Prove the whole debate cites only within an external frozen citation set."""
        for item in self.arguments:
            item.validate_against_citations(citation_ids)

    @property
    def frozen_citations(self) -> tuple[str, ...]:
        return tuple(sorted({ref for item in self.arguments for ref in item.evidence_refs}))

    def _content_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "debate_id": str(self.debate_id),
            "context_id": str(self.context_id),
            "bundle_id": str(self.bundle_id),
            "bundle_hash": self.bundle_hash,
            "arguments": [item.to_wire() for item in self.arguments],
            "complete": self.complete,
        }

    def compute_hash(self) -> str:
        return hashlib.sha256(
            JsonObject.from_value(self._content_wire()).to_json().encode()
        ).hexdigest()

    def to_wire(self) -> dict[str, JsonValue]:
        return {**self._content_wire(), "debate_hash": self.debate_hash}

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        if type(r["complete"]) is not bool:
            raise ValueError("complete must be an exact bool")
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["debate_id"]),
            _run_id(r["context_id"]),
            _run_id(r["bundle_id"]),
            _hash(r["bundle_hash"], "bundle_hash"),
            cast(
                tuple[RiskArgument, ...],
                _sequence(
                    r["arguments"], "arguments", RiskArgument.from_wire, maximum=6, unique=False
                ),
            ),
            r["complete"],
            _hash(r["debate_hash"], "debate_hash"),
        )


def build_risk_debate(
    *,
    meta: ContractMeta,
    context_id: RunId,
    bundle: ResearchBundle,
    arguments: Sequence[RiskArgument],
) -> RiskDebateState:
    """Build a debate while deriving, never trusting, debate_id and debate_hash."""
    provisional = object.__new__(RiskDebateState)
    for name, value in {
        "meta": meta,
        "debate_id": derive_debate_id(context_id),
        "context_id": context_id,
        "bundle_id": bundle.bundle_id,
        "bundle_hash": bundle.bundle_hash,
        "arguments": tuple(arguments),
        "complete": len(arguments) == 6,
        "debate_hash": "0" * 64,
    }.items():
        object.__setattr__(provisional, name, value)
    return RiskDebateState(
        meta,
        derive_debate_id(context_id),
        context_id,
        bundle.bundle_id,
        bundle.bundle_hash,
        tuple(arguments),
        len(arguments) == 6,
        provisional.compute_hash(),
    )


@dataclass(frozen=True, slots=True)
class PortfolioProposal:
    """The evolved strict proposal bound to one ProposalContext and one research bundle."""

    meta: ContractMeta
    proposal_id: RunId
    attempt: int
    context_id: RunId
    context_hash: str
    bundle_id: RunId
    bundle_hash: str
    superseded_proposal_id: RunId | None
    universe_hash: str
    snapshot_hash: str
    window: AnalysisWindow
    requests: tuple[PortfolioRequest, ...]
    graph_version: str
    prompt_version: str
    model_version: str
    provider_version: str
    data_version: str
    memory_version: str
    expiration_at: UtcTimestamp
    status: AnalysisStatus

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "proposal_id",
            "attempt",
            "context_id",
            "context_hash",
            "bundle_id",
            "bundle_hash",
            "superseded_proposal_id",
            "universe_hash",
            "snapshot_hash",
            "window",
            "requests",
            "graph_version",
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
            "expiration_at",
            "status",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        self.meta.__post_init__()
        _require_type(self.proposal_id, RunId, "proposal_id")
        _integer(self.attempt, "attempt", minimum=1, maximum=2)
        _require_type(self.context_id, RunId, "context_id")
        _hash(self.context_hash, "context_hash")
        _require_type(self.bundle_id, RunId, "bundle_id")
        _hash(self.bundle_hash, "bundle_hash")
        if self.superseded_proposal_id is not None:
            _require_type(self.superseded_proposal_id, RunId, "superseded_proposal_id")
        if (self.attempt == 1) != (self.superseded_proposal_id is None):
            raise ValueError("attempt 1 has no superseded id; attempt 2 requires one")
        if self.superseded_proposal_id == self.proposal_id:
            raise ValueError("proposal cannot supersede itself")
        if self.proposal_id != derive_proposal_id(self.context_id):
            raise ValueError("proposal identity does not match deterministic derivation")
        if self.meta.run_id != self.proposal_id:
            raise ValueError("proposal meta run identity must equal proposal_id")
        _hash(self.universe_hash, "universe_hash")
        _hash(self.snapshot_hash, "snapshot_hash")
        _require_type(self.window, AnalysisWindow, "window")
        if (
            type(self.requests) not in {list, tuple}
            or len(self.requests) > MAX_ALLOWED_SYMBOLS
            or any(type(x) is not PortfolioRequest for x in self.requests)
        ):
            raise ValueError("requests must be bounded exact PortfolioRequest values")
        object.__setattr__(self, "requests", tuple(self.requests))
        for request in self.requests:
            request.__post_init__()
        symbols = [request.symbol for request in self.requests]
        if len(symbols) != len(set(symbols)):
            raise ValueError("proposal request symbols must be unique")
        for name in (
            "graph_version",
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
        ):
            object.__setattr__(self, name, _version(getattr(self, name), name))
        _require_type(self.expiration_at, UtcTimestamp, "expiration_at")
        _require_type(self.status, AnalysisStatus, "status")
        if self.status is AnalysisStatus.VALID and not self.requests:
            raise ValueError("VALID proposal requires at least one request")
        if self.status is not AnalysisStatus.VALID and self.requests:
            raise ValueError("INVALID or ABSTAIN proposal must not contain requests")

    def validate_integrity(self) -> None:
        """Re-run nested request and aggregate proposal invariants on a built proposal."""
        self.__post_init__()

    def validate_against(self, context: ProposalContext) -> None:
        """Prove exact context, bundle, snapshot, universe, citation and deadline closure."""
        _require_type(context, ProposalContext, "context")
        context.validate_integrity()
        if (
            self.attempt != context.attempt
            or self.context_id != context.context_id
            or self.context_hash != context.context_hash
            or self.bundle_id != context.bundle_id
            or self.bundle_hash != context.bundle_hash
            or self.superseded_proposal_id != context.superseded_proposal_id
            or self.universe_hash != context.universe_hash
            or self.snapshot_hash != context.snapshot_hash
            or self.window is not context.window
            or self.meta.created_at != context.meta.created_at
            or self.meta.schema_version != context.meta.schema_version
        ):
            raise ValueError("proposal does not match the exact proposal context boundary")
        allowed = set(context.allowed_symbols)
        if any(request.symbol not in allowed for request in self.requests):
            raise ValueError("proposal request symbol is outside the allowed context universe")
        frozen = set(context.citation_ids)
        if any(not set(request.evidence_refs) <= frozen for request in self.requests):
            raise ValueError("proposal cites evidence outside the frozen bundle set")
        if context.window is AnalysisWindow.EMERGENCY and any(
            request.action.value in {"OPEN", "INCREASE"} for request in self.requests
        ):
            raise ValueError("emergency proposal cannot open or increase exposure")
        if self.expiration_at.value > context.deadline.value:
            raise ValueError("proposal expiration cannot exceed the context deadline")
        for name in (
            "graph_version",
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
        ):
            if getattr(self, name) != getattr(context, name):
                raise ValueError("proposal producer versions must equal the context versions")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "proposal_id": str(self.proposal_id),
            "attempt": self.attempt,
            "context_id": str(self.context_id),
            "context_hash": self.context_hash,
            "bundle_id": str(self.bundle_id),
            "bundle_hash": self.bundle_hash,
            "superseded_proposal_id": None
            if self.superseded_proposal_id is None
            else str(self.superseded_proposal_id),
            "universe_hash": self.universe_hash,
            "snapshot_hash": self.snapshot_hash,
            "window": self.window.value,
            "requests": [x.to_wire() for x in self.requests],
            "graph_version": self.graph_version,
            "prompt_version": self.prompt_version,
            "model_version": self.model_version,
            "provider_version": self.provider_version,
            "data_version": self.data_version,
            "memory_version": self.memory_version,
            "expiration_at": str(self.expiration_at),
            "status": self.status.value,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        superseded = (
            None if r["superseded_proposal_id"] is None else _run_id(r["superseded_proposal_id"])
        )
        requests = cast(
            tuple[PortfolioRequest, ...],
            _sequence(
                r["requests"],
                "requests",
                PortfolioRequest.from_wire,
                maximum=MAX_ALLOWED_SYMBOLS,
                unique=False,
            ),
        )
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["proposal_id"]),
            _integer(r["attempt"], "attempt", minimum=1, maximum=2),
            _run_id(r["context_id"]),
            _hash(r["context_hash"], "context_hash"),
            _run_id(r["bundle_id"]),
            _hash(r["bundle_hash"], "bundle_hash"),
            superseded,
            _hash(r["universe_hash"], "universe_hash"),
            _hash(r["snapshot_hash"], "snapshot_hash"),
            cast(AnalysisWindow, _exact_enum(r["window"], AnalysisWindow, "window")),
            requests,
            _version(r["graph_version"], "graph_version"),
            _version(r["prompt_version"], "prompt_version"),
            _version(r["model_version"], "model_version"),
            _version(r["provider_version"], "provider_version"),
            _version(r["data_version"], "data_version"),
            _version(r["memory_version"], "memory_version"),
            _timestamp(r["expiration_at"]),
            cast(AnalysisStatus, _exact_enum(r["status"], AnalysisStatus, "status")),
        )


def build_portfolio_proposal(
    *,
    context: ProposalContext,
    requests: Sequence[PortfolioRequest],
    expiration_at: UtcTimestamp,
    status: AnalysisStatus,
) -> PortfolioProposal:
    """Build a proposal while deriving, never trusting, proposal_id and lineage fields."""
    context.validate_integrity()
    provisional = object.__new__(PortfolioProposal)
    proposal_id = derive_proposal_id(context.context_id)
    for name, value in {
        "meta": ContractMeta(
            SCHEMA_VERSION, proposal_id, context.meta.created_at, context.meta.producer_version
        ),
        "proposal_id": proposal_id,
        "attempt": context.attempt,
        "context_id": context.context_id,
        "context_hash": context.context_hash,
        "bundle_id": context.bundle_id,
        "bundle_hash": context.bundle_hash,
        "superseded_proposal_id": context.superseded_proposal_id,
        "universe_hash": context.universe_hash,
        "snapshot_hash": context.snapshot_hash,
        "window": context.window,
        "requests": tuple(requests),
        "graph_version": context.graph_version,
        "prompt_version": context.prompt_version,
        "model_version": context.model_version,
        "provider_version": context.provider_version,
        "data_version": context.data_version,
        "memory_version": context.memory_version,
        "expiration_at": expiration_at,
        "status": status,
    }.items():
        object.__setattr__(provisional, name, value)
    return PortfolioProposal(
        ContractMeta(
            SCHEMA_VERSION, proposal_id, context.meta.created_at, context.meta.producer_version
        ),
        proposal_id,
        context.attempt,
        context.context_id,
        context.context_hash,
        context.bundle_id,
        context.bundle_hash,
        context.superseded_proposal_id,
        context.universe_hash,
        context.snapshot_hash,
        context.window,
        tuple(requests),
        context.graph_version,
        context.prompt_version,
        context.model_version,
        context.provider_version,
        context.data_version,
        context.memory_version,
        expiration_at,
        status,
    )
