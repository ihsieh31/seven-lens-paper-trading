"""Ordered deterministic validation for P3-F reflection and memory candidates."""

from __future__ import annotations

from dataclasses import dataclass

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.memory.contracts import (
    ArtifactState,
    DailyReflectionRecord,
    FactKind,
    MemoryArtifact,
    MemoryEntry,
    MemoryInvalidationReason,
    ObservationKind,
)
from seven_lens.memory.fact_closure import reject_instruction_like_text, validate_text_fact_closure
from seven_lens.memory.selection import (
    MemoryCandidate,
    derive_entry_policy,
    deterministic_importance,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    stage: str
    code: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    artifact: MemoryArtifact
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues and self.artifact.state is ArtifactState.VALIDATED

    @property
    def invalidation_reason_code(self) -> str:
        """Return the closed DB reason code for an invalid deterministic result."""
        if self.valid or self.artifact.state is not ArtifactState.INVALID or not self.issues:
            raise ValueError("only an invalid result has an invalidation reason")
        reasons = {_INVALIDATION_REASONS.get(issue.stage) for issue in self.issues}
        if None in reasons:
            raise ValueError("validation issue has no invalidation reason")
        typed_reasons = {reason for reason in reasons if reason is not None}
        if len(typed_reasons) != 1:
            return MemoryInvalidationReason.INTEGRITY.value
        return next(iter(typed_reasons)).value


_INVALIDATION_REASONS: dict[str, MemoryInvalidationReason] = {
    "schema_resource": MemoryInvalidationReason.SCHEMA,
    "source_lineage": MemoryInvalidationReason.LINEAGE,
    "correction_lineage": MemoryInvalidationReason.LINEAGE,
    "point_in_time": MemoryInvalidationReason.FUTURE_LEAKAGE,
    "prompt_injection": MemoryInvalidationReason.PROMPT_INJECTION,
    "fact_token_closure": MemoryInvalidationReason.FACT_CLOSURE,
    "evidence_closure": MemoryInvalidationReason.FACT_CLOSURE,
    "deterministic_policy": MemoryInvalidationReason.BOUNDS,
    "canonical_integrity": MemoryInvalidationReason.INTEGRITY,
}


def _entry_text(entry: MemoryEntry) -> str:
    return "\n".join(
        (
            entry.observation,
            entry.reusable_lesson,
            *entry.applies_when,
            *entry.invalid_when,
        )
    )


def _validate_correction_graph(records: dict[str, DailyReflectionRecord]) -> str | None:
    """Return a stable failure code for untrusted persisted correction lineage."""
    targets: dict[str, str] = {}
    for record_id, record in records.items():
        correction_targets = {
            item.supersedes_record_id
            for item in record.observations
            if item.kind is ObservationKind.CORRECTION
        }
        if not correction_targets:
            continue
        if None in correction_targets or len(correction_targets) != 1:
            return "correction_target_invalid"
        possible_target = next(iter(correction_targets))
        if possible_target is None:
            return "correction_target_invalid"
        target_id = possible_target
        if target_id == record_id:
            return "correction_self_link"
        target = records.get(target_id)
        if target is None:
            return "correction_target_unknown"
        if (
            target.available_at.value > record.cutoff_at.value
            or target.cutoff_at.value > record.cutoff_at.value
            or target.created_at.value > record.created_at.value
        ):
            return "correction_target_future"
        targets[record_id] = target_id
    for origin in targets:
        visited: set[str] = set()
        cursor: str | None = origin
        while cursor is not None:
            if cursor in visited:
                return "correction_cycle"
            visited.add(cursor)
            cursor = targets.get(cursor)
    return None


class MemoryValidator:
    """Fail-closed validator; no model score can produce VALIDATED state."""

    def validate(
        self,
        artifact: MemoryArtifact,
        *,
        source_records: dict[str, DailyReflectionRecord],
        requested_cutoff: UtcTimestamp,
    ) -> ValidationResult:
        if type(artifact) is not MemoryArtifact or type(source_records) is not dict:
            raise ValueError("validator inputs must use exact contract types")
        if type(requested_cutoff) is not UtcTimestamp:
            raise ValueError("requested_cutoff must be an exact UtcTimestamp")
        if artifact.state is not ArtifactState.CANDIDATE:
            raise ValueError("only a candidate artifact can be validated")

        def invalid(stage: str, code: str) -> ValidationResult:
            return ValidationResult(
                artifact.with_state(ArtifactState.INVALID), (ValidationIssue(stage, code),)
            )

        # 1. Exact schema/types and 2. resource bounds are re-run even after construction.
        try:
            artifact.verify_integrity()
        except ValueError:
            return invalid("schema_resource", "artifact_integrity")

        # 3. Source authority and immutable lineage.
        if set(source_records) != set(artifact.source_record_ids):
            return invalid("source_lineage", "source_set_mismatch")
        try:
            seen_fact_ids: set[str] = set()
            for key, record in source_records.items():
                if type(record) is not DailyReflectionRecord:
                    raise ValueError
                if type(key) is not str or key != record.record_id:
                    return invalid("source_lineage", "source_key_mismatch")
                record.verify_integrity()
                for source in record.sources:
                    for fact in source.facts:
                        if fact.fact_id in seen_fact_ids:
                            return invalid("source_lineage", "duplicate_fact_id")
                        seen_fact_ids.add(fact.fact_id)
        except ValueError:
            return invalid("source_lineage", "source_integrity")
        correction_issue = _validate_correction_graph(source_records)
        if correction_issue is not None:
            return invalid("correction_lineage", correction_issue)

        # 4. Cutoff / available_at / future leakage.
        if artifact.cutoff_at != requested_cutoff:
            return invalid("point_in_time", "requested_cutoff_mismatch")
        if any(
            record.available_at.value > requested_cutoff.value
            or record.cutoff_at.value > artifact.cutoff_at.value
            for record in source_records.values()
        ):
            return invalid("point_in_time", "future_source")

        # 5. Explicit and textual prompt-injection isolation.
        if any(
            source.prompt_injection_flags
            for record in source_records.values()
            for source in record.sources
        ):
            return invalid("prompt_injection", "flagged_source")
        try:
            for entry in artifact.entries:
                reject_instruction_like_text((_entry_text(entry),))
        except ValueError:
            return invalid("prompt_injection", "instruction_like_content")

        # 6. Numeric/date/symbol/risk fact-token closure and 7. evidence closure.
        facts = {
            fact.fact_id: fact
            for record in source_records.values()
            for source in record.sources
            for fact in source.facts
        }
        for entry in artifact.entries:
            entry_records = [source_records[item] for item in entry.source_record_ids]
            allowed = {
                fact.fact_id
                for record in entry_records
                for source in record.sources
                for fact in source.facts
            }
            if not set(entry.evidence_ids).issubset(allowed):
                return invalid("evidence_closure", "foreign_fact_id")
            try:
                validate_text_fact_closure(
                    (_entry_text(entry),),
                    available_facts=facts,
                    cited_fact_ids=entry.evidence_ids,
                    risk_reason_values=entry.risk_reason_codes,
                )
            except ValueError as error:
                message = str(error)
                if "number" in message or "numeric" in message:
                    code = "unreferenced_number"
                elif "date" in message:
                    code = "unreferenced_date"
                else:
                    code = "unreferenced_symbol_or_risk_reason"
                return invalid("fact_token_closure", code)
            reason_facts = {
                facts[fact_id].value
                for fact_id in entry.evidence_ids
                if facts[fact_id].kind is FactKind.RISK_REASON
            }
            if not set(entry.risk_reason_codes).issubset(reason_facts):
                return invalid("evidence_closure", "foreign_risk_reason")
            category, available_at, recurrence_count, unresolved = derive_entry_policy(
                entry, source_records
            )
            if entry.category is not category:
                return invalid("deterministic_policy", "provider_category_override")
            expected_importance = deterministic_importance(
                MemoryCandidate(
                    entry,
                    available_at,
                    recurrence_count=recurrence_count,
                    unresolved=unresolved,
                )
            )
            if entry.importance != expected_importance:
                return invalid("deterministic_policy", "provider_importance_override")

        # 8. Canonical bytes and hash already verified at the first stage; verify once more after
        # all lineage reads to make mutation/collision bugs fail closed.
        try:
            artifact.verify_integrity()
        except ValueError:
            return invalid("canonical_integrity", "artifact_changed")
        return ValidationResult(artifact.with_state(ArtifactState.VALIDATED), ())
