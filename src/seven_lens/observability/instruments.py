"""Closed P1-C2 instrument registry, validation, and series cardinality guard."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Final

from seven_lens.application.ports.telemetry import (
    AttributeKey,
    ErrorCode,
    MetricInstrument,
    MetricPoint,
    SpanEnd,
    SpanInstrument,
    SpanStart,
    SpanStatus,
    TelemetryAttribute,
)
from seven_lens.observability.context import validate_telemetry_context

MAX_INSTRUMENT_NAME_LENGTH: Final = 100
MAX_ATTRIBUTES: Final = 4
MAX_ATTRIBUTE_VALUE_LENGTH: Final = 64
MAX_ACTIVE_SERIES: Final = 64

_INSTRUMENT_NAME_PATTERN = re.compile(
    r"^seven_lens\.[a-z0-9_]+(?:\.[a-z0-9_]+)*$",
    flags=re.ASCII,
)
_ATTRIBUTE_VALUE_PATTERN = re.compile(r"^[a-z0-9_]+$", flags=re.ASCII)
_PROHIBITED_ATTRIBUTE_KEYS: Final = frozenset(
    {
        "run_id",
        "correlation_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "secret",
        "account_id",
        "job_key",
        "symbol",
        "url",
        "dsn",
        "authorization",
        "payload",
        "exception",
        "exception_message",
        "stack",
        "repr",
    }
)


class SecretKindAttribute(StrEnum):
    ALPACA_PAPER_KEY_ID = "alpaca_paper_key_id"
    ALPACA_PAPER_SECRET_KEY = "alpaca_paper_secret_key"
    OPENAI_API_KEY = "openai_api_key"
    POSTGRES_RUNTIME_PASSWORD = "postgres_runtime_password"
    TAVILY_API_KEY = "tavily_api_key"


class SecretLookupOutcome(StrEnum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    ACCESS_DENIED = "access_denied"
    KEYCHAIN_LOCKED = "keychain_locked"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    CAPABILITY_DENIED = "capability_denied"


class JobTransitionOutcome(StrEnum):
    SUCCESS = "success"
    STALE_LEASE = "stale_lease"
    AUDIT_FAILURE = "audit_failure"
    DATABASE_FAILURE = "database_failure"


class FailureStage(StrEnum):
    METRIC_RECORD = "metric_record"
    SPAN_START = "span_start"
    SPAN_END = "span_end"
    CLOCK = "clock"
    SPAN_ID = "span_id"


@dataclass(frozen=True, slots=True)
class AttributeDefinition:
    key: AttributeKey
    allowed_values: frozenset[str]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    instrument: MetricInstrument
    unit: str
    attributes: tuple[AttributeDefinition, ...]
    maximum_active_series: int = MAX_ACTIVE_SERIES


@dataclass(frozen=True, slots=True)
class SpanDefinition:
    instrument: SpanInstrument
    start_attributes: tuple[AttributeDefinition, ...]
    end_attributes: tuple[AttributeDefinition, ...]
    allowed_error_codes: frozenset[ErrorCode]


_SECRET_KINDS: Final = frozenset(item.value for item in SecretKindAttribute)
_SECRET_OUTCOMES: Final = frozenset(item.value for item in SecretLookupOutcome)
_JOB_STATUSES: Final = frozenset({"planned", "running", "complete", "failed", "expired"})
_JOB_OUTCOMES: Final = frozenset(item.value for item in JobTransitionOutcome)
_FAILURE_STAGES: Final = frozenset(item.value for item in FailureStage)

_METRIC_DEFINITIONS: Final = {
    MetricInstrument.SECRET_LOOKUP_COUNT: MetricDefinition(
        MetricInstrument.SECRET_LOOKUP_COUNT,
        "1",
        (
            AttributeDefinition(AttributeKey.SECRET_KIND, _SECRET_KINDS),
            AttributeDefinition(AttributeKey.OUTCOME, _SECRET_OUTCOMES),
        ),
    ),
    MetricInstrument.SECRET_LOOKUP_DURATION: MetricDefinition(
        MetricInstrument.SECRET_LOOKUP_DURATION,
        "ms",
        (
            AttributeDefinition(AttributeKey.SECRET_KIND, _SECRET_KINDS),
            AttributeDefinition(AttributeKey.OUTCOME, _SECRET_OUTCOMES),
        ),
    ),
    MetricInstrument.JOB_TRANSITION_COUNT: MetricDefinition(
        MetricInstrument.JOB_TRANSITION_COUNT,
        "1",
        (
            AttributeDefinition(AttributeKey.TARGET_STATUS, _JOB_STATUSES),
            AttributeDefinition(AttributeKey.OUTCOME, _JOB_OUTCOMES),
        ),
    ),
    MetricInstrument.JOB_TRANSITION_DURATION: MetricDefinition(
        MetricInstrument.JOB_TRANSITION_DURATION,
        "ms",
        (
            AttributeDefinition(AttributeKey.TARGET_STATUS, _JOB_STATUSES),
            AttributeDefinition(AttributeKey.OUTCOME, _JOB_OUTCOMES),
        ),
    ),
    MetricInstrument.TELEMETRY_DROP_COUNT: MetricDefinition(
        MetricInstrument.TELEMETRY_DROP_COUNT,
        "1",
        (AttributeDefinition(AttributeKey.FAILURE_STAGE, _FAILURE_STAGES),),
    ),
}

_SPAN_DEFINITIONS: Final = {
    SpanInstrument.SECRET_LOOKUP: SpanDefinition(
        SpanInstrument.SECRET_LOOKUP,
        (AttributeDefinition(AttributeKey.SECRET_KIND, _SECRET_KINDS),),
        (AttributeDefinition(AttributeKey.OUTCOME, _SECRET_OUTCOMES),),
        frozenset(
            {
                ErrorCode.SECRET_NOT_FOUND,
                ErrorCode.SECRET_AMBIGUOUS,
                ErrorCode.SECRET_ACCESS_DENIED,
                ErrorCode.KEYCHAIN_LOCKED,
                ErrorCode.SECRET_LOOKUP_TIMEOUT,
                ErrorCode.MALFORMED_SECRET,
                ErrorCode.SECRET_BACKEND_UNAVAILABLE,
                ErrorCode.SECRET_CAPABILITY_DENIED,
            }
        ),
    ),
    SpanInstrument.JOB_TRANSITION_WITH_AUDIT: SpanDefinition(
        SpanInstrument.JOB_TRANSITION_WITH_AUDIT,
        (AttributeDefinition(AttributeKey.TARGET_STATUS, _JOB_STATUSES),),
        (AttributeDefinition(AttributeKey.OUTCOME, _JOB_OUTCOMES),),
        frozenset(
            {
                ErrorCode.STALE_LEASE,
                ErrorCode.AUDIT_FAILURE,
                ErrorCode.DATABASE_FAILURE,
            }
        ),
    ),
}


class TelemetryValidationError(ValueError):
    """A fixed validation failure that never includes rejected telemetry data."""

    def __init__(self) -> None:
        super().__init__("telemetry record is not registered or valid")


def metric_definitions() -> tuple[MetricDefinition, ...]:
    return tuple(_METRIC_DEFINITIONS.values())


def span_definitions() -> tuple[SpanDefinition, ...]:
    return tuple(_SPAN_DEFINITIONS.values())


def validate_metric_point(point: object) -> MetricPoint:
    if type(point) is not MetricPoint or type(point.instrument) is not MetricInstrument:
        raise TelemetryValidationError
    definition = _METRIC_DEFINITIONS.get(point.instrument)
    if definition is None:
        raise TelemetryValidationError
    _validate_attributes(point.attributes, definition.attributes)
    if point.instrument in {
        MetricInstrument.SECRET_LOOKUP_DURATION,
        MetricInstrument.JOB_TRANSITION_DURATION,
    }:
        if (
            type(point.value) not in {int, float}
            or not math.isfinite(point.value)
            or point.value < 0
        ):
            raise TelemetryValidationError
    elif type(point.value) is not int or point.value < 1:
        raise TelemetryValidationError
    return point


def validate_span_start(span: object) -> SpanStart:
    if type(span) is not SpanStart or type(span.instrument) is not SpanInstrument:
        raise TelemetryValidationError
    definition = _SPAN_DEFINITIONS.get(span.instrument)
    if definition is None:
        raise TelemetryValidationError
    try:
        validate_telemetry_context(span.context)
    except ValueError:
        raise TelemetryValidationError from None
    _validate_attributes(span.attributes, definition.start_attributes)
    return span


def validate_span_end(span: object) -> SpanEnd:
    if type(span) is not SpanEnd or type(span.instrument) is not SpanInstrument:
        raise TelemetryValidationError
    definition = _SPAN_DEFINITIONS.get(span.instrument)
    if definition is None:
        raise TelemetryValidationError
    try:
        validate_telemetry_context(span.context)
    except ValueError:
        raise TelemetryValidationError from None
    _validate_attributes(span.attributes, definition.end_attributes)
    if span.status is SpanStatus.OK:
        if span.error_code is not None:
            raise TelemetryValidationError
    elif (
        span.status is not SpanStatus.ERROR
        or span.error_code is None
        or span.error_code not in definition.allowed_error_codes
    ):
        raise TelemetryValidationError
    return span


def series_key(point: MetricPoint) -> tuple[str, ...]:
    validate_metric_point(point)
    return tuple(f"{attribute.key.value}={attribute.value}" for attribute in point.attributes)


class SeriesCardinalityTracker:
    """Track process-local active metric series with a strict per-instrument bound."""

    def __init__(self, maximum_active_series: int = MAX_ACTIVE_SERIES) -> None:
        if type(maximum_active_series) is not int or not 1 <= maximum_active_series <= 64:
            raise ValueError("maximum_active_series must be from 1 to 64")
        self._maximum_active_series = maximum_active_series
        self._series: dict[MetricInstrument, set[tuple[str, ...]]] = {}
        self._lock = Lock()

    @property
    def maximum_active_series(self) -> int:
        return self._maximum_active_series

    def reserve(self, point: MetricPoint) -> bool:
        key = series_key(point)
        with self._lock:
            active = self._series.setdefault(point.instrument, set())
            if key in active:
                return True
            if len(active) >= self._maximum_active_series:
                return False
            active.add(key)
            return True

    def active_series(self, instrument: MetricInstrument) -> int:
        if type(instrument) is not MetricInstrument:
            raise ValueError("metric instrument is not registered")
        with self._lock:
            return len(self._series.get(instrument, set()))


def _validate_attributes(
    attributes: tuple[TelemetryAttribute, ...],
    definitions: tuple[AttributeDefinition, ...],
) -> None:
    if type(attributes) is not tuple or len(attributes) > MAX_ATTRIBUTES:
        raise TelemetryValidationError
    if len(attributes) != len(definitions):
        raise TelemetryValidationError
    seen: set[AttributeKey] = set()
    values: dict[AttributeKey, str] = {}
    for attribute in attributes:
        if type(attribute) is not TelemetryAttribute or type(attribute.key) is not AttributeKey:
            raise TelemetryValidationError
        if attribute.key.value.lower() in _PROHIBITED_ATTRIBUTE_KEYS:
            raise TelemetryValidationError
        if (
            type(attribute.value) is not str
            or not 1 <= len(attribute.value) <= MAX_ATTRIBUTE_VALUE_LENGTH
            or _ATTRIBUTE_VALUE_PATTERN.fullmatch(attribute.value) is None
            or attribute.key in seen
        ):
            raise TelemetryValidationError
        seen.add(attribute.key)
        values[attribute.key] = attribute.value
    if seen != {definition.key for definition in definitions}:
        raise TelemetryValidationError
    if any(values[definition.key] not in definition.allowed_values for definition in definitions):
        raise TelemetryValidationError


def _validate_registry() -> None:
    if set(_METRIC_DEFINITIONS) != set(MetricInstrument):
        raise RuntimeError("metric registry is incomplete")
    if set(_SPAN_DEFINITIONS) != set(SpanInstrument):
        raise RuntimeError("span registry is incomplete")
    for metric_definition in _METRIC_DEFINITIONS.values():
        name = metric_definition.instrument.value
        if (
            len(name) > MAX_INSTRUMENT_NAME_LENGTH
            or _INSTRUMENT_NAME_PATTERN.fullmatch(name) is None
        ):
            raise RuntimeError("telemetry registry contains an invalid instrument name")
        if len(metric_definition.attributes) > MAX_ATTRIBUTES:
            raise RuntimeError("telemetry registry contains too many attributes")
        for attribute in metric_definition.attributes:
            _validate_attribute_definition(attribute)
    for span_definition in _SPAN_DEFINITIONS.values():
        name = span_definition.instrument.value
        if (
            len(name) > MAX_INSTRUMENT_NAME_LENGTH
            or _INSTRUMENT_NAME_PATTERN.fullmatch(name) is None
        ):
            raise RuntimeError("telemetry registry contains an invalid instrument name")
        attribute_definitions = (
            *span_definition.start_attributes,
            *span_definition.end_attributes,
        )
        if len(attribute_definitions) > MAX_ATTRIBUTES:
            raise RuntimeError("telemetry registry contains too many attributes")
        for attribute in attribute_definitions:
            _validate_attribute_definition(attribute)


def _validate_attribute_definition(attribute: AttributeDefinition) -> None:
    if attribute.key.value.lower() in _PROHIBITED_ATTRIBUTE_KEYS:
        raise RuntimeError("telemetry registry contains a prohibited attribute")
    if not attribute.allowed_values or any(
        not 1 <= len(value) <= MAX_ATTRIBUTE_VALUE_LENGTH
        or _ATTRIBUTE_VALUE_PATTERN.fullmatch(value) is None
        for value in attribute.allowed_values
    ):
        raise RuntimeError("telemetry registry contains an invalid attribute value")


_validate_registry()
