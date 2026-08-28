"""P4-B quarantine authority: split confirmation and entry quarantine.

This module is the single authoritative evaluator for corporate-action
confirmation and entry quarantine.  Any qualified discovery blocks entry
immediately; automatic confirmation requires every §4 prerequisite at once:
exact identity closure, complete exact facts, at least one SEC, issuer-IR, or
listing-exchange official announcement, every read source available by
decision time, no contradictions or withdrawals, and auditable source
identity.  Conflicts are never resolved by source votes, and the absence of a
provider's data is never counter-evidence.

The same module owns the unified entry-quarantine query: candidate creation,
P4 Risk approval, and future submit-time recheck all call one evaluator with
one purpose marker and receive canonical-identical, hash-auditable decisions.
Uncertainty never degrades to ELIGIBLE, and no failure path is swallowed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Final

from seven_lens.domain.value_objects import TradingDate, UtcTimestamp
from seven_lens.securities.contracts import (
    SecurityId,
    SecurityIdentityRecord,
    SecuritySymbol,
    SourceRef,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionRecord,
    CorporateActionState,
    CorporateActionType,
    SplitRatio,
    validate_lineage,
)
from seven_lens.securities.identity import (
    IdentityQuery,
    IdentityResolutionStatus,
    resolve_identity,
)
from seven_lens.sources.roles import P4SourceFamily

MAX_OBSERVATIONS: Final = 64
MAX_EVENT_LINEAGES: Final = 16
MAX_DECISION_SOURCE_REFS: Final = 256

_DECISION_HASH_DOMAIN: Final = b"seven-lens.p4b.quarantine-decision.v1\x00"
_QUARANTINE_PRODUCER_VERSION: Final = "p4b.quarantine.v1"
_MAX_MASTER_VERSION_LENGTH: Final = 128
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")

_OFFICIAL_ANNOUNCEMENT_FAMILIES: Final[frozenset[P4SourceFamily]] = frozenset(
    {
        P4SourceFamily.SEC_EDGAR,
        P4SourceFamily.ISSUER_IR,
        P4SourceFamily.EXCHANGE_OFFICIAL,
    }
)

_PRE_CONFIRMATION_STATES: Final[frozenset[CorporateActionState]] = frozenset(
    {CorporateActionState.DETECTED, CorporateActionState.ENTRY_BLOCKED}
)


class QuarantineReason(StrEnum):
    """Closed quarantine reasons; semantics are never merged into a catch-all."""

    UNKNOWN_SECURITY = "UNKNOWN_SECURITY"
    AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"
    SYMBOL_AS_OF_MISMATCH = "SYMBOL_AS_OF_MISMATCH"
    IDENTITY_INTERVAL_CONFLICT = "IDENTITY_INTERVAL_CONFLICT"
    SOURCE_NOT_YET_AVAILABLE = "SOURCE_NOT_YET_AVAILABLE"
    STALE_SECURITY_MASTER = "STALE_SECURITY_MASTER"
    SPLIT_DETECTED = "SPLIT_DETECTED"
    FORMAL_CONFIRMATION_MISSING = "FORMAL_CONFIRMATION_MISSING"
    SPLIT_RATIO_CONFLICT = "SPLIT_RATIO_CONFLICT"
    SPLIT_DATE_CONFLICT = "SPLIT_DATE_CONFLICT"
    SPLIT_IDENTITY_CONFLICT = "SPLIT_IDENTITY_CONFLICT"
    SOURCE_WITHDRAWN_OR_CORRECTED = "SOURCE_WITHDRAWN_OR_CORRECTED"
    UNSUPPORTED_CORPORATE_ACTION = "UNSUPPORTED_CORPORATE_ACTION"
    EFFECTIVE_OR_LATE_EVENT_REVIEW = "EFFECTIVE_OR_LATE_EVENT_REVIEW"
    SPLIT_TYPE_CONFLICT = "SPLIT_TYPE_CONFLICT"


class ConfirmationOutcome(StrEnum):
    """Closed confirmation outcomes for one detected split event."""

    ENTRY_BLOCKED = "ENTRY_BLOCKED"
    CONFIRMED = "CONFIRMED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class QuarantineOutcome(StrEnum):
    """Closed entry-quarantine outcomes; a bool is never enough."""

    ELIGIBLE = "ELIGIBLE"
    ENTRY_BLOCKED = "ENTRY_BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class QuarantinePurpose(StrEnum):
    """The three caller seams; all share one evaluator and one decision rule."""

    CANDIDATE_CREATION = "CANDIDATE_CREATION"
    RISK_APPROVAL = "RISK_APPROVAL"
    SUBMIT_RECHECK = "SUBMIT_RECHECK"


def master_version_for(identity: SecurityIdentityRecord) -> str:
    """Return the canonical security-master version token for one identity."""
    if type(identity) is not SecurityIdentityRecord:
        raise ValueError("master version requires an exact SecurityIdentityRecord")
    return f"{identity.producer_version}:{identity.identity_hash}"


@dataclass(frozen=True, slots=True)
class QuarantineQuery:
    """One entry-quarantine query; identical in shape for all three seams."""

    purpose: QuarantinePurpose
    security_id: SecurityId
    symbol_as_of: SecuritySymbol
    decision_at: UtcTimestamp
    master_version: str

    def __post_init__(self) -> None:
        if type(self.purpose) is not QuarantinePurpose:
            raise ValueError("purpose requires an exact QuarantinePurpose")
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.symbol_as_of) is not SecuritySymbol:
            raise ValueError("symbol_as_of requires an exact SecuritySymbol")
        if type(self.decision_at) is not UtcTimestamp:
            raise ValueError("decision_at requires canonical UTC")
        if (
            type(self.master_version) is not str
            or not self.master_version
            or len(self.master_version) > _MAX_MASTER_VERSION_LENGTH
        ):
            raise ValueError("master_version must be non-empty bounded text")


@dataclass(frozen=True, slots=True)
class EventEvidence:
    """One corporate-action lineage plus every source observation read for it.

    The lineage must be legal from its ``DETECTED`` root, and the observations
    must cover every source the detection itself was read from.
    """

    lineage: tuple[CorporateActionRecord, ...]
    observations: tuple[SourceObservation, ...]

    def __post_init__(self) -> None:
        validate_lineage(self.lineage)
        _validate_observations(self.observations, self.lineage)


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """One read source record about a split event.

    Claims are explicit; ``None`` means the source is silent on that fact, and
    silence never contradicts.  ``withdrawn`` marks append-only supersession
    of the source; ``auditable`` marks that content hash, provider id, and URL
    identity were verified for it.
    """

    source_ref: SourceRef
    available_at: UtcTimestamp
    withdrawn: bool
    auditable: bool
    claimed_type: CorporateActionType | None = None
    claimed_ratio: SplitRatio | None = None
    claimed_ex_date: TradingDate | None = None
    claimed_effective_date: TradingDate | None = None

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceRef:
            raise ValueError("source_ref requires an exact SourceRef")
        if type(self.available_at) is not UtcTimestamp:
            raise ValueError("available_at requires canonical UTC")
        if type(self.withdrawn) is not bool:
            raise ValueError("withdrawn must be an exact bool")
        if type(self.auditable) is not bool:
            raise ValueError("auditable must be an exact bool")
        if self.claimed_type is not None and type(self.claimed_type) is not CorporateActionType:
            raise ValueError("claimed_type requires an exact CorporateActionType or None")
        if self.claimed_ratio is not None and type(self.claimed_ratio) is not SplitRatio:
            raise ValueError("claimed_ratio requires an exact SplitRatio or None")
        if self.claimed_ex_date is not None and type(self.claimed_ex_date) is not TradingDate:
            raise ValueError("claimed_ex_date requires an exact TradingDate or None")
        if (
            self.claimed_effective_date is not None
            and type(self.claimed_effective_date) is not TradingDate
        ):
            raise ValueError("claimed_effective_date requires an exact TradingDate or None")


@dataclass(frozen=True, slots=True)
class ConfirmationEvaluation:
    """The closed result of evaluating one detected split event."""

    outcome: ConfirmationOutcome
    reasons: tuple[QuarantineReason, ...]


def _sorted_reasons(reasons: set[QuarantineReason]) -> tuple[QuarantineReason, ...]:
    order = {reason: index for index, reason in enumerate(QuarantineReason)}
    return tuple(sorted(reasons, key=order.__getitem__))


def _validate_observations(
    observations: object, lineage: tuple[CorporateActionRecord, ...]
) -> tuple[SourceObservation, ...]:
    if type(observations) is not tuple or not observations or len(observations) > MAX_OBSERVATIONS:
        raise ValueError(
            f"observations must be a non-empty tuple of at most {MAX_OBSERVATIONS} values"
        )
    if any(type(observation) is not SourceObservation for observation in observations):
        raise ValueError("observations require exact SourceObservation values")
    record_ids = [observation.source_ref.record_id for observation in observations]
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("observations must carry unique record identifiers")
    observed = {
        (observation.source_ref.record_id, observation.source_ref.record_hash)
        for observation in observations
    }
    # The root is the minimum source closure needed to evaluate confirmation.
    # Later transition rows may add evidence that is intentionally absent from
    # a replay payload; the head then fails closed as evidence-regressed rather
    # than raising before the caller receives a REVIEW_REQUIRED result.
    for ref in lineage[0].source_refs:
        if (ref.record_id, ref.record_hash) not in observed:
            raise ValueError("observations must cover every event source ref")
    return observations


def _fact_conflicts(
    event: CorporateActionRecord, observations: tuple[SourceObservation, ...]
) -> set[QuarantineReason]:
    findings: set[QuarantineReason] = set()
    claimed_types = {o.claimed_type for o in observations if o.claimed_type is not None}
    if len(claimed_types) > 1 or any(claimed != event.action_type for claimed in claimed_types):
        findings.add(QuarantineReason.SPLIT_TYPE_CONFLICT)
    claimed_ratios = {o.claimed_ratio for o in observations if o.claimed_ratio is not None}
    if len(claimed_ratios) > 1 or any(claimed != event.ratio for claimed in claimed_ratios):
        findings.add(QuarantineReason.SPLIT_RATIO_CONFLICT)
    claimed_ex_dates = {o.claimed_ex_date for o in observations if o.claimed_ex_date is not None}
    if len(claimed_ex_dates) > 1 or any(claimed != event.ex_date for claimed in claimed_ex_dates):
        findings.add(QuarantineReason.SPLIT_DATE_CONFLICT)
    claimed_effective_dates = {
        o.claimed_effective_date for o in observations if o.claimed_effective_date is not None
    }
    if len(claimed_effective_dates) > 1 or any(
        claimed != event.effective_date for claimed in claimed_effective_dates
    ):
        findings.add(QuarantineReason.SPLIT_DATE_CONFLICT)
    return findings


def _qualifying_official(observation: SourceObservation, decision_at: UtcTimestamp) -> bool:
    return (
        observation.source_ref.family in _OFFICIAL_ANNOUNCEMENT_FAMILIES
        and not observation.withdrawn
        and observation.auditable
        and observation.available_at.value <= decision_at.value
        and observation.claimed_type is not None
        and observation.claimed_ratio is not None
        and observation.claimed_ex_date is not None
        and observation.claimed_effective_date is not None
    )


def evaluate_confirmation(
    *,
    event: CorporateActionRecord,
    identity: SecurityIdentityRecord,
    observations: tuple[SourceObservation, ...],
    decision_at: UtcTimestamp,
) -> ConfirmationEvaluation:
    """Evaluate one detected split event against every read source.

    Any conflict, withdrawal, late source, identity drift, or already-
    effective event forces ``REVIEW_REQUIRED``; otherwise at least one
    auditable official announcement yields ``CONFIRMED``, and anything less
    stays ``ENTRY_BLOCKED``.  Reasons are closed codes in canonical order;
    free text never participates.
    """
    if type(event) is not CorporateActionRecord:
        raise ValueError("event requires an exact CorporateActionRecord")
    if type(identity) is not SecurityIdentityRecord:
        raise ValueError("identity requires an exact SecurityIdentityRecord")
    if type(decision_at) is not UtcTimestamp:
        raise ValueError("decision_at requires canonical UTC")
    if event.state not in _PRE_CONFIRMATION_STATES:
        raise ValueError("confirmation evaluation requires a pre-confirmation event state")
    event.verify_integrity()
    identity.verify_integrity()
    _validate_observations(observations, (event,))

    findings: set[QuarantineReason] = set()
    if (
        identity.security_id != event.security_id
        or identity.identity_hash != event.security_identity_hash
    ):
        findings.add(QuarantineReason.SPLIT_IDENTITY_CONFLICT)
    if decision_at.value.date() >= event.ex_date.value:
        findings.add(QuarantineReason.EFFECTIVE_OR_LATE_EVENT_REVIEW)
    for observation in observations:
        if observation.available_at.value > decision_at.value:
            findings.add(QuarantineReason.SOURCE_NOT_YET_AVAILABLE)
        if observation.withdrawn:
            findings.add(QuarantineReason.SOURCE_WITHDRAWN_OR_CORRECTED)
    findings.update(_fact_conflicts(event, observations))

    if findings:
        findings.add(QuarantineReason.SPLIT_DETECTED)
        return ConfirmationEvaluation(
            outcome=ConfirmationOutcome.REVIEW_REQUIRED, reasons=_sorted_reasons(findings)
        )

    officially_confirmed = any(
        _qualifying_official(observation, decision_at) for observation in observations
    )
    fully_auditable = all(observation.auditable for observation in observations)
    if not officially_confirmed or not fully_auditable:
        return ConfirmationEvaluation(
            outcome=ConfirmationOutcome.ENTRY_BLOCKED,
            reasons=(
                QuarantineReason.SPLIT_DETECTED,
                QuarantineReason.FORMAL_CONFIRMATION_MISSING,
            ),
        )
    return ConfirmationEvaluation(outcome=ConfirmationOutcome.CONFIRMED, reasons=())


@dataclass(frozen=True, slots=True)
class QuarantineDecision:
    """The closed, hash-auditable entry-quarantine decision.

    Carries exactly the §8B audit fields.  The caller's purpose marker is
    deliberately not part of the decision content: it never changes the
    decision, so it never enters the wire form or the hash.
    """

    security_id: SecurityId
    symbol_as_of: SecuritySymbol
    master_version: str
    decision_at: UtcTimestamp
    outcome: QuarantineOutcome
    reasons: tuple[QuarantineReason, ...]
    event_ids: tuple[str, ...]
    source_refs: tuple[SourceRef, ...]
    decision_hash: str

    def __post_init__(self) -> None:
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.symbol_as_of) is not SecuritySymbol:
            raise ValueError("symbol_as_of requires an exact SecuritySymbol")
        if (
            type(self.master_version) is not str
            or not self.master_version
            or len(self.master_version) > _MAX_MASTER_VERSION_LENGTH
        ):
            raise ValueError("master_version must be non-empty bounded text")
        if type(self.decision_at) is not UtcTimestamp:
            raise ValueError("decision_at requires canonical UTC")
        if type(self.outcome) is not QuarantineOutcome:
            raise ValueError("outcome requires an exact QuarantineOutcome")
        if type(self.reasons) is not tuple or any(
            type(reason) is not QuarantineReason for reason in self.reasons
        ):
            raise ValueError("reasons require exact QuarantineReason values")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("reasons must be unique")
        if self.reasons != _sorted_reasons(set(self.reasons)):
            raise ValueError("reasons must use the canonical order")
        if type(self.event_ids) is not tuple or any(
            type(event_id) is not str or _EVENT_ID.fullmatch(event_id) is None
            for event_id in self.event_ids
        ):
            raise ValueError("event_ids require canonical event identifiers")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event_ids must be unique")
        if self.event_ids != tuple(sorted(self.event_ids)):
            raise ValueError("event_ids must use the canonical order")
        if (
            type(self.source_refs) is not tuple
            or len(self.source_refs) > MAX_DECISION_SOURCE_REFS
            or any(type(ref) is not SourceRef for ref in self.source_refs)
        ):
            raise ValueError(
                "source_refs must be a tuple of at most "
                f"{MAX_DECISION_SOURCE_REFS} SourceRef values"
            )
        source_keys = [
            (ref.record_id, ref.family.value, ref.record_hash) for ref in self.source_refs
        ]
        if len({key[0] for key in source_keys}) != len(source_keys):
            raise ValueError("source_refs must carry unique record identifiers")
        if source_keys != sorted(source_keys):
            raise ValueError("source_refs must use the canonical order")
        if type(self.decision_hash) is not str or _HASH_TEXT.fullmatch(self.decision_hash) is None:
            raise ValueError("decision hash must be a SHA-256 digest")
        if self.decision_hash != self.compute_hash():
            raise ValueError("decision hash does not match frozen decision content")

    @property
    def producer_version(self) -> str:
        return _QUARANTINE_PRODUCER_VERSION

    def wire(self) -> dict[str, object]:
        """Return the canonical content used for the decision hash."""
        return {
            "security_id": self.security_id.value,
            "symbol_as_of": self.symbol_as_of.value,
            "master_version": self.master_version,
            "decision_at": str(self.decision_at),
            "outcome": self.outcome.value,
            "reasons": [reason.value for reason in self.reasons],
            "event_ids": list(self.event_ids),
            "source_refs": [
                {
                    "record_id": ref.record_id,
                    "family": ref.family.value,
                    "record_hash": ref.record_hash,
                }
                for ref in self.source_refs
            ],
            "producer_version": self.producer_version,
        }

    def compute_hash(self) -> str:
        canonical = json.dumps(
            self.wire(), allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return sha256(_DECISION_HASH_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        if self.decision_hash != self.compute_hash():
            raise ValueError("decision hash does not match frozen decision content")
        return True


def _build_decision(**body: object) -> QuarantineDecision:
    provisional = object.__new__(QuarantineDecision)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "decision_hash", "")
    computed = provisional.compute_hash()
    return QuarantineDecision(**body, decision_hash=computed)  # type: ignore[arg-type]


_OUTCOME_RANK: Final[Mapping[QuarantineOutcome, int]] = {
    QuarantineOutcome.ELIGIBLE: 0,
    QuarantineOutcome.ENTRY_BLOCKED: 1,
    QuarantineOutcome.REVIEW_REQUIRED: 2,
}

_STATUS_TO_REASON: Final[Mapping[IdentityResolutionStatus, QuarantineReason]] = {
    IdentityResolutionStatus.UNKNOWN: QuarantineReason.UNKNOWN_SECURITY,
    IdentityResolutionStatus.AMBIGUOUS: QuarantineReason.AMBIGUOUS_IDENTITY,
    IdentityResolutionStatus.CONFLICT: QuarantineReason.IDENTITY_INTERVAL_CONFLICT,
    IdentityResolutionStatus.STALE: QuarantineReason.SOURCE_NOT_YET_AVAILABLE,
}


def _symbol_claims_are_ambiguous(
    records: tuple[SecurityIdentityRecord, ...], query: QuarantineQuery
) -> bool:
    """Return whether several distinct securities claim the symbol as-of.

    The resolver scopes by ``security_id`` when both keys are given, so
    cross-security symbol contention is reconciled here, never by the
    resolver and never by picking one claimant.
    """
    claimants = {
        record.security_id.value
        for record in records
        if record.symbol == query.symbol_as_of
        and record.answers_as_of(as_of=query.decision_at, known_at=query.decision_at)
    }
    return len(claimants) > 1


def _event_quarantine(
    head: CorporateActionRecord,
    root: CorporateActionRecord,
    resolved: SecurityIdentityRecord | None,
    evidence: EventEvidence,
    decision_at: UtcTimestamp,
) -> tuple[QuarantineOutcome, tuple[QuarantineReason, ...]]:
    """Combine one event's visible head with a fresh confirmation evaluation.

    The evaluation always runs from the ``DETECTED`` root so replayed
    decisions never trust a stale head; the visible head then guards against
    regressions the evaluation alone cannot see.
    """
    if resolved is None:
        return QuarantineOutcome.REVIEW_REQUIRED, (QuarantineReason.SPLIT_DETECTED,)
    evaluation = evaluate_confirmation(
        event=root,
        identity=resolved,
        observations=evidence.observations,
        decision_at=decision_at,
    )
    if evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED:
        return QuarantineOutcome.REVIEW_REQUIRED, evaluation.reasons
    if evaluation.outcome is ConfirmationOutcome.ENTRY_BLOCKED:
        if head.state in _PRE_CONFIRMATION_STATES:
            return QuarantineOutcome.ENTRY_BLOCKED, evaluation.reasons
        # The head advanced past blocking without auditable evidence still in
        # hand; a regression never reads back as safe.
        return QuarantineOutcome.REVIEW_REQUIRED, evaluation.reasons
    if head.state is CorporateActionState.CONFIRMED:
        # Confirmed but not yet effective and reconciled: entry stays blocked.
        return QuarantineOutcome.ENTRY_BLOCKED, (QuarantineReason.SPLIT_DETECTED,)
    if head.state is CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION:
        return QuarantineOutcome.REVIEW_REQUIRED, (
            QuarantineReason.SPLIT_DETECTED,
            QuarantineReason.EFFECTIVE_OR_LATE_EVENT_REVIEW,
        )
    # A CONFIRMED evaluation against a REVIEW_REQUIRED head: the review stands.
    return QuarantineOutcome.REVIEW_REQUIRED, (QuarantineReason.SPLIT_DETECTED,)


def _collect_source_refs(
    resolved: SecurityIdentityRecord | None, event_refs: list[SourceRef]
) -> tuple[SourceRef, ...]:
    seen: dict[str, SourceRef] = {}
    ordered: list[SourceRef] = []
    if resolved is not None:
        ordered.extend(resolved.source_refs)
    ordered.extend(event_refs)
    for ref in ordered:
        existing = seen.get(ref.record_id)
        if existing is not None and existing != ref:
            raise ValueError("source lineage has conflicting hashes for one record identifier")
        seen[ref.record_id] = ref
    deduped = sorted(
        seen.values(), key=lambda ref: (ref.record_id, ref.family.value, ref.record_hash)
    )
    if len(deduped) > MAX_DECISION_SOURCE_REFS:
        raise ValueError(f"decision source lineage exceeds {MAX_DECISION_SOURCE_REFS} record refs")
    return tuple(deduped)


def evaluate_quarantine(
    *,
    query: QuarantineQuery,
    identity_records: tuple[SecurityIdentityRecord, ...],
    event_lineages: tuple[EventEvidence, ...] = (),
) -> QuarantineDecision:
    """Answer one entry-quarantine query for all three caller seams.

    Failure ordering is fixed: validate the query, resolve the point-in-time
    identity, fail closed on any identity finding, then evaluate every
    corporate-action lineage visible at the decision time from its DETECTED
    root.  The purpose marker never changes the decision; uncertainty never
    degrades to ELIGIBLE; no exception is swallowed.
    """
    if type(query) is not QuarantineQuery:
        raise ValueError("query requires an exact QuarantineQuery")
    if (
        type(event_lineages) is not tuple
        or len(event_lineages) > MAX_EVENT_LINEAGES
        or any(type(evidence) is not EventEvidence for evidence in event_lineages)
    ):
        raise ValueError(
            f"event_lineages must be a tuple of at most {MAX_EVENT_LINEAGES} EventEvidence values"
        )
    event_ids_in_input = [evidence.lineage[0].event_id for evidence in event_lineages]
    if len(set(event_ids_in_input)) != len(event_ids_in_input):
        raise ValueError("event_lineages must carry unique event identifiers")

    findings: set[QuarantineReason] = set()
    resolution = resolve_identity(
        identity_records,
        IdentityQuery(
            as_of=query.decision_at,
            known_at=query.decision_at,
            security_id=query.security_id,
            symbol=query.symbol_as_of,
        ),
    )
    resolved: SecurityIdentityRecord | None = None
    if resolution.status is IdentityResolutionStatus.RESOLVED:
        record = resolution.record
        if record is None:
            raise ValueError("resolver returned RESOLVED without an identity record")
        resolved = record
        if resolved.symbol != query.symbol_as_of:
            findings.add(QuarantineReason.SYMBOL_AS_OF_MISMATCH)
        if query.master_version != master_version_for(resolved):
            findings.add(QuarantineReason.STALE_SECURITY_MASTER)
    else:
        findings.add(_STATUS_TO_REASON[resolution.status])
    if _symbol_claims_are_ambiguous(identity_records, query):
        findings.add(QuarantineReason.AMBIGUOUS_IDENTITY)

    event_outcome = QuarantineOutcome.ELIGIBLE
    event_reasons: set[QuarantineReason] = set()
    event_ids: set[str] = set()
    event_refs: list[SourceRef] = []
    for evidence in event_lineages:
        visible = tuple(row for row in evidence.lineage if row.known_at(query.decision_at))
        if not visible:
            continue
        head = visible[-1]
        if head.security_id != query.security_id:
            continue
        event_ids.add(head.event_id)
        event_refs.extend(head.source_refs)
        event_refs.extend(observation.source_ref for observation in evidence.observations)
        outcome, reasons = _event_quarantine(
            head, visible[0], resolved, evidence, query.decision_at
        )
        event_reasons.update(reasons)
        if _OUTCOME_RANK[outcome] > _OUTCOME_RANK[event_outcome]:
            event_outcome = outcome

    return _build_decision(
        security_id=query.security_id,
        symbol_as_of=query.symbol_as_of,
        master_version=query.master_version if resolved is None else master_version_for(resolved),
        decision_at=query.decision_at,
        outcome=QuarantineOutcome.REVIEW_REQUIRED if findings else event_outcome,
        reasons=_sorted_reasons(findings | event_reasons),
        event_ids=tuple(sorted(event_ids)),
        source_refs=_collect_source_refs(resolved, event_refs),
    )
