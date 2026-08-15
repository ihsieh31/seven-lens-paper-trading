"""Transactional job operations that make audit/state atomic by construction."""

from __future__ import annotations

from seven_lens.application.ports.persistence import UnitOfWork
from seven_lens.domain.events import (
    AuditEvent,
    JobStatusTransitionAuditPayload,
    RecordedAuditEvent,
)
from seven_lens.domain.jobs import JobInstance, JobStatus, LeaseGrant
from seven_lens.observability.context import TelemetryContext, validate_telemetry_context
from seven_lens.observability.failsafe import FailSafeTelemetry
from seven_lens.observability.instruments import JobTransitionOutcome


class StaleLeaseError(RuntimeError):
    """Raised when an owner/token pair no longer authorizes a protected write."""


class AuditTelemetryContextMismatchError(ValueError):
    """Raised before side effects when audit and trace identity are not aligned."""

    def __init__(self) -> None:
        super().__init__("telemetry context does not match the required audit identity")


def transition_job_with_audit(
    unit_of_work: UnitOfWork,
    *,
    grant: LeaseGrant,
    status: JobStatus,
    audit_event: AuditEvent,
    telemetry_context: TelemetryContext,
    telemetry: FailSafeTelemetry,
) -> tuple[JobInstance, RecordedAuditEvent]:
    """Persist a fenced status transition and its audit record in one transaction."""
    _validate_audit_telemetry_identity(audit_event, telemetry_context)
    if (
        type(audit_event.payload) is not JobStatusTransitionAuditPayload
        or audit_event.payload.target_status is not status
    ):
        raise ValueError("audit payload does not match the requested job status transition")
    observation = telemetry.start_job_transition(telemetry_context, status)
    failure_stage = JobTransitionOutcome.DATABASE_FAILURE
    try:
        with unit_of_work as transaction:
            job = transaction.jobs.set_status(grant, status)
            if job is None:
                raise StaleLeaseError(
                    "job transition rejected because the lease owner or fencing token is stale"
                )
            failure_stage = JobTransitionOutcome.AUDIT_FAILURE
            recorded_audit = transaction.audit_events.add(audit_event)
            failure_stage = JobTransitionOutcome.DATABASE_FAILURE
            transaction.commit()
    except StaleLeaseError:
        telemetry.finish_job_transition(observation, JobTransitionOutcome.STALE_LEASE)
        raise
    except Exception:
        telemetry.finish_job_transition(observation, failure_stage)
        raise
    telemetry.finish_job_transition(observation, JobTransitionOutcome.SUCCESS)
    return job, recorded_audit


def _validate_audit_telemetry_identity(
    audit_event: AuditEvent,
    telemetry_context: TelemetryContext,
) -> None:
    try:
        validate_telemetry_context(telemetry_context)
    except ValueError:
        raise AuditTelemetryContextMismatchError from None
    if (
        type(audit_event) is not AuditEvent
        or audit_event.run_id is None
        or telemetry_context.run_id != audit_event.run_id
        or telemetry_context.correlation_id != audit_event.correlation_id
    ):
        raise AuditTelemetryContextMismatchError
