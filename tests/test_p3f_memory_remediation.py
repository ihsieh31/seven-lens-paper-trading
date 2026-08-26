from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.content_store import FileContentStore
from seven_lens.infrastructure.postgres_memory import (
    PostgresMemoryPromotionCoordinator,
    PostgresMemoryRepository,
)
from seven_lens.memory import curation as curation_module
from seven_lens.memory.contracts import (
    ArtifactState,
    DailyReflectionRecord,
    FactRef,
    MemoryArtifact,
    MemoryCategory,
    MemoryEntry,
    ReflectionSourceRef,
)
from seven_lens.memory.curation import (
    CurationAuditError,
    CurationAuditRecord,
    CurationPipeline,
    InMemoryAppendOnlyCurationAuditRepository,
    ScriptedCurationProvider,
)
from seven_lens.memory.reflection import (
    InMemoryReflectionRepository,
    ReflectionPipeline,
    ResolvedReflectionSource,
    ScriptedReflectionProvider,
    TrustedReflectionSourceResolver,
)
from seven_lens.memory.selection import MemoryCandidate, select_entries
from seven_lens.memory.validation import MemoryValidator, ValidationResult
from test_p3f_memory_contracts import entry, observation, record, source, ts


def _curation_fields(
    record_id: str = "reflection.1", execution_id: str = "test.execution.1"
) -> dict[str, object]:
    return {
        "execution_id": execution_id,
        "artifact_id": "memory.1",
        "schema_version": "1.0.0",
        "created_at": ts(3),
        "cutoff_at": ts(2),
        "source_record_ids": (record_id,),
        "previous_artifact_id": None,
        "prompt_version": "p3f.prompt.1",
        "model_version": "scripted.1",
        "provider_version": "offline.1",
    }


def test_curation_requires_audit_capability_before_provider() -> None:
    called = False

    class Provider:
        def curate(self, _request: Any) -> tuple[MemoryCandidate, ...]:
            nonlocal called
            called = True
            return ()

    with pytest.raises(ValueError, match="audit capability"):
        CurationPipeline(Provider(), MemoryValidator(), None)  # type: ignore[arg-type]
    assert not called


def test_prepare_exposes_candidate_only_and_run_audit_has_report_hash() -> None:
    source_record = record()
    audits = InMemoryAppendOnlyCurationAuditRepository()
    pipeline = CurationPipeline(
        ScriptedCurationProvider((MemoryCandidate(entry(), ts(1)),)),
        MemoryValidator(),
        audits,
    )
    prepared = pipeline.prepare(
        source_records=(source_record,),
        **_curation_fields(),
    )
    assert prepared.artifact.state is ArtifactState.CANDIDATE
    assert not hasattr(prepared, "result")
    assert audits.records == ()
    result = CurationPipeline(
        ScriptedCurationProvider((MemoryCandidate(entry(), ts(1)),)),
        MemoryValidator(),
        audits,
    ).run(
        source_records=(source_record,),
        **_curation_fields(execution_id="test.execution.2"),
    )
    assert result.valid
    assert len(audits.records) == 1
    assert audits.records[0].report_hash is not None
    assert "prompt" not in repr(audits.records[0].db_parameters()).lower()


def test_same_execution_identity_rejects_changed_output_metadata() -> None:
    source_record = record()
    audits = InMemoryAppendOnlyCurationAuditRepository()

    class Provider:
        calls = 0

        def curate(self, _request: Any) -> tuple[MemoryCandidate, ...]:
            self.calls += 1
            changed = replace(entry(), reusable_lesson="Recheck borrow before increasing exposure ")
            return (MemoryCandidate(entry() if self.calls == 1 else changed, ts(1)),)

    provider = Provider()
    pipeline = CurationPipeline(provider, MemoryValidator(), audits)
    pipeline.run(source_records=(source_record,), **_curation_fields())
    with pytest.raises(CurationAuditError, match="append failed"):
        pipeline.run(source_records=(source_record,), **_curation_fields())
    assert len(audits.records) == 1


@pytest.mark.parametrize("clock_values", [(100, 99), (0, 900_001)])
def test_clock_rollback_or_latency_overflow_fails_closed(clock_values: tuple[int, int]) -> None:
    source_record = record()
    audits = InMemoryAppendOnlyCurationAuditRepository()
    values = iter(clock_values)
    pipeline = CurationPipeline(
        ScriptedCurationProvider((MemoryCandidate(entry(), ts(1)),)),
        MemoryValidator(),
        audits,
        clock=lambda: next(values),
    )
    with pytest.raises(CurationAuditError, match="latency"):
        pipeline.run(source_records=(source_record,), **_curation_fields())
    assert audits.records == ()


def test_candidate_aggregate_bytes_and_nodes_reject_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_record = record()
    audits = InMemoryAppendOnlyCurationAuditRepository()

    class BytesOverflowProvider:
        def curate(self, _request: Any) -> tuple[MemoryCandidate, ...]:
            monkeypatch.setattr(curation_module, "MAX_CURATION_BYTES", 1)
            return (MemoryCandidate(entry(), ts(1)),)

    with pytest.raises(ValueError, match="candidate aggregate"):
        CurationPipeline(
            BytesOverflowProvider(),
            MemoryValidator(),
            audits,
        ).run(source_records=(source_record,), **_curation_fields())
    monkeypatch.setattr(curation_module, "MAX_CURATION_BYTES", 8 * 1024 * 1024)

    class NodesOverflowProvider:
        def curate(self, _request: Any) -> tuple[MemoryCandidate, ...]:
            monkeypatch.setattr(curation_module, "MAX_CURATION_NODES", 1)
            return (MemoryCandidate(entry(), ts(1)),)

    with pytest.raises(ValueError, match="candidate aggregate"):
        CurationPipeline(
            NodesOverflowProvider(),
            MemoryValidator(),
            audits,
        ).run(
            source_records=(source_record,),
            **_curation_fields("reflection.1", "test.execution.2"),
        )


def test_reflection_rejects_duplicate_envelope_before_resolver_or_provider() -> None:
    calls = {"resolver": 0, "provider": 0}

    class Resolver:
        def read_approved(self, item: ReflectionSourceRef) -> ResolvedReflectionSource:
            calls["resolver"] += 1
            raise AssertionError(f"resolver was called for {item}")

    class Provider:
        def reflect(self, request: Any) -> tuple[Any, ...]:
            calls["provider"] += 1
            return (observation(),)

    fields = {
        "record_id": "reflection.dup",
        "schema_version": "1.0.0",
        "as_of": ts(),
        "cutoff_at": ts(),
        "proposal_id": "proposal.dup",
        "decision_id": "decision.dup",
        "research_bundle_hash": "b" * 64,
        "portfolio_snapshot_hash": "c" * 64,
        "prompt_version": "p3f.prompt.1",
        "model_version": "scripted.1",
        "provider_version": "offline.1",
        "data_version": "fixture.1",
        "memory_version": "p3f.1",
    }
    pipeline = ReflectionPipeline(Provider(), InMemoryReflectionRepository(), Resolver())
    with pytest.raises(ValueError, match="source ids must be unique"):
        pipeline.run(sources=(source(), source()), now=ts(1), **fields)
    assert calls == {"resolver": 0, "provider": 0}


def test_reflection_rejects_more_than_64_provider_observations() -> None:
    provider = ScriptedReflectionProvider(tuple(observation() for _ in range(65)))
    item = source()
    resolver = TrustedReflectionSourceResolver({item: b"approved decision source fixture"})
    fields = {
        "record_id": "reflection.overflow",
        "schema_version": "1.0.0",
        "as_of": ts(),
        "cutoff_at": ts(),
        "proposal_id": "proposal.overflow",
        "decision_id": "decision.overflow",
        "research_bundle_hash": "b" * 64,
        "portfolio_snapshot_hash": "c" * 64,
        "prompt_version": "p3f.prompt.1",
        "model_version": "scripted.1",
        "provider_version": "offline.1",
        "data_version": "fixture.1",
        "memory_version": "p3f.1",
    }
    with pytest.raises(ValueError, match="non-empty exact tuple"):
        ReflectionPipeline(provider, InMemoryReflectionRepository(), resolver).run(
            sources=(item,), now=ts(1), **fields
        )


def test_dedup_key_is_nfkc_casefold_for_all_semantic_text() -> None:
    first = MemoryEntry(
        MemoryCategory.GENERAL,
        1,
        "Alpha",
        "Use this lesson",
        ("When available",),
        ("Never forced",),
        ("fact.alpha",),
        ("record.alpha",),
    )
    second = replace(
        first,
        observation="ＡＬＰＨＡ",  # noqa: RUF001 - fullwidth input is the NFKC regression case
        reusable_lesson="USE THIS LESSON",
        applies_when=("Ｗｈｅｎ ａｖａｉｌａｂｌｅ",),  # noqa: RUF001 - NFKC regression case
        invalid_when=("Ｎｅｖｅｒ ｆｏｒｃｅｄ",),  # noqa: RUF001 - NFKC regression case
    )
    assert first.dedup_key == second.dedup_key


def _second_record() -> DailyReflectionRecord:
    first_source = source()
    facts = tuple(
        FactRef(f"{fact.fact_id}.two", fact.kind, fact.value) for fact in first_source.facts
    )
    second_source = replace(first_source, source_id="decision.fact-source.two", facts=facts)
    second_observation = replace(observation(), fact_ids=tuple(fact.fact_id for fact in facts))
    return record(
        record_id="reflection.2",
        sources=(second_source,),
        observations=(second_observation,),
    )


def test_selection_merges_repeated_risk_lineage_and_recomputes_recurrence() -> None:
    first = record()
    second = _second_record()
    first_entry = entry()
    second_entry = replace(
        first_entry,
        evidence_ids=tuple(fact.fact_id for fact in second.sources[0].facts),
        source_record_ids=(second.record_id,),
    )
    selected = select_entries(
        (
            MemoryCandidate(first_entry, ts(1)),
            MemoryCandidate(second_entry, ts(1)),
        ),
        cutoff_at=ts(2),
        source_records={first.record_id: first, second.record_id: second},
    )
    assert len(selected) == 1
    assert set(selected[0].source_record_ids) == {first.record_id, second.record_id}
    assert len(selected[0].evidence_ids) == 8
    assert selected[0].importance == 82
    assert (
        select_entries(
            tuple(
                reversed(
                    (
                        MemoryCandidate(first_entry, ts(1)),
                        MemoryCandidate(second_entry, ts(1)),
                    )
                )
            ),
            cutoff_at=ts(2),
            source_records={first.record_id: first, second.record_id: second},
        )
        == selected
    )


def test_selection_rejects_merged_lineage_overflow_instead_of_truncating() -> None:
    candidates = tuple(
        MemoryCandidate(
            MemoryEntry(
                MemoryCategory.GENERAL,
                1,
                "same",
                "lesson",
                (),
                (),
                (f"fact.{index}",),
                (f"record.{index}",),
            ),
            ts(1),
        )
        for index in range(17)
    )
    with pytest.raises(ValueError, match="merged candidate lineage"):
        select_entries(candidates, cutoff_at=ts(2))


class _FakePostgresMemoryRepository:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.current: MemoryArtifact | None = None
        self.fail_audit = False

    @contextmanager
    def transaction(self) -> Iterator[None]:
        before = (list(self.events), self.current)
        try:
            yield
        except Exception:
            self.events, self.current = before
            raise

    def register_candidate(self, artifact: MemoryArtifact, cas_hash: str, byte_count: int) -> None:
        assert cas_hash == artifact.content_hash
        assert byte_count == len(artifact.canonical_content_bytes())
        self.events.append("register")

    def append_curation_audit(self, audit: CurationAuditRecord) -> bool:
        self.events.append("audit")
        if self.fail_audit:
            raise RuntimeError("audit failure")
        return True

    def mark_validated(self, result: ValidationResult, report_hash: str, version: str) -> None:
        assert result.valid and len(report_hash) == 64 and version == "p3f.validator.1"
        self.events.append("validate")

    def database_now(self) -> UtcTimestamp:
        return ts(4)

    def promote(self, artifact_id: str, requested_as_of: UtcTimestamp) -> bool:
        assert artifact_id == "memory.1" and requested_as_of == ts(10)
        self.events.append("promote")
        return False

    def current_pointer(self) -> MemoryArtifact | None:
        return self.current


def test_postgres_coordinator_orders_register_audit_validate_promote_and_readback(
    tmp_path: Path,
) -> None:
    source_record = record()
    audits = InMemoryAppendOnlyCurationAuditRepository()
    pipeline = CurationPipeline(
        ScriptedCurationProvider((MemoryCandidate(entry(), ts(1)),)),
        MemoryValidator(),
        audits,
    )
    prepared = pipeline.prepare(source_records=(source_record,), **_curation_fields())
    repository = _FakePostgresMemoryRepository()
    repository.current = replace(prepared.artifact, state=ArtifactState.CURRENT)
    coordinator = PostgresMemoryPromotionCoordinator(
        cast(PostgresMemoryRepository, repository),
        FileContentStore(tmp_path.resolve()),
        MemoryValidator(),
    )
    result = coordinator.validate_and_promote(
        prepared,
        source_records={source_record.record_id: source_record},
        requested_cutoff=ts(2),
        requested_as_of=ts(10),
    )
    assert result.valid
    assert repository.events == ["register", "audit", "validate", "promote"]


def test_postgres_coordinator_rolls_back_candidate_when_audit_append_fails(tmp_path: Path) -> None:
    source_record = record()
    pipeline = CurationPipeline(
        ScriptedCurationProvider((MemoryCandidate(entry(), ts(1)),)),
        MemoryValidator(),
        InMemoryAppendOnlyCurationAuditRepository(),
    )
    prepared = pipeline.prepare(source_records=(source_record,), **_curation_fields())
    repository = _FakePostgresMemoryRepository()
    repository.fail_audit = True
    coordinator = PostgresMemoryPromotionCoordinator(
        cast(PostgresMemoryRepository, repository),
        FileContentStore(tmp_path.resolve()),
        MemoryValidator(),
    )
    with pytest.raises(RuntimeError, match="audit failure"):
        coordinator.validate_and_promote(
            prepared,
            source_records={source_record.record_id: source_record},
            requested_cutoff=ts(2),
            requested_as_of=ts(10),
        )
    assert repository.events == []
    assert repository.current is None
