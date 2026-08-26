# mypy: ignore-errors
"""Reproduction suite: paused entries must never reach the broker.

Defect A (pause bypass): ``submit_from_outbox`` has no control-state
dependency, so ``pause_entries`` only blocks the operator shell, never the
engine path.  These tests fail on the pre-fix engine and lock the intended
contract: a pause must fail closed in the engine before any broker call,
while emergency risk exits (RISK_EXIT) must stay available.
"""

from __future__ import annotations

import pytest

from fakes.control import FakeControlRepository
from fakes.orders import FakeOrderRepository
from seven_lens.application.execution_service import (
    ControlPersistenceError,
    ExecutionEngine,
    ExecutionPausedError,
)
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import FakePaperBroker, FakeSubmitOutcome, FakeSubmitPlan
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

_BASE_TIME = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TRADING_DATE = TradingDate.from_isoformat("2026-08-17")


class MutableClock:
    def __init__(self, now: UtcTimestamp = _BASE_TIME) -> None:
        self.now = now

    def __call__(self) -> UtcTimestamp:
        return self.now


class FakeOrderUnitOfWork:
    def __init__(
        self, orders: FakeOrderRepository, control: FakeControlRepository | None = None
    ) -> None:
        self.orders = orders
        if control is not None:
            self.control = control
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class DurabilityGuardBroker:
    """Fails the test unless SUBMITTING is durable before any broker submit call."""

    def __init__(self, inner: FakePaperBroker, orders: FakeOrderRepository) -> None:
        self._inner = inner
        self._orders = orders
        self.submit_calls = 0

    def submit_order(self, intent: OrderIntent):  # type: ignore[no-untyped-def]
        current = self._orders.get(intent.client_order_id)
        assert current is not None and current.status in (
            OrderStatus.SUBMITTING,
            OrderStatus.UNKNOWN,
        ), "engine must durably persist SUBMITTING (or a resolved UNKNOWN) before broker calls"
        self.submit_calls += 1
        return self._inner.submit_order(intent)

    def get_order(self, client_order_id):  # type: ignore[no-untyped-def]
        return self._inner.get_order(client_order_id)

    def list_fills(self, broker_order_id: str):  # type: ignore[no-untyped-def]
        return self._inner.list_fills(broker_order_id)

    def cancel_order(self, broker_order_id: str) -> bool:
        return self._inner.cancel_order(broker_order_id)

    def list_open_orders(self):  # type: ignore[no-untyped-def]
        return self._inner.list_open_orders()

    def account(self):  # type: ignore[no-untyped-def]
        return self._inner.account()

    def list_positions(self):  # type: ignore[no-untyped-def]
        return self._inner.list_positions()

    def list_recent_orders(self, *, since):  # type: ignore[no-untyped-def]
        return self._inner.list_recent_orders(since=since)

    def get_asset(self, symbol):  # type: ignore[no-untyped-def]
        return self._inner.get_asset(symbol)


def _intent(*, intent_type: OrderIntentType = OrderIntentType.REBALANCE) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TRADING_DATE,
        window="open",
        target_version=1,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=intent_type,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE_TIME,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_BASE_TIME,
    )


def _outbox_intent(orders: FakeOrderRepository, intent: OrderIntent) -> OrderIntent:
    orders.add(intent)
    orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
    orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
    return orders.get(intent.client_order_id) or intent


def _engine(
    orders: FakeOrderRepository,
    broker: FakePaperBroker,
    *,
    paused: bool,
) -> tuple[ExecutionEngine, FakeOrderUnitOfWork, DurabilityGuardBroker]:
    guard = DurabilityGuardBroker(broker, orders)
    control = FakeControlRepository(_BASE_TIME)
    if paused:
        control.set_entries_paused(True, "reproduction: entries paused")
    engine = ExecutionEngine(broker=guard, clock=MutableClock(), control=control)
    unit_of_work = FakeOrderUnitOfWork(orders, control)
    return engine, unit_of_work, guard


class TestPauseBlocksSubmissions:
    def test_paused_engine_rejects_new_entry_before_any_broker_call(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders, _intent())
        engine, unit_of_work, guard = _engine(
            orders, FakePaperBroker(clock=MutableClock()), paused=True
        )

        with pytest.raises(ExecutionPausedError, match="paused"):
            engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        current = orders.get(intent.client_order_id)
        assert current is not None and current.status is OrderStatus.OUTBOX_PENDING
        assert guard.submit_calls == 0
        assert unit_of_work.commit_count == 0

    def test_resume_unblocks_submission_without_rebuilding_the_engine(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders, _intent())
        guard = DurabilityGuardBroker(FakePaperBroker(clock=MutableClock()), orders)
        control = FakeControlRepository(_BASE_TIME)
        control.set_entries_paused(True, "reproduction: entries paused")
        engine = ExecutionEngine(broker=guard, clock=MutableClock(), control=control)
        unit_of_work = FakeOrderUnitOfWork(orders)
        with pytest.raises(ExecutionPausedError):
            engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        control.set_entries_paused(False, None)
        result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert result.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 1

    def test_unpaused_submission_still_reaches_acknowledged(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders, _intent())
        engine, unit_of_work, guard = _engine(
            orders, FakePaperBroker(clock=MutableClock()), paused=False
        )

        result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert result.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 1

    def test_paused_recovery_never_resubmits_a_missing_order(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders, _intent())
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)
        guard = DurabilityGuardBroker(FakePaperBroker(clock=MutableClock()), orders)
        control = FakeControlRepository(_BASE_TIME)
        control.set_entries_paused(True, "reproduction: pause raced with reservation")
        engine = ExecutionEngine(broker=guard, clock=MutableClock(), control=control)
        unit_of_work = FakeOrderUnitOfWork(orders)

        result = engine.resolve(unit_of_work, intent.client_order_id)

        assert result.status is OrderStatus.UNKNOWN
        assert guard.submit_calls == 0


class TestPauseAllowsRiskExit:
    def test_emergency_risk_exit_submission_is_never_blocked_by_pause(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders, _intent(intent_type=OrderIntentType.RISK_EXIT))
        engine, unit_of_work, guard = _engine(
            orders, FakePaperBroker(clock=MutableClock()), paused=True
        )

        result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert result.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 1


class TestAmbiguousPauseDurability:
    def test_audit_failure_keeps_unknown_and_pause_durable_and_is_observable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(
            clock=MutableClock(),
            plans={
                intent.client_order_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.TIMEOUT_AFTER_ACCEPT
                )
            },
        )
        engine, unit_of_work, guard = _engine(orders, broker, paused=False)
        _outbox_intent(orders, intent)

        def fail_audit(_record: object) -> object:
            raise RuntimeError("injected audit repository failure")

        monkeypatch.setattr(unit_of_work.control, "add_command", fail_audit)
        with pytest.raises(ControlPersistenceError, match="audit"):
            engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        current = orders.get(intent.client_order_id)
        assert current is not None and current.status is OrderStatus.UNKNOWN
        assert unit_of_work.control.state().entries_paused is True
        assert guard.submit_calls == 1


class TestPauseNeverBlocksRiskReduction:
    def test_cancel_and_expiry_stay_available_while_paused(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders, _intent())
        broker = FakePaperBroker(clock=MutableClock())
        guard = DurabilityGuardBroker(broker, orders)
        control = FakeControlRepository(_BASE_TIME)
        engine = ExecutionEngine(broker=guard, clock=MutableClock(now=_CANCEL_AT), control=control)
        unit_of_work = FakeOrderUnitOfWork(orders)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None

        control.set_entries_paused(True, "reproduction: entries paused")
        closed = engine.expire_overdue(unit_of_work)

        assert closed[0].status is OrderStatus.CANCELED
        assert guard.submit_calls == 1
