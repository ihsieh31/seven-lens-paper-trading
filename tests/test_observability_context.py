# mypy: ignore-errors
"""Canonical telemetry identity, propagation, and structured-log tests."""

from __future__ import annotations

import json
import logging
from io import StringIO
from uuid import UUID

import pytest

from fakes.telemetry import (
    FIXED_CHILD_SPAN_ID,
    FIXED_CORRELATION_ID,
    FIXED_ROOT_SPAN_ID,
    FIXED_RUN_ID,
    FIXED_TRACE_ID,
    fixed_context,
)
from seven_lens.observability.context import SpanId, TelemetryContext, TraceId
from seven_lens.observability.structured_logging import JsonFormatter, log_event


@pytest.mark.parametrize(
    ("identifier_type", "value"),
    [
        (TraceId, "0" * 32),
        (TraceId, "0123456789ABCDEF0123456789ABCDEF"),
        (TraceId, "0123456789abcdef"),
        (TraceId, "g" * 32),
        (SpanId, "0" * 16),
        (SpanId, "0123456789ABCDEF"),
        (SpanId, "01234567"),
        (SpanId, "g" * 16),
    ],
)
def test_trace_and_span_ids_reject_zero_uppercase_wrong_length_and_non_hex(
    identifier_type: type[TraceId] | type[SpanId],
    value: str,
) -> None:
    with pytest.raises(ValueError):
        identifier_type(value)


def test_fixed_root_and_child_context_preserve_identity_and_parentage() -> None:
    root = fixed_context()
    child = root.child(span_id=FIXED_CHILD_SPAN_ID)

    assert root == TelemetryContext.root(
        run_id=FIXED_RUN_ID,
        correlation_id=FIXED_CORRELATION_ID,
        trace_id=FIXED_TRACE_ID,
        span_id=FIXED_ROOT_SPAN_ID,
    )
    assert child.run_id is root.run_id
    assert child.correlation_id == root.correlation_id
    assert child.trace_id is root.trace_id
    assert child.span_id is FIXED_CHILD_SPAN_ID
    assert child.parent_span_id is root.span_id


def test_context_rejects_nil_correlation_and_reused_parent_span_id() -> None:
    with pytest.raises(ValueError, match="non-nil UUID"):
        TelemetryContext.root(
            run_id=FIXED_RUN_ID,
            correlation_id=UUID(int=0),
            trace_id=FIXED_TRACE_ID,
            span_id=FIXED_ROOT_SPAN_ID,
        )

    with pytest.raises(ValueError, match="differ"):
        fixed_context().child(span_id=FIXED_ROOT_SPAN_ID)


def test_context_is_immutable() -> None:
    context = fixed_context()

    with pytest.raises((AttributeError, TypeError)):
        context.trace_id = TraceId.new()  # type: ignore[misc]


def test_structured_logging_injects_only_validated_context_when_present() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("seven_lens.tests.telemetry_context")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        log_event(logger, "processing", telemetry_context=fixed_context(), status="ok")
        event = json.loads(stream.getvalue())
        assert event["fields"]["telemetry"] == {
            "correlation_id": str(FIXED_CORRELATION_ID),
            "parent_span_id": None,
            "run_id": str(FIXED_RUN_ID),
            "span_id": str(FIXED_ROOT_SPAN_ID),
            "trace_id": str(FIXED_TRACE_ID),
        }
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_startup_log_does_not_fabricate_context_and_invalid_context_is_rejected() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("seven_lens.tests.startup_context")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        log_event(logger, "startup", configured=True)
        assert "telemetry" not in json.loads(stream.getvalue())["fields"]
        with pytest.raises(ValueError, match="TelemetryContext"):
            log_event(logger, "invalid", telemetry_context="forged")  # type: ignore[arg-type]
    finally:
        logger.removeHandler(handler)
        handler.close()
