"""Deterministic P3-F entry scoring, quota selection, and bounded serialization."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from typing import Final

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.memory.contracts import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_ENTRIES,
    MAX_ARTIFACT_LINES,
    DailyReflectionRecord,
    MemoryArtifact,
    MemoryCategory,
    MemoryEntry,
    ObservationKind,
    build_memory_artifact,
)

_CATEGORY_BASE: Final = {
    MemoryCategory.RISK_REJECTION: 78,
    MemoryCategory.FORECAST_CALIBRATION: 72,
    MemoryCategory.POSITION_MANAGEMENT: 68,
    MemoryCategory.SAME_DAY_LOSS: 82,
    MemoryCategory.BORROW_LIQUIDITY: 82,
    MemoryCategory.MARKET_REGIME: 66,
    MemoryCategory.UNRESOLVED_RISK: 86,
    MemoryCategory.GENERAL: 40,
}

DEFAULT_CATEGORY_QUOTAS: Final = {category: 64 for category in MemoryCategory}
_SOURCE_CATEGORY_MARKERS: Final = {
    "same_day_loss": MemoryCategory.SAME_DAY_LOSS,
    "borrow_liquidity": MemoryCategory.BORROW_LIQUIDITY,
    "market_regime": MemoryCategory.MARKET_REGIME,
    "unresolved_risk": MemoryCategory.UNRESOLVED_RISK,
    "open_position": MemoryCategory.POSITION_MANAGEMENT,
}
_MARKER_PRIORITY: Final = (
    MemoryCategory.UNRESOLVED_RISK,
    MemoryCategory.SAME_DAY_LOSS,
    MemoryCategory.BORROW_LIQUIDITY,
    MemoryCategory.MARKET_REGIME,
    MemoryCategory.POSITION_MANAGEMENT,
)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    entry: MemoryEntry
    available_at: UtcTimestamp
    recurrence_count: int = 1
    unresolved: bool = False
    model_importance: int = 0

    def __post_init__(self) -> None:
        if type(self.entry) is not MemoryEntry or type(self.available_at) is not UtcTimestamp:
            raise ValueError("memory candidate types are invalid")
        if type(self.recurrence_count) is not int or not 1 <= self.recurrence_count <= 10_000:
            raise ValueError("recurrence_count is outside its bound")
        if type(self.unresolved) is not bool:
            raise ValueError("unresolved must be an exact bool")
        if type(self.model_importance) is not int or not 0 <= self.model_importance <= 100:
            raise ValueError("model_importance is outside its bound")


def _merge_candidates(candidates: tuple[MemoryCandidate, ...]) -> tuple[MemoryCandidate, ...]:
    """Merge semantic duplicates without discarding any source or fact lineage."""
    groups: dict[str, list[MemoryCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.entry.dedup_key, []).append(candidate)
    merged: list[MemoryCandidate] = []
    for dedup_key in sorted(groups):
        group = groups[dedup_key]

        def text_key(value: str) -> str:
            return unicodedata.normalize("NFKC", value).casefold()

        def tie_breaker(item: MemoryCandidate) -> tuple[object, ...]:
            entry = item.entry
            semantic = (
                text_key(entry.observation),
                text_key(entry.reusable_lesson),
                tuple(text_key(value) for value in entry.applies_when),
                tuple(text_key(value) for value in entry.invalid_when),
            )
            raw = (
                entry.observation,
                entry.reusable_lesson,
                entry.applies_when,
                entry.invalid_when,
            )
            return (
                semantic,
                entry.evidence_ids,
                entry.source_record_ids,
                entry.risk_reason_codes,
                str(item.available_at),
                raw,
            )

        representative = min(
            group,
            key=tie_breaker,
        )

        def merge_refs(field: str, current_group: list[MemoryCandidate] = group) -> tuple[str, ...]:
            values: list[str] = []
            for item in sorted(current_group, key=tie_breaker):
                for value in getattr(item.entry, field):
                    if value not in values:
                        values.append(value)
            return tuple(values)

        evidence_ids = merge_refs("evidence_ids")
        source_record_ids = merge_refs("source_record_ids")
        risk_reason_codes = merge_refs("risk_reason_codes")
        if len(evidence_ids) > 32 or len(source_record_ids) > 16 or len(risk_reason_codes) > 16:
            raise ValueError("merged candidate lineage exceeds hard entry bounds")
        merged.append(
            MemoryCandidate(
                replace(
                    representative.entry,
                    evidence_ids=evidence_ids,
                    source_record_ids=source_record_ids,
                    risk_reason_codes=risk_reason_codes,
                ),
                representative.available_at,
                recurrence_count=representative.recurrence_count,
                unresolved=representative.unresolved,
                model_importance=representative.model_importance,
            )
        )
    return tuple(merged)


def deterministic_importance(candidate: MemoryCandidate) -> int:
    """Recompute importance without trusting either model or caller-provided score."""
    score = _CATEGORY_BASE[candidate.entry.category]
    score += min(candidate.recurrence_count - 1, 8) * 2
    score += min(len(candidate.entry.risk_reason_codes), 3) * 2
    score += 6 if candidate.unresolved else 0
    return min(score, 100)


def derive_entry_policy(
    entry: MemoryEntry,
    source_records: dict[str, DailyReflectionRecord],
) -> tuple[MemoryCategory, UtcTimestamp, int, bool]:
    """Derive category, visibility, recurrence and unresolved state from immutable sources."""
    if type(source_records) is not dict:
        raise ValueError("source records must be an exact mapping")
    try:
        records = tuple(source_records[item] for item in entry.source_record_ids)
    except KeyError as error:
        raise ValueError("candidate contains foreign source-record lineage") from error
    seen_fact_ids: set[str] = set()
    for record in records:
        if (
            type(record) is not DailyReflectionRecord
            or source_records.get(record.record_id) is not record
        ):
            raise ValueError("candidate source-record identity is invalid")
        record.verify_integrity()
        for source in record.sources:
            for fact in source.facts:
                if fact.fact_id in seen_fact_ids:
                    raise ValueError("candidate source fact ids are not globally unique")
                seen_fact_ids.add(fact.fact_id)
    fact_ids = set(entry.evidence_ids)
    matching = tuple(
        observation
        for record in records
        for observation in record.observations
        if fact_ids.intersection(observation.fact_ids)
    )
    kinds = {observation.kind for observation in matching}
    marker_categories = {
        _SOURCE_CATEGORY_MARKERS[source.source_type]
        for record in records
        for source in record.sources
        if source.source_type in _SOURCE_CATEGORY_MARKERS
        and fact_ids.intersection(fact.fact_id for fact in source.facts)
    }
    marked_category = next(
        (category for category in _MARKER_PRIORITY if category in marker_categories), None
    )
    category: MemoryCategory
    if marked_category is not None:
        category = marked_category
    elif ObservationKind.RISK_REJECTION in kinds:
        category = MemoryCategory.RISK_REJECTION
    elif ObservationKind.OPEN_POSITION in kinds:
        category = MemoryCategory.POSITION_MANAGEMENT
    elif kinds.intersection({ObservationKind.FORECAST, ObservationKind.OUTCOME}):
        category = MemoryCategory.FORECAST_CALIBRATION
    else:
        category = MemoryCategory.GENERAL
    available_at = max((record.available_at for record in records), key=lambda item: item.value)
    return (
        category,
        available_at,
        max(1, len(matching)),
        MemoryCategory.UNRESOLVED_RISK in marker_categories
        or ObservationKind.OPEN_POSITION in kinds,
    )


def _derive_candidate_policy(
    candidate: MemoryCandidate,
    source_records: dict[str, DailyReflectionRecord] | None,
) -> MemoryCandidate:
    """Replace all provider-controlled ranking fields with source-derived values."""
    if source_records is None:
        return replace(
            candidate,
            entry=replace(candidate.entry, category=MemoryCategory.GENERAL),
            recurrence_count=1,
            unresolved=False,
            model_importance=0,
        )
    category, available_at, recurrence_count, unresolved = derive_entry_policy(
        candidate.entry, source_records
    )
    return replace(
        candidate,
        entry=replace(candidate.entry, category=category),
        available_at=available_at,
        recurrence_count=recurrence_count,
        unresolved=unresolved,
        model_importance=0,
    )


def select_entries(
    candidates: tuple[MemoryCandidate, ...],
    *,
    cutoff_at: UtcTimestamp,
    category_quotas: dict[MemoryCategory, int] | None = None,
    maximum_entries: int = MAX_ARTIFACT_ENTRIES,
    source_records: dict[str, DailyReflectionRecord] | None = None,
) -> tuple[MemoryEntry, ...]:
    if type(candidates) is not tuple or type(cutoff_at) is not UtcTimestamp:
        raise ValueError("selection input types are invalid")
    if type(maximum_entries) is not int or not 1 <= maximum_entries <= MAX_ARTIFACT_ENTRIES:
        raise ValueError("maximum_entries is outside its bound")
    quotas = DEFAULT_CATEGORY_QUOTAS if category_quotas is None else category_quotas
    if type(quotas) is not dict or set(quotas) != set(MemoryCategory):
        raise ValueError("category quotas must cover every exact category")
    if any(
        type(value) is not int or not 0 <= value <= MAX_ARTIFACT_ENTRIES
        for value in quotas.values()
    ):
        raise ValueError("category quota is outside its bound")

    merged = _merge_candidates(candidates)
    derived = tuple(_derive_candidate_policy(candidate, source_records) for candidate in merged)
    safe = [candidate for candidate in derived if candidate.available_at.value <= cutoff_at.value]
    scored = [(candidate, deterministic_importance(candidate)) for candidate in safe]
    scored.sort(
        key=lambda pair: (
            -pair[1],
            -int(pair[0].available_at.value.timestamp() * 1_000_000),
            pair[0].entry.category.value,
            pair[0].entry.dedup_key,
            pair[0].entry.source_record_ids,
        )
    )
    chosen: list[MemoryEntry] = []
    seen: set[str] = set()
    category_counts = {category: 0 for category in MemoryCategory}
    for candidate, score in scored:
        entry = candidate.entry
        if entry.dedup_key in seen or category_counts[entry.category] >= quotas[entry.category]:
            continue
        normalized = replace(entry, importance=score)
        # The frozen renderer is nine lines per entry plus five artifact header lines.
        if 5 + 9 * (len(chosen) + 1) > MAX_ARTIFACT_LINES:
            break
        chosen.append(normalized)
        seen.add(entry.dedup_key)
        category_counts[entry.category] += 1
        if len(chosen) >= maximum_entries:
            break
    return tuple(chosen)


def build_selected_artifact(
    candidates: tuple[MemoryCandidate, ...],
    *,
    source_records: dict[str, DailyReflectionRecord] | None = None,
    **artifact_fields: object,
) -> MemoryArtifact:
    cutoff = artifact_fields.get("cutoff_at")
    if type(cutoff) is not UtcTimestamp:
        raise ValueError("cutoff_at must be an exact UtcTimestamp")
    selected = select_entries(candidates, cutoff_at=cutoff, source_records=source_records)
    while selected:
        try:
            return build_memory_artifact(entries=selected, **artifact_fields)
        except ValueError as error:
            if "canonical byte bound" not in str(error):
                raise
            selected = selected[:-1]
    raise ValueError(f"no complete entry fits the {MAX_ARTIFACT_BYTES}-byte artifact bound")
