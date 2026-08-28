"""Non-durable in-memory P4-A record log used by unit and service tests.

The log keeps every immutable version for exact source-lineage replay while
preserving the existing ``get``/``records`` current-head behavior.
"""

from __future__ import annotations

from seven_lens.application.ports.p4_source_records import (
    AppendOutcome,
    RecordLineageError,
)
from seven_lens.sources.adapters.records import NormalizedSourceRecord


class InMemoryP4RecordLog:
    """Process-local append-only log; not durable and not an authority substitute.

    PostgreSQL persistence, ACL, and concurrency evidence are deferred to the
    first gate that consumes durable record storage (P4-B security master).
    """

    def __init__(self) -> None:
        self._records: dict[str, NormalizedSourceRecord] = {}
        self._versions: dict[tuple[str, str], NormalizedSourceRecord] = {}
        self._order: list[str] = []

    def append(self, record: NormalizedSourceRecord) -> AppendOutcome:
        if type(record) is not NormalizedSourceRecord:
            raise TypeError("record log accepts only NormalizedSourceRecord values")
        record.verify_integrity()
        exact = self._versions.get((record.record_id, record.record_hash))
        if exact is not None:
            if exact.wire() != record.wire():
                raise RecordLineageError("source record hash collision carries different content")
            return AppendOutcome.IDEMPOTENT_DUPLICATE
        existing = self._records.get(record.record_id)
        if existing is None:
            self._records[record.record_id] = record
            self._order.append(record.record_id)
            self._versions[(record.record_id, record.record_hash)] = record
            return AppendOutcome.APPENDED
        existing_available = (
            existing.available_at if existing.available_at is not None else existing.retrieved_at
        )
        incoming_available = (
            record.available_at if record.available_at is not None else record.retrieved_at
        )
        if incoming_available.value < existing_available.value:
            raise RecordLineageError("source supersession availability cannot move backwards")
        if (
            incoming_available.value == existing_available.value
            and record.content_hash != existing.content_hash
        ):
            raise RecordLineageError("source supersession at equal availability is unorderable")
        if record.supersedes_content_hash != existing.content_hash:
            raise RecordLineageError(
                "same provider identity with different content requires explicit supersession"
            )
        self._records[record.record_id] = record
        self._versions[(record.record_id, record.record_hash)] = record
        return AppendOutcome.APPENDED

    def get(self, record_id: str) -> NormalizedSourceRecord | None:
        return self._records.get(record_id)

    def get_version(self, record_id: str, record_hash: str) -> NormalizedSourceRecord | None:
        return self._versions.get((record_id, record_hash))

    def versions(self, record_id: str) -> tuple[NormalizedSourceRecord, ...]:
        """Return all immutable versions in their append order."""
        if type(record_id) is not str:
            raise ValueError("record_id must be text")
        return tuple(
            record
            for (stored_record_id, _record_hash), record in self._versions.items()
            if stored_record_id == record_id
        )

    def lock_record(self, record_id: str) -> None:
        """Process-local source identity lock for the single in-memory authority."""
        if type(record_id) is not str:
            raise ValueError("record_id must be text")

    def records(self) -> tuple[NormalizedSourceRecord, ...]:
        return tuple(self._records[record_id] for record_id in self._order)

    def count(self) -> int:
        return len(self._records)
