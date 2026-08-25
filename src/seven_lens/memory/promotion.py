"""Exact-byte CAS staging and atomic current-pointer semantics for P3-F memory."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, cast

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.memory.contracts import ArtifactState, DailyReflectionRecord, MemoryArtifact
from seven_lens.memory.validation import MemoryValidator, ValidationResult


class StoredObject(Protocol):
    content_hash: str
    size: int


class ContentStore(Protocol):
    def put(self, content: bytes, *, declared_hash: str | None = None) -> object: ...

    def get(self, content_hash: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class MemoryAlert:
    code: str
    requested_as_of: UtcTimestamp


@dataclass(frozen=True, slots=True)
class MemorySelection:
    artifact: MemoryArtifact | None
    alert: MemoryAlert | None


@dataclass(frozen=True, slots=True)
class PromotionEvent:
    artifact_id: str
    promoted_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class ValidationContext:
    source_records: dict[str, DailyReflectionRecord]
    requested_cutoff: UtcTimestamp


class InMemoryPromotionRepository:
    """Metadata state machine with an atomic single-current pointer and full history."""

    def __init__(self, *, now: Callable[[], UtcTimestamp] = UtcTimestamp.now) -> None:
        self._artifacts: dict[str, MemoryArtifact] = {}
        self._current_id: str | None = None
        self._promotion_history: list[PromotionEvent] = []
        self._validation_contexts: dict[str, ValidationContext] = {}
        self._lock = threading.Lock()
        self._now = now

    def register_candidate(self, artifact: MemoryArtifact) -> None:
        if type(artifact) is not MemoryArtifact or artifact.state is not ArtifactState.CANDIDATE:
            raise ValueError("only an exact candidate can be registered")
        artifact.verify_integrity()
        with self._lock:
            existing = self._artifacts.get(artifact.artifact_id)
            if existing is not None:
                if existing.content_hash == artifact.content_hash:
                    return
                raise RuntimeError("memory artifact identity collision")
            if self._current_id is None:
                if artifact.previous_artifact_id is not None:
                    raise RuntimeError("initial artifact has foreign predecessor lineage")
            elif artifact.previous_artifact_id != self._current_id:
                raise RuntimeError("candidate predecessor is not the exact current artifact")
            if artifact.previous_artifact_id is not None:
                previous = self._artifacts[artifact.previous_artifact_id]
                if (
                    artifact.cutoff_at.value < previous.cutoff_at.value
                    or artifact.created_at.value < previous.created_at.value
                ):
                    raise RuntimeError("candidate predecessor chronology is invalid")
            self._artifacts[artifact.artifact_id] = artifact

    def save_validation(
        self,
        result: ValidationResult,
        *,
        source_records: dict[str, DailyReflectionRecord] | None = None,
        requested_cutoff: UtcTimestamp | None = None,
    ) -> None:
        artifact = result.artifact
        if artifact.state not in {ArtifactState.VALIDATED, ArtifactState.INVALID}:
            raise ValueError("validation result state is invalid")
        with self._lock:
            existing = self._artifacts.get(artifact.artifact_id)
            if existing is None or existing.content_hash != artifact.content_hash:
                raise RuntimeError("candidate is unavailable for validation")
            if existing.state is ArtifactState.CURRENT:
                return
            if (
                existing.state is ArtifactState.INVALID
                and artifact.state is not ArtifactState.INVALID
            ):
                raise RuntimeError("invalid artifact cannot be revived")
            self._artifacts[artifact.artifact_id] = artifact
            if artifact.state is ArtifactState.VALIDATED:
                if type(source_records) is not dict or type(requested_cutoff) is not UtcTimestamp:
                    # Direct repository state-machine tests may omit context, but such artifacts
                    # are intentionally ineligible for historical selection.
                    self._validation_contexts.pop(artifact.artifact_id, None)
                else:
                    self._validation_contexts[artifact.artifact_id] = ValidationContext(
                        dict(source_records), requested_cutoff
                    )

    def promote(self, artifact_id: str, content_hash: str) -> MemoryArtifact:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None or artifact.content_hash != content_hash:
                raise RuntimeError("validated artifact identity is unavailable")
            if artifact.state is ArtifactState.CURRENT:
                return artifact
            if artifact.state is not ArtifactState.VALIDATED:
                raise RuntimeError("only a validated artifact can be promoted")
            if artifact.previous_artifact_id != self._current_id:
                raise RuntimeError("candidate lost the atomic current-pointer race")
            promoted_at = self._now()
            if (
                type(promoted_at) is not UtcTimestamp
                or promoted_at.value < artifact.created_at.value
            ):
                raise RuntimeError("promotion time predates artifact creation")
            if (
                self._promotion_history
                and promoted_at.value < self._promotion_history[-1].promoted_at.value
            ):
                raise RuntimeError("promotion clock moved backwards")
            current = artifact.with_state(ArtifactState.CURRENT)
            self._artifacts[artifact_id] = current
            self._current_id = artifact_id
            self._promotion_history.append(PromotionEvent(artifact_id, promoted_at))
            return current

    @property
    def current(self) -> MemoryArtifact | None:
        with self._lock:
            if self._current_id is None:
                return None
            return self._artifacts[self._current_id]

    def validation_context(self, artifact_id: str) -> ValidationContext | None:
        with self._lock:
            context = self._validation_contexts.get(artifact_id)
            if context is None:
                return None
            return ValidationContext(dict(context.source_records), context.requested_cutoff)

    def safe_history(self, as_of: UtcTimestamp) -> tuple[MemoryArtifact, ...]:
        with self._lock:
            events = tuple(reversed(self._promotion_history))
            return tuple(
                self._artifacts[event.artifact_id]
                for event in events
                if self._artifacts[event.artifact_id].cutoff_at.value <= as_of.value
                and self._artifacts[event.artifact_id].created_at.value <= as_of.value
                and event.promoted_at.value <= as_of.value
            )


class MemoryPromoter:
    def __init__(
        self,
        content_store: ContentStore,
        repository: InMemoryPromotionRepository,
        validator: MemoryValidator,
        *,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._content_store = content_store
        self._repository = repository
        self._validator = validator
        self._failure_injector = failure_injector or (lambda _stage: None)

    def validate_and_promote(
        self,
        artifact: MemoryArtifact,
        *,
        source_records: dict[str, DailyReflectionRecord],
        requested_cutoff: UtcTimestamp,
    ) -> ValidationResult:
        if artifact.state is not ArtifactState.CANDIDATE:
            raise ValueError("promotion requires a candidate artifact")
        content = artifact.canonical_content_bytes()
        stored = cast(
            StoredObject,
            self._content_store.put(content, declared_hash=artifact.content_hash),
        )
        readback = self._content_store.get(artifact.content_hash)
        if (
            type(readback) is not bytes
            or readback != content
            or stored.content_hash != artifact.content_hash
            or stored.size != len(content)
            or hashlib.sha256(readback).hexdigest() != artifact.content_hash
        ):
            raise RuntimeError("staged memory bytes failed exact verification")
        self._failure_injector("bytes")
        self._repository.register_candidate(artifact)
        self._failure_injector("register")
        result = self._validator.validate(
            artifact, source_records=source_records, requested_cutoff=requested_cutoff
        )
        self._repository.save_validation(
            result,
            source_records=source_records,
            requested_cutoff=requested_cutoff,
        )
        self._failure_injector("validate")
        if result.valid:
            self._repository.promote(artifact.artifact_id, artifact.content_hash)
            self._failure_injector("promote")
        return result

    def select_for_as_of(self, as_of: UtcTimestamp) -> MemorySelection:
        if type(as_of) is not UtcTimestamp:
            raise ValueError("as_of must be an exact UtcTimestamp")
        for artifact in self._repository.safe_history(as_of):
            context = self._repository.validation_context(artifact.artifact_id)
            if context is None:
                continue
            try:
                content = self._content_store.get(artifact.content_hash)
                revalidated = self._validator.validate(
                    replace(artifact, state=ArtifactState.CANDIDATE),
                    source_records=context.source_records,
                    requested_cutoff=context.requested_cutoff,
                )
            except Exception:
                continue
            if (
                content == artifact.canonical_content_bytes()
                and hashlib.sha256(content).hexdigest() == artifact.content_hash
                and revalidated.valid
            ):
                return MemorySelection(artifact, None)
        return MemorySelection(None, MemoryAlert("NO_SAFE_MEMORY", as_of))
