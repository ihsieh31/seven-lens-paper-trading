"""Resource-budget tests for persisted canonical JSON values."""

from __future__ import annotations

import json

import pytest

from seven_lens.domain.json_values import (
    MAX_KEY_BYTES,
    MAX_LIST_ITEMS,
    MAX_OBJECT_MEMBERS,
    MAX_SERIALIZED_BYTES,
    MAX_STRING_BYTES,
    MAX_TOTAL_NODES,
    JsonObject,
)


@pytest.mark.parametrize("size", [MAX_KEY_BYTES - 1, MAX_KEY_BYTES])
def test_json_object_accepts_key_utf8_byte_boundaries(size: int) -> None:
    key = "x" * size

    assert JsonObject.from_value({key: True}).to_dict() == {key: True}


def test_json_object_rejects_key_over_utf8_byte_budget() -> None:
    with pytest.raises(ValueError, match="object key exceeds"):
        JsonObject.from_value({"x" * (MAX_KEY_BYTES + 1): True})


@pytest.mark.parametrize("size", [MAX_STRING_BYTES - 1, MAX_STRING_BYTES])
def test_json_object_accepts_string_utf8_byte_boundaries(size: int) -> None:
    value = "x" * size

    assert JsonObject.from_value({"value": value}).to_dict() == {"value": value}


def test_json_object_rejects_string_over_utf8_byte_budget_without_echo() -> None:
    marker = "never-echo-this-marker"
    value = marker + ("x" * MAX_STRING_BYTES)

    with pytest.raises(ValueError) as failure:
        JsonObject.from_value({"value": value})

    assert marker not in str(failure.value)


@pytest.mark.parametrize("count", [MAX_OBJECT_MEMBERS - 1, MAX_OBJECT_MEMBERS])
def test_json_object_accepts_object_member_boundaries(count: int) -> None:
    payload = {f"k{index:03d}": index for index in range(count)}

    assert len(JsonObject.from_value(payload).to_dict()) == count


def test_json_object_rejects_object_member_over_budget() -> None:
    payload = {f"k{index:03d}": index for index in range(MAX_OBJECT_MEMBERS + 1)}

    with pytest.raises(ValueError, match="object exceeds"):
        JsonObject.from_value(payload)


@pytest.mark.parametrize("count", [MAX_LIST_ITEMS - 1, MAX_LIST_ITEMS])
def test_json_object_accepts_list_item_boundaries(count: int) -> None:
    assert len(JsonObject.from_value({"items": [None] * count}).to_dict()["items"]) == count  # type: ignore[arg-type]


def test_json_object_rejects_list_item_over_budget() -> None:
    with pytest.raises(ValueError, match="list exceeds"):
        JsonObject.from_value({"items": [None] * (MAX_LIST_ITEMS + 1)})


def _node_boundary_payload(total_nodes: int) -> dict[str, object]:
    # root object + outer list + 512 inner lists + their scalar children
    scalar_nodes = total_nodes - 2 - MAX_LIST_ITEMS
    quotient, remainder = divmod(scalar_nodes, MAX_LIST_ITEMS)
    inner = [[None] * (quotient + (1 if index < remainder else 0)) for index in range(512)]
    return {"items": inner}


@pytest.mark.parametrize("nodes", [MAX_TOTAL_NODES - 1, MAX_TOTAL_NODES])
def test_json_object_accepts_total_node_boundaries(nodes: int) -> None:
    assert JsonObject.from_value(_node_boundary_payload(nodes))


def test_json_object_rejects_total_node_over_budget() -> None:
    with pytest.raises(ValueError, match="total JSON nodes"):
        JsonObject.from_value(_node_boundary_payload(MAX_TOTAL_NODES + 1))


def _serialized_boundary_payload(size: int) -> dict[str, str]:
    payload = {
        "a": "x" * MAX_STRING_BYTES,
        "b": "x" * MAX_STRING_BYTES,
        "c": "x" * MAX_STRING_BYTES,
        "d": "",
    }
    base_size = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    )
    payload["d"] = "x" * (size - base_size)
    return payload


@pytest.mark.parametrize("size", [MAX_SERIALIZED_BYTES - 1, MAX_SERIALIZED_BYTES])
def test_json_object_accepts_serialized_utf8_byte_boundaries(size: int) -> None:
    value = JsonObject.from_value(_serialized_boundary_payload(size))

    assert len(value.to_json().encode()) == size


def test_json_object_rejects_serialized_utf8_byte_over_budget() -> None:
    with pytest.raises(ValueError, match="persisted payload exceeds"):
        JsonObject.from_value(_serialized_boundary_payload(MAX_SERIALIZED_BYTES + 1))


def test_json_object_unicode_limits_count_utf8_bytes_not_code_points() -> None:
    valid = "界" * (MAX_STRING_BYTES // 3)
    invalid = valid + "界"

    assert JsonObject.from_value({"value": valid})
    with pytest.raises(ValueError, match="string exceeds"):
        JsonObject.from_value({"value": invalid})


def test_direct_constructor_rejects_oversized_canonical_before_json_parse() -> None:
    oversized = '{"value":"' + ("x" * MAX_SERIALIZED_BYTES) + '"}'

    with pytest.raises(ValueError, match="canonical JSON exceeds"):
        JsonObject(oversized)
