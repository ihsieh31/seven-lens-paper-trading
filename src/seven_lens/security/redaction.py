"""Bounded secret redaction that produces JSON-safe values only."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Final, Protocol, cast

REDACTED: Final = "[REDACTED]"
UNSAFE_LOG_VALUE: Final = "[UNSAFE_LOG_VALUE]"
DEFAULT_MAX_REDACTION_DEPTH: Final = 16

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_SENSITIVE_KEY_PATTERN: Final = re.compile(
    r"(?:api[_-]?key|private[_-]?key|authorization|credential|password|secret|token)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?i)\b(api[_-]?key|private[_-]?key|authorization|credential|password|secret|token)"
        r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,;\r\n]+)"
    ),
    re.compile(r"(?i)\b(?:Basic|Bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:pk|sk)[_-](?:test|live)[_-][A-Za-z0-9_-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_REDACTED_KEY_PREFIX: Final = "[REDACTED_KEY_"


class UnsafeLogValueError(ValueError):
    """Raised when a structure cannot be traversed without ambiguity or recursion."""


class SecretRedactor(Protocol):
    """Port for converting untrusted values into redacted JSON-safe values."""

    def redact(self, value: object) -> JsonValue:
        """Return a bounded, recursively sanitized JSON value."""
        ...


class DefaultSecretRedactor:
    """Conservative sanitizer for untrusted structured-log data."""

    def __init__(self, *, max_depth: int = DEFAULT_MAX_REDACTION_DEPTH) -> None:
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
            raise ValueError("max_depth must be a positive integer")
        self._max_depth = max_depth

    def redact(self, value: object) -> JsonValue:
        return self._redact(value, depth=0, active_container_ids=set())

    def _redact(
        self,
        value: object,
        *,
        depth: int,
        active_container_ids: set[int],
    ) -> JsonValue:
        if value is None or type(value) in {bool, int}:
            return cast(JsonScalar, value)
        if type(value) is float:
            return value if math.isfinite(value) else UNSAFE_LOG_VALUE
        if type(value) is str:
            return _redact_string(value)
        if isinstance(value, Mapping):
            return self._redact_mapping(
                value,
                depth=depth,
                active_container_ids=active_container_ids,
            )
        if type(value) in {list, tuple}:
            return self._redact_sequence(
                cast(list[object] | tuple[object, ...], value),
                depth=depth,
                active_container_ids=active_container_ids,
            )
        return UNSAFE_LOG_VALUE

    def _redact_mapping(
        self,
        value: Mapping[object, object],
        *,
        depth: int,
        active_container_ids: set[int],
    ) -> dict[str, JsonValue]:
        self._enter_container(value, depth=depth, active_container_ids=active_container_ids)
        try:
            items: list[tuple[str, object]] = []
            for raw_key, nested_value in value.items():
                if type(raw_key) is not str:
                    raise UnsafeLogValueError("structured log mappings require exact string keys")
                items.append((raw_key, nested_value))

            original_keys = {key for key, _ in items}
            result: dict[str, JsonValue] = {}
            redacted_key_index = 0
            for raw_key, nested_value in items:
                key = raw_key  # narrowed by the exact-type check above
                if _contains_secret_material(key):
                    key, redacted_key_index = _next_redacted_key(
                        redacted_key_index,
                        occupied_keys=original_keys | result.keys(),
                    )
                    result[key] = REDACTED
                    continue
                if key in result:
                    raise UnsafeLogValueError("structured log mapping contains duplicate keys")
                if _is_sensitive_field_name(key):
                    result[key] = REDACTED
                else:
                    result[key] = self._redact(
                        nested_value,
                        depth=depth + 1,
                        active_container_ids=active_container_ids,
                    )
            return result
        finally:
            active_container_ids.remove(id(value))

    def _redact_sequence(
        self,
        value: list[object] | tuple[object, ...],
        *,
        depth: int,
        active_container_ids: set[int],
    ) -> list[JsonValue]:
        self._enter_container(value, depth=depth, active_container_ids=active_container_ids)
        try:
            return [
                self._redact(
                    item,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                )
                for item in value
            ]
        finally:
            active_container_ids.remove(id(value))

    def _enter_container(
        self,
        value: object,
        *,
        depth: int,
        active_container_ids: set[int],
    ) -> None:
        if depth >= self._max_depth:
            raise UnsafeLogValueError("structured log value exceeds maximum depth")
        value_id = id(value)
        if value_id in active_container_ids:
            raise UnsafeLogValueError("structured log value contains a cycle")
        active_container_ids.add(value_id)


def _is_sensitive_field_name(key: str) -> bool:
    return _SENSITIVE_KEY_PATTERN.search(key) is not None


def _contains_secret_material(value: str) -> bool:
    return _redact_string(value) != value


def _next_redacted_key(
    index: int,
    *,
    occupied_keys: set[str],
) -> tuple[str, int]:
    candidate_index = index
    while True:
        candidate = f"{_REDACTED_KEY_PREFIX}{candidate_index}]"
        candidate_index += 1
        if candidate not in occupied_keys:
            return candidate, candidate_index


def _redact_string(value: str) -> str:
    redacted = value
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted
