# mypy: ignore-errors
"""True PostgreSQL least-privilege and SECURITY DEFINER acceptance tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import cast

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from seven_lens.application.control_service import ControlPlane
from seven_lens.application.execution_service import ExecutionEngine, ExecutionPausedError
from seven_lens.domain.jobs import JobSpec, JobStatus, LeaseDuration
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import FakePaperBroker
from seven_lens.execution.orders import (
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    Price,
    PriceCollar,
    Symbol,
)
from seven_lens.execution.reconciliation import ReconciliationResult, ReconciliationScope
from seven_lens.infrastructure.postgres import PostgresUnitOfWork
from seven_lens.infrastructure.postgres_roles import (
    PostgresRoleError,
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


def _intent() -> OrderIntent:
    timestamp = UtcTimestamp.from_isoformat("2026-08-15T13:35:00.000000Z")
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=TradingDate.from_isoformat("2026-08-15"),
        window="open",
        target_version=1,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=timestamp,
        cancel_at=UtcTimestamp.from_isoformat("2026-08-15T13:45:00.000000Z"),
        run_id=RunId.new(),
        created_at=timestamp,
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


def test_runtime_role_direct_control_state_update_is_denied_and_pause_survives(
    migrated_postgres: str,
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    runtime_dsn, _ = runtime_postgres
    with PostgresUnitOfWork(migrated_postgres) as owner:
        owner.control.set_entries_paused(True, "NEW-P2-01 direct-update PoC")
        owner.commit()

    with psycopg.connect(runtime_dsn, autocommit=False) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as failure:
            runtime.execute(
                "UPDATE public.control_state SET entries_paused = FALSE WHERE singleton"
            )
        assert failure.value.sqlstate == "42501"
        runtime.rollback()

        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState) as failure:
            runtime.execute("SELECT * FROM public.resume_entries()")
        assert failure.value.sqlstate == "55000"
        runtime.rollback()

    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        assert owner.execute(
            "SELECT entries_paused FROM public.control_state WHERE singleton"
        ).fetchone() == (True,)


def test_runtime_execution_engine_stays_blocked_while_entries_are_paused(
    migrated_postgres: str,
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    runtime_dsn, _ = runtime_postgres
    intent = _intent()
    with PostgresUnitOfWork(migrated_postgres) as owner:
        owner.control.set_entries_paused(True, "NEW-P2-01 paused submission")
        owner.commit()

    with PostgresUnitOfWork(runtime_dsn) as runtime:
        runtime.orders.add(intent)
        runtime.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        runtime.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        runtime.commit()

    broker = FakePaperBroker(clock=lambda: UtcTimestamp.from_isoformat("2026-08-15T13:35:00Z"))
    with PostgresUnitOfWork(runtime_dsn) as runtime, pytest.raises(ExecutionPausedError):
        ExecutionEngine(
            broker=broker,
            clock=lambda: UtcTimestamp.from_isoformat("2026-08-15T13:35:00Z"),
            control=runtime.control,
        ).submit_from_outbox(runtime, intent.client_order_id)

    assert broker.list_open_orders() == ()
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        assert owner.execute(
            "SELECT entries_paused FROM public.control_state WHERE singleton"
        ).fetchone() == (True,)
        assert owner.execute("SELECT count(*) FROM public.broker_orders").fetchone() == (0,)


def test_runtime_control_plane_can_pause_and_legitimately_resume_with_audit(
    migrated_postgres: str,
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    runtime_dsn, _ = runtime_postgres
    timestamp = UtcTimestamp.from_isoformat("2026-08-15T13:35:00.000000Z")
    plane = ControlPlane(clock=lambda: timestamp)
    clean = ReconciliationResult.create(
        trading_date=TradingDate.from_isoformat("2026-08-15"),
        mismatches=(),
        checked_orders=0,
        checked_fills=0,
        observed_at=timestamp,
        scope=ReconciliationScope.FULL,
    )

    with PostgresUnitOfWork(runtime_dsn) as runtime:
        plane.pause_entries(runtime, reason="runtime control-plane test", actor="runtime")
    with PostgresUnitOfWork(runtime_dsn) as runtime:
        runtime.reconciliations.add(clean)
        runtime.commit()
    with PostgresUnitOfWork(runtime_dsn) as runtime:
        snapshot = plane.resume_entries(runtime, actor="runtime")
        assert snapshot.entries_paused is False

    intent = _intent()
    broker = FakePaperBroker(clock=lambda: timestamp)
    with PostgresUnitOfWork(runtime_dsn) as runtime:
        runtime.orders.add(intent)
        runtime.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        runtime.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        result = ExecutionEngine(
            broker=broker,
            clock=lambda: timestamp,
            control=runtime.control,
        ).submit_from_outbox(runtime, intent.client_order_id)
        assert result.status is OrderStatus.ACKNOWLEDGED
        runtime.commit()

    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        assert owner.execute(
            "SELECT entries_paused FROM public.control_state WHERE singleton"
        ).fetchone() == (False,)
        commands = owner.execute(
            "SELECT command, applied_at FROM public.control_commands ORDER BY requested_at"
        ).fetchall()
        broker_order_count = owner.execute("SELECT count(*) FROM public.broker_orders").fetchone()
    assert [row[0] for row in commands] == ["PAUSE_ENTRIES", "RESUME_ENTRIES"]
    assert all(row[1] is not None for row in commands)
    assert broker_order_count == (1,)


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


def test_runtime_role_verifier_rejects_control_state_update_privilege_drift(
    migrated_postgres: str,
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    del runtime_postgres
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute(
            sql.SQL("GRANT UPDATE ON TABLE public.control_state TO {}").format(
                sql.Identifier(_RUNTIME_ROLE)
            )
        )
    try:
        with pytest.raises(PostgresRoleError, match="runtime role privileges"):
            verify_runtime_role(migrated_postgres, _RUNTIME_ROLE)
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as owner:
            owner.execute(
                sql.SQL("REVOKE UPDATE ON TABLE public.control_state FROM {}").format(
                    sql.Identifier(_RUNTIME_ROLE)
                )
            )
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute(
            sql.SQL("GRANT UPDATE (entries_paused) ON TABLE public.control_state TO {}").format(
                sql.Identifier(_RUNTIME_ROLE)
            )
        )
    try:
        with pytest.raises(PostgresRoleError, match="runtime role privileges"):
            verify_runtime_role(migrated_postgres, _RUNTIME_ROLE)
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as owner:
            owner.execute(
                sql.SQL(
                    "REVOKE UPDATE (entries_paused) ON TABLE public.control_state FROM {}"
                ).format(sql.Identifier(_RUNTIME_ROLE))
            )
    verify_runtime_role(migrated_postgres, _RUNTIME_ROLE)


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


def test_runtime_role_verification_rejects_a_disabled_guard_trigger(
    migrated_postgres: str,
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    del runtime_postgres
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute(
            "ALTER TABLE public.job_instances DISABLE TRIGGER job_instances_guard_status_write"
        )
    try:
        with pytest.raises(PostgresRoleError, match="guard trigger inventory"):
            verify_runtime_role(migrated_postgres, _RUNTIME_ROLE)
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as owner:
            owner.execute(
                "ALTER TABLE public.job_instances ENABLE TRIGGER job_instances_guard_status_write"
            )
    verify_runtime_role(migrated_postgres, _RUNTIME_ROLE)


def test_runtime_role_verification_rejects_a_missing_guard_trigger(
    migrated_postgres: str,
    runtime_postgres: tuple[str, RuntimeRoleEvidence],
) -> None:
    del runtime_postgres
    with psycopg.connect(migrated_postgres, autocommit=True) as owner:
        owner.execute("DROP TRIGGER job_instances_guard_status_write ON public.job_instances")
    try:
        with pytest.raises(PostgresRoleError, match="guard trigger inventory"):
            verify_runtime_role(migrated_postgres, _RUNTIME_ROLE)
    finally:
        with psycopg.connect(migrated_postgres, autocommit=True) as owner:
            owner.execute(
                """
                CREATE TRIGGER job_instances_guard_status_write
                BEFORE UPDATE ON public.job_instances
                FOR EACH ROW
                EXECUTE FUNCTION public.guard_job_instance_status_write()
                """
            )
    verify_runtime_role(migrated_postgres, _RUNTIME_ROLE)
