# mypy: ignore-errors
"""Unit tests for the control plane's audited, fail-closed operator levers."""

from __future__ import annotations

import pytest

from fakes.control import FakeControlRepository, FakeReconciliationRepository
from fakes.orders import FakeOrderRepository
from seven_lens.application.control_service import (
    ControlPlane,
    ControlPlaneError,
    LedgerFlattenPriceProvider,
    ResumeBlockedError,
)
from seven_lens.application.execution_service import ExecutionEngine
from seven_lens.application.ports.broker import (
    BrokerTransportError,
    RejectionReason,
    SubmitAccepted,
    SubmitRejected,
    SubmitResult,
)
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import (
    FakeCancelMode,
    FakeFillStep,
    FakePaperBroker,
    FakeSubmitOutcome,
    FakeSubmitPlan,
)
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
from seven_lens.execution.reconciliation import (
    MismatchKind,
    ReconciliationMismatch,
    ReconciliationResult,
    ReconciliationScope,
)

_BASE_TIME = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TRADING_DATE = TradingDate.from_isoformat("2026-08-17")


class _FixedClock:
    def __call__(self) -> UtcTimestamp:
        return _BASE_TIME


class _UnitOfWork:
    def __init__(self) -> None:
        self.orders = FakeOrderRepository()
        self.reconciliations = FakeReconciliationRepository()
        self.control = FakeControlRepository(_BASE_TIME)
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


def _intent(*, target_version: int, quantity: int = 10) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TRADING_DATE,
        window="open",
        target_version=target_version,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(quantity),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE_TIME,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_BASE_TIME,
    )


def _clean_run() -> ReconciliationResult:
    return ReconciliationResult.create(
        trading_date=_TRADING_DATE,
        mismatches=(),
        checked_orders=0,
        checked_fills=0,
        observed_at=_BASE_TIME,
        scope=ReconciliationScope.FULL,
    )


def _mismatch_run() -> ReconciliationResult:
    return ReconciliationResult.create(
        trading_date=_TRADING_DATE,
        mismatches=(ReconciliationMismatch(kind=MismatchKind.STATUS_MISMATCH, detail="b-1"),),
        checked_orders=1,
        checked_fills=0,
        observed_at=_BASE_TIME,
    )


class TestPauseAndResume:
    def test_pause_blocks_entries_and_records_the_command(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock())

        snapshot = plane.pause_entries(unit_of_work, reason="operator drill", actor="owner")

        assert snapshot.entries_paused is True
        assert plane.entries_allowed(unit_of_work) is False
        assert [record.command.value for record in unit_of_work.control.commands] == [
            "PAUSE_ENTRIES"
        ]
        with pytest.raises(ControlPlaneError, match="entries are paused"):
            plane.assert_entries_allowed(unit_of_work)

    def test_resume_requires_a_clean_reconciliation(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock())
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        with pytest.raises(ResumeBlockedError):
            plane.resume_entries(unit_of_work, actor="owner")
        unit_of_work.reconciliations.add(_mismatch_run())
        with pytest.raises(ResumeBlockedError):
            plane.resume_entries(unit_of_work, actor="owner")

        unit_of_work.reconciliations.add(
            ReconciliationResult.create(
                trading_date=_TRADING_DATE,
                mismatches=(),
                checked_orders=0,
                checked_fills=0,
                observed_at=_BASE_TIME,
            )
        )
        with pytest.raises(ResumeBlockedError, match="full-scope"):
            plane.resume_entries(unit_of_work, actor="owner")

        unit_of_work.reconciliations.add(_clean_run())
        snapshot = plane.resume_entries(unit_of_work, actor="owner")
        assert snapshot.entries_paused is False
        assert plane.entries_allowed(unit_of_work) is True


class TestCancelOpenOrders:
    def test_cancel_cancels_every_live_acknowledged_intent(self) -> None:
        unit_of_work = _UnitOfWork()
        broker = FakePaperBroker(clock=_FixedClock())
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane = ControlPlane(clock=_FixedClock())
        for version in (1, 2):
            intent = _intent(target_version=version)
            unit_of_work.orders.add(intent)
            unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
            unit_of_work.orders.transition_status(
                intent.client_order_id, OrderStatus.OUTBOX_PENDING
            )
            engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        canceled = plane.cancel_open_orders(
            unit_of_work, engine=engine, reason="drill", actor="owner"
        )

        assert [item.status.value for item in canceled] == ["CANCELED", "CANCELED"]
        assert [record.command.value for record in unit_of_work.control.commands] == [
            "CANCEL_OPEN_ORDERS"
        ]

    def test_cancel_transport_failure_records_partial_attempt_and_per_order_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit_of_work = _UnitOfWork()
        broker = FakePaperBroker(clock=_FixedClock())
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane = ControlPlane(clock=_FixedClock())
        intents: list[OrderIntent] = []
        for version in (1, 2):
            intent = _intent(target_version=version)
            unit_of_work.orders.add(intent)
            unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
            unit_of_work.orders.transition_status(
                intent.client_order_id, OrderStatus.OUTBOX_PENDING
            )
            intents.append(engine.submit_from_outbox(unit_of_work, intent.client_order_id))

        original_cancel = broker.cancel_order
        calls = 0

        def fail_second_cancel(broker_order_id: str) -> bool:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise BrokerTransportError("cancel outcome unknown")
            return original_cancel(broker_order_id)

        monkeypatch.setattr(broker, "cancel_order", fail_second_cancel)

        with pytest.raises(ControlPlaneError, match="partially failed"):
            plane.cancel_open_orders(unit_of_work, engine=engine, reason="drill", actor="owner")

        first = unit_of_work.orders.get(intents[0].client_order_id)
        second = unit_of_work.orders.get(intents[1].client_order_id)
        assert first is not None and first.status is OrderStatus.CANCELED
        assert second is not None and second.status is OrderStatus.CANCEL_PENDING
        record = unit_of_work.control.commands[-1]
        assert record.command.value == "CANCEL_OPEN_ORDERS"
        assert record.applied_at is None
        assert record.reason.startswith("PARTIAL_FAILURE 1/2 BrokerTransportError")


class _FixedPrices:
    def __init__(self, cents: int) -> None:
        self._cents = cents

    def current_price(self, symbol: Symbol) -> Price:
        return Price.from_cents(self._cents)


class _FailingPrices:
    def current_price(self, symbol: Symbol) -> Price:
        raise ControlPlaneError("quote unavailable")


def _filled_position(
    unit_of_work: _UnitOfWork,
    quantity: int = 10,
    unknown_assets: set[str] | None = None,
    cancel_mode: FakeCancelMode = FakeCancelMode.IMMEDIATE,
) -> FakePaperBroker:
    intent = OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TRADING_DATE,
        window="open",
        target_version=1,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(quantity),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE_TIME,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_BASE_TIME,
    )
    plan = FakeSubmitPlan(
        outcome=FakeSubmitOutcome.ACKNOWLEDGE,
        first_fill=FakeFillStep(quantity=OrderQuantity(quantity), price=Price.from_cents(10_000)),
    )
    broker = FakePaperBroker(
        clock=_FixedClock(),
        plans={intent.client_order_id.value: plan},
        unknown_assets=unknown_assets,
        cancel_mode=cancel_mode,
    )
    unit_of_work.orders.add(intent)
    unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
    unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
    accepted = broker.submit_order(intent)
    assert isinstance(accepted, SubmitAccepted)
    unit_of_work.orders.record_broker_order(accepted.order)
    for fill in broker.list_fills(accepted.order.broker_order_id):
        unit_of_work.orders.add_fill(fill)
    return broker


def _seed_filled_position(
    unit_of_work: _UnitOfWork,
    broker: FakePaperBroker,
    *,
    symbol: str,
    quantity: int,
    target_version: int = 1,
    price_cents: int = 10_000,
) -> None:
    intent = OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TRADING_DATE,
        window="open",
        target_version=target_version,
        symbol=Symbol(symbol),
        side=OrderSide.BUY,
        quantity=OrderQuantity(quantity),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(price_cents),
        collar=PriceCollar(reference=Price.from_cents(price_cents), offset_bps=100),
        earliest_submit_at=_BASE_TIME,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_BASE_TIME,
    )
    unit_of_work.orders.add(intent)
    unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
    unit_of_work.orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
    accepted = broker.submit_order(intent)
    assert isinstance(accepted, SubmitAccepted)
    unit_of_work.orders.record_broker_order(accepted.order)
    fill = broker.apply_fill(
        accepted.order.broker_order_id,
        FakeFillStep(quantity=OrderQuantity(quantity), price=Price.from_cents(price_cents)),
    )
    unit_of_work.orders.add_fill(fill)


class _RejectMsftFlattenBroker(FakePaperBroker):
    def submit_order(self, intent: OrderIntent) -> SubmitResult:
        if intent.intent_type is OrderIntentType.RISK_EXIT and intent.symbol == Symbol("MSFT"):
            return SubmitRejected(reason=RejectionReason.ORDER_PARAMETERS_REJECTED)
        return super().submit_order(intent)


class TestFlattenPaper:
    def test_flatten_requires_pause_and_explicit_confirmation(self) -> None:
        unit_of_work = _UnitOfWork()
        engine = ExecutionEngine(broker=FakePaperBroker(clock=_FixedClock()), clock=_FixedClock())
        plane = ControlPlane(clock=_FixedClock())

        with pytest.raises(ControlPlaneError, match="explicit FLATTEN_PAPER"):
            plane.flatten_paper(
                unit_of_work,
                engine=engine,
                trading_date=_TRADING_DATE,
                reason="drill",
                actor="owner",
                confirmation="flatten",
            )
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")
        with pytest.raises(ControlPlaneError, match="confirmation"):
            plane.flatten_paper(
                unit_of_work,
                engine=engine,
                trading_date=_TRADING_DATE,
                reason="drill",
                actor="owner",
                confirmation="flatten",
            )

    def test_flatten_cancels_open_orders_and_sells_positions(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock())
        broker = _filled_position(unit_of_work)
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        submitted = plane.flatten_paper(
            unit_of_work,
            engine=engine,
            trading_date=_TRADING_DATE,
            reason="drill",
            actor="owner",
            confirmation="FLATTEN_PAPER",
        )

        assert len(submitted) == 1
        assert submitted[0].side is OrderSide.SELL
        assert submitted[0].symbol == Symbol("AAPL")
        assert submitted[0].quantity.value == 10
        assert submitted[0].intent_type is OrderIntentType.RISK_EXIT
        assert submitted[0].quantity.value <= 10
        assert submitted[0].status is OrderStatus.ACKNOWLEDGED
        assert submitted[0].target_version == 1
        assert [record.command.value for record in unit_of_work.control.commands] == [
            "PAUSE_ENTRIES",
            "FLATTEN_PAPER",
        ]

    def test_flatten_rejection_after_one_exit_records_partial_failure_and_stays_paused(
        self,
    ) -> None:
        unit_of_work = _UnitOfWork()
        broker = _RejectMsftFlattenBroker(clock=_FixedClock())
        _seed_filled_position(unit_of_work, broker, symbol="AAPL", quantity=10)
        _seed_filled_position(unit_of_work, broker, symbol="MSFT", quantity=5)
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane = ControlPlane(clock=_FixedClock(), prices=_FixedPrices(cents=10_000))
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        with pytest.raises(ControlPlaneError, match="partially failed while submitting exits"):
            plane.flatten_paper(
                unit_of_work,
                engine=engine,
                trading_date=_TRADING_DATE,
                reason="drill",
                actor="owner",
                confirmation="FLATTEN_PAPER",
            )

        assert unit_of_work.control.state().entries_paused is True
        exits = [
            intent
            for status in (OrderStatus.ACKNOWLEDGED, OrderStatus.REJECTED)
            for intent in unit_of_work.orders.list_by_status(status)
            if intent.intent_type is OrderIntentType.RISK_EXIT
        ]
        assert [intent.status for intent in exits] == [
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.REJECTED,
        ]
        record = unit_of_work.control.commands[-1]
        assert record.command.value == "FLATTEN_PAPER"
        assert record.applied_at is None
        assert record.reason.startswith("PARTIAL_FAILURE 1/2 ControlPlaneError")

    def test_flatten_aborts_when_the_broker_position_view_disagrees(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock())
        broker = _filled_position(unit_of_work)
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        stray = OrderIntent.create(
            strategy="seven-lens",
            trading_date=_TRADING_DATE,
            window="open",
            target_version=9,
            symbol=Symbol("MSFT"),
            side=OrderSide.BUY,
            quantity=OrderQuantity(5),
            intent_type=OrderIntentType.REBALANCE,
            limit_price=Price.from_cents(40_000),
            collar=PriceCollar(reference=Price.from_cents(40_000), offset_bps=100),
            earliest_submit_at=_BASE_TIME,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_BASE_TIME,
        )
        accepted = broker.submit_order(stray)
        assert isinstance(accepted, SubmitAccepted)
        broker.apply_fill(
            accepted.order.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(5), price=Price.from_cents(40_000)),
        )
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        with pytest.raises(ControlPlaneError, match="disagrees with the local ledger"):
            plane.flatten_paper(
                unit_of_work,
                engine=engine,
                trading_date=_TRADING_DATE,
                reason="drill",
                actor="owner",
                confirmation="FLATTEN_PAPER",
            )

        assert [record.command.value for record in unit_of_work.control.commands] == [
            "PAUSE_ENTRIES",
            "FLATTEN_PAPER",
        ]
        assert unit_of_work.control.commands[-1].reason.startswith(
            "PARTIAL_FAILURE 0/1 ControlPlaneError"
        )
        assert unit_of_work.orders.list_by_status(OrderStatus.SUBMITTING) == ()
        assert unit_of_work.orders.list_by_status(OrderStatus.UNKNOWN) == ()

    def test_flatten_aborts_when_the_price_provider_cannot_price(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock(), prices=_FailingPrices())
        broker = _filled_position(unit_of_work)
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        with pytest.raises(ControlPlaneError, match="quote unavailable"):
            plane.flatten_paper(
                unit_of_work,
                engine=engine,
                trading_date=_TRADING_DATE,
                reason="drill",
                actor="owner",
                confirmation="FLATTEN_PAPER",
            )

        assert unit_of_work.orders.list_by_status(OrderStatus.ACKNOWLEDGED) == ()
        assert [record.command.value for record in unit_of_work.control.commands] == [
            "PAUSE_ENTRIES",
            "FLATTEN_PAPER",
        ]
        assert unit_of_work.control.commands[-1].reason.startswith(
            "PARTIAL_FAILURE 0/1 ControlPlaneError"
        )

    def test_flatten_aborts_while_an_existing_cancel_is_still_pending(self) -> None:
        unit_of_work = _UnitOfWork()
        broker = _filled_position(unit_of_work, cancel_mode=FakeCancelMode.PENDING)
        open_intent = _intent(target_version=2)
        unit_of_work.orders.add(open_intent)
        unit_of_work.orders.transition_status(
            open_intent.client_order_id, OrderStatus.RISK_APPROVED
        )
        unit_of_work.orders.transition_status(
            open_intent.client_order_id, OrderStatus.OUTBOX_PENDING
        )
        accepted = broker.submit_order(open_intent)
        assert isinstance(accepted, SubmitAccepted)
        unit_of_work.orders.record_broker_order(accepted.order)
        unit_of_work.orders.transition_status(open_intent.client_order_id, OrderStatus.SUBMITTING)
        unit_of_work.orders.transition_status(open_intent.client_order_id, OrderStatus.ACKNOWLEDGED)
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane = ControlPlane(clock=_FixedClock(), prices=_FixedPrices(cents=10_000))
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        with pytest.raises(ControlPlaneError, match="remain unresolved"):
            plane.flatten_paper(
                unit_of_work,
                engine=engine,
                trading_date=_TRADING_DATE,
                reason="drill",
                actor="owner",
                confirmation="FLATTEN_PAPER",
            )

        assert unit_of_work.orders.list_by_status(OrderStatus.CANCEL_PENDING)
        assert unit_of_work.control.state().flatten_generation == 0
        record = unit_of_work.control.commands[-1]
        assert record.command.value == "FLATTEN_PAPER"
        assert record.applied_at is None
        assert record.reason.startswith("PARTIAL_FAILURE 0/1 ControlPlaneError")

    def test_flatten_uses_the_price_provider_as_the_sell_reference(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock(), prices=_FixedPrices(cents=20_000))
        broker = _filled_position(unit_of_work)
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        submitted = plane.flatten_paper(
            unit_of_work,
            engine=engine,
            trading_date=_TRADING_DATE,
            reason="drill",
            actor="owner",
            confirmation="FLATTEN_PAPER",
        )

        assert submitted[0].collar.reference == Price.from_cents(20_000)
        assert submitted[0].limit_price == Price.from_cents(19_000)

    def test_flatten_resolves_a_stuck_submitting_intent_before_canceling(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock(), prices=_FixedPrices(cents=10_000))
        stuck = OrderIntent.create(
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
            earliest_submit_at=_BASE_TIME,
            cancel_at=_CANCEL_AT,
            run_id=RunId.new(),
            created_at=_BASE_TIME,
        )
        unit_of_work.orders.add(stuck)
        unit_of_work.orders.transition_status(stuck.client_order_id, OrderStatus.RISK_APPROVED)
        unit_of_work.orders.transition_status(stuck.client_order_id, OrderStatus.OUTBOX_PENDING)
        broker = FakePaperBroker(
            clock=_FixedClock(),
            plans={
                stuck.client_order_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.TIMEOUT_AFTER_ACCEPT
                )
            },
        )
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        submitting = engine.submit_from_outbox(unit_of_work, stuck.client_order_id)
        assert submitting.status is OrderStatus.UNKNOWN
        # UNKNOWN now durably pauses via the unit_of_work's control (same DB connection)
        assert unit_of_work.control.state().entries_paused is True
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        submitted = plane.flatten_paper(
            unit_of_work,
            engine=engine,
            trading_date=_TRADING_DATE,
            reason="drill",
            actor="owner",
            confirmation="FLATTEN_PAPER",
        )

        assert submitted == ()
        assert unit_of_work.orders.list_by_status(OrderStatus.SUBMITTING) == ()
        assert unit_of_work.orders.list_by_status(OrderStatus.UNKNOWN) == ()
        assert unit_of_work.orders.list_by_status(OrderStatus.CANCEL_PENDING) == ()
        canceled = unit_of_work.orders.list_by_status(OrderStatus.CANCELED)
        assert len(canceled) == 1
        # First PAUSE is from UNKNOWN durable gate, second from explicit pause
        assert [record.command.value for record in unit_of_work.control.commands] == [
            "PAUSE_ENTRIES",
            "PAUSE_ENTRIES",
            "FLATTEN_PAPER",
        ]

    def test_flatten_aborts_when_the_broker_does_not_trade_a_position_symbol(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock(), prices=_FixedPrices(cents=10_000))
        broker = _filled_position(unit_of_work, unknown_assets={"AAPL"})
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        with pytest.raises(ControlPlaneError, match="cannot trade a projected position"):
            plane.flatten_paper(
                unit_of_work,
                engine=engine,
                trading_date=_TRADING_DATE,
                reason="drill",
                actor="owner",
                confirmation="FLATTEN_PAPER",
            )

        assert unit_of_work.control.state().flatten_generation == 0
        assert unit_of_work.orders.list_by_status(OrderStatus.ACKNOWLEDGED) == ()
        assert [record.command.value for record in unit_of_work.control.commands] == [
            "PAUSE_ENTRIES",
            "FLATTEN_PAPER",
        ]
        assert unit_of_work.control.commands[-1].reason.startswith(
            "PARTIAL_FAILURE 0/1 ControlPlaneError"
        )

    def test_default_flatten_price_uses_the_latest_local_fill(self) -> None:
        unit_of_work = _UnitOfWork()
        broker = FakePaperBroker(clock=_FixedClock())
        _seed_filled_position(
            unit_of_work,
            broker,
            symbol="AAPL",
            quantity=10,
            target_version=1,
            price_cents=10_000,
        )
        _seed_filled_position(
            unit_of_work,
            broker,
            symbol="AAPL",
            quantity=5,
            target_version=2,
            price_cents=12_000,
        )

        provider = LedgerFlattenPriceProvider(unit_of_work)

        assert provider.current_price(Symbol("AAPL")) == Price.from_cents(12_000)

    def test_each_flatten_uses_a_new_durable_generation(self) -> None:
        unit_of_work = _UnitOfWork()
        plane = ControlPlane(clock=_FixedClock(), prices=_FixedPrices(cents=10_000))
        broker = _filled_position(unit_of_work)
        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        plane.pause_entries(unit_of_work, reason="drill", actor="owner")

        first = plane.flatten_paper(
            unit_of_work,
            engine=engine,
            trading_date=_TRADING_DATE,
            reason="drill",
            actor="owner",
            confirmation="FLATTEN_PAPER",
        )
        second = plane.flatten_paper(
            unit_of_work,
            engine=engine,
            trading_date=_TRADING_DATE,
            reason="drill",
            actor="owner",
            confirmation="FLATTEN_PAPER",
        )
        third = plane.flatten_paper(
            unit_of_work,
            engine=engine,
            trading_date=_TRADING_DATE,
            reason="drill",
            actor="owner",
            confirmation="FLATTEN_PAPER",
        )

        assert first[0].target_version == 1
        assert second[0].target_version == 2
        assert third[0].target_version == 3
        assert (
            len(
                {
                    first[0].client_order_id,
                    second[0].client_order_id,
                    third[0].client_order_id,
                }
            )
            == 3
        )
        assert unit_of_work.control.state().flatten_generation == 3
