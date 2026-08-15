"""Immutable, explicitly validated JSON objects for persisted domain data."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import NoReturn, cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

MAX_JSON_DEPTH = 32
MAX_SERIALIZED_BYTES = 65_536
MAX_TOTAL_NODES = 4_096
MAX_OBJECT_MEMBERS = 256
MAX_LIST_ITEMS = 512
MAX_KEY_BYTES = 128
MAX_STRING_BYTES = 16_384


@dataclass(slots=True)
class _JsonBudget:
    nodes: int = 0

    def consume_node(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_TOTAL_NODES:
            raise ValueError("persisted payload exceeds maximum total JSON nodes")


@dataclass(frozen=True, slots=True)
class JsonObject:
    """An immutable canonical JSON object with no implicit string coercion."""

    _canonical: str

    def __post_init__(self) -> None:
        if type(self._canonical) is not str:
            raise ValueError("canonical JSON must be text")
        _encoded_size(self._canonical, field="canonical JSON", maximum=MAX_SERIALIZED_BYTES)
        try:
            decoded = json.loads(self._canonical, parse_constant=_reject_json_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("canonical JSON must be valid and finite") from error
        normalized = _normalize_json(
            decoded,
            depth=0,
            active_container_ids=set(),
            budget=_JsonBudget(),
        )
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
        normalized = _normalize_json(
            value,
            depth=0,
            active_container_ids=set(),
            budget=_JsonBudget(),
        )
        if not isinstance(normalized, dict):
            raise ValueError("persisted payload must be a JSON object")
        canonical = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        _encoded_size(canonical, field="persisted payload", maximum=MAX_SERIALIZED_BYTES)
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
    budget: _JsonBudget,
) -> JsonValue:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("persisted payload exceeds maximum JSON depth")
    budget.consume_node()
    if value is None or type(value) in {bool, int, str}:
        if type(value) is str:
            if "\x00" in value:
                raise ValueError("persisted payload strings must not contain NUL")
            _encoded_size(value, field="persisted payload string", maximum=MAX_STRING_BYTES)
        return cast(JsonScalar, value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("persisted payload numbers must be finite")
        return value
    if type(value) is dict:
        if len(value) > MAX_OBJECT_MEMBERS:
            raise ValueError("persisted payload object exceeds maximum members")
        container_id = _enter_container(value, active_container_ids)
        try:
            result: dict[str, JsonValue] = {}
            for raw_key, nested_value in cast(dict[object, object], value).items():
                if type(raw_key) is not str:
                    raise ValueError("persisted payload object keys must be exact strings")
                key = raw_key
                if "\x00" in key:
                    raise ValueError("persisted payload object keys must not contain NUL")
                _encoded_size(key, field="persisted payload object key", maximum=MAX_KEY_BYTES)
                result[key] = _normalize_json(
                    nested_value,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    budget=budget,
                )
            return result
        finally:
            active_container_ids.remove(container_id)
    if type(value) is list:
        if len(value) > MAX_LIST_ITEMS:
            raise ValueError("persisted payload list exceeds maximum items")
        container_id = _enter_container(value, active_container_ids)
        try:
            return [
                _normalize_json(
                    item,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    budget=budget,
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


def _encoded_size(value: str, *, field: str, maximum: int) -> int:
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as error:
        raise ValueError("persisted payload strings must contain valid Unicode") from error
    if size > maximum:
        raise ValueError(f"{field} exceeds maximum UTF-8 bytes")
    return size
