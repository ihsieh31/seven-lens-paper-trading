"""Real PostgreSQL unit-of-work, transaction, and lease acceptance tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest

from fakes.telemetry import (
    FIXED_ROOT_SPAN_ID,
    FIXED_TRACE_ID,
    FakeDiagnosticSink,
    FakeMetricRecorder,
    FakeTraceRecorder,
    FixedMonotonicClock,
    FixedSpanIdFactory,
)
from seven_lens.application.job_service import (
    StaleLeaseError,
    transition_job_with_audit,
)
from seven_lens.domain.events import (
    AuditEvent,
    DomainEvent,
    JobCreatedPayload,
    JobStatusTransitionAuditPayload,
    JobTransitionReason,
)
from seven_lens.domain.jobs import JobSpec, JobStatus, LeaseDuration, LeaseGrant
from seven_lens.domain.value_objects import (
    RunId,
    SchemaVersion,
    TradingDate,
    UtcTimestamp,
)
from seven_lens.infrastructure.postgres import (
    PostgresUnitOfWork,
    UnitOfWorkStateError,
)
from seven_lens.observability.context import TelemetryContext
from seven_lens.observability.failsafe import FailSafeTelemetry

pytestmark = pytest.mark.integration

RUN_ID = RunId.from_string("223e4567-e89b-12d3-a456-426614174000")
SCHEMA_VERSION = SchemaVersion("1.0.0")
OCCURRED_AT = UtcTimestamp(datetime(2020, 1, 1, 12, 0, tzinfo=UTC))
TRADING_DATE = TradingDate(datetime(2026, 8, 14, tzinfo=UTC).date())


def fetch_one(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    assert row is not None
    return cast(tuple[Any, ...], row)


def fake_telemetry(
    *,
    metric_failure: BaseException | None = None,
    end_failure: BaseException | None = None,
) -> FailSafeTelemetry:
    return FailSafeTelemetry(
        FakeMetricRecorder(failure=metric_failure),
        FakeTraceRecorder(end_failure=end_failure),
        FakeDiagnosticSink(),
        monotonic_clock=FixedMonotonicClock((1.0, 1.01)),
        span_id_factory=FixedSpanIdFactory(),
    )


def domain_event(
    event_id: str,
    *,
    aggregate_id: str = "job-aggregate",
    sequence: int = 1,
) -> DomainEvent:
    return DomainEvent.create(
        event_id=UUID(event_id),
        schema_version=SCHEMA_VERSION,
        aggregate_type="job",
        aggregate_id=aggregate_id,
        aggregate_sequence=sequence,
        run_id=RUN_ID,
        correlation_id=UUID("223e4567-e89b-12d3-a456-426614174001"),
        causation_id=None,
        occurred_at=OCCURRED_AT,
        payload=JobCreatedPayload(status=JobStatus.PLANNED, attempt_count=0),
        producer_version="seven-lens-tests/1.0",
    )


def audit_event(
    audit_id: str,
    *,
    target_status: JobStatus = JobStatus.RUNNING,
) -> AuditEvent:
    return AuditEvent.create(
        audit_id=UUID(audit_id),
        run_id=RUN_ID,
        correlation_id=UUID("223e4567-e89b-12d3-a456-426614174002"),
        causation_id=None,
        occurred_at=OCCURRED_AT,
        payload=JobStatusTransitionAuditPayload(
            target_status=target_status,
            reason_code=JobTransitionReason.SCHEDULED,
        ),
        producer_version="seven-lens-tests/1.0",
    )


def telemetry_context_for(audit: AuditEvent) -> TelemetryContext:
    assert audit.run_id is not None
    return TelemetryContext.root(
        run_id=audit.run_id,
        correlation_id=audit.correlation_id,
        trace_id=FIXED_TRACE_ID,
        span_id=FIXED_ROOT_SPAN_ID,
    )


def job_spec(*, job_type: str = "research", window: str = "open") -> JobSpec:
    return JobSpec(trading_date=TRADING_DATE, job_type=job_type, window=window)


def create_job(dsn: str, spec: JobSpec) -> None:
    with PostgresUnitOfWork(dsn) as unit_of_work:
        created = unit_of_work.jobs.add(spec)
        unit_of_work.commit()
    assert created.spec == spec
    assert created.status is JobStatus.PLANNED


def acquire_job(
    dsn: str,
    job_key: str,
    owner: str,
    *,
    duration: timedelta = timedelta(minutes=5),
) -> LeaseGrant:
    with PostgresUnitOfWork(dsn) as unit_of_work:
        grant = unit_of_work.jobs.acquire(job_key, owner, LeaseDuration(duration))
        assert grant is not None
        unit_of_work.commit()
    return grant


def test_uow_event_and_audit_roundtrip_uses_database_recorded_timestamps(
    migrated_postgres: str,
) -> None:
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        recorded_domain = unit_of_work.domain_events.add(
            domain_event("223e4567-e89b-12d3-a456-426614174010")
        )
        recorded_audit = unit_of_work.audit_events.add(
            audit_event("223e4567-e89b-12d3-a456-426614174011")
        )
        unit_of_work.commit()

    for recorded in (recorded_domain.recorded_at, recorded_audit.recorded_at):
        assert recorded.value.tzinfo is not None
        assert recorded.value.utcoffset() == timedelta(0)
        assert recorded.value >= OCCURRED_AT.value

    with (
        psycopg.connect(migrated_postgres, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT count(*) FROM domain_events")
        assert fetch_one(cursor)[0] == 1
        cursor.execute("SELECT count(*) FROM audit_events")
        assert fetch_one(cursor)[0] == 1


def test_uow_requires_active_context_and_rolls_back_uncommitted_writes(
    migrated_postgres: str,
) -> None:
    unit_of_work = PostgresUnitOfWork(migrated_postgres)

    with pytest.raises(UnitOfWorkStateError):
        unit_of_work.domain_events.add(domain_event("223e4567-e89b-12d3-a456-426614174020"))

    with unit_of_work:
        unit_of_work.domain_events.add(domain_event("223e4567-e89b-12d3-a456-426614174021"))

    with (
        psycopg.connect(migrated_postgres, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT count(*) FROM domain_events")
        assert fetch_one(cursor)[0] == 0


def test_duplicate_event_id_and_aggregate_sequence_are_rejected(
    migrated_postgres: str,
) -> None:
    first = domain_event("223e4567-e89b-12d3-a456-426614174030")
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.domain_events.add(first)
        unit_of_work.commit()

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        with pytest.raises(psycopg.Error):
            unit_of_work.domain_events.add(first)
        unit_of_work.rollback()

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        with pytest.raises(psycopg.Error):
            unit_of_work.domain_events.add(
                domain_event(
                    "223e4567-e89b-12d3-a456-426614174031",
                    sequence=3,
                )
            )
        unit_of_work.rollback()
        second = unit_of_work.domain_events.add(
            domain_event("223e4567-e89b-12d3-a456-426614174032", sequence=2)
        )
        unit_of_work.commit()
    assert second.event.aggregate_sequence == 2


def test_state_and_audit_failure_roll_back_as_one_transaction(migrated_postgres: str) -> None:
    spec = job_spec()
    create_job(migrated_postgres, spec)
    grant = acquire_job(migrated_postgres, spec.job_key, "worker-01")
    existing_audit = audit_event("223e4567-e89b-12d3-a456-426614174040")

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.audit_events.add(existing_audit)
        unit_of_work.commit()

    with pytest.raises(psycopg.Error):
        transition_job_with_audit(
            PostgresUnitOfWork(migrated_postgres),
            grant=grant,
            status=JobStatus.RUNNING,
            audit_event=existing_audit,
            telemetry_context=telemetry_context_for(existing_audit),
            telemetry=fake_telemetry(),
        )

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        job = unit_of_work.jobs.get(spec.job_key)
        assert job is not None
        assert job.status is JobStatus.PLANNED
        unit_of_work.commit()


def test_telemetry_failure_cannot_change_postgres_state_and_audit_atomicity(
    migrated_postgres: str,
) -> None:
    spec = job_spec(job_type="telemetry", window="open")
    create_job(migrated_postgres, spec)
    grant = acquire_job(migrated_postgres, spec.job_key, "worker-telemetry")
    telemetry = fake_telemetry(
        metric_failure=RuntimeError("unsafe metric backend detail"),
        end_failure=RuntimeError("unsafe trace backend detail"),
    )

    transition_audit = audit_event("223e4567-e89b-12d3-a456-426614174041")
    job, _ = transition_job_with_audit(
        PostgresUnitOfWork(migrated_postgres),
        grant=grant,
        status=JobStatus.RUNNING,
        audit_event=transition_audit,
        telemetry_context=telemetry_context_for(transition_audit),
        telemetry=telemetry,
    )

    assert job.status is JobStatus.RUNNING
    assert telemetry.drop_count >= 1
    with (
        psycopg.connect(migrated_postgres, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT status FROM job_instances WHERE job_key = %s", (spec.job_key,))
        assert fetch_one(cursor)[0] == "RUNNING"
        cursor.execute("SELECT count(*) FROM audit_events")
        assert fetch_one(cursor)[0] == 1


def test_concurrent_acquire_has_exactly_one_successful_owner(migrated_postgres: str) -> None:
    spec = job_spec()
    create_job(migrated_postgres, spec)

    def worker(owner: str) -> LeaseGrant | None:
        with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
            grant = unit_of_work.jobs.acquire(
                spec.job_key,
                owner,
                LeaseDuration(timedelta(minutes=5)),
            )
            unit_of_work.commit()
            return grant

    with ThreadPoolExecutor(max_workers=2) as executor:
        grants = list(executor.map(worker, ("worker-01", "worker-02")))

    successful = [grant for grant in grants if grant is not None]
    assert len(successful) == 1
    assert successful[0].lease_owner in {"worker-01", "worker-02"}


def test_renew_and_release_require_current_owner_and_fencing_token(
    migrated_postgres: str,
) -> None:
    spec = job_spec()
    create_job(migrated_postgres, spec)
    grant = acquire_job(migrated_postgres, spec.job_key, "worker-01")
    wrong_owner = replace(grant, lease_owner="worker-02")
    wrong_token = replace(grant, fencing_token=grant.fencing_token + 1)

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        assert unit_of_work.jobs.renew(wrong_owner, LeaseDuration(timedelta(minutes=5))) is None
        assert unit_of_work.jobs.release(wrong_owner) is False
        assert unit_of_work.jobs.renew(wrong_token, LeaseDuration(timedelta(minutes=5))) is None
        assert unit_of_work.jobs.release(wrong_token) is False
        renewed = unit_of_work.jobs.renew(grant, LeaseDuration(timedelta(minutes=10)))
        assert renewed is not None
        assert renewed.fencing_token == grant.fencing_token
        unit_of_work.commit()

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        assert unit_of_work.jobs.release(renewed) is True
        assert unit_of_work.jobs.release(renewed) is False
        unit_of_work.commit()


def test_expiry_takeover_increments_token_and_fences_stale_owner(
    migrated_postgres: str,
) -> None:
    spec = job_spec()
    create_job(migrated_postgres, spec)
    original = acquire_job(
        migrated_postgres,
        spec.job_key,
        "worker-01",
        duration=timedelta(milliseconds=1),
    )
    with (
        psycopg.connect(migrated_postgres, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT pg_sleep(0.02)")

    # A fresh unit of work models process restart and must be able to take over.
    takeover = acquire_job(migrated_postgres, spec.job_key, "worker-02")
    assert takeover.fencing_token == original.fencing_token + 1
    assert takeover.attempt_count == original.attempt_count + 1

    stale = original
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        assert unit_of_work.jobs.renew(stale, LeaseDuration(timedelta(minutes=5))) is None
        assert unit_of_work.jobs.release(stale) is False
        assert unit_of_work.jobs.set_status(stale, JobStatus.RUNNING) is None
        current = unit_of_work.jobs.set_status(takeover, JobStatus.RUNNING)
        assert current is not None
        assert current.status is JobStatus.RUNNING
        unit_of_work.commit()

    with (
        psycopg.connect(migrated_postgres, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT released_at, release_reason FROM job_leases "
            "WHERE job_key = %s AND fencing_token = %s",
            (spec.job_key, original.fencing_token),
        )
        released_at, release_reason = fetch_one(cursor)
    assert released_at is not None
    assert release_reason == "EXPIRED_TAKEOVER"

    stale_audit = audit_event(
        "223e4567-e89b-12d3-a456-426614174050",
        target_status=JobStatus.FAILED,
    )
    with pytest.raises(StaleLeaseError):
        transition_job_with_audit(
            PostgresUnitOfWork(migrated_postgres),
            grant=stale,
            status=JobStatus.FAILED,
            audit_event=stale_audit,
            telemetry_context=telemetry_context_for(stale_audit),
            telemetry=fake_telemetry(),
        )


def test_direct_lease_field_mutation_is_rejected_by_database_guard(
    migrated_postgres: str,
) -> None:
    spec = job_spec()
    create_job(migrated_postgres, spec)
    acquire_job(migrated_postgres, spec.job_key, "worker-01")

    with (
        psycopg.connect(migrated_postgres, autocommit=False) as connection,
        connection.cursor() as cursor,
    ):
        with pytest.raises(psycopg.Error) as failure:
            cursor.execute(
                "UPDATE job_instances "
                "SET leased_until = statement_timestamp() + INTERVAL '1 hour' "
                "WHERE job_key = %s",
                (spec.job_key,),
            )
        assert failure.value.sqlstate == "55000"
        connection.rollback()


def test_lease_timestamps_come_from_postgres_not_monkeypatched_local_clock(
    migrated_postgres: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    monkeypatch.setattr(time, "time", lambda: 0.0)
    spec = job_spec()
    create_job(migrated_postgres, spec)

    with (
        psycopg.connect(migrated_postgres, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT statement_timestamp()")
        before = fetch_one(cursor)[0]

    grant = acquire_job(migrated_postgres, spec.job_key, "worker-01")

    with (
        psycopg.connect(migrated_postgres, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT statement_timestamp()")
        after = fetch_one(cursor)[0]

    assert before <= grant.database_time.value <= after + timedelta(seconds=5)
    assert grant.leased_until.value > grant.database_time.value
    assert grant.database_time.value > datetime(2000, 1, 1, tzinfo=UTC)
