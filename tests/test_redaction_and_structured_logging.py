# mypy: ignore-errors
"""Secret-redaction tests at both utility and structured-log boundaries."""

from __future__ import annotations

import json
import logging
from io import StringIO
from typing import Any, cast

import pytest

from seven_lens.observability.structured_logging import JsonFormatter, log_event
from seven_lens.security.redaction import (
    REDACTED,
    UNSAFE_LOG_VALUE,
    DefaultSecretRedactor,
)

FAKE_ALPACA_KEY = "pk_test_FAKE000000000000"
FAKE_OPENAI_KEY = "sk-test-000000000000000000"
FAKE_BEARER = "Bearer fake-token-value"
FAKE_BASIC = "Basic ZmFrZS11c2VyOmZha2UtcGFzc3dvcmQ="


class SecretBearingObject:
    def __str__(self) -> str:
        return f"object:{FAKE_OPENAI_KEY}"

    def __repr__(self) -> str:
        return f"SecretBearingObject({FAKE_OPENAI_KEY})"


def test_secret_like_standalone_values_are_redacted() -> None:
    redactor = DefaultSecretRedactor()

    assert redactor.redact(FAKE_ALPACA_KEY) == REDACTED
    assert redactor.redact(FAKE_OPENAI_KEY) == REDACTED
    assert redactor.redact(FAKE_BEARER) == REDACTED
    assert redactor.redact(FAKE_BASIC) == REDACTED
    assert redactor.redact('password="correct horse battery staple"') == REDACTED
    assert redactor.redact("credential=alpha beta gamma") == REDACTED
    assert redactor.redact("ordinary research text") == "ordinary research text"


def test_nested_sensitive_keys_and_secret_like_values_are_redacted_without_mutating_input() -> None:
    payload = {
        "symbol": "AAPL",
        "api_key": FAKE_ALPACA_KEY,
        "nested": {
            "Authorization": FAKE_BEARER,
            "ordinary": "keep this value",
            "deeper": [{"client_secret": FAKE_OPENAI_KEY}, {"count": 2}],
        },
        "credential_hint": f"token={FAKE_OPENAI_KEY}",
    }

    redacted = cast(dict[str, Any], DefaultSecretRedactor().redact(payload))

    assert payload["api_key"] == FAKE_ALPACA_KEY
    assert redacted["symbol"] == "AAPL"
    assert redacted["api_key"] == REDACTED
    assert redacted["nested"]["Authorization"] == REDACTED
    assert redacted["nested"]["ordinary"] == "keep this value"
    assert redacted["nested"]["deeper"][0]["client_secret"] == REDACTED
    assert redacted["nested"]["deeper"][1]["count"] == 2
    assert redacted["credential_hint"] == f"{REDACTED}"
    assert FAKE_ALPACA_KEY not in repr(redacted)
    assert FAKE_OPENAI_KEY not in repr(redacted)
    assert FAKE_BEARER not in repr(redacted)


def test_secret_bearing_mapping_keys_are_redacted_without_key_collision() -> None:
    payload = {
        FAKE_OPENAI_KEY: "must not survive",
        "[REDACTED_KEY_0]": "ordinary value",
    }

    redacted = cast(dict[str, Any], DefaultSecretRedactor().redact(payload))

    assert len(redacted) == len(payload)
    assert FAKE_OPENAI_KEY not in redacted
    assert redacted["[REDACTED_KEY_0]"] == "ordinary value"
    assert redacted["[REDACTED_KEY_1]"] == REDACTED


def _logger_with_stream() -> tuple[logging.Logger, StringIO, logging.Handler]:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("seven_lens.tests.structured")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, stream, handler


def test_json_formatter_and_log_event_redact_nested_fields_before_serialization() -> None:
    logger, stream, handler = _logger_with_stream()
    try:
        log_event(
            logger,
            "paper_startup",
            run_id="run-0001",
            metadata={
                "api_key": FAKE_ALPACA_KEY,
                "nested": {"token": FAKE_OPENAI_KEY},
            },
            decision={"status": "NO_TRADE", "reason": "missing evidence"},
        )

        serialized = stream.getvalue()
        assert FAKE_ALPACA_KEY not in serialized
        assert FAKE_OPENAI_KEY not in serialized
        event = json.loads(serialized)
        assert event["event"] == "paper_startup"
        assert event["level"] == "INFO"
        assert event["fields"]["run_id"] == "run-0001"
        assert event["fields"]["metadata"]["api_key"] == REDACTED
        assert event["fields"]["metadata"]["nested"]["token"] == REDACTED
        assert event["fields"]["decision"]["status"] == "NO_TRADE"
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_non_json_safe_values_never_reach_string_or_repr_serialization() -> None:
    logger, stream, handler = _logger_with_stream()
    try:
        log_event(
            logger,
            "unsafe_types",
            raw_bytes=FAKE_OPENAI_KEY.encode(),
            unordered={FAKE_OPENAI_KEY, "ordinary"},
            custom=SecretBearingObject(),
        )

        serialized = stream.getvalue()
        assert FAKE_OPENAI_KEY not in serialized
        event = json.loads(serialized)
        assert event["event"] == "unsafe_types"
        assert event["fields"] == {
            "custom": UNSAFE_LOG_VALUE,
            "raw_bytes": UNSAFE_LOG_VALUE,
            "unordered": UNSAFE_LOG_VALUE,
        }
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_non_string_mapping_keys_produce_field_free_fallback_without_collision() -> None:
    logger, stream, handler = _logger_with_stream()
    try:
        log_event(
            logger,
            "ambiguous_mapping",
            payload={1: FAKE_OPENAI_KEY, "1": "ordinary"},
        )

        serialized = stream.getvalue()
        assert FAKE_OPENAI_KEY not in serialized
        event = json.loads(serialized)
        assert event == {
            "event": "structured_log_serialization_failed",
            "fields": {},
            "level": "ERROR",
            "reason": "unsafe_or_unserializable_fields",
            "timestamp": event["timestamp"],
        }
    finally:
        logger.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize("failure_kind", ["cycle", "too_deep"])
def test_recursive_or_overdeep_fields_emit_safe_fallback(failure_kind: str) -> None:
    logger, stream, handler = _logger_with_stream()
    try:
        if failure_kind == "cycle":
            unsafe: list[object] = []
            unsafe.append(unsafe)
        else:
            unsafe = []
            current = unsafe
            for _ in range(20):
                nested: list[object] = []
                current.append(nested)
                current = nested

        log_event(logger, "recursive_fields", payload=unsafe)

        event = json.loads(stream.getvalue())
        assert event["event"] == "structured_log_serialization_failed"
        assert event["reason"] == "unsafe_or_unserializable_fields"
        assert event["fields"] == {}
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_log_event_honors_explicit_level_and_rejects_empty_event_names() -> None:
    logger, stream, handler = _logger_with_stream()
    try:
        log_event(logger, "quota_exhausted", level=logging.WARNING, account_id="acct-01")
        event = json.loads(stream.getvalue())
        assert event["level"] == "WARNING"
        assert event["event"] == "quota_exhausted"

        with pytest.raises(ValueError):
            log_event(logger, "   ")
    finally:
        logger.removeHandler(handler)
        handler.close()
