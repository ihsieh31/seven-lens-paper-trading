"""Append-only P4-A source record log contract (durable adapters land with P4-B)."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from seven_lens.sources.adapters.records import NormalizedSourceRecord


class RecordLineageError(ValueError):
    """Raised when an append would silently rewrite an existing record lineage."""


class AppendOutcome(StrEnum):
    APPENDED = "APPENDED"
    IDEMPOTENT_DUPLICATE = "IDEMPOTENT_DUPLICATE"


class P4SourceRecordLog(Protocol):
    """Append-only store for validated normalized records; no update or delete.

    ``get`` deliberately returns only the current head.  Authority consumers
    that need to replay an older point in time must use ``get_version`` so a
    current supersession cannot silently replace the historical source row;
    ``versions`` supports determining which immutable head was knowable at a
    historical cutoff.
    """

    def append(self, record: NormalizedSourceRecord) -> AppendOutcome:
        """Append one validated record, enforcing idempotent same-hash lineage."""
        ...

    def get(self, record_id: str) -> NormalizedSourceRecord | None:
        """Return the current record for one identifier, or None."""
        ...

    def get_version(self, record_id: str, record_hash: str) -> NormalizedSourceRecord | None:
        """Return one exact immutable version, or None when it is unknown."""
        ...

    def versions(self, record_id: str) -> tuple[NormalizedSourceRecord, ...]:
        """Return every immutable version for one provider identity."""
        ...

    def lock_record(self, record_id: str) -> None:
        """Hold the record-identity lock until the surrounding transaction ends."""
        ...

    def records(self) -> tuple[NormalizedSourceRecord, ...]:
        """Return the stored records in append order."""
        ...

    def count(self) -> int:
        """Return the number of distinct stored records."""
        ...
