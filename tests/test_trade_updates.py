# mypy: ignore-errors
"""Trade-update consumer tests: duplicates, out-of-order, and fail-closed edges."""

from __future__ import annotations

# mypy: ignore-errors
import pytest

from fakes.orders import FakeOrderRepository
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
    FillUpdate,
    OrderStatusUpdate,
    TradeUpdateConsumer,
    TradeUpdateError,
    TradeUpdateOutcome,
)

_T0 = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_T1 = UtcTimestamp.from_isoformat("2026-08-17T13:35:01.000000Z")
_T2 = UtcTimestamp.from_isoformat("2026-08-17T13:35:02.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TRADING_DATE = TradingDate.from_isoformat("2026-08-17")


class _UnitOfWork:
    def __init__(self, orders: FakeOrderRepository) -> None:
        self.orders = orders
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


def _setup() -> tuple[_UnitOfWork, OrderIntent, BrokerOrder]:
    intent = OrderIntent.create(
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
        earliest_submit_at=_T0,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_T0,
    )
    orders = FakeOrderRepository()
    orders.add(intent)
    for status in (
        OrderStatus.RISK_APPROVED,
        OrderStatus.OUTBOX_PENDING,
        OrderStatus.SUBMITTING,
        OrderStatus.ACKNOWLEDGED,
    ):
        orders.transition_status(intent.client_order_id, status)
    mirror = BrokerOrder(
        broker_order_id="b-1",
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        filled_quantity=0,
        limit_price=intent.limit_price,
        status=BrokerOrderStatus.ACCEPTED,
        submitted_at=_T0,
        updated_at=_T1,
    )
    orders.record_broker_order(mirror)
    return _UnitOfWork(orders), intent, mirror


def _fill_update(execution_id: str, quantity: int, occurred_at: UtcTimestamp) -> FillUpdate:
    from seven_lens.execution.orders import Fill

    return FillUpdate(
        fill=Fill(
            execution_id=execution_id,
            broker_order_id="b-1",
            quantity=OrderQuantity(quantity),
            price=Price.from_cents(9_998),
            occurred_at=occurred_at,
        )
    )


def _status_update(
    intent: OrderIntent, status: BrokerOrderStatus, filled: int, at: UtcTimestamp
) -> OrderStatusUpdate:
    return OrderStatusUpdate(
        client_order_id=intent.client_order_id,
        broker_order_id="b-1",
        status=status,
        filled_quantity=filled,
        observed_at=at,
    )


class TestFillUpdates:
    def test_partial_then_full_fill_and_duplicate_replay(self) -> None:
        unit_of_work, intent, mirror = _setup()
        consumer = TradeUpdateConsumer()

        first = consumer.apply(unit_of_work, _fill_update("e-1", 4, _T1))
        assert first is TradeUpdateOutcome.APPLIED
        after_first = unit_of_work.orders.get(intent.client_order_id)
        assert after_first is not None
        assert after_first.status is OrderStatus.PARTIALLY_FILLED

        duplicate = consumer.apply(unit_of_work, _fill_update("e-1", 4, _T1))
        assert duplicate is TradeUpdateOutcome.DUPLICATE
        assert unit_of_work.orders.fill_count == 1

        second = consumer.apply(unit_of_work, _fill_update("e-2", 6, _T2))
        assert second is TradeUpdateOutcome.APPLIED
        final = unit_of_work.orders.get(intent.client_order_id)
        assert final is not None and final.status is OrderStatus.FILLED
        refreshed_mirror = unit_of_work.orders.get_broker_order_by_id(mirror.broker_order_id)
        assert refreshed_mirror is not None and refreshed_mirror.filled_quantity == 10

    def test_overfill_fails_closed(self) -> None:
        unit_of_work, _, _ = _setup()
        consumer = TradeUpdateConsumer()
        consumer.apply(unit_of_work, _fill_update("e-1", 8, _T1))
        with pytest.raises(TradeUpdateError, match="exceed"):
            consumer.apply(unit_of_work, _fill_update("e-2", 3, _T2))

    def test_unknown_broker_order_is_not_guessed(self) -> None:
        from dataclasses import replace

        unit_of_work, _, _ = _setup()
        consumer = TradeUpdateConsumer()
        orphan = FillUpdate(
            fill=replace(_fill_update("e-9", 1, _T1).fill, broker_order_id="missing")
        )
        assert consumer.apply(unit_of_work, orphan) is TradeUpdateOutcome.UNKNOWN_ORDER
        assert unit_of_work.orders.fill_count == 0


class TestStatusUpdates:
    def test_out_of_order_update_is_stale_and_changes_nothing(self) -> None:
        unit_of_work, intent, _ = _setup()
        consumer = TradeUpdateConsumer()
        consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T2)
        )

        stale = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.ACCEPTED, 0, _T1)
        )

        assert stale is TradeUpdateOutcome.STALE
        current = unit_of_work.orders.get(intent.client_order_id)
        assert current is not None and current.status is OrderStatus.PARTIALLY_FILLED

    def test_unknown_intent_and_mismatched_broker_id_fail_closed(self) -> None:
        unit_of_work, intent, _ = _setup()
        consumer = TradeUpdateConsumer()
        forged = OrderStatusUpdate(
            client_order_id=intent.client_order_id,
            broker_order_id="b-other",
            status=BrokerOrderStatus.FILLED,
            filled_quantity=10,
            observed_at=_T2,
        )
        assert consumer.apply(unit_of_work, forged) is TradeUpdateOutcome.UNKNOWN_ORDER

    def test_cancel_observation_reaches_canceled(self) -> None:
        unit_of_work, intent, _ = _setup()
        consumer = TradeUpdateConsumer()
        outcome = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.CANCELED, 0, _T2)
        )
        assert outcome is TradeUpdateOutcome.APPLIED
        canceled = unit_of_work.orders.get(intent.client_order_id)
        assert canceled is not None and canceled.status is OrderStatus.CANCELED

    def test_pending_cancel_is_not_regressed_by_a_plain_live_status(self) -> None:
        unit_of_work, intent, _ = _setup()
        consumer = TradeUpdateConsumer()
        unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.CANCEL_PENDING)
        # A plain live-status replay changes nothing and is classified duplicate;
        # the pending cancel is never regressed either way.
        outcome = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.ACCEPTED, 0, _T2)
        )
        assert outcome is TradeUpdateOutcome.DUPLICATE
        current = unit_of_work.orders.get(intent.client_order_id)
        assert current is not None and current.status is OrderStatus.CANCEL_PENDING

    def test_replayed_status_event_is_a_duplicate_not_a_write(self) -> None:
        unit_of_work, intent, _ = _setup()
        consumer = TradeUpdateConsumer()
        first = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T2)
        )
        replay = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T2)
        )
        assert first is TradeUpdateOutcome.APPLIED
        assert replay is TradeUpdateOutcome.DUPLICATE
        current = unit_of_work.orders.get(intent.client_order_id)
        assert current is not None and current.status is OrderStatus.PARTIALLY_FILLED

    def test_input_contracts_are_enforced(self) -> None:
        unit_of_work, intent, _ = _setup()
        with pytest.raises(ValueError, match="ClientOrderId"):
            OrderStatusUpdate(
                client_order_id="not-a-client-id",  # type: ignore[arg-type]
                broker_order_id="b-1",
                status=BrokerOrderStatus.FILLED,
                filled_quantity=0,
                observed_at=_T1,
            )
        del unit_of_work, intent
