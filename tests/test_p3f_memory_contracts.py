from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.memory.contracts import (
    MEMORY_SCHEMA_VERSION,
    ArtifactState,
    DailyReflectionRecord,
    FactKind,
    FactRef,
    ForecastObservation,
    MemoryArtifact,
    MemoryCategory,
    MemoryEntry,
    ObservationKind,
    OutcomeObservation,
    ReflectionObservation,
    ReflectionSourceRef,
    RiskRejectionObservation,
    build_daily_reflection,
    build_memory_artifact,
)
from seven_lens.memory.reflection import (
    InMemoryReflectionRepository,
    ReflectionPipeline,
    ReflectionRequest,
    ResolvedReflectionSource,
    ScriptedReflectionProvider,
    SourceAuthority,
    TrustedReflectionSourceResolver,
)

SOURCE_BYTES = b"approved decision source fixture"


def ts(minutes: int = 0) -> UtcTimestamp:
    return UtcTimestamp(datetime(2026, 8, 24, 12, tzinfo=UTC) + timedelta(minutes=minutes))


def source(*, available: int = 0, flags: tuple[str, ...] = ()) -> ReflectionSourceRef:
    return ReflectionSourceRef(
        "decision.fact-source",
        "approved_decision",
        hashlib.sha256(SOURCE_BYTES).hexdigest(),
        ts(available),
        (
            FactRef("fact.symbol", FactKind.SYMBOL, "MSFT"),
            FactRef("fact.loss", FactKind.NUMBER, "12.50"),
            FactRef("fact.date", FactKind.DATE, "2026-08-24"),
            FactRef("fact.risk", FactKind.RISK_REASON, "BORROW"),
        ),
        flags,
    )


def resolver_for(*sources: ReflectionSourceRef) -> TrustedReflectionSourceResolver:
    return TrustedReflectionSourceResolver({item: SOURCE_BYTES for item in sources})


def observation(
    *,
    text: str = "MSFT lost 12.50 on 2026-08-24 after BORROW rejection",
    kind: ObservationKind = ObservationKind.RISK_REJECTION,
    fact_ids: tuple[str, ...] = ("fact.symbol", "fact.loss", "fact.date", "fact.risk"),
    supersedes: str | None = None,
) -> ReflectionObservation:
    return ReflectionObservation(
        kind,
        text,
        "Recheck borrow before increasing exposure",
        ("Borrow is constrained",),
        ("Borrow is confirmed",),
        fact_ids,
        supersedes,
    )


def record(
    *,
    record_id: str = "reflection.1",
    sources: tuple[ReflectionSourceRef, ...] | None = None,
    observations: tuple[ReflectionObservation, ...] | None = None,
    created: int = 1,
    cutoff: int = 0,
) -> DailyReflectionRecord:
    return build_daily_reflection(
        record_id=record_id,
        schema_version=MEMORY_SCHEMA_VERSION,
        created_at=ts(created),
        available_at=ts(created),
        as_of=ts(cutoff),
        cutoff_at=ts(cutoff),
        proposal_id="proposal.1",
        decision_id="decision.1",
        research_bundle_hash="b" * 64,
        portfolio_snapshot_hash="c" * 64,
        sources=sources or (source(),),
        observations=observations or (observation(),),
        prompt_version="p3f.prompt.1",
        model_version="scripted.1",
        provider_version="offline.1",
        data_version="fixture.1",
        memory_version="p3f.1",
    )


def entry(
    *,
    importance: int = 80,
    text: str = "MSFT lost 12.50 on 2026-08-24 after BORROW rejection",
    evidence: tuple[str, ...] = ("fact.symbol", "fact.loss", "fact.date", "fact.risk"),
    source_ids: tuple[str, ...] = ("reflection.1",),
) -> MemoryEntry:
    return MemoryEntry(
        MemoryCategory.RISK_REJECTION,
        importance,
        text,
        "Recheck borrow before increasing exposure",
        ("Borrow is constrained",),
        ("Borrow is confirmed",),
        evidence,
        source_ids,
        ("BORROW",),
    )


def artifact(
    *,
    artifact_id: str = "memory.1",
    entries: tuple[MemoryEntry, ...] | None = None,
    source_ids: tuple[str, ...] = ("reflection.1",),
    cutoff: int = 2,
    created: int = 3,
    previous: str | None = None,
) -> MemoryArtifact:
    return build_memory_artifact(
        artifact_id=artifact_id,
        schema_version=MEMORY_SCHEMA_VERSION,
        created_at=ts(created),
        cutoff_at=ts(cutoff),
        source_record_ids=source_ids,
        previous_artifact_id=previous,
        entries=entries or (entry(),),
        prompt_version="p3f.prompt.1",
        model_version="scripted.1",
        provider_version="offline.1",
    )


def test_reflection_is_frozen_domain_hashed_and_rejects_future_source() -> None:
    item = record()
    item.verify_integrity()
    with pytest.raises(FrozenInstanceError):
        item.record_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="unavailable at cutoff"):
        record(sources=(source(available=1),))


def test_reflection_rejects_foreign_fact_and_timestamp_reordering() -> None:
    with pytest.raises(ValueError, match="foreign fact"):
        record(observations=(observation(fact_ids=("fact.foreign",)),))
    with pytest.raises(ValueError, match="timestamps"):
        record(created=-1)


def test_fact_contract_rejects_impossible_calendar_date() -> None:
    with pytest.raises(ValueError, match="real calendar"):
        FactRef("fact.bad-date", FactKind.DATE, "2026-02-30")


def test_correction_is_a_new_linked_record_and_original_hash_stays_unchanged() -> None:
    original = record()
    before = original.content_hash
    correction = record(
        record_id="reflection.2",
        created=2,
        cutoff=1,
        observations=(
            observation(
                text="MSFT lost 12.50 on 2026-08-24 after BORROW correction",
                kind=ObservationKind.CORRECTION,
                supersedes=original.record_id,
            ),
        ),
    )
    assert correction.observations[0].supersedes_record_id == original.record_id
    assert original.content_hash == before
    assert correction.content_hash != original.content_hash
    repository = InMemoryReflectionRepository()
    repository.append(original)
    repository.append(correction)
    assert repository.get(original.record_id) is original
    assert original.content_hash == before


def test_typed_observation_subclasses_reject_mismatched_kind() -> None:
    values = (
        "Observation",
        "Reusable lesson",
        ("Condition",),
        ("Invalidator",),
        ("fact.symbol",),
        None,
    )
    with pytest.raises(ValueError, match="does not match"):
        ForecastObservation(ObservationKind.OUTCOME, *values)
    with pytest.raises(ValueError, match="does not match"):
        OutcomeObservation(ObservationKind.RISK_REJECTION, *values)
    with pytest.raises(ValueError, match="does not match"):
        RiskRejectionObservation(ObservationKind.FORECAST, *values)


def test_reflection_record_rejects_arbitrary_observation_subclass() -> None:
    class ForeignObservation(ReflectionObservation):
        pass

    with pytest.raises(ValueError, match="observations must contain"):
        record(
            observations=(
                ForeignObservation(
                    ObservationKind.RISK_REJECTION,
                    observation().observation,
                    observation().reusable_lesson,
                    observation().applies_when,
                    observation().invalid_when,
                    observation().fact_ids,
                ),
            )
        )


def test_correction_rejects_self_unknown_and_mixed_observations() -> None:
    with pytest.raises(ValueError, match="supersede itself"):
        record(
            observations=(observation(kind=ObservationKind.CORRECTION, supersedes="reflection.1"),)
        )
    with pytest.raises(ValueError, match="cannot mix"):
        record(
            record_id="reflection.2",
            observations=(
                observation(kind=ObservationKind.CORRECTION, supersedes="reflection.1"),
                observation(),
            ),
        )
    repository = InMemoryReflectionRepository()
    unknown = record(
        record_id="reflection.2",
        observations=(
            observation(kind=ObservationKind.CORRECTION, supersedes="reflection.unknown"),
        ),
    )
    with pytest.raises(RuntimeError, match="target is unknown"):
        repository.append(unknown)


def test_append_rejects_preexisting_correction_cycle_drift() -> None:
    first = record(
        record_id="reflection.a",
        created=0,
        cutoff=0,
        observations=(observation(kind=ObservationKind.CORRECTION, supersedes="reflection.b"),),
    )
    second = record(
        record_id="reflection.b",
        created=0,
        cutoff=0,
        observations=(observation(kind=ObservationKind.CORRECTION, supersedes="reflection.a"),),
    )
    repository = InMemoryReflectionRepository()
    # Failure injection models a corrupted persistence adapter. Normal append order cannot create
    # this state because unknown targets fail closed, but append must not trust existing rows.
    repository._records[first.record_id] = first
    repository._records[second.record_id] = second
    third = record(
        record_id="reflection.c",
        created=1,
        cutoff=0,
        observations=(observation(kind=ObservationKind.CORRECTION, supersedes="reflection.a"),),
    )
    with pytest.raises(RuntimeError, match="contains a cycle"):
        repository.append(third)


def test_append_only_repository_is_same_hash_idempotent_and_rejects_collision() -> None:
    repository = InMemoryReflectionRepository()
    original = record()
    repository.append(original)
    repository.append(original)
    collision = record(observations=(replace(observation(), reusable_lesson="Different lesson"),))
    with pytest.raises(RuntimeError, match="identity collision"):
        repository.append(collision)
    assert repository.get(original.record_id) == original


def test_pipeline_replays_without_provider_and_rechecks_source_identity() -> None:
    provider = ScriptedReflectionProvider((observation(),))
    repository = InMemoryReflectionRepository()
    pipeline = ReflectionPipeline(provider, repository, resolver_for(source()))
    fields = {
        "record_id": "reflection.1",
        "schema_version": MEMORY_SCHEMA_VERSION,
        "as_of": ts(),
        "cutoff_at": ts(),
        "proposal_id": "proposal.1",
        "decision_id": "decision.1",
        "research_bundle_hash": "b" * 64,
        "portfolio_snapshot_hash": "c" * 64,
        "prompt_version": "p3f.prompt.1",
        "model_version": "scripted.1",
        "provider_version": "offline.1",
        "data_version": "fixture.1",
        "memory_version": "p3f.1",
    }
    first = pipeline.run(sources=(source(),), now=ts(1), **fields)
    assert pipeline.run(sources=(source(),), now=ts(1), **fields) == first
    assert len(provider.calls) == 1
    with pytest.raises(RuntimeError, match="lineage changed"):
        pipeline.run(sources=(replace(source(), content_hash="d" * 64),), now=ts(1), **fields)


@pytest.mark.parametrize(
    "changed_source",
    [
        replace(source(), source_id="decision.other-source"),
        replace(source(), available_at=ts(-1)),
        replace(
            source(),
            facts=(
                FactRef("fact.symbol", FactKind.SYMBOL, "AAPL"),
                *source().facts[1:],
            ),
        ),
    ],
)
def test_resume_rejects_same_hash_with_changed_full_source_lineage(
    changed_source: ReflectionSourceRef,
) -> None:
    provider = ScriptedReflectionProvider((observation(),))
    repository = InMemoryReflectionRepository()
    pipeline = ReflectionPipeline(provider, repository, resolver_for(source()))
    fields = {
        "record_id": "reflection.1",
        "schema_version": MEMORY_SCHEMA_VERSION,
        "as_of": ts(),
        "cutoff_at": ts(),
        "proposal_id": "proposal.1",
        "decision_id": "decision.1",
        "research_bundle_hash": "b" * 64,
        "portfolio_snapshot_hash": "c" * 64,
        "prompt_version": "p3f.prompt.1",
        "model_version": "scripted.1",
        "provider_version": "offline.1",
        "data_version": "fixture.1",
        "memory_version": "p3f.1",
    }
    pipeline.run(sources=(source(),), now=ts(1), **fields)
    with pytest.raises(RuntimeError, match="lineage changed"):
        pipeline.run(sources=(changed_source,), now=ts(1), **fields)


def test_resume_rejects_changed_requested_record_fields_and_future_visibility() -> None:
    provider = ScriptedReflectionProvider((observation(),))
    repository = InMemoryReflectionRepository()
    pipeline = ReflectionPipeline(provider, repository, resolver_for(source()))
    fields = {
        "record_id": "reflection.1",
        "schema_version": MEMORY_SCHEMA_VERSION,
        "as_of": ts(),
        "cutoff_at": ts(),
        "proposal_id": "proposal.1",
        "decision_id": "decision.1",
        "research_bundle_hash": "b" * 64,
        "portfolio_snapshot_hash": "c" * 64,
        "prompt_version": "p3f.prompt.1",
        "model_version": "scripted.1",
        "provider_version": "offline.1",
        "data_version": "fixture.1",
        "memory_version": "p3f.1",
    }
    pipeline.run(sources=(source(),), now=ts(1), **fields)
    with pytest.raises(RuntimeError, match="lineage changed"):
        pipeline.run(sources=(source(),), now=ts(1), **(fields | {"decision_id": "decision.2"}))
    with pytest.raises(RuntimeError, match="lineage changed"):
        pipeline.run(sources=(source(),), now=ts(), **fields)


def test_memory_artifact_exact_line_hash_and_state_transition() -> None:
    item = artifact()
    assert item.line_count == 14
    assert len(item.render_lines()) == item.line_count
    item.verify_integrity()
    validated = item.with_state(ArtifactState.VALIDATED)
    assert validated.content_hash == item.content_hash
    assert validated.with_state(ArtifactState.CURRENT).state is ArtifactState.CURRENT
    with pytest.raises(ValueError, match="transition"):
        item.with_state(ArtifactState.CURRENT)


def test_memory_artifact_rejects_513_entries_and_foreign_lineage() -> None:
    with pytest.raises(ValueError, match="item bound"):
        artifact(entries=tuple(entry() for _ in range(513)))
    with pytest.raises(ValueError, match="foreign source-record"):
        artifact(entries=(entry(source_ids=("reflection.foreign",)),))


def test_memory_entry_rejects_overlong_essential_lineage_instead_of_truncating() -> None:
    with pytest.raises(ValueError, match="item bound"):
        entry(evidence=tuple(f"fact.{index}" for index in range(33)))


def test_multiline_field_cannot_bypass_deterministic_line_count() -> None:
    with pytest.raises(ValueError, match="single-line"):
        entry(text="one\ntwo")


def test_exact_4001_rendered_lines_are_rejected() -> None:
    with pytest.raises(ValueError, match="line count"):
        artifact(entries=tuple(entry() for _ in range(444)))


def test_canonical_512kib_plus_payload_is_rejected() -> None:
    large = replace(
        entry(),
        observation="x" * 2_048,
        reusable_lesson="y" * 2_048,
        applies_when=("z" * 2_048,),
        invalid_when=("w" * 2_048,),
    )
    with pytest.raises(ValueError, match="bound"):
        artifact(entries=tuple(large for _ in range(100)))


def test_reflection_pipeline_rejects_invented_fact_before_append() -> None:
    provider = ScriptedReflectionProvider((observation(text="MSFT lost 99.99"),))
    repository = InMemoryReflectionRepository()
    pipeline = ReflectionPipeline(provider, repository, resolver_for(source()))
    with pytest.raises(ValueError, match="unreferenced number"):
        pipeline.run(
            sources=(source(),),
            now=ts(1),
            record_id="reflection.1",
            schema_version=MEMORY_SCHEMA_VERSION,
            as_of=ts(),
            cutoff_at=ts(),
            proposal_id="proposal.1",
            decision_id="decision.1",
            research_bundle_hash="b" * 64,
            portfolio_snapshot_hash="c" * 64,
            prompt_version="p3f.prompt.1",
            model_version="scripted.1",
            provider_version="offline.1",
            data_version="fixture.1",
            memory_version="p3f.1",
        )
    assert repository.get("reflection.1") is None


def test_reflection_provider_receives_bounded_typed_fact_values_with_redacted_repr() -> None:
    provider = ScriptedReflectionProvider((observation(),))
    repository = InMemoryReflectionRepository()
    ReflectionPipeline(provider, repository, resolver_for(source())).run(
        sources=(source(),),
        now=ts(1),
        record_id="reflection.1",
        schema_version=MEMORY_SCHEMA_VERSION,
        as_of=ts(),
        cutoff_at=ts(),
        proposal_id="proposal.1",
        decision_id="decision.1",
        research_bundle_hash="b" * 64,
        portfolio_snapshot_hash="c" * 64,
        prompt_version="p3f.prompt.1",
        model_version="scripted.1",
        provider_version="offline.1",
        data_version="fixture.1",
        memory_version="p3f.1",
    )
    request = provider.calls[0]
    assert {(fact.kind, fact.value) for fact in request.facts} >= {
        (FactKind.SYMBOL, "MSFT"),
        (FactKind.NUMBER, "12.50"),
    }
    assert repr(request) == "ReflectionRequest(<redacted>)"


def reflection_fields() -> dict[str, object]:
    return {
        "record_id": "reflection.new",
        "schema_version": MEMORY_SCHEMA_VERSION,
        "as_of": ts(),
        "cutoff_at": ts(),
        "proposal_id": "proposal.1",
        "decision_id": "decision.1",
        "research_bundle_hash": "b" * 64,
        "portfolio_snapshot_hash": "c" * 64,
        "prompt_version": "p3f.prompt.1",
        "model_version": "scripted.1",
        "provider_version": "offline.1",
        "data_version": "fixture.1",
        "memory_version": "p3f.1",
    }


def test_initial_reflection_requires_approved_exact_byte_source_resolver() -> None:
    approved_source = source()
    without_resolver = ReflectionPipeline(
        ScriptedReflectionProvider((observation(),)), InMemoryReflectionRepository()
    )
    with pytest.raises(RuntimeError, match="authority resolver is required"):
        without_resolver.run(sources=(approved_source,), now=ts(1), **reflection_fields())

    wrong_resolver = TrustedReflectionSourceResolver(
        {
            replace(
                approved_source, content_hash=hashlib.sha256(b"wrong bytes").hexdigest()
            ): b"wrong bytes"
        }
    )
    with pytest.raises(RuntimeError, match="lacks approved authority"):
        ReflectionPipeline(
            ScriptedReflectionProvider((observation(),)),
            InMemoryReflectionRepository(),
            wrong_resolver,
        ).run(sources=(approved_source,), now=ts(1), **reflection_fields())

    changed_facts = replace(
        approved_source,
        facts=(
            FactRef("fact.changed", FactKind.TEXT, "changed"),
            *approved_source.facts[1:],
        ),
    )
    with pytest.raises(RuntimeError, match="lacks approved authority"):
        ReflectionPipeline(
            ScriptedReflectionProvider((observation(),)),
            InMemoryReflectionRepository(),
            resolver_for(approved_source),
        ).run(sources=(changed_facts,), now=ts(1), **reflection_fields())


class ChangingResolver:
    def __init__(self, approved_source: ReflectionSourceRef) -> None:
        self.source = approved_source
        self.calls = 0

    def read_approved(self, source_ref: ReflectionSourceRef) -> ResolvedReflectionSource:
        self.calls += 1
        content = SOURCE_BYTES if self.calls == 1 else b"changed source bytes"
        return ResolvedReflectionSource(
            source_ref,
            content,
            SourceAuthority.APPROVED,
        )


class ResumeTamperingResolver:
    def __init__(self) -> None:
        self.calls = 0

    def read_approved(self, source_ref: ReflectionSourceRef) -> ResolvedReflectionSource:
        self.calls += 1
        content = SOURCE_BYTES if self.calls <= 2 else b"tampered resume bytes"
        return ResolvedReflectionSource(
            source_ref,
            content,
            SourceAuthority.APPROVED,
        )


def test_pipeline_rereads_authority_and_checks_injected_clock_deadline_before_persist() -> None:
    approved_source = source()
    with pytest.raises(RuntimeError, match="authority/readback"):
        ReflectionPipeline(
            ScriptedReflectionProvider((observation(),)),
            InMemoryReflectionRepository(),
            ChangingResolver(approved_source),
        ).run(sources=(approved_source,), now=ts(1), **reflection_fields())

    deadline_open = [True]

    class DeadlineClosingProvider:
        def reflect(self, request: ReflectionRequest) -> tuple[ReflectionObservation, ...]:
            deadline_open[0] = False
            return (observation(),)

    with pytest.raises(TimeoutError, match="deadline"):
        ReflectionPipeline(
            DeadlineClosingProvider(),
            InMemoryReflectionRepository(),
            resolver_for(approved_source),
            deadline=lambda: deadline_open[0],
        ).run(sources=(approved_source,), now=ts(1), **reflection_fields())

    with pytest.raises(ValueError, match="became invalid"):
        ReflectionPipeline(
            ScriptedReflectionProvider((observation(),)),
            InMemoryReflectionRepository(),
            resolver_for(approved_source),
            clock=lambda: ts(-1),
        ).run(sources=(approved_source,), now=ts(1), **reflection_fields())


def test_resume_revalidates_approved_source_authority_without_calling_provider() -> None:
    approved_source = source()
    resolver = ResumeTamperingResolver()
    provider = ScriptedReflectionProvider((observation(),))
    pipeline = ReflectionPipeline(
        provider,
        InMemoryReflectionRepository(),
        resolver,
    )
    pipeline.run(sources=(approved_source,), now=ts(1), **reflection_fields())
    with pytest.raises(RuntimeError, match="authority/readback"):
        pipeline.run(sources=(approved_source,), now=ts(1), **reflection_fields())
    assert len(provider.calls) == 1


def test_pipeline_rejects_provider_observation_subclass() -> None:
    subclassed = RiskRejectionObservation(
        ObservationKind.RISK_REJECTION,
        observation().observation,
        observation().reusable_lesson,
        observation().applies_when,
        observation().invalid_when,
        observation().fact_ids,
    )
    with pytest.raises(ValueError, match="invalid observation"):
        ReflectionPipeline(
            ScriptedReflectionProvider((subclassed,)),
            InMemoryReflectionRepository(),
            resolver_for(source()),
        ).run(sources=(source(),), now=ts(1), **reflection_fields())


class SubclassReadbackRepository:
    class ForeignRecord(DailyReflectionRecord):
        pass

    def __init__(self) -> None:
        self.stored: DailyReflectionRecord | None = None

    def append(self, record_value: DailyReflectionRecord) -> None:
        forged = object.__new__(self.ForeignRecord)
        for name in DailyReflectionRecord.__dataclass_fields__:
            object.__setattr__(forged, name, getattr(record_value, name))
        self.stored = forged

    def get(self, record_id: str) -> DailyReflectionRecord | None:
        return self.stored


def test_pipeline_requires_exact_integrity_checked_persistence_readback() -> None:
    with pytest.raises(RuntimeError, match="persistence verification"):
        ReflectionPipeline(
            ScriptedReflectionProvider((observation(),)),
            SubclassReadbackRepository(),
            resolver_for(source()),
        ).run(sources=(source(),), now=ts(1), **reflection_fields())


def test_daily_open_position_source_requires_open_position_observation() -> None:
    position_source = replace(source(), source_id="position.MSFT", source_type="open_position")
    with pytest.raises(ValueError, match="omitted an open-position"):
        ReflectionPipeline(
            ScriptedReflectionProvider((observation(),)),
            InMemoryReflectionRepository(),
            resolver_for(position_source),
        ).run(sources=(position_source,), now=ts(1), **reflection_fields())

    open_observation = observation(kind=ObservationKind.OPEN_POSITION)
    result = ReflectionPipeline(
        ScriptedReflectionProvider((open_observation,)),
        InMemoryReflectionRepository(),
        resolver_for(position_source),
    ).run(sources=(position_source,), now=ts(1), **reflection_fields())
    assert result.observations[0].kind is ObservationKind.OPEN_POSITION


def test_repository_rejects_correction_target_not_available_at_correction_cutoff() -> None:
    repository = InMemoryReflectionRepository()
    original = record(record_id="reflection.original", created=2, cutoff=1)
    repository.append(original)
    correction = record(
        record_id="reflection.fix",
        created=3,
        cutoff=0,
        observations=(
            observation(
                kind=ObservationKind.CORRECTION,
                supersedes=original.record_id,
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="chronology"):
        repository.append(correction)


def test_artifact_rejects_rendered_line_over_2048_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="rendered line exceeds byte bound"):
        artifact(entries=(replace(entry(), applies_when=("x" * 1_100, "y" * 1_100)),))
