"""Domain event and audit envelope validation tests (no database required)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from seven_lens.domain.events import AuditEvent, DomainEvent
from seven_lens.domain.json_values import JsonObject
from seven_lens.domain.value_objects import RunId, SchemaVersion, UtcTimestamp

RUN_ID = RunId.from_string("123e4567-e89b-12d3-a456-426614174000")
SCHEMA_VERSION = SchemaVersion("1.0.0")
CORRELATION_ID = UUID("123e4567-e89b-12d3-a456-426614174001")
CAUSATION_ID = UUID("123e4567-e89b-12d3-a456-426614174002")
OCCURRED_AT = UtcTimestamp(datetime(2026, 8, 14, 12, 0, tzinfo=UTC))


def make_domain_event(**overrides: object) -> DomainEvent:
    values: dict[str, object] = {
        "event_type": "job.created",
        "schema_version": SCHEMA_VERSION,
        "aggregate_type": "job",
        "aggregate_id": "job-2026-08-14-open",
        "aggregate_sequence": 1,
        "run_id": RUN_ID,
        "correlation_id": CORRELATION_ID,
        "causation_id": CAUSATION_ID,
        "occurred_at": OCCURRED_AT,
        "payload": {"status": "PLANNED", "attempt": 0},
        "producer_version": "seven-lens-test/1.0",
    }
    values.update(overrides)
    return DomainEvent.create(**values)  # type: ignore[arg-type]


def make_audit_event(**overrides: object) -> AuditEvent:
    values: dict[str, object] = {
        "event_type": "job.created",
        "run_id": RUN_ID,
        "correlation_id": CORRELATION_ID,
        "causation_id": CAUSATION_ID,
        "occurred_at": OCCURRED_AT,
        "payload": {"status": "PLANNED", "attempt": 0},
        "producer_version": "seven-lens-test/1.0",
    }
    values.update(overrides)
    return AuditEvent.create(**values)  # type: ignore[arg-type]


def test_domain_event_create_validates_and_snapshots_normal_payload() -> None:
    source_payload = {"status": "PLANNED", "nested": {"attempt": 0}}

    event = make_domain_event(payload=source_payload)

    assert event.event_type == "job.created"
    assert event.aggregate_sequence == 1
    assert event.payload.to_dict() == source_payload
    source_payload["status"] = "MUTATED"
    assert event.payload.to_dict()["status"] == "PLANNED"
    assert event.payload.to_json() == '{"nested":{"attempt":0},"status":"PLANNED"}'


def test_domain_event_create_generates_non_nil_id_when_omitted() -> None:
    event = make_domain_event()

    assert event.event_id.int != 0


def test_json_object_direct_constructor_accepts_only_canonical_objects() -> None:
    value = JsonObject('{"a":1,"b":2}')

    assert value.to_dict() == {"a": 1, "b": 2}
    assert value.to_json() == '{"a":1,"b":2}'


@pytest.mark.parametrize(
    "canonical",
    [
        "{not-json}",
        '{"b":2,"a":1}',
        '{"a": 1}',
        '{"a":NaN}',
        "[1, 2, 3]",
        '"scalar"',
        "null",
    ],
)
def test_json_object_direct_constructor_rejects_invalid_or_noncanonical_values(
    canonical: str,
) -> None:
    with pytest.raises(ValueError):
        JsonObject(canonical)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_type", ""),
        ("event_type", "   "),
        ("event_type", "x" * 201),
        ("aggregate_type", ""),
        ("aggregate_id", ""),
        ("aggregate_id", "x" * 501),
        ("producer_version", ""),
        ("aggregate_sequence", 0),
        ("aggregate_sequence", -1),
        ("aggregate_sequence", True),
        ("schema_version", "1.0.0"),
        ("run_id", "123e4567-e89b-12d3-a456-426614174000"),
        ("occurred_at", datetime(2026, 8, 14, 12, 0)),
        ("event_id", UUID(int=0)),
        ("correlation_id", UUID(int=0)),
    ],
)
def test_domain_event_rejects_invalid_boundary_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        make_domain_event(**{field: value})


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "not-an-object",
        b"bytes",
        {1: "non-string-key"},
        {"set": {"not", "json"}},
        {"object": object()},
        {"nan": float("nan")},
        {"infinity": float("inf")},
        {"nul": "bad\x00text"},
    ],
)
def test_domain_event_rejects_malformed_or_non_json_safe_payload(payload: object) -> None:
    with pytest.raises(ValueError):
        make_domain_event(payload=payload)


def test_domain_event_rejects_cycles_and_excessive_nesting() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)

    deep: object = {"leaf": True}
    for _ in range(40):
        deep = {"nested": deep}

    with pytest.raises(ValueError, match="cycle"):
        make_domain_event(payload={"cycle": cyclic})
    with pytest.raises(ValueError, match="depth"):
        make_domain_event(payload=deep)


def test_audit_event_accepts_safe_payload_and_snapshots_it() -> None:
    event = make_audit_event(payload={"action": "acquire", "ok": True})

    assert event.audit_id.int != 0
    assert event.payload.to_dict() == {"action": "acquire", "ok": True}


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "sk-test-fake"},
        {"nested": {"Authorization": "Bearer fake-token"}},
        {"message": "token=sk-test-fake"},
        {"message": "Basic ZmFrZS11c2VyOmZha2UtcGFzcw=="},
        {"private_key": "-----BEGIN PRIVATE KEY-----"},
    ],
)
def test_audit_event_rejects_secret_bearing_payload(payload: object) -> None:
    with pytest.raises(ValueError, match="secret"):
        make_audit_event(payload=payload)


def test_audit_event_accepts_none_run_and_causation_ids() -> None:
    event = make_audit_event(run_id=None, causation_id=None)

    assert event.run_id is None
    assert event.causation_id is None


def test_domain_and_persistence_ports_remain_database_library_neutral() -> None:
    source_root = Path(__file__).parents[1] / "src" / "seven_lens"
    source_files = sorted(
        [
            *source_root.joinpath("domain").rglob("*.py"),
            *source_root.joinpath("application", "ports").rglob("*.py"),
        ]
    )
    assert source_files
    combined_source = "\n".join(path.read_text(encoding="utf-8") for path in source_files).lower()

    for forbidden in ("psycopg", "sqlalchemy", "alembic", "sqlite"):
        assert forbidden not in combined_source
