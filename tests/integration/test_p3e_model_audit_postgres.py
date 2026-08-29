# mypy: ignore-errors
"""Real PostgreSQL acceptance for P3-E authoritative model-call audit."""

from __future__ import annotations

from collections.abc import Iterator
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


# --- 0022 generic analysis route identity -----------------------------------


GENERIC_ROUTE_HASH = "0659d8fa9b38c9e7a800ce2bdc89b14eeb76a5c83f157f6b65afcbe568162524"


@pytest.fixture
def generic_route_postgres(test_database_url: str) -> Iterator[str]:
    """One migrated database that self-cleans even when the down path refuses."""

    from seven_lens.infrastructure.migrations import current_version, migrate, rollback

    while current_version(test_database_url):
        rollback(test_database_url)
    migrate(test_database_url)
    yield test_database_url
    _force_schema_reset(test_database_url)


def _force_schema_reset(dsn: str) -> None:
    """Reset the disposable test database when the down path is refuse-closed."""

    import time

    from seven_lens.infrastructure.migrations import migrate

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        connection.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA public")
    for _ in range(3):
        try:
            migrate(dsn)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("test schema reset failed")


def _per_record_result(record):
    """One canonical parsed result bound to the exact record call identity."""

    from seven_lens.analysis.contracts import AnalysisStatus
    from seven_lens.analysis.model_audit import CanonicalModelCallResult, ModelCallResultKind
    from test_analysis_contracts import report as contract_report

    return CanonicalModelCallResult.from_contract(
        record.call_id,
        ModelCallResultKind.ANALYST_REPORT,
        contract_report(AnalysisStatus.VALID),
    )


def _generic_record(input_number: int):
    """A fully valid generic-route audit record with a fresh call identity."""

    from seven_lens.analysis.model_audit import ModelCallRole, ModelCallStage, derive_model_call_id
    from seven_lens.config.provider import ProviderKind
    from test_p3e_model_audit import _rid

    input_id = _rid(input_number)
    context_id = _rid(input_number + 1)
    call_id = derive_model_call_id(
        input_id,
        context_id,
        ModelCallStage.ANALYST,
        ModelCallRole.TECHNICAL,
        0,
        1,
    )
    return replace(
        audit_record(),
        call_id=call_id,
        input_id=input_id,
        context_id=context_id,
        provider=ProviderKind.OPENAI_COMPATIBLE,
        model="openai/gpt-oss-120b",
        endpoint_policy_id=f"analysis-route-v1:{GENERIC_ROUTE_HASH}",
    )


def test_legacy_agnes_rows_survive_migration_with_backfilled_route_hash(
    migrated_postgres: str,
) -> None:
    from seven_lens.config.analysis_provider import LEGACY_ROUTE_CONFIG_HASH

    with psycopg.connect(migrated_postgres) as connection:
        repository = PostgresModelCallAuditRepository(connection)
        assert (
            repository.claim(audit_record().to_claim()).decision is ModelCallClaimDecision.CLAIMED
        )
        assert repository.persist(audit_record(), canonical_result()) is True
        connection.commit()
        stored_hash = connection.execute(
            "SELECT route_config_hash FROM public.model_call_claims WHERE call_id = %s",
            (str(audit_record().call_id.value),),
        ).fetchone()
        assert stored_hash is not None
        assert stored_hash[0] == LEGACY_ROUTE_CONFIG_HASH
        stored_audit_hash = connection.execute(
            "SELECT route_config_hash FROM public.model_call_audits WHERE call_id = %s",
            (str(audit_record().call_id.value),),
        ).fetchone()
        assert stored_audit_hash is not None
        assert stored_audit_hash[0] == LEGACY_ROUTE_CONFIG_HASH


def test_generic_route_claim_and_audit_are_accepted_with_exact_hash(
    generic_route_postgres: str,
) -> None:
    from seven_lens.config.analysis_provider import LEGACY_ROUTE_CONFIG_HASH

    record = _generic_record(900)
    assert record.route_config_hash == GENERIC_ROUTE_HASH
    assert record.route_config_hash != LEGACY_ROUTE_CONFIG_HASH
    with psycopg.connect(generic_route_postgres) as connection:
        repository = PostgresModelCallAuditRepository(connection)
        generic_result = _per_record_result(record)
        assert repository.claim(record.to_claim()).decision is ModelCallClaimDecision.CLAIMED
        assert repository.persist(record, generic_result) is True
        connection.commit()
        row = connection.execute(
            "SELECT provider, model, route_config_hash FROM public.model_call_audits "
            "WHERE call_id = %s",
            (str(record.call_id.value),),
        ).fetchone()
        assert row is not None
        assert row[0] == "OPENAI_COMPATIBLE"
        assert row[1] == "openai/gpt-oss-120b"
        assert row[2] == GENERIC_ROUTE_HASH


def test_generic_route_model_traversal_is_rejected_by_the_database(
    generic_route_postgres: str,
) -> None:
    with psycopg.connect(generic_route_postgres) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(
                "SELECT public.claim_model_call_attempt("
                "gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
                "'ANALYST', 'TECHNICAL', 0, 'OPENAI_COMPATIBLE', '../etc/passwd', "
                "'CHAT_COMPLETIONS', %s, 1, %s, %s, 'MAX')",
                (f"analysis-route-v1:{GENERIC_ROUTE_HASH}", "a" * 64, "b" * 64),
            )
        connection.rollback()


def test_generic_route_claim_collides_with_the_same_call_id_under_a_foreign_route(
    generic_route_postgres: str,
) -> None:
    from dataclasses import replace as _replace

    record = _generic_record(960)
    foreign = _replace(record, model="foreign-model")
    with psycopg.connect(generic_route_postgres) as connection:
        repository = PostgresModelCallAuditRepository(connection)
        assert repository.claim(record.to_claim()).decision is ModelCallClaimDecision.CLAIMED
        with pytest.raises(PostgresModelCallAuditError, match="collision") as failure:
            repository.claim(foreign.to_claim())
        assert failure.value.sqlstate == "23505"
        connection.rollback()


def test_down_migration_refuses_generic_route_rows_and_keeps_data(
    generic_route_postgres: str,
) -> None:
    from seven_lens.infrastructure.migrations import (
        MigrationError,
        current_version,
        rollback,
    )

    dsn = generic_route_postgres
    try:
        with psycopg.connect(dsn) as connection:
            repository = PostgresModelCallAuditRepository(connection)
            record = _generic_record(970)
            assert repository.claim(record.to_claim()).decision is ModelCallClaimDecision.CLAIMED
            assert repository.persist(record, _per_record_result(record)) is True
            connection.commit()

        with pytest.raises((MigrationError, psycopg.Error)):
            rollback(dsn)
        assert current_version(dsn) == 22
        with psycopg.connect(dsn) as connection:
            count = connection.execute(
                "SELECT count(*) FROM public.model_call_audits WHERE call_id = %s",
                (str(record.call_id.value),),
            ).fetchone()
            assert count == (1,)
    finally:
        _force_schema_reset(generic_route_postgres)


def test_clean_database_up_down_up_cycle(test_database_url: str) -> None:
    from seven_lens.infrastructure.migrations import current_version, migrate, rollback

    while current_version(test_database_url):
        rollback(test_database_url)
    try:
        assert migrate(test_database_url) == 22
        rollback(test_database_url)
        assert current_version(test_database_url) == 21
        assert migrate(test_database_url) == 22
    finally:
        _force_schema_reset(test_database_url)


def test_0022_up_backfills_legacy_rows_written_before_the_migration(
    test_database_url: str,
) -> None:
    """F-02 regression: the route-hash backfill must succeed on a populated table.

    A real upgrade applies 0022 to a database that already holds Agnes-era
    claim/audit rows.  The append-only guard only legalises CLAIMED->CLOSED
    transitions, so the provenance backfill has to run with the row-write
    guard disabled inside the migration transaction; this test pins that the
    historical rows survive byte-exact with the canonical legacy hash.
    """

    from seven_lens.analysis.model_audit import LEGACY_ROUTE_CONFIG_HASH
    from seven_lens.infrastructure.migrations import current_version, migrate, rollback

    while current_version(test_database_url):
        rollback(test_database_url)
    try:
        assert migrate(test_database_url) == 22
        assert rollback(test_database_url) == 21
        with psycopg.connect(test_database_url) as connection:
            repository = PostgresModelCallAuditRepository(connection)
            claim = audit_record().to_claim()
            assert repository.claim(claim).decision is ModelCallClaimDecision.CLAIMED
            assert repository.persist(audit_record(), canonical_result()) is True
            connection.commit()
        assert current_version(test_database_url) == 21
        assert migrate(test_database_url) == 22
        with psycopg.connect(test_database_url) as connection:
            claim_row = connection.execute(
                "SELECT provider, model, endpoint_policy_id, route_config_hash, status "
                "FROM public.model_call_claims WHERE call_id = %s",
                (str(claim.call_id.value),),
            ).fetchone()
            assert claim_row == (
                "AGNES",
                "agnes-2.5-flash",
                "p3e-agnes-2.5-flash-only-v1",
                LEGACY_ROUTE_CONFIG_HASH,
                "CLOSED",
            )
            audit_row = connection.execute(
                "SELECT provider, model, endpoint_policy_id, route_config_hash, outcome "
                "FROM public.model_call_audits WHERE call_id = %s",
                (str(claim.call_id.value),),
            ).fetchone()
            assert audit_row == (
                "AGNES",
                "agnes-2.5-flash",
                "p3e-agnes-2.5-flash-only-v1",
                LEGACY_ROUTE_CONFIG_HASH,
                "SUCCESS",
            )
            # The guard is re-enabled: post-migration mutation stays illegal.
            with pytest.raises(psycopg.Error) as failure:
                connection.execute(
                    "UPDATE public.model_call_claims SET model='evil' WHERE call_id = %s",
                    (str(claim.call_id.value),),
                )
            assert failure.value.sqlstate == "55000"
            connection.rollback()
        # Down is still refused while the legacy-only rows exist?  No: legacy
        # rows are Agnes-only, so down succeeds and re-up must stay clean.
        assert rollback(test_database_url) == 21
        assert migrate(test_database_url) == 22
    finally:
        _force_schema_reset(test_database_url)
