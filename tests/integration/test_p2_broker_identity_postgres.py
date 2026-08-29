# mypy: ignore-errors
"""PostgreSQL P2 regressions for immutable broker-order identity and fills."""

from __future__ import annotations

import threading

import psycopg
import pytest

from seven_lens.application.execution_service import BrokerMirrorMismatchError, ExecutionEngine
from seven_lens.application.ports.broker import SubmitAccepted
from seven_lens.application.ports.persistence import BrokerOrderIdentityConflictError
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import FakePaperBroker
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
from seven_lens.infrastructure.postgres import PostgresUnitOfWork

pytestmark = pytest.mark.integration

_BASE = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_CANCEL = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")


def _intent(version: int) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=TradingDate.from_isoformat("2026-08-17"),
        window="open",
        target_version=version,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE,
        cancel_at=_CANCEL,
        run_id=RunId.new(),
        created_at=_BASE,
    )


def _mirror(
    intent: OrderIntent,
    broker_order_id: str,
    *,
    status: BrokerOrderStatus = BrokerOrderStatus.ACCEPTED,
    filled_quantity: int = 0,
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        filled_quantity=filled_quantity,
        limit_price=intent.limit_price,
        status=status,
        submitted_at=_BASE,
        updated_at=_BASE,
    )


def _seed(
    dsn: str,
    intents: tuple[OrderIntent, ...],
    mirrors: tuple[BrokerOrder, ...] = (),
    *,
    acknowledged: bool = False,
) -> None:
    with PostgresUnitOfWork(dsn) as unit_of_work:
        for intent in intents:
            unit_of_work.orders.add(intent)
            if acknowledged:
                for status in (
                    OrderStatus.RISK_APPROVED,
                    OrderStatus.OUTBOX_PENDING,
                    OrderStatus.SUBMITTING,
                    OrderStatus.ACKNOWLEDGED,
                ):
                    unit_of_work.orders.transition_status(intent.client_order_id, status)
        for mirror in mirrors:
            unit_of_work.orders.record_broker_order(mirror)
        unit_of_work.commit()


class _FixedBroker(FakePaperBroker):
    def __init__(self, order: BrokerOrder, fills: tuple[Fill, ...] = ()) -> None:
        super().__init__(clock=lambda: _BASE)
        self.order = order
        self.fills = fills
        self.submit_calls = 0

    def submit_order(self, intent: OrderIntent) -> SubmitAccepted:
        del intent
        self.submit_calls += 1
        return SubmitAccepted(order=self.order)

    def get_order(self, client_order_id):  # type: ignore[no-untyped-def]
        return self.order if client_order_id == self.order.client_order_id else None

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]:
        return self.fills if broker_order_id == self.order.broker_order_id else ()


@pytest.mark.parametrize("conflict", ("broker", "client"))
def test_repository_rejects_foreign_identity_without_mutation(
    migrated_postgres: str, conflict: str
) -> None:
    first = _intent(9_001)
    second = _intent(9_002)
    first_mirror = _mirror(first, "broker-identity-X")
    incoming = (
        _mirror(
            second,
            first_mirror.broker_order_id,
            status=BrokerOrderStatus.PARTIALLY_FILLED,
            filled_quantity=4,
        )
        if conflict == "broker"
        else _mirror(first, "broker-identity-Y")
    )
    _seed(migrated_postgres, (first,), (first_mirror,), acknowledged=True)
    _seed(migrated_postgres, (second,))

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        with pytest.raises(BrokerOrderIdentityConflictError):
            unit_of_work.orders.record_broker_order(incoming)
        stored = unit_of_work.orders.get_broker_order_by_id(first_mirror.broker_order_id)
        assert stored == first_mirror
        unit_of_work.rollback()

    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute("SELECT count(*) FROM broker_orders").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM fills").fetchone() == (0,)


def test_engine_cross_order_broker_id_marks_review_without_overwrite(
    migrated_postgres: str,
) -> None:
    first = _intent(9_011)
    second = _intent(9_012)
    first_mirror = _mirror(first, "broker-shared-X")
    observed_for_second = _mirror(second, first_mirror.broker_order_id)
    _seed(migrated_postgres, (first,), (first_mirror,), acknowledged=True)
    _seed(migrated_postgres, (second,))
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.orders.transition_status(second.client_order_id, OrderStatus.RISK_APPROVED)
        unit_of_work.orders.transition_status(second.client_order_id, OrderStatus.OUTBOX_PENDING)
        unit_of_work.commit()

    broker = _FixedBroker(observed_for_second)
    with (
        PostgresUnitOfWork(migrated_postgres) as unit_of_work,
        pytest.raises(BrokerMirrorMismatchError, match="broker_order_id"),
    ):
        ExecutionEngine(
            broker=broker,
            clock=lambda: _BASE,
            control=unit_of_work.control,
        ).submit_from_outbox(unit_of_work, second.client_order_id)

    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute(
            "SELECT client_order_id, status, filled_quantity FROM broker_orders"
        ).fetchone() == (first.client_order_id.value, "ACCEPTED", 0)
        assert connection.execute(
            "SELECT client_order_id, status FROM order_intents ORDER BY client_order_id"
        ).fetchall() == [
            (first.client_order_id.value, "ACKNOWLEDGED"),
            (second.client_order_id.value, "REVIEW_REQUIRED"),
        ]
        assert connection.execute("SELECT count(*) FROM fills").fetchone() == (0,)
        assert connection.execute(
            "SELECT entries_paused FROM control_state WHERE singleton"
        ).fetchone() == (True,)
        assert connection.execute("SELECT command, actor FROM control_commands").fetchall() == [
            ("PAUSE_ENTRIES", "execution_engine")
        ]
    assert broker.submit_calls == 1


def test_engine_cross_order_execution_id_preserves_original_fill_and_gate(
    migrated_postgres: str,
) -> None:
    first = _intent(9_021)
    second = _intent(9_022)
    first_mirror = _mirror(first, "broker-fill-X")
    second_mirror = _mirror(second, "broker-fill-Y")
    original = Fill(
        execution_id="execution-global-X",
        broker_order_id=first_mirror.broker_order_id,
        quantity=OrderQuantity(2),
        price=Price.from_cents(9_999),
        occurred_at=_BASE,
    )
    conflicting = Fill(
        execution_id=original.execution_id,
        broker_order_id=second_mirror.broker_order_id,
        quantity=OrderQuantity(4),
        price=Price.from_cents(9_998),
        occurred_at=_BASE,
    )
    observed = _mirror(
        second,
        second_mirror.broker_order_id,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=4,
    )
    _seed(migrated_postgres, (first, second), (first_mirror, second_mirror), acknowledged=True)
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        assert unit_of_work.orders.add_fill(original) is True
        unit_of_work.commit()

    with (
        PostgresUnitOfWork(migrated_postgres) as unit_of_work,
        pytest.raises(BrokerMirrorMismatchError, match="execution id"),
    ):
        ExecutionEngine(
            broker=_FixedBroker(observed, (conflicting,)),
            clock=lambda: _BASE,
            control=unit_of_work.control,
        ).apply_fills(unit_of_work, second.client_order_id)

    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute(
            "SELECT execution_id, broker_order_id, quantity FROM fills"
        ).fetchall() == [(original.execution_id, original.broker_order_id, 2)]
        assert connection.execute(
            "SELECT client_order_id, status, filled_quantity "
            "FROM broker_orders ORDER BY client_order_id"
        ).fetchall() == [
            (first.client_order_id.value, "ACCEPTED", 0),
            (second.client_order_id.value, "ACCEPTED", 0),
        ]
        assert connection.execute(
            "SELECT status FROM order_intents WHERE client_order_id=%s",
            (second.client_order_id.value,),
        ).fetchone() == ("REVIEW_REQUIRED",)
        assert connection.execute(
            "SELECT entries_paused FROM control_state WHERE singleton"
        ).fetchone() == (True,)


def test_engine_duplicate_fill_batch_is_one_row(
    migrated_postgres: str,
) -> None:
    intent = _intent(9_031)
    mirror = _mirror(intent, "broker-batch-X")
    fill = Fill(
        execution_id="execution-batch-X",
        broker_order_id=mirror.broker_order_id,
        quantity=OrderQuantity(4),
        price=Price.from_cents(9_998),
        occurred_at=_BASE,
    )
    observed = _mirror(
        intent,
        mirror.broker_order_id,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=4,
    )
    _seed(migrated_postgres, (intent,), (mirror,), acknowledged=True)
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        result = ExecutionEngine(
            broker=_FixedBroker(observed, (fill, fill)),
            clock=lambda: _BASE,
            control=unit_of_work.control,
        ).apply_fills(unit_of_work, intent.client_order_id)
        assert result.status is OrderStatus.PARTIALLY_FILLED

    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute("SELECT count(*) FROM fills").fetchone() == (1,)
        assert connection.execute(
            "SELECT entries_paused FROM control_state WHERE singleton"
        ).fetchone() == (False,)


def test_two_connections_same_execution_id_have_one_binding(migrated_postgres: str) -> None:
    first = _intent(9_041)
    second = _intent(9_042)
    first_mirror = _mirror(first, "broker-race-X")
    second_mirror = _mirror(second, "broker-race-Y")
    _seed(migrated_postgres, (first, second), (first_mirror, second_mirror))
    fills = (
        Fill(
            "execution-race-X",
            first_mirror.broker_order_id,
            OrderQuantity(1),
            Price.from_cents(10_000),
            _BASE,
        ),
        Fill(
            "execution-race-X",
            second_mirror.broker_order_id,
            OrderQuantity(1),
            Price.from_cents(10_001),
            _BASE,
        ),
    )
    barrier = threading.Barrier(2)
    results: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def insert(fill: Fill) -> None:
        try:
            with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
                barrier.wait(timeout=5)
                inserted = unit_of_work.orders.add_fill(fill)
                unit_of_work.commit()
                with lock:
                    results.append(inserted)
        except BaseException as error:
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=insert, args=(fill,)) for fill in fills]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(results) == [False, True]

    with psycopg.connect(migrated_postgres) as connection:
        assert connection.execute(
            "SELECT count(*), min(broker_order_id), max(broker_order_id) FROM fills"
        ).fetchone() in {
            (1, first_mirror.broker_order_id, first_mirror.broker_order_id),
            (1, second_mirror.broker_order_id, second_mirror.broker_order_id),
        }
