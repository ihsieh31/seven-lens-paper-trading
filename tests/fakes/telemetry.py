"""Deterministic, dependency-free telemetry fakes used only by tests."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from seven_lens.application.ports.telemetry import (
    MetricInstrument,
    MetricPoint,
    SpanEnd,
    SpanStart,
)
from seven_lens.domain.value_objects import RunId
from seven_lens.observability.context import SpanId, TelemetryContext, TraceId
from seven_lens.observability.failsafe import TelemetryDiagnostic
from seven_lens.observability.instruments import (
    SeriesCardinalityTracker,
    TelemetryValidationError,
    validate_metric_point,
    validate_span_end,
    validate_span_start,
)

FIXED_RUN_ID = RunId.from_string("123e4567-e89b-12d3-a456-426614174000")
FIXED_CORRELATION_ID = UUID("123e4567-e89b-12d3-a456-426614174001")
FIXED_TRACE_ID = TraceId("0123456789abcdef0123456789abcdef")
FIXED_ROOT_SPAN_ID = SpanId("0123456789abcdef")
FIXED_CHILD_SPAN_ID = SpanId("fedcba9876543210")


def fixed_context() -> TelemetryContext:
    return TelemetryContext.root(
        run_id=FIXED_RUN_ID,
        correlation_id=FIXED_CORRELATION_ID,
        trace_id=FIXED_TRACE_ID,
        span_id=FIXED_ROOT_SPAN_ID,
    )


class FixedMonotonicClock:
    def __init__(self, values: Iterable[float]) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return next(self._values)


class FixedSpanIdFactory:
    def __init__(self, values: Iterable[SpanId] = (FIXED_CHILD_SPAN_ID,)) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> SpanId:
        self.calls += 1
        return next(self._values)


class FakeDiagnosticSink:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.diagnostics: list[TelemetryDiagnostic] = []

    def __call__(self, diagnostic: TelemetryDiagnostic) -> None:
        self.diagnostics.append(diagnostic)
        if self.failure is not None:
            raise self.failure


class FakeMetricRecorder:
    def __init__(
        self,
        *,
        events: list[tuple[str, object]] | None = None,
        failure: BaseException | None = None,
        fail_calls: Iterable[int] = (),
        maximum_active_series: int = 64,
    ) -> None:
        self.events = events if events is not None else []
        self.failure = failure
        self.fail_calls = frozenset(fail_calls)
        self.attempts: list[MetricPoint] = []
        self.points: list[MetricPoint] = []
        self._cardinality = SeriesCardinalityTracker(maximum_active_series)

    def record(self, point: MetricPoint) -> None:
        validate_metric_point(point)
        self.attempts.append(point)
        call_number = len(self.attempts)
        if self.failure is not None and (not self.fail_calls or call_number in self.fail_calls):
            raise self.failure
        if not self._cardinality.reserve(point):
            raise TelemetryValidationError
        self.points.append(point)
        self.events.append(("metric", point))

    def active_series(self, instrument: MetricInstrument) -> int:
        return self._cardinality.active_series(instrument)


class FakeTraceRecorder:
    def __init__(
        self,
        *,
        events: list[tuple[str, object]] | None = None,
        start_failure: BaseException | None = None,
        end_failure: BaseException | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.start_failure = start_failure
        self.end_failure = end_failure
        self.start_attempts: list[SpanStart] = []
        self.end_attempts: list[SpanEnd] = []
        self.starts: list[SpanStart] = []
        self.ends: list[SpanEnd] = []

    def start_span(self, span: SpanStart) -> None:
        validate_span_start(span)
        self.start_attempts.append(span)
        if self.start_failure is not None:
            raise self.start_failure
        self.starts.append(span)
        self.events.append(("span_start", span))

    def end_span(self, span: SpanEnd) -> None:
        validate_span_end(span)
        self.end_attempts.append(span)
        if self.end_failure is not None:
            raise self.end_failure
        self.ends.append(span)
        self.events.append(("span_end", span))
