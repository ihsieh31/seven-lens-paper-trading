"""Least-privilege grants for an externally created PostgreSQL runtime login role."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

import psycopg
from psycopg import sql

_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class PostgresRoleError(RuntimeError):
    """Raised when runtime-role provisioning cannot prove the trust boundary."""


@dataclass(frozen=True, slots=True)
class RuntimeRoleEvidence:
    owner_role: str
    runtime_role: str
    database_name: str


def provision_runtime_role(owner_dsn: str, runtime_role: str) -> RuntimeRoleEvidence:
    """Grant only the privileges used by the current application repositories.

    The login role must already exist.  Role creation and its credential remain an
    operator concern so neither the application nor this repository handles a runtime
    password.  The supplied connection must be the owner of every authoritative object.
    """

    _validate_dsn(owner_dsn)
    _validate_role_name(runtime_role)
    with psycopg.connect(owner_dsn, autocommit=False) as connection, connection.cursor() as cursor:
        owner_role, database_name = _current_identity(cursor)
        if owner_role == runtime_role:
            raise PostgresRoleError("runtime role must differ from the migration owner role")
        _assert_runtime_role_flags(cursor, runtime_role)
        _assert_not_owner_member(cursor, runtime_role, owner_role)
        _assert_authoritative_object_owner(cursor, owner_role)

        role = sql.Identifier(runtime_role)
        database = sql.Identifier(database_name)
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role))
        cursor.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM {}").format(database, role))
        cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(role))
        cursor.execute(sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {}").format(role))
        cursor.execute(
            sql.SQL(
                "GRANT SELECT ON TABLE public.schema_metadata, public.schema_migrations, "
                "public.domain_events, public.audit_events, public.job_instances TO {}"
            ).format(role)
        )
        cursor.execute(
            sql.SQL(
                "GRANT INSERT ON TABLE public.domain_events, public.audit_events, "
                "public.job_instances TO {}"
            ).format(role)
        )
        for signature in (
            "public.acquire_job_lease(TEXT, TEXT, INTERVAL)",
            "public.renew_job_lease(TEXT, TEXT, BIGINT, INTERVAL)",
            "public.release_job_lease(TEXT, TEXT, BIGINT, TEXT)",
            "public.transition_job_status(TEXT, TEXT, BIGINT, TEXT)",
            "public.domain_event_payload_is_valid(TEXT, JSONB)",
            "public.audit_event_payload_is_valid(TEXT, JSONB)",
        ):
            cursor.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                    sql.SQL(signature),
                    role,
                )
            )
        connection.commit()
    return RuntimeRoleEvidence(owner_role, runtime_role, database_name)


def verify_runtime_role(owner_dsn: str, runtime_role: str) -> RuntimeRoleEvidence:
    """Fail closed unless the configured runtime role remains non-owner and restricted."""

    _validate_dsn(owner_dsn)
    _validate_role_name(runtime_role)
    with psycopg.connect(owner_dsn, autocommit=True) as connection, connection.cursor() as cursor:
        owner_role, database_name = _current_identity(cursor)
        if owner_role == runtime_role:
            raise PostgresRoleError("runtime role must differ from the migration owner role")
        _assert_runtime_role_flags(cursor, runtime_role)
        _assert_not_owner_member(cursor, runtime_role, owner_role)
        _assert_runtime_is_not_object_owner(cursor, runtime_role)
        _assert_runtime_privileges(cursor, runtime_role, database_name)
    return RuntimeRoleEvidence(owner_role, runtime_role, database_name)


def _validate_dsn(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise PostgresRoleError("owner DSN must be non-empty text")
    return value


def _validate_role_name(value: object) -> str:
    if type(value) is not str or _ROLE_PATTERN.fullmatch(value) is None:
        raise PostgresRoleError("runtime role must use the canonical bounded role format")
    return value


def _current_identity(cursor: psycopg.Cursor[object]) -> tuple[str, str]:
    cursor.execute("SELECT current_user, current_database()")
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or len(row) != 2 or type(row[0]) is not str or type(row[1]) is not str:
        raise PostgresRoleError("could not determine PostgreSQL owner identity")
    return row[0], row[1]


def _assert_runtime_role_flags(cursor: psycopg.Cursor[object], runtime_role: str) -> None:
    cursor.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin "
        "FROM pg_catalog.pg_roles WHERE rolname = %s",
        (runtime_role,),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or row != (False, False, False, True):
        raise PostgresRoleError("runtime role must be LOGIN and have no elevated role flags")


def _assert_not_owner_member(
    cursor: psycopg.Cursor[object],
    runtime_role: str,
    owner_role: str,
) -> None:
    cursor.execute(
        "SELECT pg_catalog.pg_has_role(%s, %s, 'MEMBER')",
        (runtime_role, owner_role),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or row[0] is not False:
        raise PostgresRoleError("runtime role must not inherit migration-owner authority")


def _assert_authoritative_object_owner(cursor: psycopg.Cursor[object], owner_role: str) -> None:
    cursor.execute(
        """
        SELECT DISTINCT owner_name
        FROM (
            SELECT pg_catalog.pg_get_userbyid(c.relowner) AS owner_name
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname IN (
                  'schema_metadata', 'schema_migrations', 'domain_events',
                  'audit_events', 'job_instances', 'job_leases'
              )
            UNION ALL
            SELECT pg_catalog.pg_get_userbyid(p.proowner) AS owner_name
            FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
              AND p.proname IN (
                  'acquire_job_lease', 'renew_job_lease', 'release_job_lease',
                  'transition_job_status', 'guard_job_instance_status_write'
              )
        ) AS owners
        """
    )
    owners = {str(cast(tuple[object, ...], row)[0]) for row in cursor.fetchall()}
    if owners != {owner_role}:
        raise PostgresRoleError("migration connection does not own every authoritative object")


def _assert_runtime_is_not_object_owner(cursor: psycopg.Cursor[object], runtime_role: str) -> None:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND pg_catalog.pg_get_userbyid(c.relowner) = %s
            UNION ALL
            SELECT 1
            FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND pg_catalog.pg_get_userbyid(p.proowner) = %s
        )
        """,
        (runtime_role, runtime_role),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None or row[0] is not False:
        raise PostgresRoleError("runtime role must not own objects in the authoritative schema")


def _assert_runtime_privileges(
    cursor: psycopg.Cursor[object], runtime_role: str, database_name: str
) -> None:
    cursor.execute(
        """
        SELECT
            has_database_privilege(%s, %s, 'TEMPORARY'),
            has_schema_privilege(%s, 'public', 'CREATE'),
            has_table_privilege(%s, 'public.job_leases', 'INSERT,UPDATE,DELETE'),
            has_table_privilege(%s, 'public.job_instances', 'UPDATE,DELETE'),
            has_function_privilege(
                %s, 'public.acquire_job_lease(text,text,interval)', 'EXECUTE'
            ),
            has_function_privilege(
                %s, 'public.transition_job_status(text,text,bigint,text)', 'EXECUTE'
            )
        """,
        (
            runtime_role,
            database_name,
            runtime_role,
            runtime_role,
            runtime_role,
            runtime_role,
            runtime_role,
        ),
    )
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row != (False, False, False, False, True, True):
        raise PostgresRoleError(
            "runtime role privileges do not match the approved least-privilege set"
        )
