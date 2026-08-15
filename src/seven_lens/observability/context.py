"""Explicit, immutable telemetry identifiers and propagation context."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from uuid import UUID

from seven_lens.domain.value_objects import RunId

_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True, slots=True)
class TraceId:
    """A canonical, non-zero W3C-compatible 128-bit trace identifier."""

    value: str

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or _TRACE_ID_PATTERN.fullmatch(self.value) is None
            or int(self.value, 16) == 0
        ):
            raise ValueError("TraceId must be 32 lowercase hexadecimal characters and non-zero")

    @classmethod
    def new(cls) -> TraceId:
        value = "0" * 32
        while int(value, 16) == 0:
            value = secrets.token_hex(16)
        return cls(value)

    @classmethod
    def from_string(cls, value: str) -> TraceId:
        return cls(value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SpanId:
    """A canonical, non-zero W3C-compatible 64-bit span identifier."""

    value: str

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or _SPAN_ID_PATTERN.fullmatch(self.value) is None
            or int(self.value, 16) == 0
        ):
            raise ValueError("SpanId must be 16 lowercase hexadecimal characters and non-zero")

    @classmethod
    def new(cls) -> SpanId:
        value = "0" * 16
        while int(value, 16) == 0:
            value = secrets.token_hex(8)
        return cls(value)

    @classmethod
    def from_string(cls, value: str) -> SpanId:
        return cls(value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TelemetryContext:
    """Explicit processing context; application code never relies on ambient state."""

    run_id: RunId
    correlation_id: UUID
    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None = None

    def __post_init__(self) -> None:
        validate_telemetry_context(self)

    @classmethod
    def root(
        cls,
        *,
        run_id: RunId,
        correlation_id: UUID,
        trace_id: TraceId | None = None,
        span_id: SpanId | None = None,
    ) -> TelemetryContext:
        """Create a root context, accepting fixed identifiers for deterministic tests."""
        return cls(
            run_id=run_id,
            correlation_id=correlation_id,
            trace_id=trace_id or TraceId.new(),
            span_id=span_id or SpanId.new(),
        )

    def child(self, *, span_id: SpanId | None = None) -> TelemetryContext:
        """Preserve run/correlation/trace identity and create one direct child span."""
        child_span_id = span_id or SpanId.new()
        if child_span_id == self.span_id:
            raise ValueError("child span_id must differ from its parent")
        return TelemetryContext(
            run_id=self.run_id,
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
            span_id=child_span_id,
            parent_span_id=self.span_id,
        )

    def to_log_fields(self) -> dict[str, str | None]:
        """Return only the validated identifiers allowed in log/span context."""
        validate_telemetry_context(self)
        return {
            "run_id": str(self.run_id),
            "correlation_id": str(self.correlation_id),
            "trace_id": str(self.trace_id),
            "span_id": str(self.span_id),
            "parent_span_id": (
                str(self.parent_span_id) if self.parent_span_id is not None else None
            ),
        }


def validate_telemetry_context(value: object) -> TelemetryContext:
    """Revalidate even a frozen instance so corrupted objects fail closed."""
    if type(value) is not TelemetryContext:
        raise ValueError("telemetry context must be a TelemetryContext")
    if type(value.run_id) is not RunId:
        raise ValueError("telemetry context run_id must be a RunId")
    if not isinstance(value.run_id.value, UUID) or value.run_id.value.int == 0:
        raise ValueError("telemetry context run_id must be non-zero")
    if not isinstance(value.correlation_id, UUID) or value.correlation_id.int == 0:
        raise ValueError("telemetry context correlation_id must be a non-nil UUID")
    if type(value.trace_id) is not TraceId:
        raise ValueError("telemetry context trace_id must be a TraceId")
    TraceId(value.trace_id.value)
    if type(value.span_id) is not SpanId:
        raise ValueError("telemetry context span_id must be a SpanId")
    SpanId(value.span_id.value)
    if value.parent_span_id is not None:
        if type(value.parent_span_id) is not SpanId:
            raise ValueError("telemetry context parent_span_id must be a SpanId or None")
        SpanId(value.parent_span_id.value)
        if value.parent_span_id == value.span_id:
            raise ValueError("telemetry context span_id must differ from parent_span_id")
    return value
