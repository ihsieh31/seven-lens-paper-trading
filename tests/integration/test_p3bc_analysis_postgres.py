# mypy: ignore-errors
from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any

import psycopg
import pytest
from test_postgres_runtime_role import runtime_postgres  # noqa: F401

from seven_lens.analysis.contracts import AnalysisInput
from seven_lens.application.ports.analysis import AnalysisStage, StoredStageResult
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.content_store import FileContentStore
from seven_lens.infrastructure.postgres_analysis import (
    PostgresAnalysisError,
    PostgresAnalysisStateRepository,
    PostgresEvidenceRepository,
)
from seven_lens.infrastructure.postgres_roles import PostgresRoleError, verify_runtime_role
from seven_lens.sources.contracts import EvidencePacket, build_evidence_packet
from test_analysis_contracts import analysis_input, meta, rid
from test_p3bc_evidence_and_infrastructure import evidence_packet

pytestmark = pytest.mark.integration


@contextmanager
def connection(dsn: str) -> Iterator[Any]:
    value = psycopg.connect(dsn)
    try:
        yield value
    finally:
        value.close()


def current_packet() -> EvidencePacket:
    base = evidence_packet()
    available_at = UtcTimestamp(datetime.now(UTC) - timedelta(minutes=1))
    source = replace(
        base.source_records[0],
        published_at=available_at,
        discovered_at=available_at,
        retrieved_at=available_at,
        available_at=available_at,
    )
    fragment = replace(base.fragments[0], available_at=available_at)
    return build_evidence_packet(
        schema_version=base.schema_version,
        packet_id=base.packet_id,
        as_of=available_at,
        source_records=(source,),
        fragments=(fragment,),
        claims=base.claims,
        contradiction_claim_ids=base.contradiction_claim_ids,
        missing_evidence=base.missing_evidence,
        freshness_status=base.freshness_status,
        status=base.status,
        universe_hash=base.universe_hash,
        portfolio_snapshot_hash=base.portfolio_snapshot_hash,
        data_snapshot_refs=base.data_snapshot_refs,
        producer_version=base.producer_version,
    )


def test_p3bc_staged_available_packet_and_idempotent_stage_authority(
    migrated_postgres: str,
    tmp_path: Path,
) -> None:
    store = FileContentStore(tmp_path / "cas")
    stored = store.put(b"fixture")
    base = current_packet()
    packet = build_evidence_packet(
        schema_version=base.schema_version,
        packet_id=base.packet_id,
        as_of=base.as_of,
        source_records=(replace(base.source_records[0], content_hash=stored.content_hash),),
        fragments=(replace(base.fragments[0], content_hash=stored.content_hash),),
        claims=base.claims,
        contradiction_claim_ids=base.contradiction_claim_ids,
        missing_evidence=base.missing_evidence,
        freshness_status=base.freshness_status,
        status=base.status,
        universe_hash=base.universe_hash,
        portfolio_snapshot_hash=base.portfolio_snapshot_hash,
        data_snapshot_refs=base.data_snapshot_refs,
        producer_version=base.producer_version,
    )
    analysis = analysis_input()
    with connection(migrated_postgres) as database:
        evidence = PostgresEvidenceRepository(database, content_store=store)
        evidence.register_staged_object(stored.content_hash, stored.size)
        with database.cursor() as cursor:
            cursor.execute(
                "SELECT state, available_at FROM public.source_objects WHERE content_hash = %s",
                (stored.content_hash,),
            )
            assert cursor.fetchone() == ("STAGED", None)
        empty_store = FileContentStore(tmp_path / "empty-cas")
        with pytest.raises(PostgresAnalysisError, match="verified"):
            PostgresEvidenceRepository(database, content_store=empty_store).publish_object(
                stored.content_hash
            )

        class ForgedVerifier:
            def verify(self, content_hash: str) -> bool:
                del content_hash
                return True

        with pytest.raises(ValueError, match="trusted FileContentStore"):
            PostgresEvidenceRepository(database, content_store=ForgedVerifier())

        evidence.publish_object(stored.content_hash)
        evidence.add_source_record(packet.source_records[0])
        evidence.add_packet(packet)
        state = PostgresAnalysisStateRepository(database)
        state.create_run(
            str(analysis.meta.run_id),
            str(analysis.input_id),
            packet.packet_hash,
            analysis.portfolio_snapshot.content_hash,
        )
        result = StoredStageResult(
            str(analysis.meta.run_id), AnalysisStage.ANALYSTS, "b" * 64, "fixture"
        )
        assert state.advance(result, AnalysisStage.PLANNED) is True
        assert state.advance(result, AnalysisStage.PLANNED) is False
        database.commit()

    with connection(migrated_postgres) as database:
        state = PostgresAnalysisStateRepository(database)
        assert state.current_stage(str(analysis.meta.run_id)) is AnalysisStage.ANALYSTS
        assert state.load(str(analysis.meta.run_id), AnalysisStage.ANALYSTS) == result
        with pytest.raises(psycopg.errors.CheckViolation, match="immutable"):
            state.advance(
                StoredStageResult(
                    str(analysis.meta.run_id),
                    AnalysisStage.ANALYSTS,
                    "c" * 64,
                    "changed",
                ),
                AnalysisStage.PLANNED,
            )
        database.rollback()


def test_p3bc_stage_transition_rolls_back_atomically(migrated_postgres: str) -> None:
    packet = current_packet()
    analysis = analysis_input()
    with connection(migrated_postgres) as database:
        PostgresEvidenceRepository(database).add_packet(packet)
        state = PostgresAnalysisStateRepository(database)
        state.create_run(
            str(analysis.meta.run_id),
            str(analysis.input_id),
            packet.packet_hash,
            analysis.portfolio_snapshot.content_hash,
        )
        database.commit()
        # Remediation R1 Fix-2 keeps the legal (DEBATE, RESEARCH) pair while the
        # run is still on PLANNED, so the guarded UPDATE itself must fail closed.
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            state.advance(
                StoredStageResult(
                    str(analysis.meta.run_id), AnalysisStage.RESEARCH, "d" * 64, "early"
                ),
                AnalysisStage.DEBATE,
            )
        database.rollback()
        assert state.current_stage(str(analysis.meta.run_id)) is AnalysisStage.PLANNED
        assert state.load(str(analysis.meta.run_id), AnalysisStage.RESEARCH) is None


def test_p3bc_database_rejects_packet_snapshot_mismatch(migrated_postgres: str) -> None:
    packet = current_packet()
    analysis = analysis_input()
    with connection(migrated_postgres) as database:
        PostgresEvidenceRepository(database).add_packet(packet)
        with pytest.raises(psycopg.errors.CheckViolation, match="snapshot"):
            PostgresAnalysisStateRepository(database).create_run(
                str(analysis.meta.run_id),
                str(analysis.input_id),
                packet.packet_hash,
                "f" * 64,
            )
        database.rollback()


@pytest.mark.parametrize("different_packet_snapshot", [False, True])
def test_p3bc_database_rejects_second_run_for_same_input(
    migrated_postgres: str, different_packet_snapshot: bool
) -> None:
    first_packet = current_packet()
    second_packet = first_packet
    second_snapshot_hash = first_packet.portfolio_snapshot_hash
    if different_packet_snapshot:
        second_snapshot_hash = "f" * 64
        second_packet = build_evidence_packet(
            schema_version=first_packet.schema_version,
            packet_id=rid(51),
            as_of=first_packet.as_of,
            source_records=first_packet.source_records,
            fragments=first_packet.fragments,
            claims=first_packet.claims,
            contradiction_claim_ids=first_packet.contradiction_claim_ids,
            missing_evidence=first_packet.missing_evidence,
            freshness_status=first_packet.freshness_status,
            status=first_packet.status,
            universe_hash=first_packet.universe_hash,
            portfolio_snapshot_hash=second_snapshot_hash,
            data_snapshot_refs=first_packet.data_snapshot_refs,
            producer_version=first_packet.producer_version,
        )
    analysis = analysis_input()
    with connection(migrated_postgres) as database:
        evidence = PostgresEvidenceRepository(database)
        evidence.add_packet(first_packet)
        if different_packet_snapshot:
            evidence.add_packet(second_packet)
        state = PostgresAnalysisStateRepository(database)
        state.create_run(
            str(analysis.meta.run_id),
            str(analysis.input_id),
            first_packet.packet_hash,
            first_packet.portfolio_snapshot_hash,
        )
        database.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            state.create_run(
                str(rid(99)),
                str(analysis.input_id),
                second_packet.packet_hash,
                second_snapshot_hash,
            )
        database.rollback()
        assert state.current_stage(str(analysis.meta.run_id)) is AnalysisStage.PLANNED
        with pytest.raises(PostgresAnalysisError, match="unavailable"):
            state.current_stage(str(rid(99)))


def _raw_advance(
    database: Any, run_id: str, expected: str, stage: str, digest: str, payload: str
) -> Any:
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT public.advance_analysis_stage(%s::uuid, %s, %s, %s, %s)",
            (run_id, expected, stage, digest, payload),
        )
        return cursor.fetchone()[0]


def _setup_run(
    migrated_postgres: str,
    analysis: AnalysisInput | None = None,
    packet: EvidencePacket | None = None,
) -> tuple[Any, str]:
    chosen_packet = current_packet() if packet is None else packet
    chosen = analysis_input() if analysis is None else analysis
    database = psycopg.connect(migrated_postgres)
    try:
        PostgresEvidenceRepository(database).add_packet(chosen_packet)
        PostgresAnalysisStateRepository(database).create_run(
            str(chosen.meta.run_id),
            str(chosen.input_id),
            chosen_packet.packet_hash,
            chosen.portfolio_snapshot.content_hash,
        )
        database.commit()
    except Exception:
        database.close()
        raise
    return database, str(chosen.meta.run_id)


def test_p3bc_transition_whitelist_is_enforced_independent_by_postgres(
    migrated_postgres: str,
) -> None:
    packet = current_packet()
    database, run_id = _setup_run(migrated_postgres, packet=packet)
    try:
        for expected, stage in (
            ("PLANNED", "TRADER"),
            ("PLANNED", "COMPLETE"),
            ("TRADER", "ANALYSTS"),
        ):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"):
                _raw_advance(database, run_id, expected, stage, "e" * 64, "abuse")
            database.rollback()

        assert _raw_advance(database, run_id, "PLANNED", "ANALYSTS", "1" * 64, "a") is True
        database.commit()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"):
            _raw_advance(database, run_id, "ANALYSTS", "ANALYSTS", "2" * 64, "self")
        database.rollback()

        assert _raw_advance(database, run_id, "ANALYSTS", "DEBATE", "3" * 64, "b") is True
        assert _raw_advance(database, run_id, "DEBATE", "RESEARCH", "4" * 64, "c") is True
        assert _raw_advance(database, run_id, "RESEARCH", "TRADER", "5" * 64, "d") is True
        assert _raw_advance(database, run_id, "TRADER", "COMPLETE", "6" * 64, "e") is True
        database.commit()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"):
            _raw_advance(database, run_id, "COMPLETE", "ANALYSTS", "7" * 64, "sink")
        database.rollback()
    finally:
        database.close()

    terminal_input = replace(
        analysis_input(), meta=replace(meta(), run_id=rid(80)), input_id=rid(81)
    )
    terminal, terminal_run = _setup_run(migrated_postgres, terminal_input, packet)
    try:
        assert _raw_advance(terminal, terminal_run, "PLANNED", "INVALID", "8" * 64, "x") is True
        terminal.commit()
        for target in ("ANALYSTS", "DEBATE", "TRADER"):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"):
                _raw_advance(terminal, terminal_run, "INVALID", target, "9" * 64, "revive")
            terminal.rollback()
    finally:
        terminal.close()


@pytest.mark.usefixtures("runtime_postgres")
def test_p3bc_runtime_role_abuse_of_advance_analysis_stage_is_rejected(
    request: pytest.FixtureRequest,
) -> None:
    runtime_dsn = request.getfixturevalue("runtime_postgres")[0]
    with connection(runtime_dsn) as runtime:
        for expected, stage in (
            ("PLANNED", "TRADER"),
            ("PLANNED", "COMPLETE"),
        ):
            with (
                pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="not legal"),
                runtime.cursor() as cursor,
            ):
                cursor.execute(
                    "SELECT public.advance_analysis_stage(%s::uuid, %s, %s, %s, %s)",
                    (
                        "00000000-0000-4000-8000-000000000001",
                        expected,
                        stage,
                        "f" * 64,
                        "runtime-jump",
                    ),
                )
            runtime.rollback()


@pytest.mark.usefixtures("runtime_postgres")
def test_p3bc_runtime_cannot_publish_unverified_cas_state(
    request: pytest.FixtureRequest,
) -> None:
    runtime_dsn = request.getfixturevalue("runtime_postgres")[0]
    with (
        connection(runtime_dsn) as runtime,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        runtime.cursor() as cursor,
    ):
        cursor.execute("SELECT public.publish_source_object(%s)", ("a" * 64,))


@pytest.mark.usefixtures("runtime_postgres")
def test_p3bc_role_verifier_detects_p3_table_privilege_drift(
    migrated_postgres: str,
    request: pytest.FixtureRequest,
) -> None:
    evidence = request.getfixturevalue("runtime_postgres")[1]
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            owner.execute(
                psycopg.sql.SQL("GRANT {} ON public.analysis_stage_results TO {}").format(
                    psycopg.sql.SQL(privilege), psycopg.sql.Identifier(evidence.runtime_role)
                )
            )
            try:
                with pytest.raises(PostgresRoleError, match="P3 table privileges"):
                    verify_runtime_role(migrated_postgres, evidence.runtime_role)
            finally:
                owner.execute(
                    psycopg.sql.SQL("REVOKE {} ON public.analysis_stage_results FROM {}").format(
                        psycopg.sql.SQL(privilege),
                        psycopg.sql.Identifier(evidence.runtime_role),
                    )
                )


def test_p3bc_same_hash_retry_increments_attempt_until_db_budget(
    migrated_postgres: str,
) -> None:
    database, run_id = _setup_run(migrated_postgres)
    try:
        state = PostgresAnalysisStateRepository(database)
        result = StoredStageResult(run_id, AnalysisStage.ANALYSTS, "b" * 64, "fixture")
        assert state.advance(result, AnalysisStage.PLANNED) is True
        # Seven same-hash retries bring the database attempt counter from 1 to 8;
        # the ninth call exceeds the CHECK-enforced budget and fails closed.
        for _ in range(7):
            assert state.advance(result, AnalysisStage.PLANNED) is False
            database.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            state.advance(result, AnalysisStage.PLANNED)
        database.rollback()
        assert state.load(run_id, AnalysisStage.ANALYSTS) == result
    finally:
        database.close()


def test_p3bc_concurrent_different_stage_results_leave_one_authority(
    migrated_postgres: str,
) -> None:
    database, run_id = _setup_run(migrated_postgres)
    database.close()
    barrier = Barrier(2)

    def advance(digest: str) -> str:
        with psycopg.connect(migrated_postgres) as worker:
            barrier.wait()
            try:
                _raw_advance(worker, run_id, "PLANNED", "ANALYSTS", digest, digest)
            except psycopg.Error as error:
                worker.rollback()
                return str(error.sqlstate)
            worker.commit()
            return "ok"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(advance, ("1" * 64, "2" * 64)))
    assert sorted(outcomes) == ["23514", "ok"]
    with psycopg.connect(migrated_postgres, autocommit=True) as authority:
        rows = authority.execute(
            "SELECT result_hash FROM public.analysis_stage_results "
            "WHERE run_id = %s AND stage = 'ANALYSTS'",
            (run_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] in {"1" * 64, "2" * 64}
