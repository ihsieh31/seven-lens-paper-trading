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

        assert migrate(test_database_url) == 8
        assert current_version(test_database_url) == 8
        assert verify_schema(test_database_url) == 8

        # Applying an already-applied migration is idempotent and checksum-checked.
        assert migrate(test_database_url) == 8
        assert verify_schema(test_database_url) == 8

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
                      'orders', 'positions', 'broker_accounts'
                  )
                ORDER BY table_name
                """
            )
            assert [row[0] for row in cursor.fetchall()] == [
                "audit_events",
                "broker_orders",
                "control_commands",
                "control_state",
                "domain_events",
                "fills",
                "job_instances",
                "job_leases",
                "order_intents",
                "reconciliation_runs",
                "schema_metadata",
                "schema_migrations",
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


def test_migration_up_down_restore_cycle_is_explicit(test_database_url: str) -> None:
    _drop_all_migrations(test_database_url)
    try:
        assert current_version(test_database_url) == 0
        assert migrate(test_database_url) == 8

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
        assert migrate(test_database_url) == 8
        assert verify_schema(test_database_url) == 8
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
    protected_functions = (
        *privileged_functions,
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
