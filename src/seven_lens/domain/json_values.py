"""Immutable, explicitly validated JSON objects for persisted domain data."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import NoReturn, cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_MAX_JSON_DEPTH = 32


@dataclass(frozen=True, slots=True)
class JsonObject:
    """An immutable canonical JSON object with no implicit string coercion."""

    _canonical: str

    def __post_init__(self) -> None:
        if type(self._canonical) is not str:
            raise ValueError("canonical JSON must be text")
        try:
            decoded = json.loads(self._canonical, parse_constant=_reject_json_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("canonical JSON must be valid and finite") from error
        normalized = _normalize_json(decoded, depth=0, active_container_ids=set())
        if not isinstance(normalized, dict):
            raise ValueError("persisted payload must be a JSON object")
        expected = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if expected != self._canonical:
            raise ValueError("JsonObject must use its canonical representation")

    @classmethod
    def from_value(cls, value: object) -> JsonObject:
        """Validate and snapshot an object using strict JSON-safe semantics."""
        normalized = _normalize_json(value, depth=0, active_container_ids=set())
        if not isinstance(normalized, dict):
            raise ValueError("persisted payload must be a JSON object")
        canonical = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            canonical.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise ValueError("persisted payload strings must contain valid Unicode") from error
        return cls(canonical)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a fresh representation suitable for a JSON database adapter."""
        value = json.loads(self._canonical)
        if not isinstance(value, dict):  # pragma: no cover - protected by the constructor
            raise RuntimeError("canonical JsonObject did not decode to an object")
        return cast(dict[str, JsonValue], value)

    def to_json(self) -> str:
        """Return the canonical JSON representation."""
        return self._canonical


def validate_json_object(value: object) -> JsonObject:
    """Validate an untrusted payload at an application boundary."""
    return JsonObject.from_value(value)


def _normalize_json(
    value: object,
    *,
    depth: int,
    active_container_ids: set[int],
) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("persisted payload exceeds maximum JSON depth")
    if value is None or type(value) in {bool, int, str}:
        if type(value) is str and "\x00" in value:
            raise ValueError("persisted payload strings must not contain NUL")
        return cast(JsonScalar, value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("persisted payload numbers must be finite")
        return value
    if type(value) is dict:
        container_id = _enter_container(value, active_container_ids)
        try:
            result: dict[str, JsonValue] = {}
            for raw_key, nested_value in cast(dict[object, object], value).items():
                if type(raw_key) is not str:
                    raise ValueError("persisted payload object keys must be exact strings")
                key = raw_key
                if "\x00" in key:
                    raise ValueError("persisted payload object keys must not contain NUL")
                result[key] = _normalize_json(
                    nested_value,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                )
            return result
        finally:
            active_container_ids.remove(container_id)
    if type(value) is list:
        container_id = _enter_container(value, active_container_ids)
        try:
            return [
                _normalize_json(
                    item,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                )
                for item in cast(list[object], value)
            ]
        finally:
            active_container_ids.remove(container_id)
    raise ValueError("persisted payload contains a non-JSON-safe value")


def _enter_container(value: object, active_container_ids: set[int]) -> int:
    container_id = id(value)
    if container_id in active_container_ids:
        raise ValueError("persisted payload contains a cycle")
    active_container_ids.add(container_id)
    return container_id


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")
