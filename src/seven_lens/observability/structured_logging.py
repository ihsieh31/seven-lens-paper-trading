"""Minimal JSON logging with bounded redaction and a non-leaking fallback."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Final, cast

from seven_lens.observability.context import TelemetryContext, validate_telemetry_context
from seven_lens.security.redaction import DefaultSecretRedactor, JsonValue, SecretRedactor

_STRUCTURED_FIELDS_ATTRIBUTE: Final = "seven_lens_fields"
_FALLBACK_EVENT: Final = "structured_log_serialization_failed"
_FALLBACK_REASON: Final = "unsafe_or_unserializable_fields"


class JsonFormatter(logging.Formatter):
    """Serialize one event only after converting every value to safe JSON."""

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        super().__init__()
        self._redactor = redactor or DefaultSecretRedactor()

    def format(self, record: logging.LogRecord) -> str:
        try:
            if type(record.msg) is not str or record.args:
                raise ValueError("structured log messages must be prevalidated strings")
            event: object = {
                "timestamp": _safe_timestamp(record.created),
                "level": record.levelname,
                "event": record.msg,
                "fields": getattr(record, _STRUCTURED_FIELDS_ATTRIBUTE, {}),
            }
            return _serialize_json(self._redactor.redact(event))
        except Exception:
            return _serialize_json(_safe_fallback_event(record.created))


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    telemetry_context: TelemetryContext | None = None,
    **fields: object,
) -> None:
    """Emit one event; unsafe fields become a fixed fallback audit record."""
    if type(event) is not str or not event.strip():
        raise ValueError("structured log event must be a non-empty string")
    if telemetry_context is not None:
        validate_telemetry_context(telemetry_context)
        fields["telemetry"] = telemetry_context.to_log_fields()
    logger.log(level, event, extra={_STRUCTURED_FIELDS_ATTRIBUTE: fields})


def _serialize_json(value: JsonValue) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _safe_timestamp(created: object) -> str:
    if type(created) not in {int, float}:
        return datetime.now(UTC).isoformat()
    try:
        return datetime.fromtimestamp(cast(float, created), tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return datetime.now(UTC).isoformat()


def _safe_fallback_event(created: object) -> JsonValue:
    return {
        "timestamp": _safe_timestamp(created),
        "level": "ERROR",
        "event": _FALLBACK_EVENT,
        "reason": _FALLBACK_REASON,
        "fields": {},
    }
