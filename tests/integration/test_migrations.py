# mypy: ignore-errors
"""PostgreSQL-only migration and schema-enforcement acceptance tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from seven_lens.infrastructure.migrations import (
    MigrationError,
    current_version,
    migrate,
    rollback,
    verify_schema,
)

pytestmark = pytest.mark.integration


@contextmanager
def _connection(dsn: str) -> Iterator[Any]:
    connection = psycopg.connect(dsn)
    try:
        yield connection
    finally:
        connection.close()


def _drop_all_migrations(dsn: str) -> None:
    """Return the disposable TEST_DATABASE_URL database to a clean state."""

    while current_version(dsn):
        rollback(dsn)


def test_clean_apply_repeat_verify_and_schema_contract(test_database_url: str) -> None:
    _drop_all_migrations(test_database_url)
    try:
        assert current_version(test_database_url) == 0

        assert migrate(test_database_url) == 24
        assert current_version(test_database_url) == 24
        assert verify_schema(test_database_url) == 24

        # Applying an already-applied migration is idempotent and checksum-checked.
        assert migrate(test_database_url) == 24
        assert verify_schema(test_database_url) == 24

        with _connection(test_database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'schema_metadata', 'schema_migrations', 'domain_events',
                      'audit_events', 'job_instances', 'job_leases',
                      'order_intents', 'broker_orders', 'fills', 'reconciliation_runs',
                      'control_commands', 'control_state',
                      'corporate_action_event_head', 'corporate_action_event_sources',
                      'corporate_action_events',
                      'source_objects', 'source_records', 'evidence_packets', 'analysis_runs',
                      'analysis_stage_results',
                      'research_bundles', 'research_bundle_items',
                      'risk_rejection_feedback', 'proposal_contexts', 'proposal_runs',
                      'risk_debates', 'portfolio_proposals', 'proposal_stage_results',
                      'model_call_claims', 'model_call_audits',
                      'orders', 'p4_source_records', 'positions', 'broker_accounts',
                      'security_identities', 'security_identity_heads',
                      'security_identity_sources', 'security_quarantine_decision_sources',
                      'security_quarantine_decisions',
                      'candidate_sets', 'candidate_set_entries', 'cluster_results',
                      'feature_vectors', 'market_snapshots', 'sector_assignments',
                      'universe_snapshot_entries', 'universe_snapshots'
                  )
                ORDER BY table_name
                """
            )
            assert [row[0] for row in cursor.fetchall()] == [
                "analysis_runs",
                "analysis_stage_results",
                "audit_events",
                "broker_orders",
                "candidate_set_entries",
                "candidate_sets",
                "cluster_results",
                "control_commands",
                "control_state",
                "corporate_action_event_head",
                "corporate_action_event_sources",
                "corporate_action_events",
                "domain_events",
                "evidence_packets",
                "feature_vectors",
                "fills",
                "job_instances",
                "job_leases",
                "market_snapshots",
                "model_call_audits",
                "model_call_claims",
                "order_intents",
                "p4_source_records",
                "portfolio_proposals",
                "proposal_contexts",
                "proposal_runs",
                "proposal_stage_results",
                "reconciliation_runs",
                "research_bundle_items",
                "research_bundles",
                "risk_debates",
                "risk_rejection_feedback",
                "schema_metadata",
                "schema_migrations",
                "sector_assignments",
                "security_identities",
                "security_identity_heads",
                "security_identity_sources",
                "security_quarantine_decision_sources",
                "security_quarantine_decisions",
                "source_objects",
                "source_records",
                "universe_snapshot_entries",
                "universe_snapshots",
            ]

            cursor.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'audit_events'
                ORDER BY ordinal_position
                """
            )
            assert [row[0] for row in cursor.fetchall()] == [
                "audit_id",
                "event_type",
                "run_id",
                "correlation_id",
                "causation_id",
                "occurred_at",
                "payload",
                "producer_version",
                "recorded_at",
            ]
    finally:
        _drop_all_migrations(test_database_url)


def test_p4c_migration_rejects_legacy_object_without_advancing_version(
    test_database_url: str,
) -> None:
    _drop_all_migrations(test_database_url)
    try:
        assert migrate(test_database_url) == 24
        assert rollback(test_database_url) == 23
        with psycopg.connect(test_database_url, autocommit=True) as connection:
            connection.execute(
                "CREATE TABLE public.market_snapshots "
                "(legacy_id INTEGER PRIMARY KEY, malformed_payload TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO public.market_snapshots (legacy_id, malformed_payload) "
                "VALUES (1, 'legacy-bad-data')"
            )

        with pytest.raises(psycopg.Error) as error:
            migrate(test_database_url)
        assert error.value.sqlstate == "23514"
        assert "legacy object blocks the P4-C storage" in str(error.value)
        assert current_version(test_database_url) == 23
        with psycopg.connect(test_database_url) as connection:
            assert connection.execute(
                "SELECT legacy_id, malformed_payload FROM public.market_snapshots"
            ).fetchall() == [(1, "legacy-bad-data")]
            assert connection.execute(
                "SELECT count(*) FROM public.schema_migrations WHERE version = 24"
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(test_database_url, autocommit=True) as connection:
            connection.execute("DROP TABLE IF EXISTS public.market_snapshots")
        _drop_all_migrations(test_database_url)


def test_migration_up_down_restore_cycle_is_explicit(test_database_url: str) -> None:
    _drop_all_migrations(test_database_url)
    try:
        assert current_version(test_database_url) == 0
        assert migrate(test_database_url) == 24
        assert rollback(test_database_url) == 23
        with _connection(test_database_url) as connection:
            checksum_0010 = connection.execute(
                "SELECT checksum FROM public.schema_migrations WHERE version = 10"
            ).fetchone()[0]
            assert (
                connection.execute(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_proc AS procedure, "
                    "LATERAL pg_catalog.aclexplode(COALESCE("
                    "procedure.proacl, pg_catalog.acldefault('f', procedure.proowner))) AS acl "
                    "WHERE procedure.oid = 'public.digest(text,text)'::regprocedure "
                    "AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE')"
                ).fetchone()[0]
                is False
            )

        assert rollback(test_database_url) == 22
        assert rollback(test_database_url) == 21
        assert rollback(test_database_url) == 20
        assert rollback(test_database_url) == 19
        assert rollback(test_database_url) == 18
        assert rollback(test_database_url) == 17
        assert rollback(test_database_url) == 16
        assert rollback(test_database_url) == 15
        assert rollback(test_database_url) == 14
        assert rollback(test_database_url) == 13
        assert rollback(test_database_url) == 12
        assert rollback(test_database_url) == 11
        assert current_version(test_database_url) == 11
        with _connection(test_database_url) as connection:
            assert connection.execute(
                "SELECT to_regclass('public.model_call_audits')"
            ).fetchone() == (None,)
            assert (
                connection.execute(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_proc AS procedure, "
                    "LATERAL pg_catalog.aclexplode(COALESCE("
                    "procedure.proacl, pg_catalog.acldefault('f', procedure.proowner))) AS acl "
                    "WHERE procedure.oid = 'public.digest(text,text)'::regprocedure "
                    "AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE')"
                ).fetchone()[0]
                is False
            )
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert migrate(test_database_url) == 24
        assert verify_schema(test_database_url) == 24
        assert rollback(test_database_url) == 23
        assert rollback(test_database_url) == 22
        assert rollback(test_database_url) == 21
        assert rollback(test_database_url) == 20
        assert rollback(test_database_url) == 19
        assert rollback(test_database_url) == 18
        assert rollback(test_database_url) == 17
        assert rollback(test_database_url) == 16
        assert rollback(test_database_url) == 15
        assert rollback(test_database_url) == 14
        assert rollback(test_database_url) == 13
        assert rollback(test_database_url) == 12
        assert rollback(test_database_url) == 11
        assert rollback(test_database_url) == 10
        assert current_version(test_database_url) == 10
        with _connection(test_database_url) as connection:
            assert (
                connection.execute(
                    "SELECT checksum FROM public.schema_migrations WHERE version = 10"
                ).fetchone()[0]
                == checksum_0010
            )
            assert (
                connection.execute(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_proc AS procedure, "
                    "LATERAL pg_catalog.aclexplode(COALESCE("
                    "procedure.proacl, pg_catalog.acldefault('f', procedure.proowner))) AS acl "
                    "WHERE procedure.oid = 'public.digest(text,text)'::regprocedure "
                    "AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE')"
                ).fetchone()[0]
                is True
            )
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert migrate(test_database_url) == 24
        assert verify_schema(test_database_url) == 24
        with _connection(test_database_url) as connection:
            assert (
                connection.execute(
                    "SELECT checksum FROM public.schema_migrations WHERE version = 10"
                ).fetchone()[0]
                == checksum_0010
            )
            assert (
                connection.execute(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_proc AS procedure, "
                    "LATERAL pg_catalog.aclexplode(COALESCE("
                    "procedure.proacl, pg_catalog.acldefault('f', procedure.proowner))) AS acl "
                    "WHERE procedure.oid = 'public.digest(text,text)'::regprocedure "
                    "AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE')"
                ).fetchone()[0]
                is False
            )
        assert rollback(test_database_url) == 23
        assert rollback(test_database_url) == 22
        assert rollback(test_database_url) == 21
        assert rollback(test_database_url) == 20
        assert rollback(test_database_url) == 19
        assert rollback(test_database_url) == 18
        assert rollback(test_database_url) == 17
        assert rollback(test_database_url) == 16
        assert rollback(test_database_url) == 15
        assert rollback(test_database_url) == 14
        assert rollback(test_database_url) == 13
        assert rollback(test_database_url) == 12
        assert rollback(test_database_url) == 11
        assert rollback(test_database_url) == 10

        assert rollback(test_database_url) == 9
        assert current_version(test_database_url) == 9
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 8
        assert current_version(test_database_url) == 8
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 7
        assert current_version(test_database_url) == 7
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 6
        assert current_version(test_database_url) == 6
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 5
        assert current_version(test_database_url) == 5
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 4
        assert current_version(test_database_url) == 4
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 3
        assert current_version(test_database_url) == 3
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 2
        assert current_version(test_database_url) == 2
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 1
        assert current_version(test_database_url) == 1
        with pytest.raises(MigrationError, match="migration version does not match"):
            verify_schema(test_database_url)

        assert rollback(test_database_url) == 0
        assert current_version(test_database_url) == 0
        with pytest.raises(MigrationError, match="schema_migrations does not exist"):
            verify_schema(test_database_url)

        # A restored/disposable database can be rebuilt exactly from the migration.
        assert migrate(test_database_url) == 24
        assert verify_schema(test_database_url) == 24
    finally:
        _drop_all_migrations(test_database_url)


def test_reconciliation_scope_upgrade_defaults_legacy_clean_to_partial(
    test_database_url: str,
) -> None:
    """Rows created before 0014 remain non-resumable after the upgrade."""
    _drop_all_migrations(test_database_url)
    try:
        assert migrate(test_database_url) == 24
        assert rollback(test_database_url) == 23
        assert rollback(test_database_url) == 22
        assert rollback(test_database_url) == 21
        assert rollback(test_database_url) == 20
        assert rollback(test_database_url) == 19
        assert rollback(test_database_url) == 18
        assert rollback(test_database_url) == 17
        assert rollback(test_database_url) == 16
        assert rollback(test_database_url) == 15
        assert rollback(test_database_url) == 14
        assert rollback(test_database_url) == 13
        with _connection(test_database_url) as connection:
            connection.execute(
                """
                INSERT INTO public.reconciliation_runs (
                    run_id, trading_date, status, mismatch_count, mismatch_kinds,
                    checked_orders, checked_fills, observed_at
                ) VALUES (
                    '00000000-0000-0000-0000-000000000014', '2026-08-17',
                    'CLEAN', 0, '{}'::TEXT[], 0, 0, '2026-08-17T13:35:00Z'
                )
                """
            )
            connection.commit()
        assert migrate(test_database_url) == 24
        with _connection(test_database_url) as connection:
            assert connection.execute(
                "SELECT scope FROM public.reconciliation_runs "
                "WHERE run_id = '00000000-0000-0000-0000-000000000014'"
            ).fetchone() == ("PARTIAL",)
    finally:
        _drop_all_migrations(test_database_url)


def test_account_hardening_down_removes_unrepresentable_mismatch_evidence(
    test_database_url: str,
) -> None:
    """A disposable downgrade must handle P2-2 rows before narrowing checks."""
    _drop_all_migrations(test_database_url)
    run_id = "00000000-0000-0000-0000-000000000008"
    try:
        assert migrate(test_database_url) == 24
        assert rollback(test_database_url) == 23
        for expected_version in (22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8):
            assert rollback(test_database_url) == expected_version

        with _connection(test_database_url) as connection:
            connection.execute(
                """
                INSERT INTO public.reconciliation_runs (
                    run_id, trading_date, status, mismatch_count, mismatch_kinds,
                    checked_orders, checked_fills, observed_at
                ) VALUES (
                    %s, '2026-08-17', 'MISMATCH', 1,
                    ARRAY['ACCOUNT_ID_MISMATCH']::TEXT[], 0, 0,
                    '2026-08-17T13:35:00Z'
                )
                """,
                (run_id,),
            )
            connection.execute(
                """
                INSERT INTO public.reconciliation_mismatches (
                    run_id, ordinal, kind, detail
                ) VALUES (%s, 1, 'ACCOUNT_ID_MISMATCH', 'sentinel account')
                """,
                (run_id,),
            )
            connection.commit()

        assert rollback(test_database_url) == 7
        with _connection(test_database_url) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM public.reconciliation_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM public.reconciliation_mismatches WHERE run_id = %s",
                (run_id,),
            ).fetchone() == (0,)

        # The downgraded database remains rebuildable from the migration set.
        assert migrate(test_database_url) == 24
        assert verify_schema(test_database_url) == 24
    finally:
        _drop_all_migrations(test_database_url)


def test_privileged_schema_catalog_is_hardened(migrated_postgres: str) -> None:
    privileged_functions = (
        "acquire_job_lease",
        "renew_job_lease",
        "release_job_lease",
        "transition_job_status",
        "guard_job_instance_status_write",
    )
    control_functions = (
        "lock_control_state_for_submission",
        "pause_entries",
        "resume_entries",
        "bump_flatten_generation",
    )
    protected_functions = (
        *privileged_functions,
        *control_functions,
        "domain_event_payload_is_valid",
        "audit_event_payload_is_valid",
    )
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.proname, p.proconfig, pg_catalog.pg_get_functiondef(p.oid),
                   EXISTS (
                       SELECT 1
                       FROM pg_catalog.aclexplode(
                           COALESCE(p.proacl, pg_catalog.acldefault('f', p.proowner))
                       ) AS privilege
                       WHERE privilege.grantee = 0
                         AND privilege.privilege_type = 'EXECUTE'
                   ) AS public_execute
            FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = ANY(%s)
            ORDER BY p.proname
            """,
            (list(protected_functions),),
        )
        rows = cursor.fetchall()

        assert {row[0] for row in rows} == set(protected_functions)
        assert all(row[3] is False for row in rows)
        by_name = {row[0]: row for row in rows}
        for function_name in privileged_functions:
            _, settings, definition, _ = by_name[function_name]
            assert settings == ["search_path=pg_catalog, public, pg_temp"]
            if function_name != "guard_job_instance_status_write":
                assert "public.job_instances" in definition
        for function_name in control_functions:
            _, settings, definition, _ = by_name[function_name]
            assert settings == ["search_path=pg_catalog, public, pg_temp"]
            assert "public.control_state" in definition

        cursor.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_namespace AS n,
                         LATERAL pg_catalog.aclexplode(
                             COALESCE(n.nspacl, pg_catalog.acldefault('n', n.nspowner))
                         ) AS privilege
                    WHERE n.nspname = 'public'
                      AND privilege.grantee = 0
                      AND privilege.privilege_type = 'CREATE'
                ),
                EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_database AS d,
                         LATERAL pg_catalog.aclexplode(
                             COALESCE(d.datacl, pg_catalog.acldefault('d', d.datdba))
                         ) AS privilege
                    WHERE d.datname = pg_catalog.current_database()
                      AND privilege.grantee = 0
                      AND privilege.privilege_type = 'TEMPORARY'
                )
            """
        )
        assert cursor.fetchone() == (False, False)


def test_database_constraints_reject_invalid_identity_and_payload(
    migrated_postgres: str,
) -> None:
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

        with pytest.raises(psycopg.Error) as blank_event:
            cursor.execute(
                """
                INSERT INTO domain_events (
                    event_id, event_type, schema_version, aggregate_type,
                    aggregate_id, aggregate_sequence, run_id, correlation_id, occurred_at,
                    payload, producer_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000001', '', '1.0.0',
                    'job', 'job-1', 1, '00000000-0000-0000-0000-000000000010',
                    '00000000-0000-0000-0000-000000000020',
                    CURRENT_TIMESTAMP, '{"ok": true}'::jsonb, 'test'
                )
                """
            )
        assert blank_event.value.sqlstate == "23514"
        connection.rollback()

        with pytest.raises(psycopg.Error) as array_payload:
            cursor.execute(
                """
                INSERT INTO domain_events (
                    event_id, event_type, schema_version, aggregate_type,
                    aggregate_id, aggregate_sequence, run_id, correlation_id, occurred_at,
                    payload, producer_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000002', 'created', '1.0.0',
                    'job', 'job-1', 1, '00000000-0000-0000-0000-000000000010',
                    '00000000-0000-0000-0000-000000000020',
                    CURRENT_TIMESTAMP, '[1, 2, 3]'::jsonb, 'test'
                )
                """
            )
        assert array_payload.value.sqlstate == "23514"
        connection.rollback()

        with pytest.raises(psycopg.Error) as malformed_json:
            cursor.execute(
                """
                INSERT INTO audit_events (
                    audit_id, event_type, run_id, correlation_id, causation_id,
                    occurred_at, payload, producer_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000003', 'bad', NULL,
                    '00000000-0000-0000-0000-000000000020', NULL,
                    CURRENT_TIMESTAMP, %s::jsonb, 'test'
                )
                """,
                ("{not-json",),
            )
        assert malformed_json.value.sqlstate == "22P02"
        connection.rollback()

        with pytest.raises(psycopg.Error) as bad_job:
            cursor.execute(
                """
                INSERT INTO job_instances (
                    job_key, trading_date, job_type, window_name, status
                ) VALUES ('job-1', '2026-08-14', 'research', 'open', 'NOT_A_STATUS')
                """
            )
        assert bad_job.value.sqlstate == "23514"
        connection.rollback()


def test_event_correlation_and_job_key_are_database_required_invariants(
    migrated_postgres: str,
) -> None:
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        with pytest.raises(psycopg.Error) as missing_correlation:
            cursor.execute(
                """
                INSERT INTO domain_events (
                    event_id, event_type, schema_version, aggregate_type,
                    aggregate_id, aggregate_sequence, run_id, occurred_at,
                    payload, producer_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000011', 'created', '1.0.0',
                    'job', 'job-correlation', 1,
                    '00000000-0000-0000-0000-000000000012', CURRENT_TIMESTAMP,
                    '{"ok": true}'::jsonb, 'test'
                )
                """
            )
        assert missing_correlation.value.sqlstate == "23502"
        connection.rollback()

        with pytest.raises(psycopg.Error) as mismatched_key:
            cursor.execute(
                """
                INSERT INTO job_instances (
                    job_key, trading_date, job_type, window_name, status
                ) VALUES ('not-the-canonical-key', '2026-08-14', 'research', 'open', 'PLANNED')
                """
            )
        assert mismatched_key.value.sqlstate == "23514"
        connection.rollback()


def test_audit_and_domain_event_ledgers_are_database_enforced_append_only(
    migrated_postgres: str,
) -> None:
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO audit_events (
                audit_id, event_type, run_id, correlation_id, causation_id,
                occurred_at, payload, producer_version
            ) VALUES (
                '00000000-0000-0000-0000-000000000101', 'job.status_changed', NULL,
                '00000000-0000-0000-0000-000000000120', NULL,
                CURRENT_TIMESTAMP,
                '{"reason_code": "SCHEDULED", "target_status": "RUNNING"}', 'test'
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO domain_events (
                event_id, event_type, schema_version, aggregate_type,
                aggregate_id, aggregate_sequence, run_id, correlation_id, occurred_at,
                payload, producer_version
            ) VALUES (
                '00000000-0000-0000-0000-000000000102', 'job.created', '1.0.0',
                'job', 'append-only-job', 1,
                '00000000-0000-0000-0000-000000000110',
                '00000000-0000-0000-0000-000000000120', CURRENT_TIMESTAMP,
                '{"attempt_count": 0, "status": "PLANNED"}'::jsonb, 'test'
            )
            """
        )
        connection.commit()

        with pytest.raises(psycopg.Error) as audit_update:
            cursor.execute(
                "UPDATE audit_events SET payload = '{\"changed\": true}'::jsonb "
                "WHERE audit_id = '00000000-0000-0000-0000-000000000101'"
            )
        assert audit_update.value.sqlstate == "55000"
        connection.rollback()

        with pytest.raises(psycopg.Error) as audit_delete:
            cursor.execute(
                "DELETE FROM audit_events WHERE audit_id = '00000000-0000-0000-0000-000000000101'"
            )
        assert audit_delete.value.sqlstate == "55000"
        connection.rollback()

        with pytest.raises(psycopg.Error) as domain_update:
            cursor.execute(
                "UPDATE domain_events SET payload = '{\"changed\": true}'::jsonb "
                "WHERE event_id = '00000000-0000-0000-0000-000000000102'"
            )
        assert domain_update.value.sqlstate == "55000"
        connection.rollback()

        with pytest.raises(psycopg.Error) as domain_delete:
            cursor.execute(
                "DELETE FROM domain_events WHERE event_id = '00000000-0000-0000-0000-000000000102'"
            )
        assert domain_delete.value.sqlstate == "55000"
        connection.rollback()

        cursor.execute(
            "SELECT payload FROM audit_events "
            "WHERE audit_id = '00000000-0000-0000-0000-000000000101'"
        )
        assert cursor.fetchone()[0] == {
            "reason_code": "SCHEDULED",
            "target_status": "RUNNING",
        }


@pytest.mark.parametrize(
    "payload",
    [
        '{"api_key": "sk-test-secret"}',
        '{"Authorization": "Bearer fake-token"}',
        '{"note": "token=secret"}',
    ],
)
def test_audit_payload_rejects_secret_bearing_fields(
    migrated_postgres: str,
    payload: str,
) -> None:
    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        with pytest.raises(psycopg.Error) as failure:
            cursor.execute(
                """
            INSERT INTO audit_events (
                audit_id, event_type, run_id, correlation_id, causation_id,
                occurred_at, payload, producer_version
            ) VALUES (
                '00000000-0000-0000-0000-000000000201', 'unsafe', NULL,
                '00000000-0000-0000-0000-000000000220', NULL,
                CURRENT_TIMESTAMP, %s::jsonb, 'test'
            )
                """,
                (payload,),
            )
        assert failure.value.sqlstate == "23514"
        connection.rollback()


def test_domain_event_id_sequence_and_ordering_constraints_are_atomic(
    migrated_postgres: str,
) -> None:
    event_sql = """
        INSERT INTO domain_events (
            event_id, event_type, schema_version, aggregate_type,
            aggregate_id, aggregate_sequence, run_id, correlation_id, occurred_at,
            recorded_at,
            payload, producer_version
        ) VALUES (%s, 'job.created', '1.0.0', 'job', 'sequence-job', %s,
                  %s, %s, CURRENT_TIMESTAMP, %s,
                  '{"attempt_count": 0, "status": "PLANNED"}'::jsonb, 'test')
    """
    run_id = "00000000-0000-0000-0000-000000000301"
    correlation_id = "00000000-0000-0000-0000-000000000310"
    supplied_recorded_at = "2000-01-01T00:00:00+00:00"

    with _connection(migrated_postgres) as connection, connection.cursor() as cursor:
        cursor.execute(
            event_sql,
            (
                "00000000-0000-0000-0000-000000000302",
                1,
                run_id,
                correlation_id,
                supplied_recorded_at,
            ),
        )
        connection.commit()

        # A duplicate event id is rejected even when the aggregate sequence is next.
        with pytest.raises(psycopg.Error) as duplicate_id:
            cursor.execute(
                event_sql,
                (
                    "00000000-0000-0000-0000-000000000302",
                    2,
                    run_id,
                    correlation_id,
                    supplied_recorded_at,
                ),
            )
        assert duplicate_id.value.sqlstate == "23505"
        connection.rollback()

        # A different id cannot reuse an aggregate sequence.
        with pytest.raises(psycopg.Error) as duplicate_sequence:
            cursor.execute(
                event_sql,
                (
                    "00000000-0000-0000-0000-000000000303",
                    1,
                    run_id,
                    correlation_id,
                    supplied_recorded_at,
                ),
            )
        assert duplicate_sequence.value.sqlstate == "23514"
        connection.rollback()

        # Sequence 3 cannot skip the required sequence 2.
        with pytest.raises(psycopg.Error) as out_of_order:
            cursor.execute(
                event_sql,
                (
                    "00000000-0000-0000-0000-000000000304",
                    3,
                    run_id,
                    correlation_id,
                    supplied_recorded_at,
                ),
            )
        assert out_of_order.value.sqlstate == "23514"
        connection.rollback()

        cursor.execute(
            event_sql,
            (
                "00000000-0000-0000-0000-000000000305",
                2,
                run_id,
                correlation_id,
                supplied_recorded_at,
            ),
        )
        connection.commit()
        cursor.execute(
            "SELECT aggregate_sequence FROM domain_events "
            "WHERE aggregate_type = 'job' AND aggregate_id = 'sequence-job' "
            "ORDER BY aggregate_sequence"
        )
        assert [row[0] for row in cursor.fetchall()] == [1, 2]

        cursor.execute(
            "SELECT occurred_at, recorded_at "
            "FROM domain_events WHERE event_id = '00000000-0000-0000-0000-000000000305'"
        )
        occurred_at, recorded_at = cursor.fetchone()
        assert occurred_at <= recorded_at
        assert recorded_at.isoformat() != supplied_recorded_at
