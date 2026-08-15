"""Application-layer secret lookup instrumentation and non-disclosure tests."""

from __future__ import annotations

import pytest

from fakes.secrets import FakeSecretProvider
from fakes.telemetry import (
    FakeDiagnosticSink,
    FakeMetricRecorder,
    FakeTraceRecorder,
    FixedMonotonicClock,
    FixedSpanIdFactory,
    fixed_context,
)
from seven_lens.application.ports.secrets import (
    KeychainLocked,
    MalformedSecret,
    SecretAccessDenied,
    SecretAmbiguous,
    SecretBackendUnavailable,
    SecretCapabilityDenied,
    SecretLookupTimeout,
    SecretNotFound,
    SecretProviderError,
)
from seven_lens.application.ports.telemetry import AttributeKey, MetricInstrument, MetricPoint
from seven_lens.application.secret_service import InstrumentedSecretProvider
from seven_lens.observability.failsafe import FailSafeTelemetry
from seven_lens.observability.instruments import SecretLookupOutcome
from seven_lens.security.secret_values import SecretKind, SecretRef, SecretValue

FAKE_SECRET_TEXT = "fake-secret-value-must-not-leak-000001"
FAKE_SECRET = SecretValue.from_bytes(FAKE_SECRET_TEXT.encode())
OPENAI_REF = SecretRef.primary(SecretKind.OPENAI_API_KEY)


def make_telemetry(
    metrics: FakeMetricRecorder | None = None,
    traces: FakeTraceRecorder | None = None,
    diagnostics: FakeDiagnosticSink | None = None,
) -> tuple[FailSafeTelemetry, FakeMetricRecorder, FakeTraceRecorder, FakeDiagnosticSink]:
    metric_recorder = metrics or FakeMetricRecorder()
    trace_recorder = traces or FakeTraceRecorder()
    diagnostic_sink = diagnostics or FakeDiagnosticSink()
    facade = FailSafeTelemetry(
        metric_recorder,
        trace_recorder,
        diagnostic_sink,
        monotonic_clock=FixedMonotonicClock((5.0, 5.012)),
        span_id_factory=FixedSpanIdFactory(),
    )
    return facade, metric_recorder, trace_recorder, diagnostic_sink


def attributes(point: MetricPoint) -> dict[str, str]:
    return {attribute.key.value: attribute.value for attribute in point.attributes}


def test_secret_lookup_success_records_only_kind_outcome_and_bounded_span() -> None:
    facade, metrics, traces, diagnostics = make_telemetry()
    backend = FakeSecretProvider({OPENAI_REF: FAKE_SECRET})
    provider = InstrumentedSecretProvider(backend, facade, fixed_context())

    assert provider.get_secret(OPENAI_REF) is FAKE_SECRET

    assert [point.instrument for point in metrics.points] == [
        MetricInstrument.SECRET_LOOKUP_COUNT,
        MetricInstrument.SECRET_LOOKUP_DURATION,
    ]
    assert all(
        attributes(point) == {"secret_kind": "openai_api_key", "outcome": "success"}
        for point in metrics.points
    )
    assert traces.ends[0].error_code is None
    assert diagnostics.diagnostics == []
    serialized_evidence = repr(
        (metrics.points, traces.starts, traces.ends, diagnostics.diagnostics)
    )
    assert FAKE_SECRET_TEXT not in serialized_evidence
    assert "account_id" not in serialized_evidence


@pytest.mark.parametrize(
    ("failure", "expected_outcome"),
    [
        (SecretNotFound(), SecretLookupOutcome.NOT_FOUND),
        (SecretAmbiguous(), SecretLookupOutcome.AMBIGUOUS),
        (SecretAccessDenied(), SecretLookupOutcome.ACCESS_DENIED),
        (KeychainLocked(), SecretLookupOutcome.KEYCHAIN_LOCKED),
        (SecretLookupTimeout(), SecretLookupOutcome.TIMEOUT),
        (MalformedSecret(), SecretLookupOutcome.MALFORMED),
        (SecretBackendUnavailable(), SecretLookupOutcome.BACKEND_UNAVAILABLE),
        (SecretCapabilityDenied(), SecretLookupOutcome.CAPABILITY_DENIED),
    ],
)
def test_all_typed_secret_failures_preserve_original_exception_and_record_bounded_outcome(
    failure: SecretProviderError,
    expected_outcome: SecretLookupOutcome,
) -> None:
    facade, metrics, traces, _ = make_telemetry()
    provider = InstrumentedSecretProvider(
        FakeSecretProvider(failures={OPENAI_REF: failure}),
        facade,
        fixed_context(),
    )

    with pytest.raises(type(failure)) as caught:
        provider.get_secret(OPENAI_REF)

    assert caught.value is failure
    assert all(attributes(point)["outcome"] == expected_outcome.value for point in metrics.points)
    assert traces.ends[0].status.value == "error"
    assert traces.ends[0].error_code is not None
    evidence = repr((metrics.points, traces.starts, traces.ends))
    assert str(failure) not in evidence
    assert FAKE_SECRET_TEXT not in evidence


def test_unexpected_lookup_exception_is_preserved_without_exception_text() -> None:
    fake_detail = "fake-secret-in-unexpected-exception"
    failure = RuntimeError(fake_detail)
    facade, metrics, traces, _ = make_telemetry()
    backend = FakeSecretProvider()
    backend._failures[OPENAI_REF] = failure  # type: ignore[assignment]
    provider = InstrumentedSecretProvider(backend, facade, fixed_context())

    with pytest.raises(RuntimeError) as caught:
        provider.get_secret(OPENAI_REF)

    assert caught.value is failure
    assert attributes(metrics.points[0])["outcome"] == "backend_unavailable"
    assert fake_detail not in repr((metrics.points, traces.ends))


@pytest.mark.parametrize("failure_location", ["metric", "span_start", "span_end"])
def test_recorder_exception_does_not_change_successful_secret_result(
    failure_location: str,
) -> None:
    metrics = FakeMetricRecorder(
        failure=RuntimeError("unsafe metric detail") if failure_location == "metric" else None
    )
    traces = FakeTraceRecorder(
        start_failure=(
            RuntimeError("unsafe start detail") if failure_location == "span_start" else None
        ),
        end_failure=(RuntimeError("unsafe end detail") if failure_location == "span_end" else None),
    )
    diagnostics = FakeDiagnosticSink()
    facade, _, _, _ = make_telemetry(metrics, traces, diagnostics)
    provider = InstrumentedSecretProvider(
        FakeSecretProvider({OPENAI_REF: FAKE_SECRET}), facade, fixed_context()
    )

    assert provider.get_secret(OPENAI_REF) is FAKE_SECRET
    assert facade.drop_count >= 1
    assert all(item.code.value == "telemetry_recording_failed" for item in diagnostics.diagnostics)


def test_recorder_exception_does_not_replace_typed_secret_failure() -> None:
    original = SecretNotFound()
    facade, _, _, _ = make_telemetry(
        FakeMetricRecorder(failure=RuntimeError("telemetry backend detail")),
        FakeTraceRecorder(end_failure=RuntimeError("span backend detail")),
    )
    provider = InstrumentedSecretProvider(
        FakeSecretProvider(failures={OPENAI_REF: original}),
        facade,
        fixed_context(),
    )

    with pytest.raises(SecretNotFound) as caught:
        provider.get_secret(OPENAI_REF)

    assert caught.value is original


def test_backend_baseexception_is_not_swallowed() -> None:
    class InterruptingProvider:
        def get_secret(self, ref: SecretRef) -> SecretValue:
            del ref
            raise KeyboardInterrupt

    facade, metrics, traces, _ = make_telemetry()
    provider = InstrumentedSecretProvider(InterruptingProvider(), facade, fixed_context())

    with pytest.raises(KeyboardInterrupt):
        provider.get_secret(OPENAI_REF)
    assert metrics.points == []
    assert len(traces.starts) == 1
    assert traces.ends == []


def test_metric_attributes_cannot_include_ids_account_url_payload_or_authorization() -> None:
    facade, metrics, _, _ = make_telemetry()
    provider = InstrumentedSecretProvider(
        FakeSecretProvider({OPENAI_REF: FAKE_SECRET}), facade, fixed_context()
    )
    provider.get_secret(OPENAI_REF)

    keys = {attribute.key for point in metrics.points for attribute in point.attributes}
    assert keys == {AttributeKey.SECRET_KIND, AttributeKey.OUTCOME}
    evidence = repr(metrics.points).lower()
    for forbidden in (
        "run_id",
        "correlation_id",
        "trace_id",
        "span_id",
        "account_id",
        "url",
        "dsn",
        "authorization",
        "payload",
    ):
        assert forbidden not in evidence
