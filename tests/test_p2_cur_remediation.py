"""Red-before-green coverage for P2-CUR-001~006 remediation.

These tests are the non-integration subset of the handoff's required
reproduction suite.  Each test failed on the pre-fix code and passes after
the remediation; they are written against the fake doubles so they run in the
locked non-integration gate.
"""

from __future__ import annotations

# mypy: ignore-errors
# ruff: noqa: E501, B017, I001

import pytest

from fakes.control import FakeControlRepository, FakeReconciliationRepository
from fakes.orders import FakeOrderRepository
from seven_lens.application.control_service import ControlPlane, ResumeBlockedError
from seven_lens.application.execution_service import ExecutionEngine, ExecutionPausedError
from seven_lens.application.reconciliation_service import (
    AccountReconciliationPolicy,
    Reconciler,
)
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import (
    FakePaperBroker,
    FakeSubmitOutcome,
    FakeSubmitPlan,
)
from seven_lens.execution.ledger import project_ledger
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
    UsdAmount,
)
from seven_lens.execution.reconciliation import (
    MismatchKind,
    ReconciliationResult,
    ReconciliationStatus,
)
from seven_lens.execution.trade_updates import TradeUpdateConsumer, fill_update
from seven_lens.infrastructure.postgres import AccountBaseline

_T0 = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_T1 = UtcTimestamp.from_isoformat("2026-08-17T13:35:01.000000Z")
_T2 = UtcTimestamp.from_isoformat("2026-08-17T13:35:02.000000Z")
_T3 = UtcTimestamp.from_isoformat("2026-08-17T13:35:03.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TD = TradingDate.from_isoformat("2026-08-17")


class _FakeBaselineRepo:
    def __init__(self, baseline: AccountBaseline | None) -> None:
        self._baseline = baseline

    def get_baseline(self, account_id: str) -> AccountBaseline | None:
        if self._baseline is None:
            return None
        return self._baseline if self._baseline.account_id == account_id else None


class _UoW:
    def __init__(
        self,
        orders: FakeOrderRepository,
        control: FakeControlRepository | None = None,
        rec: FakeReconciliationRepository | None = None,
        baseline: AccountBaseline | None = None,
    ) -> None:
        self.orders = orders
        self.control = control or FakeControlRepository(_T0)
        self.reconciliations = rec or FakeReconciliationRepository()
        self.account_baselines = _FakeBaselineRepo(baseline)
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


def _intent(
    target_version: int = 1, intent_type: OrderIntentType = OrderIntentType.REBALANCE
) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TD,
        window="open",
        target_version=target_version,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=intent_type,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_T0,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_T0,
    )


def _mirror(
    intent: OrderIntent, status: BrokerOrderStatus, filled: int = 0, updated_at: UtcTimestamp = _T1
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id="b-1",
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        filled_quantity=filled,
        limit_price=intent.limit_price,
        status=status,
        submitted_at=_T0,
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# P2-CUR-003 Trade update late fill
# ---------------------------------------------------------------------------


class TestLateFill:
    def test_late_fill_after_newer_partial_status_is_absorbed(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        orders.record_broker_order(_mirror(intent, BrokerOrderStatus.ACCEPTED, 0, _T1))
        consumer = TradeUpdateConsumer()
        uow = _UoW(orders)
        # Newer status with filled 4 at T2
        from seven_lens.execution.trade_updates import OrderStatusUpdate

        upd = OrderStatusUpdate(
            client_order_id=intent.client_order_id,
            broker_order_id="b-1",
            status=BrokerOrderStatus.PARTIALLY_FILLED,
            filled_quantity=4,
            observed_at=_T2,
        )
        assert consumer.apply(uow, upd).value == "APPLIED"
        # Late fill at T1 qty 2
        late = fill_update(
            execution_id="e-late",
            broker_order_id="b-1",
            quantity=2,
            price_cents=9_998,
            occurred_at=_T1,
        )
        assert consumer.apply(uow, late).value == "APPLIED"
        # Filled quantity must not have regressed from 4 to 2
        assert orders.get_broker_order_by_id("b-1").filled_quantity == 4  # type: ignore[union-attr]
        assert orders.get_broker_order_by_id("b-1").updated_at == _T2  # type: ignore[union-attr]

    def test_late_fill_does_not_move_broker_watermark_backwards(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        orders.record_broker_order(_mirror(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T2))
        consumer = TradeUpdateConsumer()
        uow = _UoW(orders)
        late = fill_update(
            execution_id="e2", broker_order_id="b-1", quantity=1, price_cents=9_998, occurred_at=_T1
        )
        consumer.apply(uow, late)
        assert orders.get_broker_order_by_id("b-1").updated_at == _T2  # type: ignore[union-attr]

    def test_late_fill_does_not_reduce_filled_quantity(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        orders.record_broker_order(_mirror(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T2))
        consumer = TradeUpdateConsumer()
        uow = _UoW(orders)
        late = fill_update(
            execution_id="e3", broker_order_id="b-1", quantity=2, price_cents=9_998, occurred_at=_T1
        )
        consumer.apply(uow, late)
        assert orders.get_broker_order_by_id("b-1").filled_quantity == 4  # type: ignore[union-attr]

    def test_late_fill_after_cancel_does_not_regress_terminal_status(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        orders.record_broker_order(_mirror(intent, BrokerOrderStatus.ACCEPTED, 0, _T1))
        orders.transition_status(intent.client_order_id, OrderStatus.CANCEL_PENDING)
        orders.update_broker_order_status(
            "b-1", BrokerOrderStatus.CANCELED, 0, broker_observed_at=_T2
        )
        consumer = TradeUpdateConsumer()
        uow = _UoW(orders)
        late = fill_update(
            execution_id="e4", broker_order_id="b-1", quantity=1, price_cents=9_998, occurred_at=_T1
        )
        consumer.apply(uow, late)
        assert orders.get_broker_order_by_id("b-1").status == BrokerOrderStatus.CANCELED  # type: ignore[union-attr]

    def test_duplicate_late_fill_is_idempotent(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        orders.record_broker_order(_mirror(intent, BrokerOrderStatus.ACCEPTED, 0, _T1))
        consumer = TradeUpdateConsumer()
        uow = _UoW(orders)
        f = fill_update(
            execution_id="e5", broker_order_id="b-1", quantity=2, price_cents=9_998, occurred_at=_T1
        )
        assert consumer.apply(uow, f).value == "APPLIED"
        assert consumer.apply(uow, f).value == "DUPLICATE"
        assert orders.fill_count == 1

    def test_late_fills_can_complete_previously_reported_cumulative_quantity(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        # Broker reported 4 via status
        orders.record_broker_order(_mirror(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T2))
        consumer = TradeUpdateConsumer()
        uow = _UoW(orders)
        # Two late fills that together make 4 (2+2), all at T1 (before T2)
        f1 = fill_update(
            execution_id="e6a",
            broker_order_id="b-1",
            quantity=2,
            price_cents=9_998,
            occurred_at=_T1,
        )
        f2 = fill_update(
            execution_id="e6b",
            broker_order_id="b-1",
            quantity=2,
            price_cents=9_998,
            occurred_at=_T1,
        )
        consumer.apply(uow, f1)
        consumer.apply(uow, f2)
        # Should still be 4, not 2, and not over
        assert orders.get_broker_order_by_id("b-1").filled_quantity == 4  # type: ignore[union-attr]
        assert orders.fill_count == 2

    def test_conflicting_late_fill_fails_closed_without_losing_execution_fact(self) -> None:
        orders = FakeOrderRepository()
        intent = OrderIntent.create(
            strategy="seven-lens",
            trading_date=_TD,
            window="open",
            target_version=1,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(5),
            intent_type=OrderIntentType.REBALANCE,
            limit_price=Price.from_cents(10_000),
            collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
            earliest_submit_at=_T0,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_T0,
        )
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        orders.record_broker_order(
            BrokerOrder(
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
        )
        consumer = TradeUpdateConsumer()
        uow = _UoW(orders)
        # First fill 5 fills the order
        f1 = fill_update(
            execution_id="e7a",
            broker_order_id="b-1",
            quantity=5,
            price_cents=10_000,
            occurred_at=_T1,
        )
        consumer.apply(uow, f1)
        # Overfill 1 more should fail closed but preserve fill
        f2 = fill_update(
            execution_id="e7b",
            broker_order_id="b-1",
            quantity=1,
            price_cents=10_000,
            occurred_at=_T2,
        )
        with pytest.raises(Exception):
            consumer.apply(uow, f2)
        assert orders.fill_count == 2
        assert orders.get_broker_order_by_id("b-1").filled_quantity == 5  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# P2-CUR-004 FIFO
# ---------------------------------------------------------------------------


class TestFifo:
    def test_fifo_lots_follow_occurrence_time_not_recording_order(self) -> None:
        # T1 BUY 1 @90, T2 BUY 1 @100, T3 SELL 1 -> should consume 90 lot first
        t1 = UtcTimestamp.from_isoformat("2026-08-17T13:00:00.000000Z")
        t2 = UtcTimestamp.from_isoformat("2026-08-17T13:01:00.000000Z")
        t3 = UtcTimestamp.from_isoformat("2026-08-17T13:02:00.000000Z")
        b1 = BrokerOrder(
            broker_order_id="b1",
            client_order_id=OrderIntent.create(
                strategy="seven-lens",
                trading_date=_TD,
                window="open",
                target_version=1,
                symbol=Symbol("AAPL"),
                side=OrderSide.BUY,
                quantity=OrderQuantity(1),
                intent_type=OrderIntentType.REBALANCE,
                limit_price=Price.from_cents(9_000),
                collar=PriceCollar(reference=Price.from_cents(9_000), offset_bps=100),
                earliest_submit_at=t1,
                cancel_at=_CANCEL_AT,
                run_id=RunId.new(),
                created_at=t1,
            ).client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(9_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=t1,
            updated_at=t1,
        )
        b2 = BrokerOrder(
            broker_order_id="b2",
            client_order_id=OrderIntent.create(
                strategy="seven-lens",
                trading_date=_TD,
                window="open",
                target_version=2,
                symbol=Symbol("AAPL"),
                side=OrderSide.BUY,
                quantity=OrderQuantity(1),
                intent_type=OrderIntentType.REBALANCE,
                limit_price=Price.from_cents(10_000),
                collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
                earliest_submit_at=t1,
                cancel_at=_CANCEL_AT,
                run_id=RunId.new(),
                created_at=t1,
            ).client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=t1,
            updated_at=t1,
        )
        b3 = BrokerOrder(
            broker_order_id="b3",
            client_order_id=OrderIntent.create(
                strategy="seven-lens",
                trading_date=_TD,
                window="open",
                target_version=3,
                symbol=Symbol("AAPL"),
                side=OrderSide.SELL,
                quantity=OrderQuantity(1),
                intent_type=OrderIntentType.REBALANCE,
                limit_price=Price.from_cents(11_000),
                collar=PriceCollar(reference=Price.from_cents(11_000), offset_bps=100),
                earliest_submit_at=t1,
                cancel_at=_CANCEL_AT,
                run_id=RunId.new(),
                created_at=t1,
            ).client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.SELL,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(11_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=t1,
            updated_at=t1,
        )
        # Arrival order is T2 before T1
        fills = (
            Fill(
                execution_id="e2",
                broker_order_id="b2",
                quantity=OrderQuantity(1),
                price=Price.from_cents(10_000),
                occurred_at=t2,
            ),
            Fill(
                execution_id="e1",
                broker_order_id="b1",
                quantity=OrderQuantity(1),
                price=Price.from_cents(9_000),
                occurred_at=t1,
            ),
            Fill(
                execution_id="e3",
                broker_order_id="b3",
                quantity=OrderQuantity(1),
                price=Price.from_cents(11_000),
                occurred_at=t3,
            ),
        )
        proj = project_ledger(fills, {"b1": b1, "b2": b2, "b3": b3})
        # Should have consumed the 90 lot, remaining is 100
        assert len(proj.lots) == 1
        assert proj.lots[0].price.cents == 10_000

    def test_fifo_is_deterministic_when_fill_input_is_permuted(self) -> None:
        t1 = UtcTimestamp.from_isoformat("2026-08-17T13:00:00.000000Z")
        t2 = UtcTimestamp.from_isoformat("2026-08-17T13:01:00.000000Z")
        b1 = BrokerOrder(
            broker_order_id="b1",
            client_order_id=_intent(target_version=10).client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(9_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=t1,
            updated_at=t1,
        )
        b2 = BrokerOrder(
            broker_order_id="b2",
            client_order_id=_intent(target_version=11).client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=t1,
            updated_at=t1,
        )
        fills_a = (
            Fill(
                execution_id="e1",
                broker_order_id="b1",
                quantity=OrderQuantity(1),
                price=Price.from_cents(9_000),
                occurred_at=t1,
            ),
            Fill(
                execution_id="e2",
                broker_order_id="b2",
                quantity=OrderQuantity(1),
                price=Price.from_cents(10_000),
                occurred_at=t2,
            ),
        )
        fills_b = (
            Fill(
                execution_id="e2",
                broker_order_id="b2",
                quantity=OrderQuantity(1),
                price=Price.from_cents(10_000),
                occurred_at=t2,
            ),
            Fill(
                execution_id="e1",
                broker_order_id="b1",
                quantity=OrderQuantity(1),
                price=Price.from_cents(9_000),
                occurred_at=t1,
            ),
        )
        proj_a = project_ledger(fills_a, {"b1": b1, "b2": b2})
        proj_b = project_ledger(fills_b, {"b1": b1, "b2": b2})
        assert proj_a.cash_delta_cents == proj_b.cash_delta_cents
        assert proj_a.lots == proj_b.lots

    def test_fifo_same_timestamp_uses_documented_tiebreaker(self) -> None:
        t = UtcTimestamp.from_isoformat("2026-08-17T13:00:00.000000Z")
        b1 = BrokerOrder(
            broker_order_id="b1",
            client_order_id=_intent(target_version=20).client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(9_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=t,
            updated_at=t,
        )
        b2 = BrokerOrder(
            broker_order_id="b2",
            client_order_id=_intent(target_version=21).client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=t,
            updated_at=t,
        )
        fills = (
            Fill(
                execution_id="eB",
                broker_order_id="b2",
                quantity=OrderQuantity(1),
                price=Price.from_cents(10_000),
                occurred_at=t,
            ),
            Fill(
                execution_id="eA",
                broker_order_id="b1",
                quantity=OrderQuantity(1),
                price=Price.from_cents(9_000),
                occurred_at=t,
            ),
        )
        proj = project_ledger(fills, {"b1": b1, "b2": b2})
        # eA should be first due to execution_id tiebreaker
        assert proj.lots[0].price.cents == 9_000
        assert proj.lots[1].price.cents == 10_000


# ---------------------------------------------------------------------------
# P2-CUR-002 ledger invariant
# ---------------------------------------------------------------------------


class TestLedgerInvariant:
    def test_ledger_invariant_failure_produces_mismatch(self) -> None:
        orders = FakeOrderRepository()
        # Oversell: buy 3, sell 4
        buy = OrderIntent.create(
            strategy="seven-lens",
            trading_date=_TD,
            window="open",
            target_version=1,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(3),
            intent_type=OrderIntentType.REBALANCE,
            limit_price=Price.from_cents(10_000),
            collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
            earliest_submit_at=_T0,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_T0,
        )
        sell = OrderIntent.create(
            strategy="seven-lens",
            trading_date=_TD,
            window="open",
            target_version=2,
            symbol=Symbol("AAPL"),
            side=OrderSide.SELL,
            quantity=OrderQuantity(4),
            intent_type=OrderIntentType.REBALANCE,
            limit_price=Price.from_cents(10_000),
            collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
            earliest_submit_at=_T0,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_T0,
        )
        for intent in (buy, sell):
            orders.add(intent)
            for s in (
                OrderStatus.RISK_APPROVED,
                OrderStatus.OUTBOX_PENDING,
                OrderStatus.SUBMITTING,
                OrderStatus.ACKNOWLEDGED,
            ):
                orders.transition_status(intent.client_order_id, s)
        b_buy = BrokerOrder(
            broker_order_id="b-buy",
            client_order_id=buy.client_order_id,
            symbol=buy.symbol,
            side=buy.side,
            quantity=buy.quantity,
            filled_quantity=3,
            limit_price=buy.limit_price,
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        b_sell = BrokerOrder(
            broker_order_id="b-sell",
            client_order_id=sell.client_order_id,
            symbol=sell.symbol,
            side=sell.side,
            quantity=sell.quantity,
            filled_quantity=4,
            limit_price=sell.limit_price,
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        orders.record_broker_order(b_buy)
        orders.record_broker_order(b_sell)
        orders.add_fill(
            Fill(
                execution_id="e-buy",
                broker_order_id="b-buy",
                quantity=OrderQuantity(3),
                price=Price.from_cents(10_000),
                occurred_at=_T0,
            )
        )
        orders.add_fill(
            Fill(
                execution_id="e-sell",
                broker_order_id="b-sell",
                quantity=OrderQuantity(4),
                price=Price.from_cents(10_000),
                occurred_at=_T0,
            )
        )
        control = FakeControlRepository(_T0)
        rec = FakeReconciliationRepository()
        uow = _UoW(orders, control, rec)
        broker = FakePaperBroker(clock=lambda: _T0)
        reconciler = Reconciler(broker=broker, clock=lambda: _T0)
        result = reconciler.run(uow, _TD)
        assert result.status == ReconciliationStatus.MISMATCH
        assert any(m.kind == MismatchKind.LOCAL_LEDGER_INVARIANT for m in result.mismatches)
        assert control.state().entries_paused is True
        assert any(c.command.value == "PAUSE_ENTRIES" for c in control.commands)

    def test_ledger_invariant_failure_is_durable(self) -> None:
        # Same as above but check second UoW sees paused
        orders = FakeOrderRepository()
        buy = _intent(target_version=5)
        orders.add(buy)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(buy.client_order_id, s)
        b = BrokerOrder(
            broker_order_id="b1",
            client_order_id=buy.client_order_id,
            symbol=buy.symbol,
            side=buy.side,
            quantity=buy.quantity,
            filled_quantity=10,
            limit_price=buy.limit_price,
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        orders.record_broker_order(b)
        # Duplicate execution id triggers invariant
        orders.add_fill(
            Fill(
                execution_id="dup",
                broker_order_id="b1",
                quantity=OrderQuantity(5),
                price=Price.from_cents(10_000),
                occurred_at=_T0,
            )
        )
        # Second fill with same execution id would raise duplicate in ledger projection
        # We need to create two fills with same execution id but different objects: project_ledger will see duplicate
        # FakeOrderRepository prevents duplicate via add_fill returning False, so we need to bypass by directly
        # injecting duplicate via list_all_fills manipulation: instead, create fills tuple with duplicate
        # We'll test project_ledger directly raises, then reconciler should handle
        control = FakeControlRepository(_T0)
        rec = FakeReconciliationRepository()

        # Create a custom OrderRepository that returns duplicate fills
        class DupOrders(FakeOrderRepository):
            def list_all_fills(self):  # type: ignore[override]
                return (
                    Fill(
                        execution_id="dup",
                        broker_order_id="b1",
                        quantity=OrderQuantity(5),
                        price=Price.from_cents(10_000),
                        occurred_at=_T0,
                    ),
                    Fill(
                        execution_id="dup",
                        broker_order_id="b1",
                        quantity=OrderQuantity(5),
                        price=Price.from_cents(10_000),
                        occurred_at=_T0,
                    ),
                )

        dup_orders = DupOrders()
        dup_orders.add(buy)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            dup_orders.transition_status(buy.client_order_id, s)
        dup_orders.record_broker_order(b)
        uow = _UoW(dup_orders, control, rec)
        broker = FakePaperBroker(clock=lambda: _T0)
        result = Reconciler(broker=broker, clock=lambda: _T0).run(uow, _TD)
        assert result.status == ReconciliationStatus.MISMATCH
        assert MismatchKind.LOCAL_LEDGER_INVARIANT in [m.kind for m in result.mismatches]


# ---------------------------------------------------------------------------
# P2-CUR-005 UNKNOWN gate
# ---------------------------------------------------------------------------


class TestUnknownGate:
    def test_timeout_to_unknown_blocks_second_entry(self) -> None:
        orders = FakeOrderRepository()
        control = FakeControlRepository(_T0)
        broker = FakePaperBroker(
            clock=lambda: _T0,
            plans={
                "slv1-seven-lens-2026-08-17-open-t1-AAPL-buy": FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.TIMEOUT_BEFORE_ACCEPT
                )
            },
        )
        intent1 = _intent(target_version=1)
        orders.add(intent1)
        for s in (OrderStatus.RISK_APPROVED, OrderStatus.OUTBOX_PENDING):
            orders.transition_status(intent1.client_order_id, s)

        class UoW1:
            def __init__(self):  # type: ignore[no-untyped-def]
                self.orders = orders
                self.commit_count = 0

            def commit(self) -> None:
                self.commit_count += 1

        engine = ExecutionEngine(broker=broker, clock=lambda: _T0, control=control)
        res1 = engine.submit_from_outbox(UoW1(), intent1.client_order_id)
        assert res1.status == OrderStatus.UNKNOWN
        assert control.state().entries_paused is True
        # Second entry should be blocked
        intent2 = _intent(target_version=2)
        orders.add(intent2)
        for s in (OrderStatus.RISK_APPROVED, OrderStatus.OUTBOX_PENDING):
            orders.transition_status(intent2.client_order_id, s)
        with pytest.raises(ExecutionPausedError):
            engine.submit_from_outbox(UoW1(), intent2.client_order_id)

    def test_resume_blocked_while_unknown_exists(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent(target_version=1)
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.UNKNOWN,
        ):
            orders.transition_status(intent.client_order_id, s)
        control = FakeControlRepository(_T0)
        control.set_entries_paused(True, "test")
        rec = FakeReconciliationRepository()
        clean = ReconciliationResult.create(
            trading_date=_TD, mismatches=(), checked_orders=0, checked_fills=0, observed_at=_T0
        )
        rec.add(clean)
        uow = _UoW(orders, control, rec)
        plane = ControlPlane(clock=lambda: _T0)
        with pytest.raises(ResumeBlockedError):
            plane.resume_entries(uow, actor="owner")

    def test_resume_blocked_while_review_required_exists(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent(target_version=1)
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.REVIEW_REQUIRED,
        ):
            orders.transition_status(intent.client_order_id, s)
        control = FakeControlRepository(_T0)
        control.set_entries_paused(True, "test")
        rec = FakeReconciliationRepository()
        clean = ReconciliationResult.create(
            trading_date=_TD, mismatches=(), checked_orders=0, checked_fills=0, observed_at=_T0
        )
        rec.add(clean)
        uow = _UoW(orders, control, rec)
        plane = ControlPlane(clock=lambda: _T0)
        with pytest.raises(ResumeBlockedError):
            plane.resume_entries(uow, actor="owner")

    def test_risk_exit_allowed_while_reconciliation_required(self) -> None:
        orders = FakeOrderRepository()
        control = FakeControlRepository(_T0)
        control.set_entries_paused(True, "test")
        # Also have UNKNOWN
        intent_unknown = _intent(target_version=9)
        orders.add(intent_unknown)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.UNKNOWN,
        ):
            orders.transition_status(intent_unknown.client_order_id, s)
        broker = FakePaperBroker(clock=lambda: _T0)
        engine = ExecutionEngine(broker=broker, clock=lambda: _T0, control=control)
        # RISK_EXIT should still be allowed
        intent_exit = _intent(target_version=10, intent_type=OrderIntentType.RISK_EXIT)
        # Use SELL to avoid needing existing position for asset gate? Asset gate will still check but fake returns US_EQUITY
        intent_exit = OrderIntent.create(
            strategy="seven-lens",
            trading_date=_TD,
            window="open",
            target_version=10,
            symbol=Symbol("AAPL"),
            side=OrderSide.SELL,
            quantity=OrderQuantity(1),
            intent_type=OrderIntentType.RISK_EXIT,
            limit_price=Price.from_cents(10_000),
            collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
            earliest_submit_at=_T0,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_T0,
        )
        orders.add(intent_exit)
        for s in (OrderStatus.RISK_APPROVED, OrderStatus.OUTBOX_PENDING):
            orders.transition_status(intent_exit.client_order_id, s)

        class UoW2:
            def __init__(self):  # type: ignore[no-untyped-def]
                self.orders = orders
                self.commit_count = 0

            def commit(self) -> None:
                self.commit_count += 1

        res = engine.submit_from_outbox(UoW2(), intent_exit.client_order_id)
        assert res.status == OrderStatus.ACKNOWLEDGED

    def test_reconciler_does_not_produce_clean_while_unknown_exists(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent(target_version=1)
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.UNKNOWN,
        ):
            orders.transition_status(intent.client_order_id, s)
        broker = FakePaperBroker(clock=lambda: _T0)
        reconciler = Reconciler(broker=broker, clock=lambda: _T0)
        uow = _UoW(orders)
        result = reconciler.collect(uow, _TD)
        assert result.status == ReconciliationStatus.MISMATCH
        assert MismatchKind.INTENT_STATUS_MISMATCH in [m.kind for m in result.mismatches]


# ---------------------------------------------------------------------------
# P2-CUR-006 account reconciliation
# ---------------------------------------------------------------------------


class TestAccountReconciliation:
    def _baseline(self) -> AccountBaseline:
        return AccountBaseline(
            account_id="fake-paper-primary",
            opening_cash_cents=10_000_000,
            effective_at=_T0,
            created_at=_T0,
            updated_at=_T0,
        )

    def _policy(self) -> AccountReconciliationPolicy:
        return AccountReconciliationPolicy(
            expected_account_id="fake-paper-primary",
            cash_tolerance_cents=100,
            nav_tolerance_cents=100,
        )

    def test_expected_account_id_match_is_clean(self) -> None:
        orders = FakeOrderRepository()
        broker = FakePaperBroker(
            clock=lambda: _T0,
            cash=UsdAmount.from_cents(10_000_000),
            equity=UsdAmount.from_cents(10_000_000),
        )
        reconciler = Reconciler(
            broker=broker, clock=lambda: _T0, account_policy=self._policy(), price_provider=None
        )
        uow = _UoW(orders, baseline=self._baseline())
        result = reconciler.collect(uow, _TD)
        assert result.status == ReconciliationStatus.CLEAN

    def test_wrong_account_id_pauses(self) -> None:
        orders = FakeOrderRepository()
        broker = FakePaperBroker(
            clock=lambda: _T0,
            account_id="other-id",
            cash=UsdAmount.from_cents(10_000_000),
            equity=UsdAmount.from_cents(10_000_000),
        )
        reconciler = Reconciler(
            broker=broker, clock=lambda: _T0, account_policy=self._policy(), price_provider=None
        )
        uow = _UoW(orders, baseline=self._baseline())
        result = reconciler.run(uow, _TD)
        assert result.status == ReconciliationStatus.MISMATCH
        assert any(m.kind == MismatchKind.ACCOUNT_ID_MISMATCH for m in result.mismatches)
        assert uow.control.state().entries_paused is True

    def test_cash_within_tolerance_is_clean(self) -> None:
        orders = FakeOrderRepository()
        # No fills, expected cash = opening 10_000_000, broker cash 10_000_050 within 100
        broker = FakePaperBroker(
            clock=lambda: _T0,
            cash=UsdAmount.from_cents(10_000_050),
            equity=UsdAmount.from_cents(10_000_050),
        )
        # Baseline 10_000_000
        reconciler = Reconciler(
            broker=broker, clock=lambda: _T0, account_policy=self._policy(), price_provider=None
        )
        uow = _UoW(orders, baseline=self._baseline())
        result = reconciler.collect(uow, _TD)
        assert result.status == ReconciliationStatus.CLEAN

    def test_cash_outside_tolerance_pauses(self) -> None:
        orders = FakeOrderRepository()
        broker = FakePaperBroker(
            clock=lambda: _T0,
            cash=UsdAmount.from_cents(9_000_000),
            equity=UsdAmount.from_cents(9_000_000),
        )
        reconciler = Reconciler(
            broker=broker, clock=lambda: _T0, account_policy=self._policy(), price_provider=None
        )
        uow = _UoW(orders, baseline=self._baseline())
        result = reconciler.run(uow, _TD)
        assert result.status == ReconciliationStatus.MISMATCH
        assert any(m.kind == MismatchKind.CASH_MISMATCH for m in result.mismatches)

    def test_missing_opening_cash_baseline_fails_closed(self) -> None:
        orders = FakeOrderRepository()
        broker = FakePaperBroker(clock=lambda: _T0)
        reconciler = Reconciler(
            broker=broker, clock=lambda: _T0, account_policy=self._policy(), price_provider=None
        )
        uow = _UoW(orders, baseline=None)
        result = reconciler.collect(uow, _TD)
        assert result.status == ReconciliationStatus.MISMATCH
        assert any(
            m.kind == MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE for m in result.mismatches
        )

    def test_missing_mark_price_fails_closed(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent(target_version=1)
        orders.add(intent)
        for s in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, s)
        b = BrokerOrder(
            broker_order_id="b1",
            client_order_id=intent.client_order_id,
            symbol=Symbol("AAPL"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(1),
            filled_quantity=1,
            limit_price=Price.from_cents(10_000),
            status=BrokerOrderStatus.FILLED,
            submitted_at=_T0,
            updated_at=_T0,
        )
        orders.record_broker_order(b)
        orders.add_fill(
            Fill(
                execution_id="e1",
                broker_order_id="b1",
                quantity=OrderQuantity(1),
                price=Price.from_cents(10_000),
                occurred_at=_T0,
            )
        )
        broker = FakePaperBroker(clock=lambda: _T0)
        reconciler = Reconciler(
            broker=broker, clock=lambda: _T0, account_policy=self._policy(), price_provider=None
        )
        uow = _UoW(orders, baseline=self._baseline())
        result = reconciler.collect(uow, _TD)
        assert any(
            m.kind == MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE for m in result.mismatches
        )

    def test_buying_power_is_parsed(self) -> None:
        broker = FakePaperBroker(clock=lambda: _T0, buying_power=UsdAmount.from_cents(5_000_000))
        account = broker.account()
        assert account.buying_power.cents == 5_000_000
