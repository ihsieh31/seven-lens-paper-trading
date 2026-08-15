"""Closed registry, validation, ordering, cardinality, and fail-safe tests."""

from __future__ import annotations

import math
import re

import pytest

from fakes.telemetry import (
    FIXED_CHILD_SPAN_ID,
    FakeDiagnosticSink,
    FakeMetricRecorder,
    FakeTraceRecorder,
    FixedMonotonicClock,
    FixedSpanIdFactory,
    fixed_context,
)
from seven_lens.application.ports.telemetry import (
    AttributeKey,
    ErrorCode,
    MetricInstrument,
    MetricPoint,
    SpanEnd,
    SpanInstrument,
    SpanStatus,
    TelemetryAttribute,
)
from seven_lens.observability.failsafe import (
    TELEMETRY_RECORDING_FAILED,
    FailSafeTelemetry,
)
from seven_lens.observability.instruments import (
    MAX_ACTIVE_SERIES,
    JobTransitionOutcome,
    SecretKindAttribute,
    SecretLookupOutcome,
    SeriesCardinalityTracker,
    TelemetryValidationError,
    metric_definitions,
    span_definitions,
    validate_metric_point,
    validate_span_end,
)


def telemetry(
    metrics: FakeMetricRecorder | None = None,
    traces: FakeTraceRecorder | None = None,
    diagnostics: FakeDiagnosticSink | None = None,
    *,
    clock: FixedMonotonicClock | None = None,
    maximum_active_series: int = 64,
) -> FailSafeTelemetry:
    return FailSafeTelemetry(
        metrics or FakeMetricRecorder(),
        traces or FakeTraceRecorder(),
        diagnostics or FakeDiagnosticSink(),
        monotonic_clock=clock or FixedMonotonicClock((1.0, 1.025)),
        span_id_factory=FixedSpanIdFactory(),
        maximum_active_series=maximum_active_series,
    )


def test_registry_is_closed_bounded_and_has_only_p1_c2_instruments() -> None:
    metrics = metric_definitions()
    spans = span_definitions()

    assert [definition.instrument.value for definition in metrics] == [
        "seven_lens.secret.lookup.count",
        "seven_lens.secret.lookup.duration",
        "seven_lens.job.transition.count",
        "seven_lens.job.transition.duration",
        "seven_lens.telemetry.drop.count",
    ]
    assert [definition.instrument.value for definition in spans] == [
        "seven_lens.secret.lookup",
        "seven_lens.job.transition_with_audit",
    ]
    assert [definition.unit for definition in metrics] == ["1", "ms", "1", "ms", "1"]
    for metric_definition in metrics:
        assert len(metric_definition.instrument.value) <= 100
        assert re.fullmatch(
            r"seven_lens\.[a-z0-9_]+(?:\.[a-z0-9_]+)*", metric_definition.instrument.value
        )
    for span_definition in spans:
        assert len(span_definition.instrument.value) <= 100
        assert re.fullmatch(
            r"seven_lens\.[a-z0-9_]+(?:\.[a-z0-9_]+)*", span_definition.instrument.value
        )
    assert all(len(definition.attributes) <= 2 for definition in metrics)
    assert all(
        len(definition.start_attributes) + len(definition.end_attributes) <= 2
        for definition in spans
    )
    assert all(definition.maximum_active_series == 64 for definition in metrics)
    assert all(
        math.prod(len(attribute.allowed_values) for attribute in definition.attributes) <= 64
        for definition in metrics
    )


def test_metric_validation_rejects_unknown_name_key_value_count_and_length() -> None:
    valid = MetricPoint(
        MetricInstrument.SECRET_LOOKUP_COUNT,
        1,
        (
            TelemetryAttribute(AttributeKey.SECRET_KIND, "openai_api_key"),
            TelemetryAttribute(AttributeKey.OUTCOME, "success"),
        ),
    )
    assert validate_metric_point(valid) is valid

    with pytest.raises(ValueError, match="not registered"):
        MetricPoint("seven_lens.unknown", 1, ())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not registered"):
        TelemetryAttribute("run_id", "forbidden")  # type: ignore[arg-type]
    with pytest.raises(TelemetryValidationError):
        validate_metric_point(
            MetricPoint(
                MetricInstrument.SECRET_LOOKUP_COUNT,
                1,
                (
                    TelemetryAttribute(AttributeKey.SECRET_KIND, "openai_api_key"),
                    TelemetryAttribute(AttributeKey.OUTCOME, "unknown"),
                ),
            )
        )
    with pytest.raises(TelemetryValidationError):
        validate_metric_point(
            MetricPoint(
                MetricInstrument.SECRET_LOOKUP_COUNT,
                1,
                (
                    TelemetryAttribute(AttributeKey.SECRET_KIND, "x" * 65),
                    TelemetryAttribute(AttributeKey.OUTCOME, "success"),
                ),
            )
        )
    with pytest.raises(TelemetryValidationError):
        validate_metric_point(
            MetricPoint(
                MetricInstrument.SECRET_LOOKUP_COUNT,
                1,
                tuple(TelemetryAttribute(AttributeKey.OUTCOME, "success") for _ in range(5)),
            )
        )


@pytest.mark.parametrize("duration", [-1.0, math.nan, math.inf, -math.inf])
def test_duration_rejects_negative_nan_and_infinity(duration: float) -> None:
    with pytest.raises(TelemetryValidationError):
        validate_metric_point(
            MetricPoint(
                MetricInstrument.JOB_TRANSITION_DURATION,
                duration,
                (
                    TelemetryAttribute(AttributeKey.TARGET_STATUS, "running"),
                    TelemetryAttribute(AttributeKey.OUTCOME, "success"),
                ),
            )
        )


def test_span_validation_rejects_unknown_outcome_and_wrong_bounded_error_code() -> None:
    observation_context = fixed_context().child(span_id=FIXED_CHILD_SPAN_ID)
    with pytest.raises(ValueError, match="not registered"):
        SpanEnd(
            "seven_lens.unknown",  # type: ignore[arg-type]
            observation_context,
            SpanStatus.ERROR,
            (TelemetryAttribute(AttributeKey.OUTCOME, "database_failure"),),
            ErrorCode.DATABASE_FAILURE,
        )
    with pytest.raises(TelemetryValidationError):
        validate_span_end(
            SpanEnd(
                SpanInstrument.SECRET_LOOKUP,
                observation_context,
                SpanStatus.ERROR,
                (TelemetryAttribute(AttributeKey.OUTCOME, "unknown"),),
                ErrorCode.SECRET_NOT_FOUND,
            )
        )
    with pytest.raises(TelemetryValidationError):
        validate_span_end(
            SpanEnd(
                SpanInstrument.SECRET_LOOKUP,
                observation_context,
                SpanStatus.ERROR,
                (TelemetryAttribute(AttributeKey.OUTCOME, "not_found"),),
                ErrorCode.DATABASE_FAILURE,
            )
        )


@pytest.mark.parametrize(
    "forbidden",
    [
        "run_id",
        "correlation_id",
        "trace_id",
        "span_id",
        "secret",
        "account_id",
        "job_key",
        "symbol",
        "url",
        "dsn",
        "authorization",
        "payload",
        "exception_message",
        "stack",
        "repr",
    ],
)
def test_forbidden_metric_attribute_names_are_not_public_keys(forbidden: str) -> None:
    with pytest.raises(ValueError):
        TelemetryAttribute(forbidden, "x")  # type: ignore[arg-type]


def test_series_cardinality_is_bounded_and_repeated_series_do_not_grow() -> None:
    assert MAX_ACTIVE_SERIES == 64
    tracker = SeriesCardinalityTracker(maximum_active_series=1)
    first = MetricPoint(
        MetricInstrument.SECRET_LOOKUP_COUNT,
        1,
        (
            TelemetryAttribute(AttributeKey.SECRET_KIND, "openai_api_key"),
            TelemetryAttribute(AttributeKey.OUTCOME, "success"),
        ),
    )
    second = MetricPoint(
        MetricInstrument.SECRET_LOOKUP_COUNT,
        1,
        (
            TelemetryAttribute(AttributeKey.SECRET_KIND, "tavily_api_key"),
            TelemetryAttribute(AttributeKey.OUTCOME, "success"),
        ),
    )

    assert tracker.reserve(first) is True
    assert tracker.reserve(first) is True
    assert tracker.reserve(second) is False
    assert tracker.active_series(MetricInstrument.SECRET_LOOKUP_COUNT) == 1


def test_deterministic_metric_and_span_ordering_and_context() -> None:
    events: list[tuple[str, object]] = []
    metrics = FakeMetricRecorder(events=events)
    traces = FakeTraceRecorder(events=events)
    facade = telemetry(metrics, traces)

    observation = facade.start_secret_lookup(fixed_context(), SecretKindAttribute.OPENAI_API_KEY)
    facade.finish_secret_lookup(observation, SecretLookupOutcome.SUCCESS)

    assert [kind for kind, _ in events] == ["span_start", "metric", "metric", "span_end"]
    assert [point.instrument for point in metrics.points] == [
        MetricInstrument.SECRET_LOOKUP_COUNT,
        MetricInstrument.SECRET_LOOKUP_DURATION,
    ]
    assert metrics.points[1].value == pytest.approx(25.0)
    assert traces.starts[0].context.span_id == FIXED_CHILD_SPAN_ID
    assert traces.starts[0].context.parent_span_id == fixed_context().span_id
    assert traces.ends[0].error_code is None


def test_metric_failure_becomes_fixed_diagnostic_and_local_drop_without_secret() -> None:
    fake_secret = "fake-secret-must-never-appear"
    metrics = FakeMetricRecorder(failure=RuntimeError(fake_secret))
    diagnostics = FakeDiagnosticSink()
    traces = FakeTraceRecorder()
    facade = telemetry(metrics, traces, diagnostics)

    observation = facade.start_secret_lookup(fixed_context(), SecretKindAttribute.TAVILY_API_KEY)
    facade.finish_secret_lookup(observation, SecretLookupOutcome.SUCCESS)

    assert facade.drop_count >= 2
    assert diagnostics.diagnostics
    assert set(diagnostics.diagnostics) == {TELEMETRY_RECORDING_FAILED}
    assert fake_secret not in repr(diagnostics.diagnostics)
    assert len(traces.ends) == 1


def test_start_and_end_span_failures_are_independently_fail_safe() -> None:
    start_diagnostics = FakeDiagnosticSink()
    start_traces = FakeTraceRecorder(start_failure=RuntimeError("unsafe-start-detail"))
    start_metrics = FakeMetricRecorder()
    start_facade = telemetry(start_metrics, start_traces, start_diagnostics)
    observation = start_facade.start_secret_lookup(
        fixed_context(), SecretKindAttribute.OPENAI_API_KEY
    )
    start_facade.finish_secret_lookup(observation, SecretLookupOutcome.SUCCESS)
    assert start_facade.drop_count == 1
    assert start_traces.end_attempts == []
    assert any(
        point.instrument is MetricInstrument.TELEMETRY_DROP_COUNT for point in start_metrics.points
    )

    end_diagnostics = FakeDiagnosticSink()
    end_traces = FakeTraceRecorder(end_failure=RuntimeError("unsafe-end-detail"))
    end_facade = telemetry(FakeMetricRecorder(), end_traces, end_diagnostics)
    observation = end_facade.start_secret_lookup(
        fixed_context(), SecretKindAttribute.OPENAI_API_KEY
    )
    end_facade.finish_secret_lookup(observation, SecretLookupOutcome.NOT_FOUND)
    assert end_facade.drop_count == 1
    assert end_facade.pending_drop_count == 1
    assert len(end_traces.start_attempts) == 1
    assert len(end_traces.end_attempts) == 1


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit()])
def test_baseexception_from_recorders_is_not_swallowed(failure: BaseException) -> None:
    metrics = FakeMetricRecorder(failure=failure)
    facade = telemetry(metrics)
    observation = facade.start_secret_lookup(fixed_context(), SecretKindAttribute.OPENAI_API_KEY)

    with pytest.raises(type(failure)):
        facade.finish_secret_lookup(observation, SecretLookupOutcome.SUCCESS)


@pytest.mark.parametrize("failure_location", ["span_start", "span_end"])
def test_baseexception_from_each_span_recorder_stage_is_not_swallowed(
    failure_location: str,
) -> None:
    traces = FakeTraceRecorder(
        start_failure=KeyboardInterrupt() if failure_location == "span_start" else None,
        end_failure=SystemExit() if failure_location == "span_end" else None,
    )
    facade = telemetry(traces=traces)

    if failure_location == "span_start":
        with pytest.raises(KeyboardInterrupt):
            facade.start_secret_lookup(fixed_context(), SecretKindAttribute.OPENAI_API_KEY)
    else:
        observation = facade.start_secret_lookup(
            fixed_context(), SecretKindAttribute.OPENAI_API_KEY
        )
        with pytest.raises(SystemExit):
            facade.finish_secret_lookup(observation, SecretLookupOutcome.SUCCESS)


def test_fixed_diagnostic_failure_never_recurses_into_backend() -> None:
    metrics = FakeMetricRecorder(failure=RuntimeError("backend detail"))
    diagnostics = FakeDiagnosticSink(failure=RuntimeError("diagnostic detail"))
    facade = telemetry(metrics, diagnostics=diagnostics)

    observation = facade.start_secret_lookup(fixed_context(), SecretKindAttribute.OPENAI_API_KEY)
    facade.finish_secret_lookup(observation, SecretLookupOutcome.SUCCESS)

    assert len(metrics.attempts) < 10
    assert len(diagnostics.diagnostics) == facade.drop_count
    assert all(item is TELEMETRY_RECORDING_FAILED for item in diagnostics.diagnostics)


def test_cardinality_drop_does_not_record_65th_series_or_change_call_result() -> None:
    metrics = FakeMetricRecorder()
    diagnostics = FakeDiagnosticSink()
    facade = telemetry(metrics, diagnostics=diagnostics, maximum_active_series=1)

    first = facade.start_secret_lookup(fixed_context(), SecretKindAttribute.OPENAI_API_KEY)
    facade.finish_secret_lookup(first, SecretLookupOutcome.SUCCESS)
    second = facade.start_secret_lookup(fixed_context(), SecretKindAttribute.TAVILY_API_KEY)
    facade.finish_secret_lookup(second, SecretLookupOutcome.SUCCESS)

    assert facade.active_series(MetricInstrument.SECRET_LOOKUP_COUNT) == 1
    assert facade.drop_count >= 1


def test_job_outcome_enum_is_closed_to_bounded_values() -> None:
    assert {item.value for item in JobTransitionOutcome} == {
        "success",
        "stale_lease",
        "audit_failure",
        "database_failure",
    }
    assert set(SpanInstrument) == {
        SpanInstrument.SECRET_LOOKUP,
        SpanInstrument.JOB_TRANSITION_WITH_AUDIT,
    }
