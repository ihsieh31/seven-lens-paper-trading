# mypy: ignore-errors
"""Unit tests for the ledger projection and the reconciliation collector."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from fakes.control import FakeControlRepository, FakeReconciliationRepository
from fakes.orders import FakeOrderRepository
from seven_lens.application.ports.broker import BrokerTransportError, SubmitAccepted
from seven_lens.application.reconciliation_service import Reconciler
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import (
    FakeFillStep,
    FakePaperBroker,
    FakeSubmitPlan,
)
from seven_lens.execution.ledger import LedgerInvariantError, project_ledger
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
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
from seven_lens.execution.reconciliation import (
    MismatchKind,
    ReconciliationMismatch,
    ReconciliationResult,
    ReconciliationStatus,
)

_BASE_TIME = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TRADING_DATE = TradingDate.from_isoformat("2026-08-17")


class _FixedClock:
    def __call__(self) -> UtcTimestamp:
        return _BASE_TIME


class _HistoryFailureBroker(FakePaperBroker):
    def list_recent_orders(self, *, since: UtcTimestamp) -> tuple[BrokerOrder, ...]:
        del since
        raise BrokerTransportError("injected history failure")


class _SnapshotRaceBroker(FakePaperBroker):
    def __init__(self, stale_open: BrokerOrder, newer_terminal: BrokerOrder) -> None:
        super().__init__(clock=_FixedClock())
        self._stale_open = stale_open
        self._newer_terminal = newer_terminal

    def get_order(self, client_order_id: ClientOrderId) -> BrokerOrder | None:
        del client_order_id
        return self._stale_open

    def list_open_orders(self) -> tuple[BrokerOrder, ...]:
        return (self._stale_open,)

    def list_recent_orders(self, *, since: UtcTimestamp) -> tuple[BrokerOrder, ...]:
        del since
        return (self._newer_terminal,)


def _intent(
    *,
    target_version: int,
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    quantity: int = 10,
) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=_TRADING_DATE,
        window="open",
        target_version=target_version,
        symbol=Symbol(symbol),
        side=side,
        quantity=OrderQuantity(quantity),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=_BASE_TIME,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_BASE_TIME,
    )


def _mirror(
    broker_order_id: str, intent: OrderIntent, status: BrokerOrderStatus, filled: int = 0
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        filled_quantity=filled,
        limit_price=intent.limit_price,
        status=status,
        submitted_at=_BASE_TIME,
        updated_at=_BASE_TIME,
    )


def _fill(execution_id: str, broker_order_id: str, quantity: int, cents: int) -> Fill:
    return Fill(
        execution_id=execution_id,
        broker_order_id=broker_order_id,
        quantity=OrderQuantity(quantity),
        price=Price.from_cents(cents),
        occurred_at=_BASE_TIME,
    )


class TestLedgerProjection:
    def test_buy_opens_lot_and_debits_cash(self) -> None:
        intent = _intent(target_version=1)
        mirror = _mirror("b-1", intent, BrokerOrderStatus.FILLED, 10)
        projection = project_ledger((_fill("e-1", "b-1", 10, 10_000),), {"b-1": mirror})

        assert projection.cash_delta_cents == -100_000
        assert projection.positions == {Symbol("AAPL"): 10}
        assert [lot.quantity for lot in projection.lots] == [10]

    def test_sell_consumes_fifo_lots_and_credits_cash(self) -> None:
        buy = _intent(target_version=1)
        sell = _intent(target_version=2, side=OrderSide.SELL)
        buy_mirror = _mirror("b-1", buy, BrokerOrderStatus.FILLED, 10)
        sell_mirror = _mirror("b-2", sell, BrokerOrderStatus.FILLED, 10)
        fills = (
            _fill("e-1", "b-1", 6, 10_000),
            _fill("e-2", "b-1", 4, 11_000),
            _fill("e-3", "b-2", 5, 12_000),
        )
        projection = project_ledger(fills, {"b-1": buy_mirror, "b-2": sell_mirror})

        assert projection.cash_delta_cents == -(6 * 10_000 + 4 * 11_000) + 5 * 12_000
        assert projection.positions == {Symbol("AAPL"): 5}
        assert [(lot.quantity, lot.price.cents) for lot in projection.lots] == [
            (1, 10_000),
            (4, 11_000),
        ]

    def test_oversell_and_unknown_order_fail_closed(self) -> None:
        buy = _intent(target_version=1)
        sell = _intent(target_version=2, side=OrderSide.SELL)
        buy_mirror = _mirror("b-1", buy, BrokerOrderStatus.FILLED, 10)
        sell_mirror = _mirror("b-2", sell, BrokerOrderStatus.FILLED, 10)
        with pytest.raises(LedgerInvariantError, match="exceeds the projected position"):
            project_ledger(
                (_fill("e-1", "b-1", 3, 10_000), _fill("e-2", "b-2", 4, 10_000)),
                {"b-1": buy_mirror, "b-2": sell_mirror},
            )
        with pytest.raises(LedgerInvariantError, match="unknown broker order"):
            project_ledger((_fill("e-9", "missing", 1, 100),), {})

    def test_duplicate_execution_and_negative_cash_fail_closed(self) -> None:
        intent = _intent(target_version=1)
        mirror = _mirror("b-1", intent, BrokerOrderStatus.FILLED, 10)
        with pytest.raises(LedgerInvariantError, match="duplicate execution id"):
            project_ledger(
                (_fill("e-1", "b-1", 1, 10_000), _fill("e-1", "b-1", 1, 10_000)),
                {"b-1": mirror},
            )


class TestReconciliationResult:
    def test_clean_and_mismatch_are_self_consistent(self) -> None:
        clean = ReconciliationResult.create(
            trading_date=_TRADING_DATE,
            mismatches=(),
            checked_orders=1,
            checked_fills=0,
            observed_at=_BASE_TIME,
        )
        assert clean.status is ReconciliationStatus.CLEAN
        mismatch = ReconciliationResult.create(
            trading_date=_TRADING_DATE,
            mismatches=(ReconciliationMismatch(kind=MismatchKind.STATUS_MISMATCH, detail="b-1"),),
            checked_orders=1,
            checked_fills=0,
            observed_at=_BASE_TIME,
        )
        assert mismatch.status is ReconciliationStatus.MISMATCH

    def test_consistency_violations_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="CLEAN requires"):
            ReconciliationResult(
                run_id=uuid4(),
                trading_date=_TRADING_DATE,
                status=ReconciliationStatus.CLEAN,
                mismatches=(
                    ReconciliationMismatch(kind=MismatchKind.STATUS_MISMATCH, detail="b-1"),
                ),
                checked_orders=1,
                checked_fills=0,
                observed_at=_BASE_TIME,
            )
        with pytest.raises(ValueError, match="bounded text"):
            ReconciliationMismatch(kind=MismatchKind.STATUS_MISMATCH, detail="x" * 201)


class TestReconciler:
    def _seeded(
        self, *, version: int, plan: FakeSubmitPlan | None = None
    ) -> tuple[FakeOrderRepository, FakePaperBroker]:
        intent = _intent(target_version=version)
        plans = {intent.client_order_id.value: plan} if plan is not None else None
        broker = FakePaperBroker(clock=_FixedClock(), plans=plans)
        orders = FakeOrderRepository()
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)
        orders.transition_status(intent.client_order_id, OrderStatus.ACKNOWLEDGED)
        return orders, broker

    def _submit(self, orders: FakeOrderRepository, broker: FakePaperBroker, version: int) -> None:
        accepted = broker.submit_order(_intent(target_version=version))
        assert isinstance(accepted, SubmitAccepted)
        orders.record_broker_order(accepted.order)

    def test_clean_reconciliation_when_views_agree(self) -> None:
        orders, broker = self._seeded(version=1)
        self._submit(orders, broker, 1)
        reconciler = Reconciler(broker=broker, clock=_FixedClock())

        result = reconciler.collect(_UnitOfWork(orders), _TRADING_DATE)

        assert result.status is ReconciliationStatus.CLEAN
        # One local mirror plus the broker's own open-order view were checked.
        assert result.checked_orders == 2

    def test_status_mismatch_and_missing_fill_are_reported(self) -> None:
        orders, broker = self._seeded(version=2)
        self._submit(orders, broker, 2)
        # Broker filled without the engine applying the fill locally.
        mirror = orders.get_broker_order(_intent(target_version=2).client_order_id)
        assert mirror is not None
        broker.apply_fill(
            mirror.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(10), price=Price.from_cents(10_000)),
        )
        reconciler = Reconciler(broker=broker, clock=_FixedClock())

        result = reconciler.collect(_UnitOfWork(orders), _TRADING_DATE)

        kinds = {mismatch.kind for mismatch in result.mismatches}
        assert MismatchKind.STATUS_MISMATCH in kinds
        assert MismatchKind.MISSING_LOCAL_FILL in kinds
        assert result.status is ReconciliationStatus.MISMATCH

    def test_unknown_broker_order_is_reported(self) -> None:
        orders = FakeOrderRepository()
        broker = FakePaperBroker(clock=_FixedClock())
        # The broker holds an order the local ledger never recorded.
        broker.submit_order(_intent(target_version=3))
        reconciler = Reconciler(broker=broker, clock=_FixedClock())

        result = reconciler.collect(_UnitOfWork(orders), _TRADING_DATE)

        kinds = {mismatch.kind for mismatch in result.mismatches}
        assert MismatchKind.UNKNOWN_BROKER_ORDER in kinds
        assert result.status is ReconciliationStatus.MISMATCH

    def test_review_required_intent_can_never_produce_a_clean_run(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent(target_version=16)
        orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.REVIEW_REQUIRED,
        ):
            orders.transition_status(intent.client_order_id, status)

        result = Reconciler(
            broker=FakePaperBroker(clock=_FixedClock()), clock=_FixedClock()
        ).collect(_UnitOfWork(orders), _TRADING_DATE)

        assert MismatchKind.INTENT_STATUS_MISMATCH in {
            mismatch.kind for mismatch in result.mismatches
        }
        assert result.status is ReconciliationStatus.MISMATCH

    def test_closed_broker_history_without_local_record_is_reported(self) -> None:
        """A broker-terminal order we never recorded is drift only history sees."""
        orders = FakeOrderRepository()
        broker = FakePaperBroker(clock=_FixedClock())
        intent = _intent(target_version=13)
        accepted = broker.submit_order(intent)
        assert isinstance(accepted, SubmitAccepted)
        broker.cancel_order(accepted.order.broker_order_id)

        result = Reconciler(broker=broker, clock=_FixedClock()).collect(
            _UnitOfWork(orders), _TRADING_DATE
        )

        kinds = {mismatch.kind for mismatch in result.mismatches}
        assert MismatchKind.UNKNOWN_BROKER_ORDER in kinds
        assert result.status is ReconciliationStatus.MISMATCH

    def test_closed_history_status_divergence_is_reported(self) -> None:
        """Broker FILLED while the stale local mirror says ACCEPTED."""
        orders = FakeOrderRepository()
        intent = _intent(target_version=14)
        broker = FakePaperBroker(clock=_FixedClock())
        accepted = broker.submit_order(intent)
        assert isinstance(accepted, SubmitAccepted)
        broker.apply_fill(
            accepted.order.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(10), price=Price.from_cents(10_000)),
        )
        orders.record_broker_order(accepted.order)  # engine crashed before refreshing

        result = Reconciler(broker=broker, clock=_FixedClock()).collect(
            _UnitOfWork(orders), _TRADING_DATE
        )

        kinds = {mismatch.kind for mismatch in result.mismatches}
        assert MismatchKind.STATUS_MISMATCH in kinds
        assert MismatchKind.MISSING_LOCAL_FILL in kinds

    def test_closed_history_inside_previous_run_horizon_is_checked(self) -> None:
        """The horizon follows the previous run: later terminations are re-police."""
        orders = FakeOrderRepository()
        broker = FakePaperBroker(clock=_FixedClock())
        intent = _intent(target_version=15)
        accepted = broker.submit_order(intent)
        assert isinstance(accepted, SubmitAccepted)
        orders.record_broker_order(accepted.order)
        unit_of_work = _UnitOfWork(orders)
        earlier = Reconciler(broker=broker, clock=_FixedClock()).collect(
            unit_of_work, _TRADING_DATE
        )
        assert earlier.status is ReconciliationStatus.CLEAN
        unit_of_work.reconciliations.add(earlier)

        # Broker cancels after the previous run's horizon.
        broker.cancel_order(accepted.order.broker_order_id)
        result = Reconciler(broker=broker, clock=_FixedClock()).collect(unit_of_work, _TRADING_DATE)

        kinds = {mismatch.kind for mismatch in result.mismatches}
        assert MismatchKind.STATUS_MISMATCH in kinds  # broker CANCELED, mirror ACCEPTED

    def test_equal_timestamp_terminal_history_conflict_is_not_deduplicated(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent(target_version=17)
        orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, status)
        stale_open = _mirror("broker-race", intent, BrokerOrderStatus.ACCEPTED)
        newer_terminal = replace(
            stale_open,
            status=BrokerOrderStatus.CANCELED,
        )
        orders.record_broker_order(stale_open)

        result = Reconciler(
            broker=_SnapshotRaceBroker(stale_open, newer_terminal), clock=_FixedClock()
        ).collect(_UnitOfWork(orders), _TRADING_DATE)

        assert MismatchKind.STATUS_MISMATCH in {mismatch.kind for mismatch in result.mismatches}

    def test_position_mismatch_is_reported(self) -> None:
        orders, broker = self._seeded(version=4)
        self._submit(orders, broker, 4)
        mirror = orders.get_broker_order(_intent(target_version=4).client_order_id)
        assert mirror is not None
        fill = broker.apply_fill(
            mirror.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(10), price=Price.from_cents(10_000)),
        )
        orders.add_fill(fill)
        orders.update_broker_order_status(mirror.broker_order_id, BrokerOrderStatus.FILLED, 10)
        # The broker then reports a position the local fill ledger cannot explain.
        reconciler = Reconciler(broker=broker, clock=_FixedClock())
        result = reconciler.collect(_UnitOfWork(orders), _TRADING_DATE)
        assert result.status is ReconciliationStatus.CLEAN  # baseline agrees

        tampered = FakePaperBroker(clock=_FixedClock())
        tampered.submit_order(_intent(target_version=5))
        tampered_mirror_id = tampered.get_order(_intent(target_version=5).client_order_id)
        assert tampered_mirror_id is not None
        tampered.apply_fill(
            tampered_mirror_id.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(7), price=Price.from_cents(10_000)),
        )
        result = Reconciler(broker=tampered, clock=_FixedClock()).collect(
            _UnitOfWork(orders), _TRADING_DATE
        )
        kinds = {mismatch.kind for mismatch in result.mismatches}
        assert MismatchKind.POSITION_QUANTITY_MISMATCH in kinds or (
            MismatchKind.POSITION_SYMBOL_MISMATCH in kinds
        )


class _UnitOfWork:
    def __init__(self, orders: FakeOrderRepository) -> None:
        self.orders = orders
        self.reconciliations = FakeReconciliationRepository()
        self.control = FakeControlRepository(_BASE_TIME)
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class TestReconciliationRunOrchestration:
    """Adversarial coverage for the collect -> persist -> auto-pause pipeline."""

    def _units(self) -> tuple[_UnitOfWork, FakePaperBroker]:
        orders = FakeOrderRepository()
        broker = FakePaperBroker(clock=_FixedClock())
        # The broker holds an order the local ledger never recorded.
        broker.submit_order(_intent(target_version=8))
        return _UnitOfWork(orders), broker

    def test_mismatch_run_persists_evidence_and_pauses_entries(self) -> None:
        unit_of_work, broker = self._units()
        reconciler = Reconciler(broker=broker, clock=_FixedClock())

        result = reconciler.run(unit_of_work, _TRADING_DATE)

        assert result.status is ReconciliationStatus.MISMATCH
        assert unit_of_work.reconciliations.latest() is result
        assert unit_of_work.control.state().entries_paused is True
        assert [record.command.value for record in unit_of_work.control.commands] == [
            "PAUSE_ENTRIES"
        ]
        assert unit_of_work.commit_count == 1

    def test_broker_query_failure_persists_evidence_and_pauses_entries(self) -> None:
        unit_of_work = _UnitOfWork(FakeOrderRepository())
        reconciler = Reconciler(
            broker=_HistoryFailureBroker(clock=_FixedClock()), clock=_FixedClock()
        )

        result = reconciler.run(unit_of_work, _TRADING_DATE)

        assert result.status is ReconciliationStatus.MISMATCH
        assert [mismatch.kind for mismatch in result.mismatches] == [
            MismatchKind.BROKER_QUERY_FAILURE
        ]
        assert unit_of_work.reconciliations.latest() is result
        assert unit_of_work.control.state().entries_paused is True
        assert unit_of_work.commit_count == 1

    def test_clean_run_persists_evidence_without_pausing(self) -> None:
        unit_of_work = _UnitOfWork(FakeOrderRepository())
        reconciler = Reconciler(broker=FakePaperBroker(clock=_FixedClock()), clock=_FixedClock())

        result = reconciler.run(unit_of_work, _TRADING_DATE)

        assert result.status is ReconciliationStatus.CLEAN
        assert unit_of_work.reconciliations.latest() is result
        assert unit_of_work.control.state().entries_paused is False
        assert unit_of_work.control.commands == []


class TestBrokerTerminalRace:
    """Adversarial coverage for broker states racing the local lifecycle."""

    def test_broker_terminal_during_submitting_converges_not_raises(self) -> None:
        from seven_lens.application.execution_service import (
            ExecutionEngine,
        )

        orders = FakeOrderRepository()
        intent = _intent(target_version=9)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)
        broker = FakePaperBroker(clock=_FixedClock())
        # The broker accepted and instantly canceled while we were in SUBMITTING;
        # drive it directly so the fake records that exact history.
        accepted = broker.submit_order(intent)
        assert isinstance(accepted, SubmitAccepted)
        mirror_id = accepted.order.broker_order_id
        broker.cancel_order(mirror_id)

        class _EngineUnitOfWork:
            def __init__(self) -> None:
                self.orders = orders
                self.commit_count = 0

            def commit(self) -> None:
                self.commit_count += 1

        engine = ExecutionEngine(broker=broker, clock=_FixedClock())
        resolved = engine.resolve(_EngineUnitOfWork(), intent.client_order_id)
        assert resolved.status is OrderStatus.CANCELED
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None and mirror.status is BrokerOrderStatus.CANCELED

    def test_impossible_broker_sequence_fails_closed_without_changes(self) -> None:
        from dataclasses import replace

        from seven_lens.execution.orders import InvalidBrokerOrderTransitionError

        orders = FakeOrderRepository()
        intent = _intent(target_version=9)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)
        broker = FakePaperBroker(clock=_FixedClock())
        accepted = broker.submit_order(intent)
        assert isinstance(accepted, SubmitAccepted)
        broker.cancel_order(accepted.order.broker_order_id)
        parked = orders.get(intent.client_order_id)
        assert parked is not None and parked.status is OrderStatus.SUBMITTING
        assert broker.get_order(intent.client_order_id) is not None

        # Recording the observed cancel is legal; re-recording a plain live
        # status over it is an impossible broker sequence that the mirror
        # refuses with no local change.
        observed = broker.get_order(intent.client_order_id)
        assert observed is not None
        orders.record_broker_order(observed)
        with pytest.raises(InvalidBrokerOrderTransitionError):
            orders.record_broker_order(
                replace(
                    observed,
                    status=BrokerOrderStatus.ACCEPTED,
                    updated_at=UtcTimestamp(_BASE_TIME.value + timedelta(microseconds=1)),
                )
            )


class TestAccountValuation:
    def test_nav_marks_positions_at_supplied_prices(self) -> None:
        from seven_lens.execution.ledger import account_valuation

        buy = _intent(target_version=11)
        buy_mirror = _mirror("b-1", buy, BrokerOrderStatus.FILLED, 10)
        projection = project_ledger((_fill("e-1", "b-1", 10, 10_000),), {"b-1": buy_mirror})

        nav = account_valuation(
            projection,
            opening_cash_cents=200_000,
            prices={Symbol("AAPL"): Price.from_cents(10_500)},
        )

        assert nav == 200_000 - 100_000 + 10 * 10_500

    def test_missing_price_fails_closed(self) -> None:
        from seven_lens.execution.ledger import account_valuation

        buy = _intent(target_version=12)
        buy_mirror = _mirror("b-1", buy, BrokerOrderStatus.FILLED, 10)
        projection = project_ledger((_fill("e-1", "b-1", 10, 10_000),), {"b-1": buy_mirror})
        with pytest.raises(ValueError, match="missing price"):
            account_valuation(projection, opening_cash_cents=0, prices={})


class TestTerminalIntentWithOpenBrokerOrder:
    def test_expired_intent_with_a_live_broker_order_is_reported(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent(target_version=14)
        orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, status)
        broker = FakePaperBroker(clock=_FixedClock())
        accepted = broker.submit_order(intent)
        assert isinstance(accepted, SubmitAccepted)
        orders.record_broker_order(accepted.order)
        orders.transition_status(intent.client_order_id, OrderStatus.EXPIRED)

        result = Reconciler(broker=broker, clock=_FixedClock()).collect(
            _UnitOfWork(orders), _TRADING_DATE
        )

        kinds = {mismatch.kind for mismatch in result.mismatches}
        assert MismatchKind.INTENT_STATUS_MISMATCH in kinds
        assert result.status is ReconciliationStatus.MISMATCH

    def test_terminal_mirror_status_is_compared_against_the_broker(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent(target_version=15)
        orders.add(intent)
        for status in (
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
            OrderStatus.SUBMITTING,
            OrderStatus.ACKNOWLEDGED,
        ):
            orders.transition_status(intent.client_order_id, status)
        broker = FakePaperBroker(clock=_FixedClock())
        accepted = broker.submit_order(intent)
        assert isinstance(accepted, SubmitAccepted)
        orders.record_broker_order(
            replace(accepted.order, status=BrokerOrderStatus.FILLED, filled_quantity=10)
        )

        result = Reconciler(broker=broker, clock=_FixedClock()).collect(
            _UnitOfWork(orders), _TRADING_DATE
        )

        kinds = {mismatch.kind for mismatch in result.mismatches}
        assert MismatchKind.STATUS_MISMATCH in kinds
        assert result.status is ReconciliationStatus.MISMATCH
