"""Fail-safe typed telemetry facade that cannot change ordinary business outcomes."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Protocol

from seven_lens.application.ports.telemetry import (
    AttributeKey,
    ErrorCode,
    MetricInstrument,
    MetricPoint,
    MetricRecorder,
    SpanEnd,
    SpanInstrument,
    SpanStart,
    SpanStatus,
    TelemetryAttribute,
    TraceRecorder,
)
from seven_lens.domain.jobs import JobStatus
from seven_lens.observability.context import SpanId, TelemetryContext, validate_telemetry_context
from seven_lens.observability.instruments import (
    FailureStage,
    JobTransitionOutcome,
    SecretKindAttribute,
    SecretLookupOutcome,
    SeriesCardinalityTracker,
    validate_metric_point,
    validate_span_end,
    validate_span_start,
)


class TelemetryDiagnosticCode(StrEnum):
    RECORDING_FAILED = "telemetry_recording_failed"


@dataclass(frozen=True, slots=True)
class TelemetryDiagnostic:
    """A fixed diagnostic with no exception, attributes, payload, or repr field."""

    code: TelemetryDiagnosticCode = TelemetryDiagnosticCode.RECORDING_FAILED


TELEMETRY_RECORDING_FAILED: TelemetryDiagnostic = TelemetryDiagnostic()


class DiagnosticSink(Protocol):
    def __call__(self, diagnostic: TelemetryDiagnostic) -> None: ...


@dataclass(frozen=True, slots=True)
class SecretLookupObservation:
    context: TelemetryContext | None
    secret_kind: SecretKindAttribute
    started_at: float | None
    span_started: bool


@dataclass(frozen=True, slots=True)
class JobTransitionObservation:
    context: TelemetryContext | None
    target_status: str
    started_at: float | None
    span_started: bool


class FailSafeTelemetry:
    """Closed facade over dependency-neutral recorders.

    Recorder and clock ``Exception`` failures become local drop counts and one fixed
    diagnostic.  ``BaseException`` subclasses such as ``KeyboardInterrupt`` and
    ``SystemExit`` deliberately pass through.
    """

    def __init__(
        self,
        metric_recorder: MetricRecorder,
        trace_recorder: TraceRecorder,
        diagnostic_sink: DiagnosticSink,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        span_id_factory: Callable[[], SpanId] = SpanId.new,
        maximum_active_series: int = 64,
    ) -> None:
        if (
            not callable(diagnostic_sink)
            or not callable(monotonic_clock)
            or not callable(span_id_factory)
        ):
            raise ValueError("telemetry collaborators must be callable")
        self._metric_recorder = metric_recorder
        self._trace_recorder = trace_recorder
        self._diagnostic_sink = diagnostic_sink
        self._monotonic_clock = monotonic_clock
        self._span_id_factory = span_id_factory
        self._cardinality = SeriesCardinalityTracker(maximum_active_series)
        self._drop_lock = Lock()
        self._drop_count = 0
        self._pending_drops: dict[FailureStage, int] = {}

    @property
    def drop_count(self) -> int:
        with self._drop_lock:
            return self._drop_count

    @property
    def pending_drop_count(self) -> int:
        with self._drop_lock:
            return sum(self._pending_drops.values())

    def active_series(self, instrument: MetricInstrument) -> int:
        return self._cardinality.active_series(instrument)

    def start_secret_lookup(
        self,
        context: TelemetryContext,
        secret_kind: SecretKindAttribute,
    ) -> SecretLookupObservation:
        validate_telemetry_context(context)
        if type(secret_kind) is not SecretKindAttribute:
            raise ValueError("secret telemetry kind is not registered")
        started_at = self._safe_clock()
        child = self._safe_child(context)
        span_started = False
        if child is not None:
            span = SpanStart(
                instrument=SpanInstrument.SECRET_LOOKUP,
                context=child,
                attributes=(TelemetryAttribute(AttributeKey.SECRET_KIND, secret_kind.value),),
            )
            span_started = self._start_span(span)
        return SecretLookupObservation(child, secret_kind, started_at, span_started)

    def finish_secret_lookup(
        self,
        observation: SecretLookupObservation,
        outcome: SecretLookupOutcome,
    ) -> None:
        if (
            type(observation) is not SecretLookupObservation
            or type(outcome) is not SecretLookupOutcome
        ):
            raise ValueError("secret telemetry observation is invalid")
        attributes = (
            TelemetryAttribute(AttributeKey.SECRET_KIND, observation.secret_kind.value),
            TelemetryAttribute(AttributeKey.OUTCOME, outcome.value),
        )
        self._record_metric(MetricPoint(MetricInstrument.SECRET_LOOKUP_COUNT, 1, attributes))
        self._record_duration(
            MetricInstrument.SECRET_LOOKUP_DURATION,
            attributes,
            observation.started_at,
        )
        if observation.span_started and observation.context is not None:
            self._end_span(
                SpanEnd(
                    instrument=SpanInstrument.SECRET_LOOKUP,
                    context=observation.context,
                    status=(
                        SpanStatus.OK
                        if outcome is SecretLookupOutcome.SUCCESS
                        else SpanStatus.ERROR
                    ),
                    attributes=(TelemetryAttribute(AttributeKey.OUTCOME, outcome.value),),
                    error_code=_secret_error_code(outcome),
                )
            )

    def start_job_transition(
        self,
        context: TelemetryContext,
        target_status: JobStatus,
    ) -> JobTransitionObservation:
        validate_telemetry_context(context)
        if type(target_status) is not JobStatus:
            raise ValueError("job target status is not registered")
        status_value = target_status.value.lower()
        started_at = self._safe_clock()
        child = self._safe_child(context)
        span_started = False
        if child is not None:
            span = SpanStart(
                instrument=SpanInstrument.JOB_TRANSITION_WITH_AUDIT,
                context=child,
                attributes=(TelemetryAttribute(AttributeKey.TARGET_STATUS, status_value),),
            )
            span_started = self._start_span(span)
        return JobTransitionObservation(child, status_value, started_at, span_started)

    def finish_job_transition(
        self,
        observation: JobTransitionObservation,
        outcome: JobTransitionOutcome,
    ) -> None:
        if (
            type(observation) is not JobTransitionObservation
            or type(outcome) is not JobTransitionOutcome
        ):
            raise ValueError("job telemetry observation is invalid")
        attributes = (
            TelemetryAttribute(AttributeKey.TARGET_STATUS, observation.target_status),
            TelemetryAttribute(AttributeKey.OUTCOME, outcome.value),
        )
        self._record_metric(MetricPoint(MetricInstrument.JOB_TRANSITION_COUNT, 1, attributes))
        self._record_duration(
            MetricInstrument.JOB_TRANSITION_DURATION,
            attributes,
            observation.started_at,
        )
        if observation.span_started and observation.context is not None:
            self._end_span(
                SpanEnd(
                    instrument=SpanInstrument.JOB_TRANSITION_WITH_AUDIT,
                    context=observation.context,
                    status=(
                        SpanStatus.OK
                        if outcome is JobTransitionOutcome.SUCCESS
                        else SpanStatus.ERROR
                    ),
                    attributes=(TelemetryAttribute(AttributeKey.OUTCOME, outcome.value),),
                    error_code=_job_error_code(outcome),
                )
            )

    def flush_pending_drops(self) -> None:
        """Best-effort export of local drop deltas without recursive backend calls."""
        with self._drop_lock:
            pending = tuple(self._pending_drops.items())
        for stage, count in pending:
            point = MetricPoint(
                MetricInstrument.TELEMETRY_DROP_COUNT,
                count,
                (TelemetryAttribute(AttributeKey.FAILURE_STAGE, stage.value),),
            )
            try:
                validate_metric_point(point)
                if not self._cardinality.reserve(point):
                    self._emit_diagnostic()
                    continue
                self._metric_recorder.record(point)
            except Exception:
                self._drop(FailureStage.METRIC_RECORD)
            else:
                with self._drop_lock:
                    current = self._pending_drops.get(stage, 0)
                    remaining = max(0, current - count)
                    if remaining:
                        self._pending_drops[stage] = remaining
                    else:
                        self._pending_drops.pop(stage, None)

    def _safe_clock(self) -> float | None:
        try:
            value = self._monotonic_clock()
            if type(value) not in {int, float} or not math.isfinite(value):
                raise ValueError("invalid monotonic clock value")
            return float(value)
        except Exception:
            self._drop(FailureStage.CLOCK)
            return None

    def _safe_child(self, parent: TelemetryContext) -> TelemetryContext | None:
        try:
            span_id = self._span_id_factory()
            if type(span_id) is not SpanId:
                raise ValueError("span id factory returned an invalid identifier")
            return parent.child(span_id=span_id)
        except Exception:
            self._drop(FailureStage.SPAN_ID)
            return None

    def _start_span(self, span: SpanStart) -> bool:
        try:
            validate_span_start(span)
            self._trace_recorder.start_span(span)
        except Exception:
            self._drop(FailureStage.SPAN_START)
            return False
        return True

    def _end_span(self, span: SpanEnd) -> None:
        try:
            validate_span_end(span)
            self._trace_recorder.end_span(span)
        except Exception:
            self._drop(FailureStage.SPAN_END)

    def _record_duration(
        self,
        instrument: MetricInstrument,
        attributes: tuple[TelemetryAttribute, ...],
        started_at: float | None,
    ) -> None:
        if started_at is None:
            return
        ended_at = self._safe_clock()
        if ended_at is None:
            return
        self._record_metric(MetricPoint(instrument, (ended_at - started_at) * 1000, attributes))

    def _record_metric(self, point: MetricPoint) -> None:
        self.flush_pending_drops()
        try:
            validate_metric_point(point)
            if not self._cardinality.reserve(point):
                self._drop(FailureStage.METRIC_RECORD)
                return
            self._metric_recorder.record(point)
        except Exception:
            self._drop(FailureStage.METRIC_RECORD)

    def _drop(self, stage: FailureStage) -> None:
        with self._drop_lock:
            self._drop_count += 1
            self._pending_drops[stage] = self._pending_drops.get(stage, 0) + 1
        self._emit_diagnostic()

    def _emit_diagnostic(self) -> None:
        # The diagnostic path is terminal and must never call telemetry again.
        with suppress(Exception):
            self._diagnostic_sink(TELEMETRY_RECORDING_FAILED)


def _secret_error_code(outcome: SecretLookupOutcome) -> ErrorCode | None:
    return {
        SecretLookupOutcome.SUCCESS: None,
        SecretLookupOutcome.NOT_FOUND: ErrorCode.SECRET_NOT_FOUND,
        SecretLookupOutcome.AMBIGUOUS: ErrorCode.SECRET_AMBIGUOUS,
        SecretLookupOutcome.ACCESS_DENIED: ErrorCode.SECRET_ACCESS_DENIED,
        SecretLookupOutcome.KEYCHAIN_LOCKED: ErrorCode.KEYCHAIN_LOCKED,
        SecretLookupOutcome.TIMEOUT: ErrorCode.SECRET_LOOKUP_TIMEOUT,
        SecretLookupOutcome.MALFORMED: ErrorCode.MALFORMED_SECRET,
        SecretLookupOutcome.BACKEND_UNAVAILABLE: ErrorCode.SECRET_BACKEND_UNAVAILABLE,
        SecretLookupOutcome.CAPABILITY_DENIED: ErrorCode.SECRET_CAPABILITY_DENIED,
    }[outcome]


def _job_error_code(outcome: JobTransitionOutcome) -> ErrorCode | None:
    return {
        JobTransitionOutcome.SUCCESS: None,
        JobTransitionOutcome.STALE_LEASE: ErrorCode.STALE_LEASE,
        JobTransitionOutcome.AUDIT_FAILURE: ErrorCode.AUDIT_FAILURE,
        JobTransitionOutcome.DATABASE_FAILURE: ErrorCode.DATABASE_FAILURE,
    }[outcome]
