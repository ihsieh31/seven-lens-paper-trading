"""Public P4-B application entry points.

The service owns the required failure ordering: validate the typed input,
resolve the point-in-time identity, durably create the entry block, evaluate
confirmation, append a compare-and-swap transition, then emit only bounded
best-effort telemetry.  It never imports portfolio, risk, intent, broker, or
model capabilities.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from re import fullmatch
from typing import Final

from seven_lens.application.ports.p4_source_records import AppendOutcome, P4SourceRecordLog
from seven_lens.application.ports.securities import SecurityMasterRepository
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.securities.contracts import (
    SecurityIdentityRecord,
    SourceRef,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionRecord,
    CorporateActionState,
    build_corporate_action_record,
)
from seven_lens.securities.identity import (
    IdentityQuery,
    IdentityResolution,
    IdentityResolutionStatus,
    resolve_identity,
)
from seven_lens.securities.quarantine import (
    ConfirmationEvaluation,
    ConfirmationOutcome,
    EventEvidence,
    QuarantineDecision,
    QuarantinePurpose,
    QuarantineQuery,
    SourceObservation,
    evaluate_confirmation,
    evaluate_quarantine,
)
from seven_lens.sources.adapters.records import NormalizedSourceRecord
from seven_lens.sources.roles import P4SourceFamily

_EVENT_ID: Final = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$"


class SecurityMasterServiceError(RuntimeError):
    """Base error for a public P4-B service boundary failure."""


class SourceLineageError(SecurityMasterServiceError):
    """A source reference is unknown, mismatched, tampered, or corrected."""


class IdentityClosureError(SecurityMasterServiceError):
    """The event cannot be safely tied to one point-in-time identity."""


@dataclass(frozen=True, slots=True)
class SecurityMasterTelemetry:
    """Sanitized telemetry context; source payloads and credentials never enter it."""

    operation: str
    security_id: str
    state: str | None
    record_hash: str | None


class SourceRecordReader:
    """Exact, hash-bound reader over the accepted P4-A source-record log."""

    def __init__(self, records: P4SourceRecordLog) -> None:
        self._records = records

    def read(self, source_ref: SourceRef) -> NormalizedSourceRecord:
        """Read one exact historical version and re-verify its source hash."""
        if type(source_ref) is not SourceRef:
            raise SourceLineageError("source reader requires an exact SourceRef")
        record = self._records.get_version(source_ref.record_id, source_ref.record_hash)
        if record is None:
            raise SourceLineageError(
                f"source record {source_ref.record_id!r} at the requested hash is unknown"
            )
        if type(record) is not NormalizedSourceRecord:
            raise SourceLineageError("source record reader returned an invalid record type")
        try:
            record.verify_integrity()
        except ValueError as error:
            raise SourceLineageError("source record failed integrity re-verification") from error
        if record.record_id != source_ref.record_id or record.family is not source_ref.family:
            raise SourceLineageError("source reference does not match the stored record identity")
        if record.record_hash != source_ref.record_hash:
            raise SourceLineageError("source reference hash does not match the stored record")
        return record

    def available_at(self, source_ref: SourceRef) -> UtcTimestamp:
        """Return the immutable source availability instant used by the authority."""
        record = self.read(source_ref)
        return record.available_at or record.retrieved_at

    def is_current(self, source_ref: SourceRef) -> bool:
        """Return whether this exact source version remains the current head."""
        current = self._records.get(source_ref.record_id)
        if current is None:
            raise SourceLineageError("source record disappeared from the append-only log")
        try:
            current.verify_integrity()
        except ValueError as error:
            raise SourceLineageError(
                "current source record failed integrity re-verification"
            ) from error
        return current.record_hash == source_ref.record_hash and current.family is source_ref.family

    def is_current_at(self, source_ref: SourceRef, *, known_at: UtcTimestamp) -> bool:
        """Return whether this exact source version was current at a cutoff.

        A later correction that is only known after ``known_at`` must not
        rewrite a historical quarantine decision.  Equal-time competing
        versions are intentionally unorderable and therefore fail closed.
        """
        if type(known_at) is not UtcTimestamp:
            raise ValueError("known_at requires canonical UTC")
        target = self.read(source_ref)
        versions = self._records.versions(source_ref.record_id)
        if type(versions) is not tuple or any(
            type(version) is not NormalizedSourceRecord for version in versions
        ):
            raise SourceLineageError("source record log returned invalid version history")
        visible = tuple(
            version
            for version in versions
            if (version.available_at or version.retrieved_at).value <= known_at.value
        )
        if not visible:
            return False
        latest_available = max(
            (version.available_at or version.retrieved_at).value for version in visible
        )
        contenders = tuple(
            version
            for version in visible
            if (version.available_at or version.retrieved_at).value == latest_available
        )
        if len(contenders) != 1:
            return False
        current = contenders[0]
        return current.record_hash == target.record_hash and current.family is target.family

    def validate_refs(
        self, source_refs: Iterable[SourceRef], *, available_by: UtcTimestamp | None = None
    ) -> tuple[NormalizedSourceRecord, ...]:
        """Read unique source refs and optionally enforce a knowledge cutoff."""
        if type(available_by) not in (UtcTimestamp, type(None)):
            raise ValueError("available_by requires canonical UTC or None")
        records: list[NormalizedSourceRecord] = []
        seen: set[str] = set()
        for source_ref in source_refs:
            if source_ref.record_id in seen:
                raise SourceLineageError("source refs must carry unique record identifiers")
            seen.add(source_ref.record_id)
            record = self.read(source_ref)
            if (
                available_by is not None
                and self.available_at(source_ref).value > available_by.value
            ):
                raise SourceLineageError("source record is not available by the authority cutoff")
            records.append(record)
        return tuple(records)

    def lock_refs(self, source_refs: Iterable[SourceRef]) -> None:
        """Lock source identities before revalidation and a state CAS."""
        refs = tuple(source_refs)
        locker = getattr(self._records, "lock_record", None)
        if locker is None:
            return
        for record_id in sorted({source_ref.record_id for source_ref in refs}):
            locker(record_id)


class SecurityMasterService:
    """The sole public application seam for P4-B identity/event/quarantine work."""

    def __init__(
        self,
        repository: SecurityMasterRepository,
        source_records: P4SourceRecordLog,
        *,
        telemetry: Callable[[SecurityMasterTelemetry], None] | None = None,
    ) -> None:
        required_methods = (
            "append_identity",
            "identity_records",
            "append_event",
            "event_lineage",
            "security_event_ids",
            "record_decision",
            "latest_decision",
        )
        if any(not callable(getattr(repository, method, None)) for method in required_methods):
            raise TypeError("repository must implement the P4-B repository port")
        self._repository = repository
        self._source_reader = SourceRecordReader(source_records)
        if telemetry is not None and not callable(telemetry):
            raise TypeError("telemetry must be callable or None")
        self._telemetry = telemetry
        self.telemetry_failures = 0

    def register_identity(self, record: SecurityIdentityRecord) -> AppendOutcome:
        """Validate source lineage before appending one identity observation."""
        if type(record) is not SecurityIdentityRecord:
            raise ValueError("identity entry requires an exact SecurityIdentityRecord")
        record.verify_integrity()
        self._source_reader.validate_refs(record.source_refs, available_by=record.available_at)
        with self._repository_transaction():
            outcome = self._repository.append_identity(record)
            readback = self._repository.identity_records(security_id=record.security_id)
            if not any(
                candidate.identity_hash == record.identity_hash
                and candidate.wire() == record.wire()
                for candidate in readback
            ):
                raise SecurityMasterServiceError(
                    "identity append readback did not match the request"
                )
        self._emit(
            SecurityMasterTelemetry(
                operation="identity_append",
                security_id=record.security_id.value,
                state=None,
                record_hash=record.identity_hash,
            )
        )
        return outcome

    def discover_split(self, event: CorporateActionRecord) -> CorporateActionRecord:
        """Append a DETECTED root and its immediate durable ENTRY_BLOCKED row."""
        if type(event) is not CorporateActionRecord:
            raise ValueError("split discovery requires an exact CorporateActionRecord")
        if event.state is not CorporateActionState.DETECTED:
            raise ValueError("split discovery requires a DETECTED event")
        event.verify_integrity()
        self._source_reader.validate_refs(event.source_refs, available_by=event.available_at)
        self._require_pinned_identity(event, known_at=event.available_at)

        with self._repository_transaction():
            self._repository.append_event(event, previous_record_hash=None)
            blocked = _event_with_state(
                event,
                state=CorporateActionState.ENTRY_BLOCKED,
                available_at=event.available_at,
            )
            self._repository.append_event(blocked, previous_record_hash=event.record_hash)
            head = _require_head(self._repository.event_lineage(event.event_id))
        self._emit(
            SecurityMasterTelemetry(
                operation="split_discovery_blocked",
                security_id=head.security_id.value,
                state=head.state.value,
                record_hash=head.record_hash,
            )
        )
        return head

    def confirm_split(
        self,
        event_id: str,
        observations: tuple[SourceObservation, ...],
        *,
        decision_at: UtcTimestamp,
    ) -> CorporateActionRecord:
        """Evaluate source evidence and append one legal confirmation transition."""
        if type(event_id) is not str or fullmatch(_EVENT_ID, event_id) is None:
            raise ValueError("event_id must be a canonical event identifier")
        if type(decision_at) is not UtcTimestamp:
            raise ValueError("decision_at requires canonical UTC")
        if type(observations) is not tuple:
            raise ValueError("observations must be a tuple of SourceObservation values")

        if any(type(observation) is not SourceObservation for observation in observations):
            raise ValueError("observations require exact SourceObservation values")
        observation_refs = tuple(observation.source_ref for observation in observations)
        self._source_reader.validate_refs(observation_refs, available_by=None)

        with self._repository_transaction():
            self._source_reader.lock_refs(observation_refs)
            validated_observations = tuple(
                self._revalidate_observation(observation) for observation in observations
            )
            lineage = self._repository.event_lineage(event_id)
            head = _require_head(lineage)
            root = lineage[0]
            if head.state is CorporateActionState.DETECTED:
                blocked = _event_with_state(
                    head,
                    state=CorporateActionState.ENTRY_BLOCKED,
                    available_at=_later(head.available_at, decision_at),
                )
                self._repository.append_event(blocked, previous_record_hash=head.record_hash)
                lineage = self._repository.event_lineage(event_id)
                head = _require_head(lineage)

            resolution = self._identity_resolution_for_event(root, known_at=decision_at)
            if resolution.status is not IdentityResolutionStatus.RESOLVED:
                result = self._append_review_if_possible(
                    head, validated_observations, decision_at=decision_at
                )
            else:
                identity = resolution.record
                if identity is None:
                    raise IdentityClosureError("identity resolver returned no record for RESOLVED")
                if identity.identity_hash != root.security_identity_hash:
                    result = self._append_review_if_possible(
                        head, validated_observations, decision_at=decision_at
                    )
                else:
                    evaluation = evaluate_confirmation(
                        event=root,
                        identity=identity,
                        observations=validated_observations,
                        decision_at=decision_at,
                    )
                    result = self._apply_confirmation_evaluation(
                        head,
                        evaluation,
                        validated_observations,
                        decision_at=decision_at,
                    )
            result = _require_head(self._repository.event_lineage(event_id))

        self._emit(
            SecurityMasterTelemetry(
                operation="split_confirmation_evaluated",
                security_id=result.security_id.value,
                state=result.state.value,
                record_hash=result.record_hash,
            )
        )
        return result

    def quarantine(self, query: QuarantineQuery) -> QuarantineDecision:
        """Run and persist the one evaluator used by all three caller seams."""
        if type(query) is not QuarantineQuery:
            raise ValueError("quarantine requires an exact QuarantineQuery")
        identity_records = _dedupe_identities(
            (
                *self._repository.identity_records(security_id=query.security_id),
                *self._repository.identity_records(symbol=query.symbol_as_of),
            )
        )
        for record in identity_records:
            self._source_reader.validate_refs(record.source_refs)

        evidence: list[EventEvidence] = []
        for event_id in self._repository.security_event_ids(query.security_id):
            lineage = self._repository.event_lineage(event_id)
            if not lineage:
                continue
            visible = tuple(row for row in lineage if row.known_at(query.decision_at))
            if not visible:
                continue
            refs = _merge_refs(*(record.source_refs for record in visible))
            observations = tuple(
                self._observation_from_stored_ref(ref, visible[-1], known_at=query.decision_at)
                for ref in refs
            )
            evidence.append(EventEvidence(lineage=visible, observations=observations))

        decision = evaluate_quarantine(
            query=query,
            identity_records=identity_records,
            event_lineages=tuple(evidence),
        )
        with self._repository_transaction():
            self._repository.record_decision(decision)
            readback = self._repository.latest_decision(decision.security_id)
            if readback is None or readback.wire() != decision.wire():
                raise SecurityMasterServiceError(
                    "quarantine decision readback did not match the request"
                )
            decision = readback
        self._emit(
            SecurityMasterTelemetry(
                operation="quarantine_decision",
                security_id=decision.security_id.value,
                state=decision.outcome.value,
                record_hash=decision.decision_hash,
            )
        )
        return decision

    def candidate_creation_check(self, query: QuarantineQuery) -> QuarantineDecision:
        """Candidate-creation seam delegating to the shared evaluator."""
        _require_purpose(query, QuarantinePurpose.CANDIDATE_CREATION)
        return self.quarantine(query)

    def risk_approval_check(self, query: QuarantineQuery) -> QuarantineDecision:
        """Risk-approval seam delegating to the shared evaluator."""
        _require_purpose(query, QuarantinePurpose.RISK_APPROVAL)
        return self.quarantine(query)

    def submit_recheck(self, query: QuarantineQuery) -> QuarantineDecision:
        """Submit-time seam delegating to the shared evaluator."""
        _require_purpose(query, QuarantinePurpose.SUBMIT_RECHECK)
        return self.quarantine(query)

    def _identity_resolution_for_event(
        self, event: CorporateActionRecord, *, known_at: UtcTimestamp
    ) -> IdentityResolution:
        records = self._repository.identity_records(security_id=event.security_id)
        return resolve_identity(
            records,
            IdentityQuery(
                as_of=_date_start(event.ex_date.value),
                known_at=known_at,
                security_id=event.security_id,
            ),
        )

    def _require_pinned_identity(
        self, event: CorporateActionRecord, *, known_at: UtcTimestamp
    ) -> None:
        resolution = self._identity_resolution_for_event(event, known_at=known_at)
        if resolution.status is not IdentityResolutionStatus.RESOLVED or resolution.record is None:
            raise IdentityClosureError(
                f"event identity resolution failed closed: {resolution.status.value}"
            )
        if resolution.record.identity_hash != event.security_identity_hash:
            raise IdentityClosureError(
                "event identity version does not match the resolved identity"
            )

    def _revalidate_observation(self, observation: SourceObservation) -> SourceObservation:
        if type(observation) is not SourceObservation:
            raise ValueError("observations require exact SourceObservation values")
        actual_available_at = self._source_reader.available_at(observation.source_ref)
        if actual_available_at != observation.available_at:
            raise SourceLineageError("source observation availability is not source-record bound")
        withdrawn = observation.withdrawn or not self._source_reader.is_current(
            observation.source_ref
        )
        return replace(observation, withdrawn=withdrawn)

    def _observation_from_stored_ref(
        self,
        source_ref: SourceRef,
        head: CorporateActionRecord,
        *,
        known_at: UtcTimestamp,
    ) -> SourceObservation:
        available_at = self._source_reader.available_at(source_ref)
        current = self._source_reader.is_current_at(source_ref, known_at=known_at)
        # A previously CONFIRMED event is allowed to replay its verified source
        # lineage.  The original confirmation facts are represented by the
        # confirmed event itself because SourceObservation claims are request
        # evidence, not fields in the append-only source record.  An
        # unconfirmed event never self-confirms merely because a source row is
        # present in the event link table.
        auditable = head.state is CorporateActionState.CONFIRMED and current
        replay_claims = auditable and source_ref.family in {
            P4SourceFamily.SEC_EDGAR,
            P4SourceFamily.ISSUER_IR,
            P4SourceFamily.EXCHANGE_OFFICIAL,
        }
        return SourceObservation(
            source_ref=source_ref,
            available_at=available_at,
            withdrawn=not current,
            auditable=auditable,
            claimed_type=head.action_type if replay_claims else None,
            claimed_ratio=head.ratio if replay_claims else None,
            claimed_ex_date=head.ex_date if replay_claims else None,
            claimed_effective_date=head.effective_date if replay_claims else None,
        )

    def _append_review_if_possible(
        self,
        head: CorporateActionRecord,
        observations: tuple[SourceObservation, ...],
        *,
        decision_at: UtcTimestamp,
    ) -> CorporateActionRecord:
        if head.state in {
            CorporateActionState.REVIEW_REQUIRED,
            CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION,
        }:
            return head
        candidate = _event_with_state(
            head,
            state=CorporateActionState.REVIEW_REQUIRED,
            available_at=_later(head.available_at, decision_at),
            source_refs=_merge_refs(
                head.source_refs,
                tuple(observation.source_ref for observation in observations),
            ),
        )
        self._repository.append_event(candidate, previous_record_hash=head.record_hash)
        return candidate

    def _apply_confirmation_evaluation(
        self,
        head: CorporateActionRecord,
        evaluation: ConfirmationEvaluation,
        observations: tuple[SourceObservation, ...],
        *,
        decision_at: UtcTimestamp,
    ) -> CorporateActionRecord:
        if evaluation.outcome is ConfirmationOutcome.ENTRY_BLOCKED:
            return head
        if evaluation.outcome is ConfirmationOutcome.REVIEW_REQUIRED:
            return self._append_review_if_possible(head, observations, decision_at=decision_at)
        if head.state is CorporateActionState.CONFIRMED:
            return head
        if head.state is not CorporateActionState.ENTRY_BLOCKED:
            raise IdentityClosureError("confirmation cannot bypass the durable entry block")
        candidate = _event_with_state(
            head,
            state=CorporateActionState.CONFIRMED,
            available_at=_later(head.available_at, decision_at),
            source_refs=_merge_refs(
                head.source_refs,
                tuple(observation.source_ref for observation in observations),
            ),
        )
        self._repository.append_event(candidate, previous_record_hash=head.record_hash)
        return candidate

    def _emit(self, telemetry: SecurityMasterTelemetry) -> None:
        if self._telemetry is None:
            return
        try:
            self._telemetry(telemetry)
        except Exception:
            # Telemetry is intentionally outside the transaction; an audit or
            # metrics failure can never undo an already durable safety state.
            self.telemetry_failures += 1

    @contextmanager
    def _repository_transaction(self) -> Iterator[None]:
        transaction = getattr(self._repository, "transaction", None)
        if transaction is None or not callable(transaction):
            yield
            return
        with transaction():
            yield


def _event_with_state(
    event: CorporateActionRecord,
    *,
    state: CorporateActionState,
    available_at: UtcTimestamp,
    source_refs: tuple[SourceRef, ...] | None = None,
) -> CorporateActionRecord:
    return build_corporate_action_record(
        event_id=event.event_id,
        security_id=event.security_id,
        security_identity_hash=event.security_identity_hash,
        action_type=event.action_type,
        ratio=event.ratio,
        declared_at=event.declared_at,
        ex_date=event.ex_date,
        effective_date=event.effective_date,
        available_at=available_at,
        state=state,
        source_refs=event.source_refs if source_refs is None else source_refs,
        schema_version=event.schema_version,
    )


def _require_head(lineage: tuple[CorporateActionRecord, ...]) -> CorporateActionRecord:
    if not lineage:
        raise SecurityMasterServiceError("corporate-action event lineage does not exist")
    return lineage[-1]


def _later(first: UtcTimestamp, second: UtcTimestamp) -> UtcTimestamp:
    return first if first.value >= second.value else second


def _date_start(value: date) -> UtcTimestamp:
    return UtcTimestamp(datetime.combine(value, time.min, tzinfo=UTC))


def _merge_refs(*groups: Iterable[SourceRef]) -> tuple[SourceRef, ...]:
    by_id: dict[str, SourceRef] = {}
    for group in groups:
        for source_ref in group:
            if type(source_ref) is not SourceRef:
                raise SourceLineageError("source lineage requires exact SourceRef values")
            existing = by_id.get(source_ref.record_id)
            if existing is not None and existing != source_ref:
                raise SourceLineageError("one source identifier has conflicting hashes or families")
            by_id[source_ref.record_id] = source_ref
    return tuple(
        sorted(by_id.values(), key=lambda ref: (ref.record_id, ref.family.value, ref.record_hash))
    )


def _dedupe_identities(
    records: Iterable[SecurityIdentityRecord],
) -> tuple[SecurityIdentityRecord, ...]:
    by_hash: dict[str, SecurityIdentityRecord] = {}
    for record in records:
        if type(record) is not SecurityIdentityRecord:
            raise IdentityClosureError("repository returned a non-identity record")
        existing = by_hash.get(record.identity_hash)
        if existing is not None and existing.wire() != record.wire():
            raise IdentityClosureError("identity hash collision carries different content")
        by_hash[record.identity_hash] = record
    return tuple(sorted(by_hash.values(), key=lambda record: record.identity_hash))


def _require_purpose(query: QuarantineQuery, purpose: QuarantinePurpose) -> None:
    if type(query) is not QuarantineQuery:
        raise ValueError("quarantine requires an exact QuarantineQuery")
    if query.purpose is not purpose:
        raise ValueError(f"query purpose must be {purpose.value}")
