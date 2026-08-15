"""True PostgreSQL least-privilege and SECURITY DEFINER acceptance tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import cast

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from seven_lens.domain.jobs import JobSpec, JobStatus, LeaseDuration
from seven_lens.domain.value_objects import TradingDate
from seven_lens.infrastructure.postgres import PostgresUnitOfWork
from seven_lens.infrastructure.postgres_roles import (
    RuntimeRoleEvidence,
    provision_runtime_role,
    verify_runtime_role,
)

pytestmark = pytest.mark.integration

_RUNTIME_ROLE = "seven_lens_runtime_test"
_RUNTIME_PASSWORD = "p1h-disposable-runtime-only"


@pytest.fixture
def runtime_postgres(migrated_postgres: str) -> Iterator[tuple[str, RuntimeRoleEvidence]]:
    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(_RUNTIME_ROLE)))
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(
                sql.Identifier(_RUNTIME_ROLE),
                sql.Literal(_RUNTIME_PASSWORD),
            )
        )
    evidence = provision_runtime_role(migrated_postgres, _RUNTIME_ROLE)
    runtime_dsn = make_conninfo(
        migrated_postgres,
        user=_RUNTIME_ROLE,
        password=_RUNTIME_PASSWORD,
    )
    try:
        yield runtime_dsn, evidence
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(_RUNTIME_ROLE)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(_RUNTIME_ROLE)))


def _job_spec() -> JobSpec:
    return JobSpec(
        TradingDate.from_isoformat("2026-08-15"),
        "runtime_security",
        "open",
    )


def test_runtime_identity_is_non_owner_and_has_only_approved_capabilities(
    migrated_postgres: str,
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    runtime_dsn, provisioned = runtime_postgres
    verified = verify_runtime_role(migrated_postgres, _RUNTIME_ROLE)

    assert provisioned == verified
    assert provisioned.owner_role != provisioned.runtime_role
    with psycopg.connect(runtime_dsn, autocommit=True) as connection:
        row = connection.execute(
            "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole "
            "FROM pg_catalog.pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert row == (_RUNTIME_ROLE, False, False, False)


def test_runtime_repository_can_create_acquire_transition_renew_and_release(
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    runtime_dsn, _ = runtime_postgres
    spec = _job_spec()

    with PostgresUnitOfWork(runtime_dsn) as unit_of_work:
        assert unit_of_work.jobs.add(spec).status is JobStatus.PLANNED
        grant = unit_of_work.jobs.acquire(
            spec.job_key,
            "runtime-worker",
            LeaseDuration(timedelta(minutes=5)),
        )
        assert grant is not None
        running = unit_of_work.jobs.set_status(grant, JobStatus.RUNNING)
        assert running is not None and running.status is JobStatus.RUNNING
        renewed = unit_of_work.jobs.renew(grant, LeaseDuration(timedelta(minutes=10)))
        assert renewed is not None
        assert unit_of_work.jobs.release(renewed) is True
        unit_of_work.commit()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE public.job_instances SET status = 'FAILED'",
        "UPDATE public.job_instances SET fencing_token = fencing_token + 1",
        "INSERT INTO public.job_leases "
        "(job_key, owner, fencing_token, leased_until) "
        "VALUES ('x', 'x', 1, statement_timestamp() + interval '1 minute')",
        "UPDATE public.job_leases SET owner = 'attacker'",
        "DELETE FROM public.job_leases",
        "ALTER TABLE public.job_instances DISABLE TRIGGER job_instances_guard_status_write",
        "DROP TRIGGER job_instances_guard_status_write ON public.job_instances",
        "CREATE OR REPLACE FUNCTION public.release_job_lease(text,text,bigint,text) "
        "RETURNS boolean LANGUAGE sql AS 'SELECT true'",
        "CREATE TEMP TABLE job_instances (job_key text)",
    ],
)
def test_runtime_cannot_mutate_or_replace_authoritative_controls(
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
    statement: str,
) -> None:
    runtime_dsn, _ = runtime_postgres

    with psycopg.connect(runtime_dsn, autocommit=False) as connection:
        with pytest.raises(psycopg.Error) as failure:
            connection.execute(statement)
        assert failure.value.sqlstate == "42501"
        connection.rollback()


def test_privileged_functions_ignore_owner_temp_relation_shadowing(
    migrated_postgres: str,
) -> None:
    spec = _job_spec()
    with psycopg.connect(migrated_postgres, autocommit=False) as connection:
        connection.execute(
            "INSERT INTO public.job_instances "
            "(job_key, trading_date, job_type, window_name, status) VALUES (%s,%s,%s,%s,%s)",
            (
                spec.job_key,
                spec.trading_date.value,
                spec.job_type,
                spec.window,
                JobStatus.PLANNED.value,
            ),
        )
        connection.execute("CREATE TEMP TABLE job_instances (job_key text)")
        connection.execute("CREATE TEMP TABLE job_leases (job_key text)")
        acquired = connection.execute(
            "SELECT * FROM public.acquire_job_lease(%s,%s,%s)",
            (spec.job_key, "shadow-test", timedelta(minutes=5)),
        ).fetchone()
        assert acquired is not None
        token = cast(int, acquired[3])
        renewed = connection.execute(
            "SELECT * FROM public.renew_job_lease(%s,%s,%s,%s)",
            (spec.job_key, "shadow-test", token, timedelta(minutes=10)),
        ).fetchone()
        assert renewed is not None
        transitioned = connection.execute(
            "SELECT * FROM public.transition_job_status(%s,%s,%s,%s)",
            (spec.job_key, "shadow-test", token, JobStatus.RUNNING.value),
        ).fetchone()
        assert transitioned is not None and transitioned[4] == JobStatus.RUNNING.value
        released = connection.execute(
            "SELECT public.release_job_lease(%s,%s,%s,%s)",
            (spec.job_key, "shadow-test", token, "shadow_test_complete"),
        ).fetchone()
        assert released == (True,)
        authoritative = connection.execute(
            "SELECT status, lease_owner FROM public.job_instances WHERE job_key = %s",
            (spec.job_key,),
        ).fetchone()
        assert authoritative == (JobStatus.RUNNING.value, None)
        connection.rollback()


def test_runtime_stale_and_expired_fencing_remain_fail_closed(
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    runtime_dsn, _ = runtime_postgres
    spec = _job_spec()
    with PostgresUnitOfWork(runtime_dsn) as unit_of_work:
        unit_of_work.jobs.add(spec)
        grant = unit_of_work.jobs.acquire(
            spec.job_key,
            "runtime-worker",
            LeaseDuration(timedelta(milliseconds=1)),
        )
        assert grant is not None
        unit_of_work.commit()

    with psycopg.connect(runtime_dsn, autocommit=True) as connection:
        connection.execute("SELECT pg_sleep(0.02)")

    with PostgresUnitOfWork(runtime_dsn) as unit_of_work:
        assert unit_of_work.jobs.set_status(grant, JobStatus.RUNNING) is None
        assert unit_of_work.jobs.renew(grant, LeaseDuration(timedelta(minutes=5))) is None
        assert unit_of_work.jobs.release(grant) is False
        unit_of_work.commit()
