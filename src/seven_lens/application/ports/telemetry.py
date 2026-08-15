"""Dependency-neutral, closed telemetry contracts for application instrumentation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seven_lens.observability.context import TelemetryContext


class MetricInstrument(StrEnum):
    SECRET_LOOKUP_COUNT = "seven_lens.secret.lookup.count"
    SECRET_LOOKUP_DURATION = "seven_lens.secret.lookup.duration"
    JOB_TRANSITION_COUNT = "seven_lens.job.transition.count"
    JOB_TRANSITION_DURATION = "seven_lens.job.transition.duration"
    TELEMETRY_DROP_COUNT = "seven_lens.telemetry.drop.count"


class SpanInstrument(StrEnum):
    SECRET_LOOKUP = "seven_lens.secret.lookup"
    JOB_TRANSITION_WITH_AUDIT = "seven_lens.job.transition_with_audit"


class AttributeKey(StrEnum):
    SECRET_KIND = "secret_kind"
    OUTCOME = "outcome"
    TARGET_STATUS = "target_status"
    FAILURE_STAGE = "failure_stage"


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class ErrorCode(StrEnum):
    SECRET_NOT_FOUND = "secret_not_found"
    SECRET_AMBIGUOUS = "secret_ambiguous"
    SECRET_ACCESS_DENIED = "secret_access_denied"
    KEYCHAIN_LOCKED = "keychain_locked"
    SECRET_LOOKUP_TIMEOUT = "secret_lookup_timeout"
    MALFORMED_SECRET = "malformed_secret"
    SECRET_BACKEND_UNAVAILABLE = "secret_backend_unavailable"
    SECRET_CAPABILITY_DENIED = "secret_capability_denied"
    STALE_LEASE = "stale_lease"
    AUDIT_FAILURE = "audit_failure"
    DATABASE_FAILURE = "database_failure"


@dataclass(frozen=True, slots=True)
class TelemetryAttribute:
    key: AttributeKey
    value: str

    def __post_init__(self) -> None:
        if type(self.key) is not AttributeKey:
            raise ValueError("telemetry attribute key is not registered")
        if type(self.value) is not str:
            raise ValueError("telemetry attribute value must be text")


@dataclass(frozen=True, slots=True)
class MetricPoint:
    instrument: MetricInstrument
    value: int | float
    attributes: tuple[TelemetryAttribute, ...]

    def __post_init__(self) -> None:
        if type(self.instrument) is not MetricInstrument:
            raise ValueError("metric instrument is not registered")
        if type(self.value) not in {int, float}:
            raise ValueError("metric value must be an integer or float")
        if type(self.attributes) is not tuple or any(
            type(attribute) is not TelemetryAttribute for attribute in self.attributes
        ):
            raise ValueError("metric attributes must be a tuple of TelemetryAttribute")


@dataclass(frozen=True, slots=True)
class SpanStart:
    instrument: SpanInstrument
    context: TelemetryContext
    attributes: tuple[TelemetryAttribute, ...]

    def __post_init__(self) -> None:
        if type(self.instrument) is not SpanInstrument:
            raise ValueError("span instrument is not registered")
        if type(self.context) is not TelemetryContext:
            raise ValueError("span context must be a TelemetryContext")
        if type(self.attributes) is not tuple or any(
            type(attribute) is not TelemetryAttribute for attribute in self.attributes
        ):
            raise ValueError("span attributes must be a tuple of TelemetryAttribute")


@dataclass(frozen=True, slots=True)
class SpanEnd:
    instrument: SpanInstrument
    context: TelemetryContext
    status: SpanStatus
    attributes: tuple[TelemetryAttribute, ...]
    error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        if type(self.instrument) is not SpanInstrument:
            raise ValueError("span instrument is not registered")
        if type(self.context) is not TelemetryContext:
            raise ValueError("span context must be a TelemetryContext")
        if type(self.status) is not SpanStatus:
            raise ValueError("span status is invalid")
        if self.error_code is not None and type(self.error_code) is not ErrorCode:
            raise ValueError("span error code is not registered")
        if type(self.attributes) is not tuple or any(
            type(attribute) is not TelemetryAttribute for attribute in self.attributes
        ):
            raise ValueError("span attributes must be a tuple of TelemetryAttribute")


class MetricRecorder(Protocol):
    def record(self, point: MetricPoint) -> None:
        """Record one already validated point without changing business state."""
        ...


class TraceRecorder(Protocol):
    def start_span(self, span: SpanStart) -> None:
        """Start one already validated span."""
        ...

    def end_span(self, span: SpanEnd) -> None:
        """End one already validated span using only a bounded error code."""
        ...
