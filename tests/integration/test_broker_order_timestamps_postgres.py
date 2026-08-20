# mypy: ignore-errors
"""Reproduction suite: broker mirrors must keep broker time separate from database time.

Defect E (timestamp mixing): ``broker_orders.updated_at`` is assigned the
local database clock, while the trade-update consumer orders events by the
broker's own ``updated_at``.  Under clock skew a legitimate broker event is
silently classified STALE and dropped.  These tests fail on the pre-fix
adapter and lock the split-contract: the mirror preserves the broker's
timestamps, the database keeps its own recorded clock, and the consumer
orders strictly by broker time.
"""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    Price,
    PriceCollar,
    Symbol,
)
from seven_lens.execution.trade_updates import (
    OrderStatusUpdate,
    TradeUpdateConsumer,
    TradeUpdateOutcome,
)
from seven_lens.infrastructure.postgres import PostgresUnitOfWork

pytestmark = pytest.mark.integration

_BROKER_TIME = UtcTimestamp.from_isoformat("2026-08-17T13:35:01.000000Z")
_LATER_BROKER_TIME = UtcTimestamp.from_isoformat("2026-08-17T13:35:02.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TRADING_DATE = TradingDate.from_isoformat("2026-08-17")


def _intent() -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TRADING_DATE,
        window="open",
        target_version=1,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BROKER_TIME,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_BROKER_TIME,
    )


def _seed_intent(migrated_postgres: str) -> OrderIntent:
    intent = _intent()
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.orders.add(intent)
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.ACKNOWLEDGED)
        unit_of_work.commit()
    return intent


def _mirror(intent: OrderIntent) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id="broker-000001",
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        filled_quantity=0,
        limit_price=intent.limit_price,
        status=BrokerOrderStatus.ACCEPTED,
        submitted_at=_BROKER_TIME,
        updated_at=_BROKER_TIME,
    )


def test_broker_updated_at_is_preserved_not_replaced_by_database_clock(
    migrated_postgres: str,
) -> None:
    intent = _seed_intent(migrated_postgres)

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.orders.record_broker_order(_mirror(intent))
        unit_of_work.commit()
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        mirror = unit_of_work.orders.get_broker_order(intent.client_order_id)
        unit_of_work.commit()

    assert mirror is not None
    assert mirror.updated_at == _BROKER_TIME


def test_consumer_orders_by_broker_time_not_by_database_time(migrated_postgres: str) -> None:
    intent = _seed_intent(migrated_postgres)
    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        unit_of_work.orders.record_broker_order(_mirror(intent))
        unit_of_work.commit()

    with PostgresUnitOfWork(migrated_postgres) as unit_of_work:
        update = OrderStatusUpdate(
            client_order_id=intent.client_order_id,
            broker_order_id="broker-000001",
            status=BrokerOrderStatus.PARTIALLY_FILLED,
            filled_quantity=4,
            observed_at=_LATER_BROKER_TIME,
        )
        outcome = TradeUpdateConsumer().apply(unit_of_work, update)
        unit_of_work.commit()

    assert outcome is TradeUpdateOutcome.APPLIED
