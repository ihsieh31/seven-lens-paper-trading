"""PostgreSQL-only tests for the order repository and engine end-to-end path."""

from __future__ import annotations

import threading

import psycopg
import pytest
from psycopg.errors import ObjectNotInPrerequisiteState

from seven_lens.application.execution_service import ExecutionEngine
from seven_lens.application.ports.broker import SubmitResult
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import (
    FakeFillStep,
    FakePaperBroker,
    FakeSubmitOutcome,
    FakeSubmitPlan,
)
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    Fill,
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    Price,
    PriceCollar,
    Symbol,
)
from seven_lens.infrastructure.postgres import (
    PersistenceInvariantError,
    PostgresUnitOfWork,
)

pytestmark = pytest.mark.integration

_BASE_TIME = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")


class _FixedClock:
    def __call__(self) -> UtcTimestamp:
        return _BASE_TIME


class _BlockingSubmitBroker(FakePaperBroker):
    def __init__(self) -> None:
        super().__init__(clock=_FixedClock())
        self.submit_started = threading.Event()
        self.allow_submit = threading.Event()

    def submit_order(self, intent: OrderIntent) -> SubmitResult:
        self.submit_started.set()
        if not self.allow_submit.wait(timeout=5):
            raise AssertionError("test did not release broker submission")
        return super().submit_order(intent)


def _intent(*, target_version: int = 1) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=TradingDate.from_isoformat("2026-08-17"),
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


def _seed_outbox(unit_of_work: PostgresUnitOfWork, intent: OrderIntent) -> None:
    unit_of_work.orders.add(intent)
    unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
    unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
    unit_of_work.commit()


def test_repository_roundtrip_preserves_typed_values(migrated_postgres: str) -> None:
    from dataclasses import replace

    intent = _intent()
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        stored = unit_of_work.orders.add(intent)
        # created_at is authoritative database time; everything else round-trips.
        assert replace(stored, created_at=intent.created_at) == intent
        loaded = unit_of_work.orders.get(intent.client_order_id)
        assert loaded is not None
        assert replace(loaded, created_at=intent.created_at) == intent
        unit_of_work.commit()


def test_repository_rejects_foreign_duplicate_identity(migrated_postgres: str) -> None:
    first = _intent(target_version=1)
    second = _intent(target_version=1)
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.orders.add(first)
        with pytest.raises(PersistenceInvariantError, match="different order intent"):
            unit_of_work.orders.add(second)


def test_repository_transitions_are_database_guarded(migrated_postgres: str) -> None:
    intent = _intent(target_version=2)
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.orders.add(intent)
        unit_of_work.commit()
        with pytest.raises(ObjectNotInPrerequisiteState):
            unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.FILLED)
        unit_of_work.rollback()
        moved = unit_of_work.orders.transition_status(
            intent.client_order_id, OrderStatus.RISK_APPROVED
        )
        assert moved.status is OrderStatus.RISK_APPROVED
        unit_of_work.commit()


def test_repository_fill_deduplication_and_ordering(migrated_postgres: str) -> None:
    intent = _intent(target_version=3)
    fill = Fill(
        execution_id="exec-000001",
        broker_order_id="broker-000001",
        quantity=OrderQuantity(4),
        price=Price.from_cents(9_998),
        occurred_at=_BASE_TIME,
    )
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.orders.add(intent)
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.ACKNOWLEDGED)
        # The mirror must exist before the fill's foreign key can hold.
        order = BrokerOrder(
            broker_order_id="broker-000001",
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            filled_quantity=0,
            limit_price=intent.limit_price,
            status=BrokerOrderStatus.ACCEPTED,
            submitted_at=_BASE_TIME,
            updated_at=_BASE_TIME,
        )
        unit_of_work.orders.record_broker_order(order)
        assert unit_of_work.orders.add_fill(fill) is True
        assert unit_of_work.orders.add_fill(fill) is False
        listed = unit_of_work.orders.list_fills("broker-000001")
        assert [item.execution_id for item in listed] == ["exec-000001"]
        unit_of_work.commit()


def test_engine_end_to_end_against_postgres(migrated_postgres: str) -> None:
    intent = _intent(target_version=4)
    broker = FakePaperBroker(clock=_FixedClock())
    engine = ExecutionEngine(broker=broker, clock=_FixedClock())
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        _seed_outbox(unit_of_work, intent)
        result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        assert result.status is OrderStatus.ACKNOWLEDGED

        mirror = unit_of_work.orders.get_broker_order(intent.client_order_id)
        assert mirror is not None
        assert mirror.status is BrokerOrderStatus.ACCEPTED

        broker.apply_fill(
            mirror.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(4), price=Price.from_cents(9_998)),
        )
        partial = engine.apply_fills(unit_of_work, intent.client_order_id)
        assert partial.status is OrderStatus.PARTIALLY_FILLED
        assert unit_of_work.orders.list_fills(mirror.broker_order_id)
        unit_of_work.commit()

    with psycopg.connect(migrated_postgres) as connection:
        row = connection.execute(
            "SELECT status FROM public.order_intents WHERE client_order_id = %s",
            (intent.client_order_id.value,),
        ).fetchone()
        assert row is not None and row[0] == "PARTIALLY_FILLED"
        fill_count = connection.execute("SELECT count(*) FROM public.fills").fetchone()
        assert fill_count is not None and fill_count[0] == 1


def test_engine_timeout_after_accept_leaves_exactly_one_order(migrated_postgres: str) -> None:
    intent = _intent(target_version=5)
    broker = FakePaperBroker(
        clock=_FixedClock(),
        plans={
            intent.client_order_id.value: FakeSubmitPlan(
                outcome=FakeSubmitOutcome.TIMEOUT_AFTER_ACCEPT
            )
        },
    )
    engine = ExecutionEngine(broker=broker, clock=_FixedClock())
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        _seed_outbox(unit_of_work, intent)
        parked = engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        assert parked.status is OrderStatus.UNKNOWN

        resolved = engine.resolve(unit_of_work, intent.client_order_id)
        assert resolved.status is OrderStatus.ACKNOWLEDGED
        unit_of_work.commit()

    with psycopg.connect(migrated_postgres) as connection:
        intent_row = connection.execute(
            "SELECT status FROM public.order_intents WHERE client_order_id = %s",
            (intent.client_order_id.value,),
        ).fetchone()
        mirror_count = connection.execute("SELECT count(*) FROM public.broker_orders").fetchone()
    assert intent_row is not None and intent_row[0] == "ACKNOWLEDGED"
    assert mirror_count is not None and mirror_count[0] == 1


def test_pause_update_cannot_linearize_during_guarded_broker_submit(
    migrated_postgres: str,
) -> None:
    intent = _intent(target_version=6)
    broker = _BlockingSubmitBroker()
    pause_attempted = threading.Event()
    pause_completed = threading.Event()
    submission_result: list[OrderIntent] = []
    failures: list[BaseException] = []

    def submit_entry() -> None:
        try:
            with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
                _seed_outbox(unit_of_work, intent)
                engine = ExecutionEngine(
                    broker=broker,
                    clock=_FixedClock(),
                    control=unit_of_work.control,
                )
                submission_result.append(
                    engine.submit_from_outbox(unit_of_work, intent.client_order_id)
                )
        except BaseException as error:
            failures.append(error)

    def pause_entries() -> None:
        try:
            with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
                pause_attempted.set()
                unit_of_work.control.set_entries_paused(True, "concurrent operator pause")
                unit_of_work.commit()
                pause_completed.set()
        except BaseException as error:
            failures.append(error)

    submit_thread = threading.Thread(target=submit_entry)
    pause_thread = threading.Thread(target=pause_entries)
    submit_thread.start()
    assert broker.submit_started.wait(timeout=5)
    pause_thread.start()
    assert pause_attempted.wait(timeout=5)
    try:
        assert pause_completed.wait(timeout=0.25) is False
        with psycopg.connect(migrated_postgres, autocommit=True) as connection:
            state = connection.execute(
                "SELECT entries_paused FROM public.control_state WHERE singleton"
            ).fetchone()
        assert state == (False,)
    finally:
        broker.allow_submit.set()
        submit_thread.join(timeout=5)
        pause_thread.join(timeout=5)

    assert not submit_thread.is_alive()
    assert not pause_thread.is_alive()
    assert failures == []
    assert submission_result[0].status is OrderStatus.ACKNOWLEDGED
    assert pause_completed.is_set()
    with psycopg.connect(migrated_postgres, autocommit=True) as connection:
        final_state = connection.execute(
            "SELECT entries_paused FROM public.control_state WHERE singleton"
        ).fetchone()
    assert final_state == (True,)
