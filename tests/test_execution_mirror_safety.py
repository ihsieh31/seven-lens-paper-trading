# mypy: ignore-errors
"""Focused P2 regressions for broker-mirror conflicts and fill replay."""

from __future__ import annotations

from dataclasses import replace

import pytest

from fakes.control import FakeControlRepository, FakeReconciliationRepository
from fakes.orders import FakeOrderRepository
from seven_lens.application.execution_service import (
    BrokerMirrorMismatchError,
    ControlPersistenceError,
    ExecutionEngine,
    ExecutionPausedError,
)
from seven_lens.application.ports.broker import (
    AssetClass,
    AssetStatus,
    PaperAccount,
    PaperAsset,
)
from seven_lens.application.reconciliation_service import Reconciler
from seven_lens.config.broker import BrokerEnvironment
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.control import ControlCommand
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

_T0 = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")
_T1 = UtcTimestamp.from_isoformat("2026-08-17T13:35:01.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TRADING_DATE = TradingDate.from_isoformat("2026-08-17")


class _UnitOfWork:
    def __init__(self, orders: FakeOrderRepository, control: FakeControlRepository) -> None:
        self.orders = orders
        self.control = control
        self.reconciliations = FakeReconciliationRepository()
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        self.orders.commit()

    def rollback(self) -> None:
        self.rollback_count += 1
        self.orders.rollback()

    def begin_reconciliation_snapshot(self) -> None:
        return None


class _StaticBroker:
    def __init__(self, order: BrokerOrder, fills: tuple[Fill, ...] = ()) -> None:
        self.order = order
        self.fills = fills
        self.submit_calls = 0

    def get_order(self, client_order_id):  # type: ignore[no-untyped-def]
        del client_order_id
        return self.order

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]:
        if broker_order_id != self.order.broker_order_id:
            return ()
        return self.fills

    def submit_order(self, intent):  # type: ignore[no-untyped-def]
        del intent
        self.submit_calls += 1
        raise AssertionError("the broker must not receive a new entry after a conflict")

    def get_asset(self, symbol: Symbol) -> PaperAsset:
        return PaperAsset(
            symbol=symbol,
            asset_class=AssetClass.US_EQUITY,
            status=AssetStatus.ACTIVE,
            tradable=True,
            exchange="ARCA",
        )

    def account(self) -> PaperAccount:
        return PaperAccount(
            account_id="fake-paper-primary",
            environment=BrokerEnvironment.PAPER,
            cash=UsdAmount.from_cents(100_000_000),
            equity=UsdAmount.from_cents(100_000_000),
            buying_power=UsdAmount.from_cents(100_000_000),
        )

    def list_positions(self):  # type: ignore[no-untyped-def]
        return ()

    def list_open_orders(self) -> tuple[BrokerOrder, ...]:
        return (self.order,)

    def list_recent_orders(self, *, since: UtcTimestamp) -> tuple[BrokerOrder, ...]:
        del since
        return (self.order,)

    def cancel_order(self, broker_order_id: str) -> bool:
        del broker_order_id
        return False


def _intent(*, target_version: int = 1) -> OrderIntent:
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
        earliest_submit_at=_T0,
        cancel_at=_CANCEL_AT,
        run_id=RunId.new(),
        created_at=_T0,
    )


def _accepted_mirror(intent: OrderIntent, *, broker_order_id: str = "b-1") -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
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


def _setup_submitting() -> tuple[
    FakeOrderRepository, FakeControlRepository, _UnitOfWork, OrderIntent, BrokerOrder
]:
    return _setup_submitting_with_orders(FakeOrderRepository())


def _setup_submitting_with_orders(
    orders: FakeOrderRepository,
) -> tuple[FakeOrderRepository, FakeControlRepository, _UnitOfWork, OrderIntent, BrokerOrder]:
    intent = _intent()
    orders.add(intent)
    for status in (
        OrderStatus.RISK_APPROVED,
        OrderStatus.OUTBOX_PENDING,
        OrderStatus.SUBMITTING,
    ):
        orders.transition_status(intent.client_order_id, status)
    mirror = _accepted_mirror(intent)
    orders.record_broker_order(mirror)
    control = FakeControlRepository(_T0)
    unit_of_work = _UnitOfWork(orders, control)
    unit_of_work.commit()
    return orders, control, unit_of_work, intent, mirror


@pytest.mark.parametrize("field", ("symbol", "side", "quantity", "limit_price", "client_order_id"))
def test_broker_parameter_mismatch_marks_review_and_pauses(field: str) -> None:
    orders, control, unit_of_work, intent, mirror = _setup_submitting()
    if field == "symbol":
        observed = replace(mirror, symbol=Symbol("MSFT"))
    elif field == "side":
        observed = replace(mirror, side=OrderSide.SELL)
    elif field == "quantity":
        observed = replace(mirror, quantity=OrderQuantity(11))
    elif field == "limit_price":
        observed = replace(mirror, limit_price=Price.from_cents(10_001))
    else:
        observed = replace(mirror, client_order_id=_intent(target_version=2).client_order_id)
    broker = _StaticBroker(observed)
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(BrokerMirrorMismatchError, match="contradict"):
        engine.resolve(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.REVIEW_REQUIRED
    assert control.state().entries_paused is True
    assert control.state().paused_reason == "reconciliation required; broker mirror mismatch"
    assert len(control.commands) == 1
    assert control.commands[0].command is ControlCommand.PAUSE_ENTRIES
    assert control.commands[0].reason == "automatic pause on broker mirror mismatch"
    assert orders.get_broker_order(intent.client_order_id) == mirror

    second = _intent(target_version=2)
    orders.add(second)
    orders.transition_status(second.client_order_id, OrderStatus.RISK_APPROVED)
    orders.transition_status(second.client_order_id, OrderStatus.OUTBOX_PENDING)
    with pytest.raises(ExecutionPausedError):
        engine.submit_from_outbox(unit_of_work, second.client_order_id)
    assert broker.submit_calls == 0


def test_broker_order_id_mismatch_marks_review_without_overwriting_local_mirror() -> None:
    orders, control, unit_of_work, intent, mirror = _setup_submitting()
    broker = _StaticBroker(replace(mirror, broker_order_id="b-other"))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(BrokerMirrorMismatchError, match="broker_order_id"):
        engine.resolve(unit_of_work, intent.client_order_id)

    assert orders.get(intent.client_order_id).status is OrderStatus.REVIEW_REQUIRED  # type: ignore[union-attr]
    assert orders.get_broker_order(intent.client_order_id) == mirror
    assert control.state().entries_paused is True


def test_mirror_mismatch_replay_is_idempotent_and_reconciliation_can_see_review() -> None:
    _orders, control, unit_of_work, intent, mirror = _setup_submitting()
    observed = replace(mirror, quantity=OrderQuantity(11))
    broker = _StaticBroker(observed)
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(BrokerMirrorMismatchError):
        engine.resolve(unit_of_work, intent.client_order_id)
    # A replay after the durable review marker is an idempotent no-op.
    replay = engine.resolve(unit_of_work, intent.client_order_id)
    assert replay.status is OrderStatus.REVIEW_REQUIRED
    assert len(control.commands) == 1

    result = Reconciler(broker=broker, clock=lambda: _T1).collect(unit_of_work, _TRADING_DATE)
    assert result.status.value == "MISMATCH"
    assert any(m.detail == intent.client_order_id.value for m in result.mismatches)


def _fill(*, execution_id: str, broker_order_id: str) -> Fill:
    return Fill(
        execution_id=execution_id,
        broker_order_id=broker_order_id,
        quantity=OrderQuantity(4),
        price=Price.from_cents(9_998),
        occurred_at=_T1,
    )


def _setup_live() -> tuple[
    FakeOrderRepository, FakeControlRepository, _UnitOfWork, OrderIntent, BrokerOrder
]:
    orders, control, unit_of_work, intent, mirror = _setup_submitting()
    orders.transition_status(intent.client_order_id, OrderStatus.ACKNOWLEDGED)
    return orders, control, unit_of_work, intent, mirror


def test_refresh_same_execution_id_same_payload_is_idempotent() -> None:
    orders, control, unit_of_work, intent, mirror = _setup_live()
    existing = _fill(execution_id="exec-1", broker_order_id=mirror.broker_order_id)
    orders.add_fill(existing)
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=4,
        updated_at=_T1,
    )
    broker = _StaticBroker(observed, fills=(existing,))
    result = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control).apply_fills(
        unit_of_work, intent.client_order_id
    )

    assert result.status is OrderStatus.PARTIALLY_FILLED
    assert orders.fill_count == 1
    assert control.state().entries_paused is False


def test_refresh_global_execution_id_collision_marks_review_without_new_fill() -> None:
    orders, control, unit_of_work, intent, mirror = _setup_live()
    existing = _fill(execution_id="exec-cross-order", broker_order_id="b-other")
    orders.add_fill(existing)
    incoming = _fill(execution_id=existing.execution_id, broker_order_id=mirror.broker_order_id)
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=4,
        updated_at=_T1,
    )
    broker = _StaticBroker(observed, fills=(incoming,))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(BrokerMirrorMismatchError, match="execution id"):
        engine.apply_fills(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.REVIEW_REQUIRED
    assert orders.fill_count == 1
    assert orders.get_broker_order(intent.client_order_id) == mirror
    assert control.state().entries_paused is True
    assert control.state().paused_reason == "reconciliation required; conflicting fill"
    assert control.commands[0].reason == "automatic pause on conflicting fill"


def test_refresh_conflicting_execution_ids_in_one_batch_is_atomic() -> None:
    orders, control, unit_of_work, intent, mirror = _setup_live()
    first = _fill(execution_id="exec-batch-conflict", broker_order_id=mirror.broker_order_id)
    second = replace(first, price=Price.from_cents(9_999))
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=8,
        updated_at=_T1,
    )
    broker = _StaticBroker(observed, fills=(first, second))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(BrokerMirrorMismatchError, match="conflicting fills"):
        engine.apply_fills(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.REVIEW_REQUIRED
    assert orders.fill_count == 0
    assert orders.get_broker_order(intent.client_order_id) == mirror
    assert control.state().entries_paused is True
    assert len(control.commands) == 1


def test_refresh_batch_cross_order_collision_is_atomic() -> None:
    orders, control, unit_of_work, intent, mirror = _setup_live()
    existing = _fill(execution_id="exec-cross-batch", broker_order_id="b-other")
    orders.add_fill(existing)
    first = _fill(execution_id="exec-new-batch", broker_order_id=mirror.broker_order_id)
    incoming = _fill(execution_id=existing.execution_id, broker_order_id=mirror.broker_order_id)
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=8,
        updated_at=_T1,
    )
    broker = _StaticBroker(observed, fills=(first, incoming))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(BrokerMirrorMismatchError, match="execution id"):
        engine.apply_fills(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.REVIEW_REQUIRED
    assert orders.fill_count == 1
    assert orders.get_fill_by_execution_id(first.execution_id) is None
    assert orders.get_broker_order(intent.client_order_id) == mirror
    assert control.state().entries_paused is True


def test_refresh_exact_duplicate_events_in_one_batch_are_idempotent() -> None:
    orders, control, unit_of_work, intent, mirror = _setup_live()
    fill = _fill(execution_id="exec-batch-duplicate", broker_order_id=mirror.broker_order_id)
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=4,
        updated_at=_T1,
    )
    broker = _StaticBroker(observed, fills=(fill, fill))
    result = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control).apply_fills(
        unit_of_work, intent.client_order_id
    )

    assert result.status is OrderStatus.PARTIALLY_FILLED
    assert orders.fill_count == 1
    assert orders.get_broker_order(intent.client_order_id) == observed
    assert control.state().entries_paused is False


def test_refresh_batch_order_mismatch_is_atomic() -> None:
    orders, control, unit_of_work, intent, mirror = _setup_live()
    first = _fill(execution_id="exec-order-mismatch", broker_order_id="different-order")
    second = _fill(execution_id="exec-order-mismatch-2", broker_order_id=mirror.broker_order_id)
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=8,
        updated_at=_T1,
    )
    broker = _StaticBroker(observed, fills=(first, second))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(BrokerMirrorMismatchError, match="different order"):
        engine.apply_fills(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.REVIEW_REQUIRED
    assert orders.fill_count == 0
    assert orders.get_broker_order(intent.client_order_id) == mirror
    assert control.state().entries_paused is True


class _FillLookupFailureOrders(FakeOrderRepository):
    fail_lookup = False

    def get_fill_by_execution_id(self, execution_id: str) -> Fill | None:
        if self.fail_lookup:
            raise RuntimeError("injected fill lookup failure")
        return super().get_fill_by_execution_id(execution_id)


def test_refresh_preflight_lookup_failure_has_no_mutation() -> None:
    orders = _FillLookupFailureOrders()
    orders, control, unit_of_work, intent, mirror = _setup_submitting_with_orders(orders)
    orders.transition_status(intent.client_order_id, OrderStatus.ACKNOWLEDGED)
    unit_of_work.commit()
    orders.fail_lookup = True
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=8,
        updated_at=_T1,
    )
    first = _fill(execution_id="exec-lookup-failure-1", broker_order_id=mirror.broker_order_id)
    second = _fill(execution_id="exec-lookup-failure-2", broker_order_id=mirror.broker_order_id)
    broker = _StaticBroker(observed, fills=(first, second))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(RuntimeError, match="injected fill lookup failure"):
        engine.apply_fills(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.ACKNOWLEDGED
    assert orders.fill_count == 0
    assert orders.get_broker_order(intent.client_order_id) == mirror
    assert control.state().entries_paused is False
    assert control.commands == []


class _PauseFailureControl(FakeControlRepository):
    def set_entries_paused(self, paused: bool, reason: str | None):  # type: ignore[no-untyped-def]
        del paused, reason
        raise RuntimeError("injected pause persistence failure")


class _AuditFailureControl(FakeControlRepository):
    def add_command(self, record):  # type: ignore[no-untyped-def]
        del record
        raise RuntimeError("injected audit persistence failure")


@pytest.mark.parametrize("control_type", (_PauseFailureControl, _AuditFailureControl))
def test_refresh_batch_conflict_has_no_fill_when_pause_or_audit_fails(
    control_type,
) -> None:  # type: ignore[no-untyped-def]
    orders, _, _, intent, mirror = _setup_live()
    control = control_type(_T0)
    unit_of_work = _UnitOfWork(orders, control)
    unit_of_work.commit()
    first = _fill(execution_id="exec-pause-failure", broker_order_id=mirror.broker_order_id)
    second = replace(first, price=Price.from_cents(9_999))
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=8,
        updated_at=_T1,
    )
    broker = _StaticBroker(observed, fills=(first, second))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(ControlPersistenceError):
        engine.apply_fills(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.REVIEW_REQUIRED
    assert orders.fill_count == 0
    assert orders.get_broker_order(intent.client_order_id) == mirror


def test_refresh_batch_conflict_marker_commit_failure_has_no_mutation() -> None:
    orders, control, unit_of_work, intent, mirror = _setup_live()
    # Establish a snapshot at the preflight state so the injected marker
    # commit failure can exercise the rollback contract deterministically.
    unit_of_work.commit()

    def fail_commit() -> None:
        raise RuntimeError("injected marker commit failure")

    unit_of_work.commit = fail_commit  # type: ignore[method-assign]
    first = _fill(execution_id="exec-marker-failure", broker_order_id=mirror.broker_order_id)
    second = replace(first, price=Price.from_cents(9_999))
    observed = replace(
        mirror,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=8,
        updated_at=_T1,
    )
    broker = _StaticBroker(observed, fills=(first, second))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(ControlPersistenceError):
        engine.apply_fills(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.ACKNOWLEDGED
    assert orders.fill_count == 0
    assert orders.get_broker_order(intent.client_order_id) == mirror
    assert control.state().entries_paused is False
    assert control.commands == []


@pytest.mark.parametrize("control_type", (_PauseFailureControl, _AuditFailureControl))
def test_review_marker_survives_pause_or_audit_failure(control_type) -> None:  # type: ignore[no-untyped-def]
    orders, _, _, intent, mirror = _setup_submitting()
    control = control_type(_T0)
    unit_of_work = _UnitOfWork(orders, control)
    broker = _StaticBroker(replace(mirror, quantity=OrderQuantity(11)))
    engine = ExecutionEngine(broker=broker, clock=lambda: _T1, control=control)

    with pytest.raises(ControlPersistenceError):
        engine.resolve(unit_of_work, intent.client_order_id)

    current = orders.get(intent.client_order_id)
    assert current is not None and current.status is OrderStatus.REVIEW_REQUIRED
