"""Job transition telemetry ordering and transaction-invariant tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from fakes.telemetry import (
    FakeDiagnosticSink,
    FakeMetricRecorder,
    FakeTraceRecorder,
    FixedMonotonicClock,
    FixedSpanIdFactory,
    fixed_context,
)
from seven_lens.application.job_service import (
    AuditTelemetryContextMismatchError,
    StaleLeaseError,
    transition_job_with_audit,
)
from seven_lens.application.ports.persistence import UnitOfWork
from seven_lens.application.ports.telemetry import AttributeKey, MetricInstrument, MetricPoint
from seven_lens.domain.events import AuditEvent, RecordedAuditEvent
from seven_lens.domain.jobs import JobInstance, JobSpec, JobStatus, LeaseGrant
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.observability.context import TelemetryContext
from seven_lens.observability.failsafe import FailSafeTelemetry

NOW = UtcTimestamp(datetime(2026, 8, 15, 1, 2, 3, tzinfo=UTC))
RUN_ID = RunId.from_string("323e4567-e89b-12d3-a456-426614174000")


def make_job(status: JobStatus = JobStatus.PLANNED) -> JobInstance:
    return JobInstance(
        spec=JobSpec(TradingDate(datetime(2026, 8, 15).date()), "research", "open"),
        status=status,
        lease_owner="worker-01",
        leased_until=UtcTimestamp(NOW.value + timedelta(minutes=5)),
        fencing_token=1,
        attempt_count=1,
        created_at=NOW,
        updated_at=NOW,
    )


def make_grant() -> LeaseGrant:
    return LeaseGrant(
        job_key=make_job().job_key,
        lease_owner="worker-01",
        leased_until=UtcTimestamp(NOW.value + timedelta(minutes=5)),
        fencing_token=1,
        attempt_count=1,
        database_time=NOW,
    )


def make_audit() -> AuditEvent:
    return AuditEvent.create(
        audit_id=UUID("323e4567-e89b-12d3-a456-426614174010"),
        event_type="job.status_changed",
        run_id=RUN_ID,
        correlation_id=UUID("323e4567-e89b-12d3-a456-426614174011"),
        causation_id=None,
        occurred_at=NOW,
        payload={"status": "RUNNING"},
        producer_version="tests/1.0",
    )


def context_for_audit(audit_event: AuditEvent) -> TelemetryContext:
    assert audit_event.run_id is not None
    fixed = fixed_context()
    return TelemetryContext.root(
        run_id=audit_event.run_id,
        correlation_id=audit_event.correlation_id,
        trace_id=fixed.trace_id,
        span_id=fixed.span_id,
    )


class FakeJobs:
    def __init__(self, unit_of_work: FakeUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def set_status(self, grant: LeaseGrant, status: JobStatus) -> JobInstance | None:
        del grant
        self._unit_of_work.events.append(("set_status", status))
        if self._unit_of_work.database_failure is not None:
            raise self._unit_of_work.database_failure
        if self._unit_of_work.stale:
            return None
        pending = make_job(status)
        self._unit_of_work.pending_job = pending
        return pending


class FakeAudits:
    def __init__(self, unit_of_work: FakeUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def add(self, event: AuditEvent) -> RecordedAuditEvent:
        self._unit_of_work.events.append(("audit_add", event.event_type))
        if self._unit_of_work.audit_failure is not None:
            raise self._unit_of_work.audit_failure
        recorded = RecordedAuditEvent(event, NOW)
        self._unit_of_work.pending_audit = recorded
        return recorded


class FakeUnitOfWork:
    def __init__(
        self,
        events: list[tuple[str, object]],
        *,
        stale: bool = False,
        database_failure: Exception | None = None,
        audit_failure: Exception | None = None,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.stale = stale
        self.database_failure = database_failure
        self.audit_failure = audit_failure
        self.commit_failure = commit_failure
        self.jobs = FakeJobs(self)
        self.audit_events = FakeAudits(self)
        self.pending_job: JobInstance | None = None
        self.pending_audit: RecordedAuditEvent | None = None
        self.persisted_job = make_job()
        self.persisted_audits: list[RecordedAuditEvent] = []
        self.committed = False

    def __enter__(self) -> FakeUnitOfWork:
        self.events.append(("uow_enter", None))
        return self

    def __exit__(self, *args: object) -> None:
        if args[0] is not None or not self.committed:
            self.rollback()
        self.events.append(("uow_exit", None))

    def commit(self) -> None:
        self.events.append(("commit", None))
        if self.commit_failure is not None:
            raise self.commit_failure
        if self.pending_job is not None:
            self.persisted_job = self.pending_job
        if self.pending_audit is not None:
            self.persisted_audits.append(self.pending_audit)
        self.committed = True

    def rollback(self) -> None:
        self.events.append(("rollback", None))
        self.pending_job = None
        self.pending_audit = None


def make_telemetry(
    events: list[tuple[str, object]],
    *,
    metric_failure: BaseException | None = None,
    start_failure: BaseException | None = None,
    end_failure: BaseException | None = None,
) -> tuple[FailSafeTelemetry, FakeMetricRecorder, FakeTraceRecorder]:
    metrics = FakeMetricRecorder(events=events, failure=metric_failure)
    traces = FakeTraceRecorder(
        events=events,
        start_failure=start_failure,
        end_failure=end_failure,
    )
    facade = FailSafeTelemetry(
        metrics,
        traces,
        FakeDiagnosticSink(),
        monotonic_clock=FixedMonotonicClock((10.0, 10.02)),
        span_id_factory=FixedSpanIdFactory(),
    )
    return facade, metrics, traces


def point_attributes(point: MetricPoint) -> dict[str, str]:
    return {attribute.key.value: attribute.value for attribute in point.attributes}


def call_transition(
    unit_of_work: FakeUnitOfWork,
    telemetry: FailSafeTelemetry,
    *,
    audit_event: AuditEvent | None = None,
    telemetry_context: TelemetryContext | None = None,
) -> tuple[JobInstance, RecordedAuditEvent]:
    event = audit_event or make_audit()
    return transition_job_with_audit(
        cast(UnitOfWork, unit_of_work),
        grant=make_grant(),
        status=JobStatus.RUNNING,
        audit_event=event,
        telemetry_context=telemetry_context or context_for_audit(event),
        telemetry=telemetry,
    )


@pytest.mark.parametrize("mismatch", ["run_id", "correlation_id", "missing_run_id"])
def test_audit_and_telemetry_identity_mismatch_fails_before_all_side_effects(
    mismatch: str,
) -> None:
    events: list[tuple[str, object]] = []
    facade, metrics, traces = make_telemetry(events)
    unit_of_work = FakeUnitOfWork(events)
    audit = make_audit()
    context = context_for_audit(audit)
    if mismatch == "run_id":
        context = TelemetryContext.root(
            run_id=RunId.from_string("423e4567-e89b-12d3-a456-426614174000"),
            correlation_id=audit.correlation_id,
            trace_id=context.trace_id,
            span_id=context.span_id,
        )
    elif mismatch == "correlation_id":
        context = TelemetryContext.root(
            run_id=RUN_ID,
            correlation_id=UUID("423e4567-e89b-12d3-a456-426614174011"),
            trace_id=context.trace_id,
            span_id=context.span_id,
        )
    else:
        audit = replace(audit, run_id=None)

    with pytest.raises(AuditTelemetryContextMismatchError) as caught:
        call_transition(
            unit_of_work,
            facade,
            audit_event=audit,
            telemetry_context=context,
        )

    assert str(caught.value) == "telemetry context does not match the required audit identity"
    assert events == []
    assert traces.start_attempts == []
    assert traces.end_attempts == []
    assert metrics.attempts == []
    assert unit_of_work.pending_job is None
    assert unit_of_work.pending_audit is None
    assert unit_of_work.persisted_job.status is JobStatus.PLANNED
    assert unit_of_work.persisted_audits == []


def test_success_metrics_and_span_are_recorded_only_after_commit_and_exit() -> None:
    events: list[tuple[str, object]] = []
    facade, metrics, traces = make_telemetry(events)
    unit_of_work = FakeUnitOfWork(events)

    job, recorded_audit = call_transition(unit_of_work, facade)

    assert job.status is JobStatus.RUNNING
    assert recorded_audit in unit_of_work.persisted_audits
    event_kinds = [kind for kind, _ in events]
    assert event_kinds == [
        "span_start",
        "uow_enter",
        "set_status",
        "audit_add",
        "commit",
        "uow_exit",
        "metric",
        "metric",
        "span_end",
    ]
    assert [point.instrument for point in metrics.points] == [
        MetricInstrument.JOB_TRANSITION_COUNT,
        MetricInstrument.JOB_TRANSITION_DURATION,
    ]
    assert all(
        point_attributes(point) == {"target_status": "running", "outcome": "success"}
        for point in metrics.points
    )
    assert traces.ends[0].error_code is None


def test_stale_lease_rolls_back_then_records_bounded_outcome_and_preserves_error() -> None:
    events: list[tuple[str, object]] = []
    facade, metrics, traces = make_telemetry(events)
    unit_of_work = FakeUnitOfWork(events, stale=True)

    with pytest.raises(StaleLeaseError) as caught:
        call_transition(unit_of_work, facade)

    assert "stale" in str(caught.value)
    assert unit_of_work.persisted_job.status is JobStatus.PLANNED
    assert unit_of_work.persisted_audits == []
    assert events.index(("rollback", None)) < next(
        index for index, event in enumerate(events) if event[0] == "metric"
    )
    assert all(point_attributes(point)["outcome"] == "stale_lease" for point in metrics.points)
    assert traces.ends[0].error_code is not None
    assert str(caught.value) not in repr((metrics.points, traces.ends))


def test_audit_failure_rolls_back_state_before_telemetry_and_preserves_original() -> None:
    events: list[tuple[str, object]] = []
    original = RuntimeError("audit backend fake detail")
    facade, metrics, traces = make_telemetry(events)
    unit_of_work = FakeUnitOfWork(events, audit_failure=original)

    with pytest.raises(RuntimeError) as caught:
        call_transition(unit_of_work, facade)

    assert caught.value is original
    assert unit_of_work.persisted_job.status is JobStatus.PLANNED
    assert unit_of_work.persisted_audits == []
    assert all(point_attributes(point)["outcome"] == "audit_failure" for point in metrics.points)
    assert "audit backend fake detail" not in repr((metrics.points, traces.ends))


@pytest.mark.parametrize("failure_point", ["set_status", "commit"])
def test_database_failure_never_emits_success_and_rolls_back(failure_point: str) -> None:
    events: list[tuple[str, object]] = []
    original = RuntimeError("database fake detail")
    facade, metrics, _ = make_telemetry(events)
    unit_of_work = FakeUnitOfWork(
        events,
        database_failure=original if failure_point == "set_status" else None,
        commit_failure=original if failure_point == "commit" else None,
    )

    with pytest.raises(RuntimeError) as caught:
        call_transition(unit_of_work, facade)

    assert caught.value is original
    assert unit_of_work.persisted_job.status is JobStatus.PLANNED
    assert unit_of_work.persisted_audits == []
    assert all(point_attributes(point)["outcome"] == "database_failure" for point in metrics.points)
    assert not any(point_attributes(point)["outcome"] == "success" for point in metrics.points)


@pytest.mark.parametrize("failure_location", ["metric", "span_start", "span_end"])
def test_telemetry_exception_keeps_state_and_audit_atomic_success(
    failure_location: str,
) -> None:
    events: list[tuple[str, object]] = []
    facade, _, _ = make_telemetry(
        events,
        metric_failure=(
            RuntimeError("metric backend detail") if failure_location == "metric" else None
        ),
        start_failure=(
            RuntimeError("start backend detail") if failure_location == "span_start" else None
        ),
        end_failure=(
            RuntimeError("end backend detail") if failure_location == "span_end" else None
        ),
    )
    unit_of_work = FakeUnitOfWork(events)

    job, _ = call_transition(unit_of_work, facade)

    assert job.status is JobStatus.RUNNING
    assert unit_of_work.persisted_job.status is JobStatus.RUNNING
    assert len(unit_of_work.persisted_audits) == 1
    assert [kind for kind, _ in events].count("commit") == 1
    assert [kind for kind, _ in events].count("rollback") == 0
    assert facade.drop_count >= 1


def test_telemetry_failure_during_audit_failure_cannot_commit_or_retry() -> None:
    events: list[tuple[str, object]] = []
    original = RuntimeError("audit failure detail")
    facade, _, _ = make_telemetry(
        events,
        metric_failure=RuntimeError("telemetry failure detail"),
        end_failure=RuntimeError("trace failure detail"),
    )
    unit_of_work = FakeUnitOfWork(events, audit_failure=original)

    with pytest.raises(RuntimeError) as caught:
        call_transition(unit_of_work, facade)

    assert caught.value is original
    assert unit_of_work.persisted_job.status is JobStatus.PLANNED
    assert unit_of_work.persisted_audits == []
    assert [kind for kind, _ in events].count("commit") == 0
    assert [kind for kind, _ in events].count("rollback") == 1
    assert [kind for kind, _ in events].count("set_status") == 1
    assert [kind for kind, _ in events].count("audit_add") == 1


def test_job_metric_keys_exclude_job_lease_ids_payload_and_exception_text() -> None:
    events: list[tuple[str, object]] = []
    facade, metrics, _ = make_telemetry(events)
    call_transition(FakeUnitOfWork(events), facade)

    keys = {attribute.key for point in metrics.points for attribute in point.attributes}
    assert keys == {AttributeKey.TARGET_STATUS, AttributeKey.OUTCOME}
    evidence = repr(metrics.points).lower()
    for forbidden in (
        "job_key",
        "lease_owner",
        "fencing",
        "payload",
        "exception",
        "run_id",
        "trace_id",
    ):
        assert forbidden not in evidence
