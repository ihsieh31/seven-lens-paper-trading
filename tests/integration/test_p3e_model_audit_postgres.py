# mypy: ignore-errors
"""Real PostgreSQL acceptance for P3-E authoritative model-call audit."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import psycopg
import pytest
from test_postgres_runtime_role import runtime_postgres  # noqa: F401

from seven_lens.analysis.model_audit import ModelCallClaimDecision
from seven_lens.infrastructure.postgres_model_audit import (
    PostgresModelCallAuditError,
    PostgresModelCallAuditRepository,
)
from test_p3e_model_audit import audit_record, canonical_result

pytestmark = pytest.mark.integration


def test_model_call_audit_exact_replay_is_idempotent_and_collision_fails(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        repository = PostgresModelCallAuditRepository(connection)
        assert (
            repository.claim(audit_record().to_claim()).decision is ModelCallClaimDecision.CLAIMED
        )
        assert repository.persist(audit_record(), canonical_result()) is True
        assert repository.persist(audit_record(), canonical_result()) is False
        with pytest.raises(PostgresModelCallAuditError, match="collision"):
            repository.persist(replace(audit_record(), response_hash="d" * 64), canonical_result())
        connection.rollback()


def test_model_call_claim_rejects_changed_pre_network_material(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        repository = PostgresModelCallAuditRepository(connection)
        claim = audit_record().to_claim()
        assert repository.claim(claim).decision is ModelCallClaimDecision.CLAIMED
        with pytest.raises(PostgresModelCallAuditError, match="collision") as failure:
            repository.claim(replace(claim, request_envelope_hash="e" * 64))
        assert failure.value.sqlstate == "23505"


def test_model_call_audit_is_append_only_and_contains_no_payload_columns(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        repository = PostgresModelCallAuditRepository(connection)
        repository.claim(audit_record().to_claim())
        repository.persist(audit_record(), canonical_result())
        connection.commit()
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='model_call_audits'"
            )
        }
        assert not columns & {
            "secret",
            "authorization",
            "prompt",
            "request_body",
            "response_body",
            "account_id",
            "broker_order_id",
        }
        for statement in (
            "UPDATE public.model_call_audits SET latency_ms = 1",
            "DELETE FROM public.model_call_audits",
            "TRUNCATE public.model_call_audits",
        ):
            with pytest.raises(psycopg.Error):
                connection.execute(statement)
            connection.rollback()


def test_model_call_audit_two_connection_same_and_different_metadata_collision(
    migrated_postgres: str,
) -> None:
    barrier = Barrier(2)

    def write(response_hash: str):
        with psycopg.connect(migrated_postgres) as connection:
            barrier.wait()
            repository = PostgresModelCallAuditRepository(connection)
            try:
                claim = repository.claim(audit_record().to_claim())
                if claim.decision is not ModelCallClaimDecision.CLAIMED:
                    return (claim.decision.value.lower(), None)
                inserted = repository.persist(
                    replace(audit_record(), response_hash=response_hash), canonical_result()
                )
                connection.commit()
                return ("ok", inserted)
            except PostgresModelCallAuditError as error:
                connection.rollback()
                return ("collision", error.sqlstate)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, ("c" * 64, "d" * 64)))

    # Only the atomic claim winner may make the network call.  The loser sees
    # IN_PROGRESS and fails closed without creating a conflicting audit row.
    assert sorted(kind for kind, _ in outcomes) == ["in_progress", "ok"]
    assert next(value for kind, value in outcomes if kind == "ok") is True

    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute("SELECT count(*) FROM public.model_call_audits").fetchone() == (
            1,
        )


def test_model_call_audit_two_connection_exact_replay_has_one_row(
    migrated_postgres: str,
) -> None:
    barrier = Barrier(2)

    def write() -> bool:
        with psycopg.connect(migrated_postgres) as connection:
            barrier.wait()
            repository = PostgresModelCallAuditRepository(connection)
            claim = repository.claim(audit_record().to_claim())
            if claim.decision is ModelCallClaimDecision.CLAIMED:
                repository.persist(audit_record(), canonical_result())
                return True
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: write(), range(2)))

    assert sorted(outcomes) == [False, True]
    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute("SELECT count(*) FROM public.model_call_audits").fetchone() == (
            1,
        )


def test_unclosed_claim_remains_unknown_and_never_authorizes_retry(
    migrated_postgres: str,
) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        repository = PostgresModelCallAuditRepository(connection)
        assert (
            repository.claim(audit_record().to_claim()).decision is ModelCallClaimDecision.CLAIMED
        )
        assert (
            repository.claim(audit_record().to_claim()).decision
            is ModelCallClaimDecision.IN_PROGRESS
        )
        assert repository.load(audit_record().call_id) is None


def test_runtime_role_has_exact_select_and_function_authority_without_direct_dml(
    request: pytest.FixtureRequest,
) -> None:
    runtime_dsn, _ = request.getfixturevalue("runtime_postgres")
    with psycopg.connect(runtime_dsn) as connection:
        repository = PostgresModelCallAuditRepository(connection)
        assert (
            repository.claim(audit_record().to_claim()).decision is ModelCallClaimDecision.CLAIMED
        )
        assert repository.persist(audit_record(), canonical_result()) is True
        replay = repository.claim(audit_record().to_claim())
        assert replay.decision is ModelCallClaimDecision.REPLAY
        assert replay.attempt is not None
        for statement in (
            "INSERT INTO public.model_call_claims "
            "(call_id) VALUES ('00000000-0000-4000-8000-000000000001')",
            "UPDATE public.model_call_claims SET status='CLOSED'",
            "DELETE FROM public.model_call_audits",
            "TRUNCATE public.model_call_audits",
        ):
            with pytest.raises(psycopg.Error) as failure:
                connection.execute(statement)
            assert failure.value.sqlstate == "42501"
            connection.rollback()
