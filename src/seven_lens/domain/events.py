"""Persistence-neutral domain and audit event envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar
from uuid import UUID, uuid4

from seven_lens.domain.jobs import JobStatus
from seven_lens.domain.json_values import JsonObject
from seven_lens.domain.value_objects import RunId, SchemaVersion, UtcTimestamp
from seven_lens.security.redaction import DefaultSecretRedactor

_MAX_EVENT_NAME_LENGTH = 200
_MAX_AGGREGATE_ID_LENGTH = 500
_MAX_PRODUCER_VERSION_LENGTH = 200


class JobTransitionReason(StrEnum):
    """Closed reason codes for an authoritative job status transition."""

    SCHEDULED = "SCHEDULED"
    RECOVERY = "RECOVERY"
    FAILURE = "FAILURE"
    EXPIRY = "EXPIRY"


@dataclass(frozen=True, slots=True)
class JobCreatedPayload:
    """The only currently-authorized domain payload: a newly planned job."""

    event_type: ClassVar[str] = "job.created"
    status: JobStatus
    attempt_count: int

    def __post_init__(self) -> None:
        if self.status is not JobStatus.PLANNED:
            raise ValueError("new job domain payload status must be PLANNED")
        if type(self.attempt_count) is not int or self.attempt_count != 0:
            raise ValueError("new job domain payload attempt_count must be zero")

    def to_json_object(self) -> JsonObject:
        return JsonObject.from_value(
            {"attempt_count": self.attempt_count, "status": self.status.value}
        )


@dataclass(frozen=True, slots=True)
class JobStatusTransitionAuditPayload:
    """Closed audit payload for one requested fenced job status transition."""

    event_type: ClassVar[str] = "job.status_changed"
    target_status: JobStatus
    reason_code: JobTransitionReason

    def __post_init__(self) -> None:
        if type(self.target_status) is not JobStatus:
            raise ValueError("job transition target_status must be a JobStatus")
        if type(self.reason_code) is not JobTransitionReason:
            raise ValueError("job transition reason_code must be a JobTransitionReason")

    def to_json_object(self) -> JsonObject:
        return JsonObject.from_value(
            {
                "reason_code": self.reason_code.value,
                "target_status": self.target_status.value,
            }
        )


type DomainEventPayload = JobCreatedPayload
type AuditEventPayload = JobStatusTransitionAuditPayload


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """An event supplied by the domain; the database supplies ``recorded_at``."""

    event_id: UUID
    event_type: str
    schema_version: SchemaVersion
    aggregate_type: str
    aggregate_id: str
    aggregate_sequence: int
    run_id: RunId
    correlation_id: UUID
    causation_id: UUID | None
    occurred_at: UtcTimestamp
    payload: DomainEventPayload
    producer_version: str

    def __post_init__(self) -> None:
        _validate_uuid(self.event_id, "event_id")
        _validate_uuid(self.correlation_id, "correlation_id")
        if self.causation_id is not None:
            _validate_uuid(self.causation_id, "causation_id")
        _validate_text(self.event_type, "event_type", _MAX_EVENT_NAME_LENGTH)
        _validate_text(self.aggregate_type, "aggregate_type", _MAX_EVENT_NAME_LENGTH)
        _validate_text(self.aggregate_id, "aggregate_id", _MAX_AGGREGATE_ID_LENGTH)
        _validate_text(
            self.producer_version,
            "producer_version",
            _MAX_PRODUCER_VERSION_LENGTH,
        )
        if type(self.aggregate_sequence) is not int or self.aggregate_sequence < 1:
            raise ValueError("aggregate_sequence must be a positive integer")
        if not isinstance(self.schema_version, SchemaVersion):
            raise ValueError("schema_version must be a SchemaVersion")
        if not isinstance(self.run_id, RunId):
            raise ValueError("run_id must be a RunId")
        if not isinstance(self.occurred_at, UtcTimestamp):
            raise ValueError("occurred_at must be a UtcTimestamp")
        if type(self.payload) is not JobCreatedPayload:
            raise ValueError("payload must be an approved typed domain event payload")
        if self.event_type != self.payload.event_type:
            raise ValueError("event_type must match the typed domain event payload")

    @classmethod
    def create(
        cls,
        *,
        schema_version: SchemaVersion,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_sequence: int,
        run_id: RunId,
        correlation_id: UUID,
        causation_id: UUID | None,
        occurred_at: UtcTimestamp,
        payload: DomainEventPayload,
        producer_version: str,
        event_id: UUID | None = None,
    ) -> DomainEvent:
        """Create an envelope while validating and snapshotting its payload."""
        if type(payload) is not JobCreatedPayload:
            raise ValueError("payload must be an approved typed domain event payload")
        return cls(
            event_id=event_id or uuid4(),
            event_type=payload.event_type,
            schema_version=schema_version,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_sequence=aggregate_sequence,
            run_id=run_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            payload=payload,
            producer_version=producer_version,
        )


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A security-validated audit record; the database supplies ``recorded_at``."""

    audit_id: UUID
    event_type: str
    run_id: RunId | None
    correlation_id: UUID
    causation_id: UUID | None
    occurred_at: UtcTimestamp
    payload: AuditEventPayload
    producer_version: str

    def __post_init__(self) -> None:
        _validate_uuid(self.audit_id, "audit_id")
        _validate_uuid(self.correlation_id, "correlation_id")
        if self.causation_id is not None:
            _validate_uuid(self.causation_id, "causation_id")
        _validate_text(self.event_type, "event_type", _MAX_EVENT_NAME_LENGTH)
        _validate_text(
            self.producer_version,
            "producer_version",
            _MAX_PRODUCER_VERSION_LENGTH,
        )
        if self.run_id is not None and not isinstance(self.run_id, RunId):
            raise ValueError("run_id must be a RunId or None")
        if not isinstance(self.occurred_at, UtcTimestamp):
            raise ValueError("occurred_at must be a UtcTimestamp")
        if type(self.payload) is not JobStatusTransitionAuditPayload:
            raise ValueError("payload must be an approved typed audit payload")
        if self.event_type != self.payload.event_type:
            raise ValueError("event_type must match the typed audit payload")
        payload = self.payload.to_json_object().to_dict()
        if DefaultSecretRedactor().redact(payload) != payload:
            raise ValueError("audit payload contains secret-bearing material")

    @classmethod
    def create(
        cls,
        *,
        run_id: RunId | None,
        correlation_id: UUID,
        causation_id: UUID | None,
        occurred_at: UtcTimestamp,
        payload: AuditEventPayload,
        producer_version: str,
        audit_id: UUID | None = None,
    ) -> AuditEvent:
        """Create a validated audit record and reject rather than persist secrets."""
        if type(payload) is not JobStatusTransitionAuditPayload:
            raise ValueError("payload must be an approved typed audit payload")
        return cls(
            audit_id=audit_id or uuid4(),
            event_type=payload.event_type,
            run_id=run_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            payload=payload,
            producer_version=producer_version,
        )


@dataclass(frozen=True, slots=True)
class RecordedDomainEvent:
    event: DomainEvent
    recorded_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class RecordedAuditEvent:
    event: AuditEvent
    recorded_at: UtcTimestamp


def _validate_uuid(value: object, field_name: str) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{field_name} must be a non-nil UUID")


def _validate_text(value: object, field_name: str, maximum_length: int) -> None:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > maximum_length
        or "\x00" in value
    ):
        raise ValueError(f"{field_name} must be non-empty text up to {maximum_length} characters")
