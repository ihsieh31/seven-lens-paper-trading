from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from seven_lens.application.ports.content_store import ContentStoreError
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.content_store import FileContentStore
from seven_lens.memory.contracts import ArtifactState, DailyReflectionRecord, MemoryArtifact
from seven_lens.memory.promotion import (
    InMemoryPromotionRepository,
    MemoryPromoter,
    MemorySelectionError,
)
from seven_lens.memory.validation import MemoryValidator, ValidationIssue, ValidationResult
from test_p3f_memory_contracts import artifact, entry, record, ts


def promoter(tmp_path: Path) -> tuple[MemoryPromoter, InMemoryPromotionRepository]:
    repository = InMemoryPromotionRepository(now=lambda: ts(4))
    return MemoryPromoter(
        FileContentStore(tmp_path.resolve()), repository, MemoryValidator()
    ), repository


def test_promotion_reads_exact_cas_bytes_and_sets_single_current(tmp_path: Path) -> None:
    service, repository = promoter(tmp_path)
    source_record = record()
    result = service.validate_and_promote(
        artifact(), source_records={source_record.record_id: source_record}, requested_cutoff=ts(2)
    )
    assert result.valid
    assert repository.current is not None
    assert repository.current.state is ArtifactState.CURRENT


def test_invalid_candidate_does_not_replace_previous_current(tmp_path: Path) -> None:
    service, repository = promoter(tmp_path)
    source_record = record()
    service.validate_and_promote(
        artifact(), source_records={source_record.record_id: source_record}, requested_cutoff=ts(2)
    )
    previous = repository.current
    bad = artifact(artifact_id="memory.2", previous="memory.1")
    result = service.validate_and_promote(
        bad, source_records={source_record.record_id: source_record}, requested_cutoff=ts(1)
    )
    assert not result.valid
    assert repository.current == previous


def test_historical_replay_never_exposes_future_cutoff_and_returns_none_when_unsafe(
    tmp_path: Path,
) -> None:
    service, _ = promoter(tmp_path)
    source_record = record()
    service.validate_and_promote(
        artifact(), source_records={source_record.record_id: source_record}, requested_cutoff=ts(2)
    )
    assert service.select_for_as_of(ts(4)).artifact is not None
    # cutoff is 12:02, but the artifact was created at 12:03 and promoted at 12:04.
    assert service.select_for_as_of(ts(2)).artifact is None
    assert service.select_for_as_of(ts(3)).artifact is None
    unsafe = service.select_for_as_of(ts(1))
    assert unsafe.artifact is None
    assert unsafe.alert is not None and unsafe.alert.code == "NO_SAFE_MEMORY"


@dataclass(frozen=True)
class ForgedStored:
    content_hash: str
    size: int


class ForgedBooleanLikeStore:
    def put(self, content: bytes, *, declared_hash: str | None = None) -> ForgedStored:
        assert declared_hash is not None
        self.content = content + b"foreign"
        return ForgedStored(declared_hash, len(content))

    def get(self, content_hash: str) -> bytes:
        return self.content


def test_promotion_rejects_forged_metadata_or_boolean_verifier() -> None:
    repository = InMemoryPromotionRepository(now=lambda: ts(4))
    service = MemoryPromoter(ForgedBooleanLikeStore(), repository, MemoryValidator())
    with pytest.raises(RuntimeError, match="exact verification"):
        service.validate_and_promote(
            artifact(), source_records={"reflection.1": record()}, requested_cutoff=ts(2)
        )
    assert repository.current is None


def test_same_hash_retry_is_idempotent_and_different_hash_collision_is_rejected(
    tmp_path: Path,
) -> None:
    service, repository = promoter(tmp_path)
    source_record = record()
    item = artifact()
    service.validate_and_promote(
        item, source_records={source_record.record_id: source_record}, requested_cutoff=ts(2)
    )
    repository.register_candidate(item)
    collision = artifact(artifact_id="memory.1", entries=(item.entries[0],), created=4)
    with pytest.raises(RuntimeError, match="identity collision"):
        repository.register_candidate(collision)


def test_atomic_promotion_allows_only_one_candidate_from_same_predecessor() -> None:
    repository = InMemoryPromotionRepository(now=lambda: ts(5))
    first = artifact()
    repository.register_candidate(first)
    repository.save_validation(ValidationResult(first.with_state(ArtifactState.VALIDATED), ()))
    repository.promote(first.artifact_id, first.content_hash)
    second = artifact(artifact_id="memory.2", previous="memory.1", created=4, cutoff=3)
    third = artifact(artifact_id="memory.3", previous="memory.1", created=4, cutoff=3)
    repository.register_candidate(second)
    repository.register_candidate(third)
    repository.save_validation(ValidationResult(second.with_state(ArtifactState.VALIDATED), ()))
    repository.save_validation(ValidationResult(third.with_state(ArtifactState.VALIDATED), ()))
    repository.promote(second.artifact_id, second.content_hash)
    with pytest.raises(RuntimeError, match="lost the atomic current-pointer race"):
        repository.promote(third.artifact_id, third.content_hash)
    assert repository.current is not None and repository.current.artifact_id == "memory.2"


def test_candidate_rejects_foreign_or_regressing_predecessor() -> None:
    repository = InMemoryPromotionRepository(now=lambda: ts(5))
    with pytest.raises(RuntimeError, match="foreign predecessor"):
        repository.register_candidate(artifact(previous="missing"))
    first = artifact()
    repository.register_candidate(first)
    repository.save_validation(ValidationResult(first.with_state(ArtifactState.VALIDATED), ()))
    repository.promote(first.artifact_id, first.content_hash)
    with pytest.raises(RuntimeError, match="chronology"):
        repository.register_candidate(
            artifact(artifact_id="memory.2", previous="memory.1", cutoff=1, created=4)
        )


def test_in_memory_invalidation_is_idempotent_and_current_is_protected() -> None:
    repository = InMemoryPromotionRepository(now=lambda: ts(5))
    candidate = artifact()
    repository.register_candidate(candidate)
    invalid = ValidationResult(
        candidate.with_state(ArtifactState.INVALID),
        (ValidationIssue("source_lineage", "synthetic"),),
    )
    assert repository.mark_invalid(invalid) is True
    assert repository.mark_invalid(invalid) is False
    different_reason = ValidationResult(
        candidate.with_state(ArtifactState.INVALID),
        (ValidationIssue("prompt_injection", "synthetic"),),
    )
    with pytest.raises(RuntimeError, match="identity collision"):
        repository.mark_invalid(different_reason)

    current_repository = InMemoryPromotionRepository(now=lambda: ts(5))
    current_repository.register_candidate(candidate)
    current_repository.save_validation(
        ValidationResult(candidate.with_state(ArtifactState.VALIDATED), ())
    )
    current_repository.promote(candidate.artifact_id, candidate.content_hash)
    with pytest.raises(RuntimeError, match="cannot be invalidated"):
        current_repository.mark_invalid(invalid)


def test_repository_rejects_a_state_result_mismatch() -> None:
    repository = InMemoryPromotionRepository(now=lambda: ts(5))
    candidate = artifact()
    repository.register_candidate(candidate)
    with pytest.raises(ValueError, match="successful validation"):
        repository.save_validation(
            ValidationResult(
                candidate.with_state(ArtifactState.VALIDATED),
                (ValidationIssue("source_lineage", "synthetic"),),
            )
        )


def test_historical_selection_revalidates_source_integrity_and_falls_back(tmp_path: Path) -> None:
    service, _ = promoter(tmp_path)
    first_source = record()
    service.validate_and_promote(
        artifact(),
        source_records={first_source.record_id: first_source},
        requested_cutoff=ts(2),
    )
    second_source = record(record_id="reflection.2")
    second = artifact(
        artifact_id="memory.2",
        previous="memory.1",
        cutoff=3,
        created=4,
        source_ids=(second_source.record_id,),
        entries=(replace(entry(), source_record_ids=(second_source.record_id,)),),
    )
    service.validate_and_promote(
        second,
        source_records={second_source.record_id: second_source},
        requested_cutoff=ts(3),
    )
    object.__setattr__(second_source, "content_hash", "0" * 64)
    selected = service.select_for_as_of(ts(5))
    assert selected.artifact is not None
    assert selected.artifact.artifact_id == "memory.1"


def test_historical_selection_skips_a_missing_stale_object(tmp_path: Path) -> None:
    service, _ = promoter(tmp_path)
    source_record = record()
    service.validate_and_promote(
        artifact(), source_records={source_record.record_id: source_record}, requested_cutoff=ts(2)
    )
    second_source = record(record_id="reflection.2")
    second = artifact(
        artifact_id="memory.2",
        previous="memory.1",
        cutoff=3,
        created=4,
        source_ids=(second_source.record_id,),
        entries=(replace(entry(), source_record_ids=(second_source.record_id,)),),
    )
    service.validate_and_promote(
        second, source_records={second_source.record_id: second_source}, requested_cutoff=ts(3)
    )
    (tmp_path / second.content_hash[:2] / second.content_hash).unlink()

    selected = service.select_for_as_of(ts(5))
    assert selected.artifact is not None and selected.artifact.artifact_id == "memory.1"


def test_historical_selection_surfaces_existing_object_corruption(tmp_path: Path) -> None:
    service, _ = promoter(tmp_path)
    source_record = record()
    candidate = artifact()
    service.validate_and_promote(
        candidate,
        source_records={source_record.record_id: source_record},
        requested_cutoff=ts(2),
    )
    content_path = tmp_path / candidate.content_hash[:2] / candidate.content_hash
    content_path.write_bytes(b"corrupt")

    with pytest.raises(MemorySelectionError, match="content store failure"):
        service.select_for_as_of(ts(5))


class _SelectableStore:
    def __init__(self, root: Path) -> None:
        self._store = FileContentStore(root)
        self.fail = False

    def put(self, content: bytes, *, declared_hash: str | None = None) -> object:
        return self._store.put(content, declared_hash=declared_hash)

    def get(self, content_hash: str) -> bytes:
        if self.fail:
            raise ContentStoreError("synthetic content store outage")
        return self._store.get(content_hash)


def test_historical_selection_surfaces_content_store_failure(tmp_path: Path) -> None:
    store = _SelectableStore(tmp_path)
    repository = InMemoryPromotionRepository(now=lambda: ts(4))
    service = MemoryPromoter(store, repository, MemoryValidator())
    source_record = record()
    service.validate_and_promote(
        artifact(), source_records={source_record.record_id: source_record}, requested_cutoff=ts(2)
    )
    store.fail = True

    with pytest.raises(MemorySelectionError, match="content store failure"):
        service.select_for_as_of(ts(5))


class _ExplodingValidator(MemoryValidator):
    def __init__(self) -> None:
        self.fail = False

    def validate(
        self,
        artifact: MemoryArtifact,
        *,
        source_records: dict[str, DailyReflectionRecord],
        requested_cutoff: UtcTimestamp,
    ) -> ValidationResult:
        if self.fail:
            raise RuntimeError("synthetic validator bug")
        return super().validate(
            artifact, source_records=source_records, requested_cutoff=requested_cutoff
        )


def test_historical_selection_surfaces_validator_implementation_failure(tmp_path: Path) -> None:
    validator = _ExplodingValidator()
    repository = InMemoryPromotionRepository(now=lambda: ts(4))
    service = MemoryPromoter(FileContentStore(tmp_path), repository, validator)
    source_record = record()
    service.validate_and_promote(
        artifact(), source_records={source_record.record_id: source_record}, requested_cutoff=ts(2)
    )
    validator.fail = True

    with pytest.raises(MemorySelectionError, match="validation failed"):
        service.select_for_as_of(ts(5))


@pytest.mark.parametrize("crash_stage", ("bytes", "register", "validate", "promote"))
def test_promotion_is_retry_safe_at_every_durable_crash_point(
    tmp_path: Path, crash_stage: str
) -> None:
    repository = InMemoryPromotionRepository(now=lambda: ts(4))
    armed = {crash_stage}

    def fail_once(stage: str) -> None:
        if stage in armed:
            armed.remove(stage)
            raise RuntimeError(f"crash after {stage}")

    service = MemoryPromoter(
        FileContentStore((tmp_path / crash_stage).resolve()),
        repository,
        MemoryValidator(),
        failure_injector=fail_once,
    )
    source_record = record()
    with pytest.raises(RuntimeError, match=f"crash after {crash_stage}"):
        service.validate_and_promote(
            artifact(),
            source_records={source_record.record_id: source_record},
            requested_cutoff=ts(2),
        )
    result = service.validate_and_promote(
        artifact(),
        source_records={source_record.record_id: source_record},
        requested_cutoff=ts(2),
    )
    assert result.valid
    assert repository.current is not None
    assert repository.current.artifact_id == "memory.1"


def test_true_threaded_promotion_has_exactly_one_winner() -> None:
    repository = InMemoryPromotionRepository(now=lambda: ts(5))
    first = artifact()
    repository.register_candidate(first)
    repository.save_validation(ValidationResult(first.with_state(ArtifactState.VALIDATED), ()))
    repository.promote(first.artifact_id, first.content_hash)
    candidates = (
        artifact(artifact_id="memory.2", previous="memory.1", created=4, cutoff=3),
        artifact(artifact_id="memory.3", previous="memory.1", created=4, cutoff=3),
    )
    for candidate in candidates:
        repository.register_candidate(candidate)
        repository.save_validation(
            ValidationResult(candidate.with_state(ArtifactState.VALIDATED), ())
        )
    barrier = threading.Barrier(3)
    winners: list[str] = []
    failures: list[str] = []

    def promote_candidate(candidate: MemoryArtifact) -> None:
        barrier.wait()
        try:
            winners.append(
                repository.promote(candidate.artifact_id, candidate.content_hash).artifact_id
            )
        except RuntimeError as error:
            failures.append(str(error))

    threads = [threading.Thread(target=promote_candidate, args=(item,)) for item in candidates]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in threads)
    assert len(winners) == 1
    assert len(failures) == 1 and "current-pointer race" in failures[0]
    assert repository.current is not None
    assert repository.current.artifact_id == winners[0]
