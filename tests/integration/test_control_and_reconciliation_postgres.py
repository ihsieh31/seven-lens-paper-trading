"""PostgreSQL-only tests for reconciliation persistence and the control plane."""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from seven_lens.application.control_service import ControlPlane, ResumeBlockedError
from seven_lens.application.ports.broker import BrokerTransportError, PaperAccount
from seven_lens.application.reconciliation_service import Reconciler
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.control import ControlCommand, ControlCommandRecord
from seven_lens.execution.fake_broker import FakePaperBroker
from seven_lens.execution.orders import (
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    Price,
    PriceCollar,
    Symbol,
)
from seven_lens.execution.reconciliation import ReconciliationStatus
from seven_lens.infrastructure.postgres import PostgresUnitOfWork

pytestmark = pytest.mark.integration

_BASE_TIME = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TRADING_DATE = TradingDate.from_isoformat("2026-08-17")


class _FixedClock:
    def __call__(self) -> UtcTimestamp:
        return _BASE_TIME


class _UnavailableBroker(FakePaperBroker):
    def account(self) -> PaperAccount:
        raise BrokerTransportError("injected reconciliation outage")


def test_partial_control_command_persists_null_applied_at(
    migrated_postgres: str,
) -> None:
    record = ControlCommandRecord(
        command_id=uuid4(),
        command=ControlCommand.CANCEL_OPEN_ORDERS,
        reason="PARTIAL_FAILURE 1/2 BrokerTransportError",
        actor="acceptance",
        run_id=None,
        requested_at=_BASE_TIME,
        applied_at=None,
    )
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        assert unit_of_work.control.add_command(record) is None
        unit_of_work.commit()
    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        stored = connection.execute(
            "SELECT applied_at FROM public.control_commands WHERE command_id = %s",
            (record.command_id,),
        ).fetchone()
    assert stored == (None,)


def test_broker_outage_persists_mismatch_and_pauses_in_postgres(
    migrated_postgres: str,
) -> None:
    reconciler = Reconciler(broker=_UnavailableBroker(clock=_FixedClock()), clock=_FixedClock())
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        result = reconciler.run(unit_of_work, _TRADING_DATE)
        assert result.status is ReconciliationStatus.MISMATCH
        assert unit_of_work.control.state().entries_paused is True
    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        stored = connection.execute(
            "SELECT mismatch_kinds FROM public.reconciliation_runs WHERE run_id = %s",
            (result.run_id,),
        ).fetchone()
    assert stored == (["BROKER_QUERY_FAILURE"],)


def _intent(*, target_version: int) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TRADING_DATE,
        window="open",
        target_version=target_version,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE_TIME,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_BASE_TIME,
    )


def test_reconciliation_run_persists_and_latest_orders_correctly(migrated_postgres: str) -> None:
    reconciler = Reconciler(broker=FakePaperBroker(clock=_FixedClock()), clock=_FixedClock())
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        first = reconciler.run(unit_of_work, _TRADING_DATE)
        assert first.status is ReconciliationStatus.CLEAN
        latest = unit_of_work.reconciliations.latest()
        assert latest is not None and latest.run_id == first.run_id

    # A second, mismatching run must become the latest and pause entries.
    broker_with_unknown_order = FakePaperBroker(clock=_FixedClock())
    broker_with_unknown_order.submit_order(_intent(target_version=1))
    reconciler2 = Reconciler(broker=broker_with_unknown_order, clock=_FixedClock())
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        second = reconciler2.run(unit_of_work, _TRADING_DATE)
        assert second.status is ReconciliationStatus.MISMATCH
        latest = unit_of_work.reconciliations.latest()
        assert latest is not None and latest.run_id == second.run_id
        assert unit_of_work.control.state().entries_paused is True

    with psycopg.connect(migrated_postgres) as connection:
        rows = connection.execute(
            """
            SELECT kind, detail
            FROM public.reconciliation_mismatches
            WHERE run_id = %s
            ORDER BY ordinal
            """,
            (second.run_id,),
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0][0] == "UNKNOWN_BROKER_ORDER"
        assert rows[0][1] == "fake-order-000001"
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute("DELETE FROM public.reconciliation_mismatches")
        connection.rollback()


def test_control_commands_append_only_and_resume_gate(migrated_postgres: str) -> None:
    plane = ControlPlane(clock=_FixedClock())
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        plane.pause_entries(unit_of_work, reason="operator drill", actor="owner")
        state = unit_of_work.control.state()
        assert state.entries_paused is True and state.paused_reason == "operator drill"

    with (
        PostgresUnitOfWork(migrated_postgres) as unit_of_work,
        pytest.raises(ResumeBlockedError),
    ):
        plane.resume_entries(unit_of_work, actor="owner")

    reconciler = Reconciler(broker=FakePaperBroker(clock=_FixedClock()), clock=_FixedClock())
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        reconciler.run(unit_of_work, _TRADING_DATE)
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        snapshot = plane.resume_entries(unit_of_work, actor="owner")
        assert snapshot.entries_paused is False

    with psycopg.connect(migrated_postgres) as connection:
        count = connection.execute("SELECT count(*) FROM public.control_commands").fetchone()
        assert count is not None and count[0] == 2  # pause + resume
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute("DELETE FROM public.control_commands")
        connection.rollback()
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute("UPDATE public.control_commands SET reason = 'forged'")
        connection.rollback()


def test_control_state_row_is_singleton_and_reason_checked(migrated_postgres: str) -> None:
    with psycopg.connect(migrated_postgres) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                UPDATE public.control_state
                SET entries_paused = TRUE, paused_reason = NULL
                WHERE singleton
                """
            )
        connection.rollback()
        rows = connection.execute("SELECT count(*) FROM public.control_state").fetchone()
        assert rows is not None and rows[0] == 1
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """
                INSERT INTO public.control_state (singleton, entries_paused)
                VALUES (TRUE, FALSE)
                """
            )
        connection.rollback()
