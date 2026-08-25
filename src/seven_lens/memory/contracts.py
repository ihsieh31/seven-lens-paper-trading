"""Immutable, bounded contracts for P3-F reflection and condensed memory.

Memory is derived context, never trading authority.  The contracts in this module carry no
broker, order, risk-approval, credential, network, shell, or filesystem-path capability.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Final, cast

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.security.sanitized_text import validate_sanitized_text

MEMORY_SCHEMA_VERSION: Final = "1.0.0"
MAX_ARTIFACT_LINES: Final = 4_000
MAX_ARTIFACT_ENTRIES: Final = 512
MAX_ARTIFACT_SOURCES: Final = 4_096
MAX_ARTIFACT_BYTES: Final = 512 * 1024
MAX_FIELD_BYTES: Final = 2_048
MAX_ENTRY_EVIDENCE: Final = 32
MAX_TOTAL_FACT_REFS: Final = 8_192
MAX_SOURCE_FACTS: Final = 256

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


class FactKind(StrEnum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    DATE = "DATE"
    SYMBOL = "SYMBOL"
    RISK_REASON = "RISK_REASON"


class ObservationKind(StrEnum):
    FORECAST = "FORECAST"
    OUTCOME = "OUTCOME"
    RISK_REJECTION = "RISK_REJECTION"
    OPEN_POSITION = "OPEN_POSITION"
    CORRECTION = "CORRECTION"


class MemoryCategory(StrEnum):
    RISK_REJECTION = "RISK_REJECTION"
    FORECAST_CALIBRATION = "FORECAST_CALIBRATION"
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
    SAME_DAY_LOSS = "SAME_DAY_LOSS"
    BORROW_LIQUIDITY = "BORROW_LIQUIDITY"
    MARKET_REGIME = "MARKET_REGIME"
    UNRESOLVED_RISK = "UNRESOLVED_RISK"
    GENERAL = "GENERAL"


class ArtifactState(StrEnum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    CURRENT = "CURRENT"
    INVALID = "INVALID"


def _exact_text(value: object, field: str, *, maximum: int = MAX_FIELD_BYTES) -> str:
    text = validate_sanitized_text(value, field, maximum=maximum)
    if "\n" in text or "\r" in text:
        raise ValueError(f"{field} must be single-line text")
    return text


def _ref(value: object, field: str) -> str:
    text = _exact_text(value, field, maximum=128)
    if _REF.fullmatch(text) is None:
        raise ValueError(f"{field} must use canonical reference text")
    return text


def _hash(value: object, field: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _version(value: object, field: str) -> str:
    text = _exact_text(value, field, maximum=64)
    if _VERSION.fullmatch(text) is None:
        raise ValueError(f"{field} must use canonical version text")
    return text


def _exact_tuple(
    value: object, field: str, *, maximum: int, nonempty: bool = False
) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{field} must be an exact tuple")
    if len(value) > maximum or (nonempty and not value):
        raise ValueError(f"{field} is outside its item bound")
    return value


def _refs(value: object, field: str, *, maximum: int, nonempty: bool = False) -> tuple[str, ...]:
    raw = _exact_tuple(value, field, maximum=maximum, nonempty=nonempty)
    result = tuple(_ref(item, f"{field} item") for item in raw)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _texts(value: object, field: str, *, maximum: int) -> tuple[str, ...]:
    raw = _exact_tuple(value, field, maximum=maximum)
    result = tuple(_exact_text(item, f"{field} item") for item in raw)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _timestamp(value: object, field: str) -> UtcTimestamp:
    if type(value) is not UtcTimestamp:
        raise ValueError(f"{field} must be an exact UtcTimestamp")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8", errors="strict")


@dataclass(frozen=True, slots=True)
class FactRef:
    fact_id: str
    kind: FactKind
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_id", _ref(self.fact_id, "fact_id"))
        if type(self.kind) is not FactKind:
            raise ValueError("fact kind must be exact")
        object.__setattr__(self, "value", _exact_text(self.value, "fact value", maximum=256))
        if self.kind is FactKind.NUMBER and _NUMBER.fullmatch(self.value) is None:
            raise ValueError("numeric fact must use canonical decimal text")
        if self.kind is FactKind.DATE and _DATE.fullmatch(self.value) is None:
            raise ValueError("date fact must use YYYY-MM-DD")
        if self.kind is FactKind.DATE:
            try:
                date.fromisoformat(self.value)
            except ValueError as error:
                raise ValueError("date fact must be a real calendar date") from error
        if self.kind is FactKind.SYMBOL and _SYMBOL.fullmatch(self.value) is None:
            raise ValueError("symbol fact must use canonical uppercase ticker text")
        if self.kind is FactKind.RISK_REASON and _REF.fullmatch(self.value) is None:
            raise ValueError("risk reason fact must use canonical reason text")

    def to_wire(self) -> dict[str, str]:
        return {"fact_id": self.fact_id, "kind": self.kind.value, "value": self.value}


@dataclass(frozen=True, slots=True)
class ReflectionSourceRef:
    source_id: str
    source_type: str
    content_hash: str
    available_at: UtcTimestamp
    facts: tuple[FactRef, ...]
    prompt_injection_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _ref(self.source_id, "source_id"))
        object.__setattr__(self, "source_type", _ref(self.source_type, "source_type"))
        _hash(self.content_hash, "source content_hash")
        _timestamp(self.available_at, "source available_at")
        raw = _exact_tuple(self.facts, "source facts", maximum=MAX_SOURCE_FACTS, nonempty=True)
        if any(type(item) is not FactRef for item in raw):
            raise ValueError("source facts must contain exact FactRef values")
        typed_facts = cast(tuple[FactRef, ...], raw)
        fact_ids = tuple(item.fact_id for item in typed_facts)
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("source facts must have unique ids")
        object.__setattr__(
            self,
            "prompt_injection_flags",
            _refs(self.prompt_injection_flags, "prompt_injection_flags", maximum=16),
        )

    def to_wire(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "content_hash": self.content_hash,
            "available_at": str(self.available_at),
            "facts": [fact.to_wire() for fact in self.facts],
            "prompt_injection_flags": list(self.prompt_injection_flags),
        }


@dataclass(frozen=True, slots=True)
class ReflectionObservation:
    kind: ObservationKind
    observation: str
    reusable_lesson: str
    applies_when: tuple[str, ...]
    invalid_when: tuple[str, ...]
    fact_ids: tuple[str, ...]
    supersedes_record_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ObservationKind:
            raise ValueError("observation kind must be exact")
        expected_kind = {
            ForecastObservation: ObservationKind.FORECAST,
            OutcomeObservation: ObservationKind.OUTCOME,
            RiskRejectionObservation: ObservationKind.RISK_REJECTION,
        }.get(type(self))
        if expected_kind is not None and self.kind is not expected_kind:
            raise ValueError("typed observation class does not match its observation kind")
        object.__setattr__(self, "observation", _exact_text(self.observation, "observation"))
        object.__setattr__(
            self, "reusable_lesson", _exact_text(self.reusable_lesson, "reusable_lesson")
        )
        object.__setattr__(
            self, "applies_when", _texts(self.applies_when, "applies_when", maximum=16)
        )
        object.__setattr__(
            self, "invalid_when", _texts(self.invalid_when, "invalid_when", maximum=16)
        )
        object.__setattr__(
            self, "fact_ids", _refs(self.fact_ids, "fact_ids", maximum=64, nonempty=True)
        )
        if self.supersedes_record_id is not None:
            object.__setattr__(
                self,
                "supersedes_record_id",
                _ref(self.supersedes_record_id, "supersedes_record_id"),
            )
        if (self.kind is ObservationKind.CORRECTION) != (self.supersedes_record_id is not None):
            raise ValueError("only a correction observation carries a supersedes record link")

    def to_wire(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "observation": self.observation,
            "reusable_lesson": self.reusable_lesson,
            "applies_when": list(self.applies_when),
            "invalid_when": list(self.invalid_when),
            "fact_ids": list(self.fact_ids),
            "supersedes_record_id": self.supersedes_record_id,
        }


class ForecastObservation(ReflectionObservation):
    pass


class OutcomeObservation(ReflectionObservation):
    pass


class RiskRejectionObservation(ReflectionObservation):
    pass


@dataclass(frozen=True, slots=True)
class DailyReflectionRecord:
    record_id: str
    schema_version: str
    created_at: UtcTimestamp
    available_at: UtcTimestamp
    as_of: UtcTimestamp
    cutoff_at: UtcTimestamp
    proposal_id: str
    decision_id: str
    research_bundle_hash: str
    portfolio_snapshot_hash: str
    sources: tuple[ReflectionSourceRef, ...]
    observations: tuple[ReflectionObservation, ...]
    prompt_version: str
    model_version: str
    provider_version: str
    data_version: str
    memory_version: str
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _ref(self.record_id, "record_id"))
        object.__setattr__(self, "schema_version", _version(self.schema_version, "schema_version"))
        for name in ("created_at", "available_at", "as_of", "cutoff_at"):
            _timestamp(getattr(self, name), name)
        if not (
            self.cutoff_at.value
            <= self.as_of.value
            <= self.created_at.value
            <= self.available_at.value
        ):
            raise ValueError("reflection timestamps violate cutoff/as-of/availability order")
        object.__setattr__(self, "proposal_id", _ref(self.proposal_id, "proposal_id"))
        object.__setattr__(self, "decision_id", _ref(self.decision_id, "decision_id"))
        _hash(self.research_bundle_hash, "research_bundle_hash")
        _hash(self.portfolio_snapshot_hash, "portfolio_snapshot_hash")
        sources = _exact_tuple(self.sources, "sources", maximum=32, nonempty=True)
        observations = _exact_tuple(self.observations, "observations", maximum=64, nonempty=True)
        if any(type(item) is not ReflectionSourceRef for item in sources):
            raise ValueError("sources must contain exact ReflectionSourceRef values")
        allowed_observation_types = {
            ReflectionObservation,
            ForecastObservation,
            OutcomeObservation,
            RiskRejectionObservation,
        }
        if any(type(item) not in allowed_observation_types for item in observations):
            raise ValueError("observations must contain reflection observation values")
        typed_sources = cast(tuple[ReflectionSourceRef, ...], sources)
        typed_observations = cast(tuple[ReflectionObservation, ...], observations)
        if len({item.source_id for item in typed_sources}) != len(typed_sources):
            raise ValueError("reflection source ids must be unique")
        all_fact_ids = [fact.fact_id for source in typed_sources for fact in source.facts]
        if len(set(all_fact_ids)) != len(all_fact_ids):
            raise ValueError("reflection fact ids must be globally unique")
        if any(source.available_at.value > self.cutoff_at.value for source in typed_sources):
            raise ValueError("reflection includes a source unavailable at cutoff")
        facts = set(all_fact_ids)
        if any(not set(item.fact_ids).issubset(facts) for item in typed_observations):
            raise ValueError("reflection observation has foreign fact lineage")
        corrections = tuple(
            item for item in typed_observations if item.kind is ObservationKind.CORRECTION
        )
        if corrections and len(corrections) != len(typed_observations):
            raise ValueError("correction observations cannot mix with ordinary observations")
        if any(item.supersedes_record_id == self.record_id for item in corrections):
            raise ValueError("correction record cannot supersede itself")
        if len({item.supersedes_record_id for item in corrections}) > 1:
            raise ValueError("one correction record must supersede one exact prior record")
        if any(source.prompt_injection_flags for source in typed_sources):
            raise ValueError("reflection source is prompt-injection flagged")
        # Import locally to avoid a module cycle: this contract is the fact-closure module's
        # immutable input type. Direct record construction must be as strict as pipeline use.
        from seven_lens.memory.fact_closure import (
            reject_instruction_like_text,
            validate_text_fact_closure,
        )

        available_facts = {fact.fact_id: fact for source in typed_sources for fact in source.facts}
        for item in typed_observations:
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
        for name in (
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
        ):
            object.__setattr__(self, name, _version(getattr(self, name), name))
        _hash(self.content_hash, "content_hash")
        if self.content_hash != self.compute_hash():
            raise ValueError("reflection content hash does not match canonical record")

    def content_wire(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "schema_version": self.schema_version,
            "created_at": str(self.created_at),
            "available_at": str(self.available_at),
            "as_of": str(self.as_of),
            "cutoff_at": str(self.cutoff_at),
            "proposal_id": self.proposal_id,
            "decision_id": self.decision_id,
            "research_bundle_hash": self.research_bundle_hash,
            "portfolio_snapshot_hash": self.portfolio_snapshot_hash,
            "sources": [item.to_wire() for item in self.sources],
            "observations": [item.to_wire() for item in self.observations],
            "prompt_version": self.prompt_version,
            "model_version": self.model_version,
            "provider_version": self.provider_version,
            "data_version": self.data_version,
            "memory_version": self.memory_version,
        }

    def compute_hash(self) -> str:
        material = b"seven-lens.p3f.reflection.v1\x00" + _canonical(self.content_wire())
        return hashlib.sha256(material).hexdigest()

    def verify_integrity(self) -> None:
        self.__post_init__()


def build_daily_reflection(**fields: object) -> DailyReflectionRecord:
    provisional = object.__new__(DailyReflectionRecord)
    for name, value in fields.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "content_hash", "0" * 64)
    return DailyReflectionRecord(
        **fields,  # type: ignore[arg-type]
        content_hash=provisional.compute_hash(),
    )


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    category: MemoryCategory
    importance: int
    observation: str
    reusable_lesson: str
    applies_when: tuple[str, ...]
    invalid_when: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    source_record_ids: tuple[str, ...]
    risk_reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.category) is not MemoryCategory:
            raise ValueError("memory category must be exact")
        if type(self.importance) is not int or not 0 <= self.importance <= 100:
            raise ValueError("memory importance must be an exact bounded integer")
        object.__setattr__(self, "observation", _exact_text(self.observation, "observation"))
        object.__setattr__(
            self, "reusable_lesson", _exact_text(self.reusable_lesson, "reusable_lesson")
        )
        object.__setattr__(
            self, "applies_when", _texts(self.applies_when, "applies_when", maximum=16)
        )
        object.__setattr__(
            self, "invalid_when", _texts(self.invalid_when, "invalid_when", maximum=16)
        )
        object.__setattr__(
            self,
            "evidence_ids",
            _refs(self.evidence_ids, "evidence_ids", maximum=MAX_ENTRY_EVIDENCE, nonempty=True),
        )
        object.__setattr__(
            self,
            "source_record_ids",
            _refs(self.source_record_ids, "source_record_ids", maximum=16, nonempty=True),
        )
        object.__setattr__(
            self,
            "risk_reason_codes",
            _refs(self.risk_reason_codes, "risk_reason_codes", maximum=16),
        )

    @property
    def dedup_key(self) -> str:
        def canonical_text(value: str) -> str:
            return unicodedata.normalize("NFKC", value).casefold()

        value = [
            canonical_text(self.category.value),
            canonical_text(self.observation),
            canonical_text(self.reusable_lesson),
            [canonical_text(item) for item in self.applies_when],
            [canonical_text(item) for item in self.invalid_when],
        ]
        return hashlib.sha256(b"seven-lens.p3f.dedup.v1\x00" + _canonical(value)).hexdigest()

    def to_wire(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "importance": self.importance,
            "observation": self.observation,
            "reusable_lesson": self.reusable_lesson,
            "applies_when": list(self.applies_when),
            "invalid_when": list(self.invalid_when),
            "evidence_ids": list(self.evidence_ids),
            "source_record_ids": list(self.source_record_ids),
            "risk_reason_codes": list(self.risk_reason_codes),
        }


@dataclass(frozen=True, slots=True)
class MemoryArtifact:
    artifact_id: str
    schema_version: str
    created_at: UtcTimestamp
    cutoff_at: UtcTimestamp
    source_record_ids: tuple[str, ...]
    previous_artifact_id: str | None
    entries: tuple[MemoryEntry, ...]
    line_count: int
    content_hash: str
    prompt_version: str
    model_version: str
    provider_version: str
    state: ArtifactState

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _ref(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "schema_version", _version(self.schema_version, "schema_version"))
        _timestamp(self.created_at, "created_at")
        _timestamp(self.cutoff_at, "cutoff_at")
        if self.created_at.value < self.cutoff_at.value:
            raise ValueError("artifact cannot be created before its cutoff")
        object.__setattr__(
            self,
            "source_record_ids",
            _refs(
                self.source_record_ids,
                "source_record_ids",
                maximum=MAX_ARTIFACT_SOURCES,
                nonempty=True,
            ),
        )
        if self.previous_artifact_id is not None:
            object.__setattr__(
                self,
                "previous_artifact_id",
                _ref(self.previous_artifact_id, "previous_artifact_id"),
            )
            if self.previous_artifact_id == self.artifact_id:
                raise ValueError("artifact cannot be its own predecessor")
        entries = _exact_tuple(self.entries, "entries", maximum=MAX_ARTIFACT_ENTRIES, nonempty=True)
        if any(type(item) is not MemoryEntry for item in entries):
            raise ValueError("entries must contain exact MemoryEntry values")
        typed_entries = cast(tuple[MemoryEntry, ...], entries)
        if any(
            not set(item.source_record_ids).issubset(self.source_record_ids)
            for item in typed_entries
        ):
            raise ValueError("memory entry has foreign source-record lineage")
        total_facts = sum(len(item.evidence_ids) for item in typed_entries)
        if total_facts > MAX_TOTAL_FACT_REFS:
            raise ValueError("artifact exceeds total fact-reference bound")
        if type(self.line_count) is not int or not 1 <= self.line_count <= MAX_ARTIFACT_LINES:
            raise ValueError("artifact line count is outside its bound")
        _hash(self.content_hash, "content_hash")
        for name in ("prompt_version", "model_version", "provider_version"):
            object.__setattr__(self, name, _version(getattr(self, name), name))
        if type(self.state) is not ArtifactState:
            raise ValueError("artifact state must be exact")
        expected_lines = len(self.render_lines())
        if self.line_count != expected_lines:
            raise ValueError("artifact line count does not match deterministic rendering")
        if any(
            len(line.encode("utf-8", errors="strict")) > MAX_FIELD_BYTES
            for line in self.render_lines()
        ):
            raise ValueError("artifact rendered line exceeds byte bound")
        material = self.canonical_content_bytes()
        if len(material) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds canonical byte bound")
        if hashlib.sha256(material).hexdigest() != self.content_hash:
            raise ValueError("artifact content hash does not match canonical bytes")

    def render_lines(self) -> tuple[str, ...]:
        lines = (
            f"artifact_id: {self.artifact_id}",
            f"schema_version: {self.schema_version}",
            f"created_at: {self.created_at}",
            f"cutoff_at: {self.cutoff_at}",
            f"previous_artifact_id: {self.previous_artifact_id or '-'}",
        )
        entry_lines: list[str] = []
        for entry in self.entries:
            entry_lines.extend(
                (
                    f"category: {entry.category.value}",
                    f"importance: {entry.importance}",
                    f"observation: {entry.observation}",
                    f"reusable_lesson: {entry.reusable_lesson}",
                    f"applies_when: {' | '.join(entry.applies_when)}",
                    f"invalid_when: {' | '.join(entry.invalid_when)}",
                    f"evidence_ids: {' | '.join(entry.evidence_ids)}",
                    f"source_record_ids: {' | '.join(entry.source_record_ids)}",
                    f"risk_reason_codes: {' | '.join(entry.risk_reason_codes)}",
                )
            )
        return lines + tuple(entry_lines)

    def content_wire(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "schema_version": self.schema_version,
            "created_at": str(self.created_at),
            "cutoff_at": str(self.cutoff_at),
            "source_record_ids": list(self.source_record_ids),
            "previous_artifact_id": self.previous_artifact_id,
            "entries": [entry.to_wire() for entry in self.entries],
            "line_count": self.line_count,
            "prompt_version": self.prompt_version,
            "model_version": self.model_version,
            "provider_version": self.provider_version,
        }

    def canonical_content_bytes(self) -> bytes:
        return _canonical(self.content_wire())

    def with_state(self, state: ArtifactState) -> MemoryArtifact:
        if type(state) is not ArtifactState:
            raise ValueError("artifact state must be exact")
        allowed = {
            ArtifactState.CANDIDATE: {ArtifactState.VALIDATED, ArtifactState.INVALID},
            ArtifactState.VALIDATED: {ArtifactState.CURRENT, ArtifactState.INVALID},
            ArtifactState.CURRENT: set(),
            ArtifactState.INVALID: set(),
        }
        if state not in allowed[self.state]:
            raise ValueError("artifact state transition is invalid")
        return replace(self, state=state)

    def verify_integrity(self) -> None:
        self.__post_init__()


def build_memory_artifact(**fields: object) -> MemoryArtifact:
    entries = fields.get("entries")
    if type(entries) is not tuple or not entries:
        raise ValueError("entries must be a non-empty exact tuple")
    line_count = 5 + 9 * len(entries)
    provisional = object.__new__(MemoryArtifact)
    for name, value in fields.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "line_count", line_count)
    object.__setattr__(provisional, "content_hash", "0" * 64)
    object.__setattr__(provisional, "state", ArtifactState.CANDIDATE)
    return MemoryArtifact(
        line_count=line_count,
        content_hash=hashlib.sha256(provisional.canonical_content_bytes()).hexdigest(),
        state=ArtifactState.CANDIDATE,
        **fields,  # type: ignore[arg-type]
    )
