# mypy: ignore-errors
"""P4-A append-only record log: idempotency, explicit supersession, no updates."""

from __future__ import annotations

import pytest

from seven_lens.application.ports.p4_source_records import AppendOutcome, RecordLineageError
from seven_lens.domain.value_objects import SchemaVersion, UtcTimestamp
from seven_lens.sources.adapters.alpaca import parse_assets
from seven_lens.sources.adapters.in_memory_p4_records import InMemoryP4RecordLog
from seven_lens.sources.adapters.records import (
    _build_normalized_record as build_normalized_record,
)
from seven_lens.sources.adapters.records import (
    content_hash_of,
)
from seven_lens.sources.roles import P4SourceFamily

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_ASSETS_JSON = b"""[
  {"id": "90927a3c-0b6a-4d5a-bd31-4d45a26b7cc8", "symbol": "AAPL", "exchange": "NASDAQ",
   "asset_class": "us_equity", "status": "active", "tradable": true}
]"""


def _record(**overrides: object):
    values = {
        "record_id": "alpaca-asset-90927a3c",
        "family": P4SourceFamily.ALPACA_ASSETS,
        "endpoint_id": "asset_detail",
        "schema_version": SchemaVersion("1.0.0"),
        "content_hash": content_hash_of(_ASSETS_JSON),
        "retrieved_at": _RETRIEVED,
        "payload": {"symbol": "AAPL"},
        "material_claim": False,
    }
    values.update(overrides)
    return build_normalized_record(**values)


def test_append_new_record_succeeds_once_then_is_idempotent() -> None:
    log = InMemoryP4RecordLog()

    assert log.append(_record()) is AppendOutcome.APPENDED
    assert log.append(_record()) is AppendOutcome.IDEMPOTENT_DUPLICATE
    assert log.count() == 1
    assert log.get("alpaca-asset-90927a3c") is not None


def test_same_record_id_different_content_requires_explicit_supersession() -> None:
    log = InMemoryP4RecordLog()
    log.append(_record())

    with pytest.raises(ValueError):
        log.append(_record(content_hash="b" * 64))


def test_explicit_supersession_moves_the_lineage_forward() -> None:
    log = InMemoryP4RecordLog()
    log.append(_record())

    superseding = build_normalized_record(
        record_id="alpaca-asset-90927a3c",
        family=P4SourceFamily.ALPACA_ASSETS,
        endpoint_id="asset_detail",
        schema_version=SchemaVersion("1.0.0"),
        content_hash="c" * 64,
        retrieved_at=UtcTimestamp.from_isoformat("2026-08-27T15:31:00.000000Z"),
        payload={"symbol": "AAPL", "status": "inactive"},
        material_claim=False,
        supersedes_content_hash=content_hash_of(_ASSETS_JSON),
    )
    assert log.append(superseding) is AppendOutcome.APPENDED
    assert log.count() == 1
    assert log.get("alpaca-asset-90927a3c").content_hash == "c" * 64


def test_supersession_cannot_backdate_source_availability() -> None:
    log = InMemoryP4RecordLog()
    log.append(_record())
    backdated = build_normalized_record(
        record_id="alpaca-asset-90927a3c",
        family=P4SourceFamily.ALPACA_ASSETS,
        endpoint_id="asset_detail",
        schema_version=SchemaVersion("1.0.0"),
        content_hash="e" * 64,
        retrieved_at=UtcTimestamp.from_isoformat("2026-08-27T15:29:00.000000Z"),
        payload={"symbol": "AAPL", "status": "inactive"},
        material_claim=False,
        supersedes_content_hash=content_hash_of(_ASSETS_JSON),
    )

    with pytest.raises(RecordLineageError):
        log.append(backdated)
    assert log.get("alpaca-asset-90927a3c").content_hash == content_hash_of(_ASSETS_JSON)


def test_supersession_requires_the_current_content_hash() -> None:
    log = InMemoryP4RecordLog()
    log.append(_record())

    forged = build_normalized_record(
        record_id="alpaca-asset-90927a3c",
        family=P4SourceFamily.ALPACA_ASSETS,
        endpoint_id="asset_detail",
        schema_version=SchemaVersion("1.0.0"),
        content_hash="d" * 64,
        retrieved_at=_RETRIEVED,
        payload={"symbol": "AAPL", "status": "halted"},
        material_claim=False,
        supersedes_content_hash="e" * 64,
    )
    with pytest.raises(ValueError):
        log.append(forged)
    assert log.count() == 1


def test_append_rejects_non_record_values() -> None:
    log = InMemoryP4RecordLog()

    with pytest.raises(TypeError):
        log.append("not-a-record")  # type: ignore[arg-type]
    assert log.count() == 0


def test_records_are_exposed_in_append_order_and_immutable() -> None:
    log = InMemoryP4RecordLog()
    first, second = parse_assets(
        b"""[
          {"id": "90927a3c-0b6a-4d5a-bd31-4d45a26b7cc8", "symbol": "AAPL",
           "exchange": "NASDAQ", "asset_class": "us_equity", "status": "active",
           "tradable": true},
          {"id": "b0b6dd9d-8b9b-48a9-ba46-b700d5a42a43", "symbol": "TSLA",
           "exchange": "NASDAQ", "asset_class": "us_equity", "status": "active",
           "tradable": true}
        ]""",
        retrieved_at=_RETRIEVED,
    )
    log.append(first)
    log.append(second)

    stored = log.records()
    assert [item.record_id for item in stored] == [first.record_id, second.record_id]
    with pytest.raises((AttributeError, TypeError)):
        stored[0] = second  # type: ignore[index-assignment]
