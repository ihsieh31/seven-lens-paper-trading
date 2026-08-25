"""Append-only, point-in-time-safe reflection pipeline and capability-minimal ports."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.memory.contracts import (
    DailyReflectionRecord,
    FactRef,
    ObservationKind,
    ReflectionObservation,
    ReflectionSourceRef,
    build_daily_reflection,
)
from seven_lens.memory.fact_closure import reject_instruction_like_text, validate_text_fact_closure


def _validate_source_envelope(
    sources: tuple[ReflectionSourceRef, ...], cutoff_at: UtcTimestamp
) -> None:
    if type(sources) is not tuple or not sources or len(sources) > 32:
        raise ValueError("reflection request source envelope is invalid")
    if any(type(item) is not ReflectionSourceRef for item in sources):
        raise ValueError("reflection request sources must use exact contracts")
    source_ids = tuple(item.source_id for item in sources)
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("reflection request source ids must be unique")
    fact_ids = tuple(fact.fact_id for item in sources for fact in item.facts)
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("reflection request fact ids must be globally unique")
    if any(item.available_at.value > cutoff_at.value for item in sources):
        raise ValueError("reflection request includes a future source")


@dataclass(frozen=True, slots=True, repr=False)
class ReflectionRequest:
    record_id: str
    cutoff_at: UtcTimestamp
    sources: tuple[ReflectionSourceRef, ...]

    def __post_init__(self) -> None:
        if type(self.record_id) is not str or type(self.cutoff_at) is not UtcTimestamp:
            raise ValueError("reflection request identity is invalid")
        _validate_source_envelope(self.sources, self.cutoff_at)

    def __repr__(self) -> str:
        return "ReflectionRequest(<redacted>)"

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.sources)

    @property
    def source_hashes(self) -> tuple[str, ...]:
        return tuple(item.content_hash for item in self.sources)

    @property
    def facts(self) -> tuple[FactRef, ...]:
        return tuple(fact for source in self.sources for fact in source.facts)


class ReflectionProvider(Protocol):
    def reflect(self, request: ReflectionRequest) -> tuple[ReflectionObservation, ...]: ...


class ReflectionRepository(Protocol):
    def append(self, record: DailyReflectionRecord) -> None: ...

    def get(self, record_id: str) -> DailyReflectionRecord | None: ...


class SourceAuthority(StrEnum):
    APPROVED = "APPROVED"


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedReflectionSource:
    source: ReflectionSourceRef
    content: bytes
    authority: SourceAuthority

    def __post_init__(self) -> None:
        if (
            type(self.source) is not ReflectionSourceRef
            or type(self.content) is not bytes
            or not self.content
            or type(self.authority) is not SourceAuthority
        ):
            raise ValueError("resolved reflection source is invalid")


class ReflectionSourceResolver(Protocol):
    def read_approved(self, source: ReflectionSourceRef) -> ResolvedReflectionSource: ...


class TrustedReflectionSourceResolver:
    """Explicit offline authority used by scripted/tests; values are exact readback bytes."""

    def __init__(self, content: dict[ReflectionSourceRef, bytes]) -> None:
        if type(content) is not dict or not content:
            raise ValueError("trusted source resolver requires an exact non-empty mapping")
        if any(
            type(key) is not ReflectionSourceRef
            or type(value) is not bytes
            or not value
            or hashlib.sha256(value).hexdigest() != key.content_hash
            for key, value in content.items()
        ):
            raise ValueError("trusted source resolver mapping is invalid")
        self._content = dict(content)

    def read_approved(self, source: ReflectionSourceRef) -> ResolvedReflectionSource:
        try:
            content = self._content[source]
        except KeyError as error:
            raise RuntimeError("reflection source lacks approved authority") from error
        return ResolvedReflectionSource(
            source,
            content,
            SourceAuthority.APPROVED,
        )


class ScriptedReflectionProvider:
    """One-shot offline provider with no network, secret, DB, shell or filesystem capability."""

    def __init__(self, observations: tuple[ReflectionObservation, ...]) -> None:
        if type(observations) is not tuple or not observations:
            raise ValueError("scripted reflection requires observations")
        self._observations = observations
        self._used = False
        self.calls: list[ReflectionRequest] = []

    def reflect(self, request: ReflectionRequest) -> tuple[ReflectionObservation, ...]:
        if self._used:
            raise RuntimeError("scripted reflection output was already consumed")
        self._used = True
        self.calls.append(request)
        return self._observations


class InMemoryReflectionRepository:
    """Append-only repository used by offline record/replay and unit tests."""

    def __init__(self) -> None:
        self._records: dict[str, DailyReflectionRecord] = {}
        self._lock = threading.Lock()

    def append(self, record: DailyReflectionRecord) -> None:
        if type(record) is not DailyReflectionRecord:
            raise ValueError("only exact reflection records can be appended")
        record.verify_integrity()
        with self._lock:
            existing = self._records.get(record.record_id)
            if existing is not None:
                if type(existing) is not DailyReflectionRecord:
                    raise RuntimeError("persisted reflection type is invalid")
                existing.verify_integrity()
                if existing.content_hash == record.content_hash:
                    return
                raise RuntimeError("reflection identity collision")
            correction_targets = {
                item.supersedes_record_id
                for item in record.observations
                if item.kind is ObservationKind.CORRECTION
            }
            if None in correction_targets or any(
                target not in self._records for target in correction_targets
            ):
                raise RuntimeError("correction supersedes target is unknown")
            for possible_target in correction_targets:
                if possible_target is None:
                    raise RuntimeError("correction supersedes target is unknown")
                target = possible_target
                prior_target = self._records[target]
                if type(prior_target) is not DailyReflectionRecord:
                    raise RuntimeError("correction target type is invalid")
                prior_target.verify_integrity()
                if (
                    prior_target.available_at.value > record.cutoff_at.value
                    or prior_target.cutoff_at.value > record.cutoff_at.value
                    or prior_target.created_at.value > record.created_at.value
                ):
                    raise RuntimeError("correction supersedes target chronology is invalid")
                cursor: str | None = target
                visited: set[str] = set()
                while cursor is not None:
                    if cursor == record.record_id or cursor in visited:
                        raise RuntimeError("correction supersedes lineage contains a cycle")
                    visited.add(cursor)
                    prior = self._records.get(cursor)
                    if prior is None:
                        break
                    if type(prior) is not DailyReflectionRecord:
                        raise RuntimeError("correction lineage type is invalid")
                    prior.verify_integrity()
                    prior_targets = {
                        item.supersedes_record_id
                        for item in prior.observations
                        if item.kind is ObservationKind.CORRECTION
                    }
                    cursor = next(iter(prior_targets)) if prior_targets else None
            self._records[record.record_id] = record

    def get(self, record_id: str) -> DailyReflectionRecord | None:
        with self._lock:
            record = self._records.get(record_id)
            if record is not None:
                if type(record) is not DailyReflectionRecord:
                    raise RuntimeError("persisted reflection type is invalid")
                record.verify_integrity()
            return record

    def records_as_of(self, as_of: UtcTimestamp) -> tuple[DailyReflectionRecord, ...]:
        if type(as_of) is not UtcTimestamp:
            raise ValueError("as_of must be an exact UtcTimestamp")
        with self._lock:
            records = list(self._records.values())
        for record in records:
            if type(record) is not DailyReflectionRecord:
                raise RuntimeError("persisted reflection type is invalid")
            record.verify_integrity()
        records = [record for record in records if record.available_at.value <= as_of.value]
        return tuple(sorted(records, key=lambda item: (item.available_at.value, item.record_id)))


class ReflectionPipeline:
    def __init__(
        self,
        provider: ReflectionProvider,
        repository: ReflectionRepository,
        source_resolver: ReflectionSourceResolver | None = None,
        *,
        clock: Callable[[], UtcTimestamp] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._source_resolver = source_resolver
        self._clock = clock
        self._deadline = deadline or (lambda: True)

    def _check_deadline(self) -> None:
        if self._deadline() is not True:
            raise TimeoutError("reflection deadline expired")

    def _read_authority(
        self, sources: tuple[ReflectionSourceRef, ...]
    ) -> tuple[ResolvedReflectionSource, ...]:
        if self._source_resolver is None:
            raise RuntimeError("reflection source authority resolver is required")
        resolved: list[ResolvedReflectionSource] = []
        for source in sources:
            item = self._source_resolver.read_approved(source)
            if (
                type(item) is not ResolvedReflectionSource
                or item.authority is not SourceAuthority.APPROVED
                or item.source != source
                or hashlib.sha256(item.content).hexdigest() != source.content_hash
            ):
                raise RuntimeError("reflection source authority/readback verification failed")
            resolved.append(item)
        return tuple(resolved)

    def run(
        self,
        *,
        sources: tuple[ReflectionSourceRef, ...],
        now: UtcTimestamp,
        **record_fields: object,
    ) -> DailyReflectionRecord:
        if type(sources) is not tuple or not sources or type(now) is not UtcTimestamp:
            raise ValueError("reflection pipeline inputs are invalid")
        cutoff = record_fields.get("cutoff_at")
        record_id = record_fields.get("record_id")
        required_open_position_source_ids = record_fields.pop(
            "required_open_position_source_ids", ()
        )
        if type(cutoff) is not UtcTimestamp or type(record_id) is not str:
            raise ValueError("reflection record identity/cutoff is invalid")
        # Reject the complete source envelope before any resolver read or provider call. The
        # resolver is an authority boundary, so duplicate identities must never reach it.
        _validate_source_envelope(sources, cutoff)
        if (
            type(required_open_position_source_ids) is not tuple
            or any(type(item) is not str for item in required_open_position_source_ids)
            or len(set(required_open_position_source_ids)) != len(required_open_position_source_ids)
        ):
            raise ValueError("required open-position source ids are invalid")
        daily_open_positions = {
            source.source_id for source in sources if source.source_type == "open_position"
        }
        required_open_positions = daily_open_positions | set(required_open_position_source_ids)
        source_ids = {source.source_id for source in sources}
        if not required_open_positions.issubset(source_ids):
            raise ValueError("required open-position source is missing")
        if now.value < cutoff.value or any(
            source.available_at.value > cutoff.value for source in sources
        ):
            raise ValueError("reflection source is not available at cutoff")
        existing = self._repository.get(record_id)
        if existing is not None:
            if type(existing) is not DailyReflectionRecord:
                raise RuntimeError("persisted reflection type is invalid")
            existing.verify_integrity()
            persisted_fields = {
                "record_id": existing.record_id,
                "schema_version": existing.schema_version,
                "as_of": existing.as_of,
                "cutoff_at": existing.cutoff_at,
                "proposal_id": existing.proposal_id,
                "decision_id": existing.decision_id,
                "research_bundle_hash": existing.research_bundle_hash,
                "portfolio_snapshot_hash": existing.portfolio_snapshot_hash,
                "prompt_version": existing.prompt_version,
                "model_version": existing.model_version,
                "provider_version": existing.provider_version,
                "data_version": existing.data_version,
                "memory_version": existing.memory_version,
            }
            if (
                existing.sources != sources
                or persisted_fields != record_fields
                or existing.available_at.value > now.value
            ):
                raise RuntimeError("persisted reflection lineage changed on resume")
            required_fact_ids = {
                fact.fact_id
                for source in sources
                if source.source_id in required_open_positions
                for fact in source.facts
            }
            covered_fact_ids = {
                fact_id
                for item in existing.observations
                if item.kind is ObservationKind.OPEN_POSITION
                for fact_id in item.fact_ids
            }
            if required_open_positions and not required_fact_ids.issubset(covered_fact_ids):
                raise RuntimeError("persisted reflection omitted an open-position source")
            self._check_deadline()
            self._read_authority(sources)
            return existing
        self._check_deadline()
        authority_snapshot = self._read_authority(sources)
        request = ReflectionRequest(
            record_id,
            cutoff,
            sources,
        )
        observations = self._provider.reflect(request)
        if type(observations) is not tuple or not observations or len(observations) > 64:
            raise ValueError("reflection provider output must be a non-empty exact tuple")
        if any(type(item) is not ReflectionObservation for item in observations):
            raise ValueError("reflection provider output contains an invalid observation")
        open_position_fact_ids = {
            fact.fact_id
            for source in sources
            if source.source_id in required_open_positions
            for fact in source.facts
        }
        covered_open_position_fact_ids = {
            fact_id
            for item in observations
            if item.kind is ObservationKind.OPEN_POSITION
            for fact_id in item.fact_ids
        }
        if required_open_positions and not open_position_fact_ids.issubset(
            covered_open_position_fact_ids
        ):
            raise ValueError("daily reflection omitted an open-position source")
        available_facts = {fact.fact_id: fact for fact in request.facts}
        for item in observations:
            texts = (
                item.observation,
                item.reusable_lesson,
                *item.applies_when,
                *item.invalid_when,
            )
            reject_instruction_like_text(texts)
            validate_text_fact_closure(
                texts,
                available_facts=available_facts,
                cited_fact_ids=item.fact_ids,
            )
        # Re-check time, deadline and exact approved bytes immediately before append.
        self._check_deadline()
        persist_now = self._clock() if self._clock is not None else now
        if type(persist_now) is not UtcTimestamp:
            raise RuntimeError("reflection clock returned an invalid timestamp")
        if persist_now.value < cutoff.value or any(
            source.available_at.value > cutoff.value for source in sources
        ):
            raise ValueError("reflection source became invalid before persistence")
        if self._read_authority(sources) != authority_snapshot:
            raise RuntimeError("reflection source authority changed before persistence")
        record = build_daily_reflection(
            sources=sources,
            observations=observations,
            created_at=persist_now,
            available_at=persist_now,
            **record_fields,
        )
        record.verify_integrity()
        self._repository.append(record)
        persisted = self._repository.get(record.record_id)
        if type(persisted) is not DailyReflectionRecord:
            raise RuntimeError("reflection persistence verification failed")
        try:
            persisted.verify_integrity()
        except ValueError as error:
            raise RuntimeError("reflection persistence verification failed") from error
        if persisted != record or persisted.content_hash != record.content_hash:
            raise RuntimeError("reflection persistence verification failed")
        return persisted
