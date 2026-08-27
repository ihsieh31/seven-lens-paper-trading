"""Capability-minimal persistence port for P3-F reflection and memory metadata."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.memory.contracts import DailyReflectionRecord, MemoryArtifact
from seven_lens.memory.curation import CurationAuditRecord
from seven_lens.memory.validation import ValidationResult


class MemoryRepository(Protocol):
    """The only PostgreSQL capabilities exposed to reflection and curation code."""

    def transaction(self) -> AbstractContextManager[None]: ...

    def append_reflection(self, record: DailyReflectionRecord) -> None: ...

    def load_reflections(self, cutoff: UtcTimestamp) -> tuple[DailyReflectionRecord, ...]: ...

    def register_candidate(
        self,
        artifact: MemoryArtifact,
        cas_hash: str,
        byte_count: int,
    ) -> None: ...

    def mark_validated(
        self,
        result: ValidationResult,
        validation_report_hash: str,
        validator_version: str,
    ) -> None: ...

    def mark_invalid(self, result: ValidationResult) -> bool: ...

    def promote(self, artifact_id: str, requested_as_of: UtcTimestamp) -> bool: ...

    def current_at(self, as_of: UtcTimestamp) -> MemoryArtifact | None: ...

    def current_pointer(self) -> MemoryArtifact | None: ...

    def append_curation_audit(self, record: CurationAuditRecord) -> bool: ...
