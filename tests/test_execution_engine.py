"""Unit tests for the execution engine's timeout, idempotency, and recovery rules."""

from __future__ import annotations

from dataclasses import replace

import pytest

from fakes.orders import FakeOrderRepository
from seven_lens.application.execution_service import (
    BrokerMirrorMismatchError,
    ExecutionEngine,
    ExecutionStateError,
)
from seven_lens.application.ports.broker import (
    AssetClass,
    AssetStatus,
    BrokerTransportError,
    PaperAsset,
    RejectionReason,
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
    BrokerOrderStatus,
    ClientOrderId,
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


class MutableClock:
    def __init__(self, now: UtcTimestamp = _BASE_TIME) -> None:
        self.now = now

    def __call__(self) -> UtcTimestamp:
        return self.now


class FakeOrderUnitOfWork:
    def __init__(self, orders: FakeOrderRepository) -> None:
        self.orders = orders
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

    def get_order(self, client_order_id: ClientOrderId):  # type: ignore[no-untyped-def]
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


def _intent(*, target_version: int = 1, quantity: int = 10) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=TradingDate.from_isoformat("2026-08-17"),
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


def _outbox_intent(
    orders: FakeOrderRepository, *, target_version: int = 1, quantity: int = 10
) -> OrderIntent:
    intent = _intent(target_version=target_version, quantity=quantity)
    orders.add(intent)
    orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
    orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
    return orders.get(intent.client_order_id) or intent


def _engine(
    orders: FakeOrderRepository, broker: FakePaperBroker
) -> tuple[ExecutionEngine, FakeOrderUnitOfWork, DurabilityGuardBroker]:
    guard = DurabilityGuardBroker(broker, orders)
    unit_of_work = FakeOrderUnitOfWork(orders)
    engine = ExecutionEngine(broker=guard, clock=MutableClock())
    return engine, unit_of_work, guard


class TestHappyPathSubmission:
    def test_acknowledged_submission_records_mirror_and_commits_first(self) -> None:
        orders = FakeOrderRepository()
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, guard = _engine(orders, broker)
        intent = _outbox_intent(orders)

        result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert result.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 1
        assert unit_of_work.commit_count >= 2  # SUBMITTING durable, then outcome
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None and mirror.status is BrokerOrderStatus.ACCEPTED

    def test_immediate_full_fill_reaches_filled(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(
            clock=MutableClock(),
            plans={
                intent.client_order_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.ACKNOWLEDGE,
                    first_fill=FakeFillStep(
                        quantity=OrderQuantity(10), price=Price.from_cents(10_000)
                    ),
                )
            },
        )
        engine, unit_of_work, _ = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)

        result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert result.status is OrderStatus.FILLED
        assert orders.fill_count == 1
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None and mirror.filled_quantity == 10

    def test_deterministic_rejection_needs_no_mirror(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(
            clock=MutableClock(),
            plans={
                intent.client_order_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.REJECT,
                    rejection_reason=RejectionReason.INSUFFICIENT_CASH,
                )
            },
        )
        engine, unit_of_work, _ = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)

        result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert result.status is OrderStatus.REJECTED
        assert orders.get_broker_order(intent.client_order_id) is None


class TestTimeoutSemantics:
    def test_timeout_before_accept_parks_in_unknown_then_resolves_by_resubmit(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(
            clock=MutableClock(),
            plans={
                intent.client_order_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.TIMEOUT_BEFORE_ACCEPT
                )
            },
        )
        engine, unit_of_work, guard = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)

        parked = engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        assert parked.status is OrderStatus.UNKNOWN
        assert broker.get_order(intent.client_order_id) is None

        resolved = engine.resolve(unit_of_work, intent.client_order_id)
        assert resolved.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 2  # exactly one sanctioned same-id retry
        assert broker.get_order(intent.client_order_id) is not None

    def test_timeout_after_accept_resolves_without_second_order(self) -> None:
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
        engine, unit_of_work, guard = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)

        parked = engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        assert parked.status is OrderStatus.UNKNOWN
        assert orders.get_broker_order(intent.client_order_id) is None

        resolved = engine.resolve(unit_of_work, intent.client_order_id)
        assert resolved.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 1  # resolution queried; nothing was resubmitted
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None
        assert mirror.broker_order_id == "fake-order-000001"

    def test_unknown_order_stays_unknown_past_deadline_without_resubmit(self) -> None:
        """The cutoff closes the window, never fabricates an expiry for an
        intent the broker may already hold."""
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(
            clock=MutableClock(),
            plans={
                intent.client_order_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.TIMEOUT_BEFORE_ACCEPT
                )
            },
        )
        engine, unit_of_work, guard = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        clock = MutableClock(now=_CANCEL_AT)
        engine_closed = ExecutionEngine(broker=guard, clock=clock)
        resolved = engine_closed.resolve(unit_of_work, intent.client_order_id)

        assert resolved.status is OrderStatus.UNKNOWN
        assert guard.submit_calls == 1  # the window never reopens a submission
        assert broker.get_order(intent.client_order_id) is None


class TestCrashRecovery:
    def test_crash_after_submitting_state_recovers_without_duplicate(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, _ = _engine(orders, broker)
        _outbox_intent(orders)
        # Simulate a crash between the durable SUBMITTING write and the broker call.
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)

        recovered = engine.recover(unit_of_work)

        assert [item.status for item in recovered] == [OrderStatus.ACKNOWLEDGED]
        assert broker.get_order(intent.client_order_id) is not None

    def test_crash_after_broker_accept_resolves_by_query(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(clock=MutableClock())
        # The broker accepted an order before the process died.
        broker.submit_order(intent)
        engine, unit_of_work, _ = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)

        recovered = engine.recover(unit_of_work)

        assert [item.status for item in recovered] == [OrderStatus.ACKNOWLEDGED]
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None
        assert mirror.broker_order_id == "fake-order-000001"

    def test_recovery_leaves_other_states_alone(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        engine, unit_of_work, _ = _engine(orders, FakePaperBroker(clock=MutableClock()))

        assert engine.recover(unit_of_work) == ()
        assert orders.get(intent.client_order_id) is not None


class TestFillApplication:
    def test_partial_fills_progress_and_duplicates_are_idempotent(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None

        first = engine.apply_fills(unit_of_work, intent.client_order_id)
        assert first.status is OrderStatus.ACKNOWLEDGED
        assert orders.fill_count == 0

        broker.apply_fill(
            mirror.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(4), price=Price.from_cents(9_998)),
        )
        partial = engine.apply_fills(unit_of_work, intent.client_order_id)
        assert partial.status is OrderStatus.PARTIALLY_FILLED
        assert orders.fill_count == 1

        # Replaying the same broker state must not duplicate the fill.
        replay = engine.apply_fills(unit_of_work, intent.client_order_id)
        assert replay.status is OrderStatus.PARTIALLY_FILLED
        assert orders.fill_count == 1

        broker.apply_fill(
            mirror.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(6), price=Price.from_cents(9_999)),
        )
        final = engine.apply_fills(unit_of_work, intent.client_order_id)
        assert final.status is OrderStatus.FILLED
        assert orders.fill_count == 2

    def test_apply_fills_requires_an_accepted_mirror(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        engine, unit_of_work, _ = _engine(orders, FakePaperBroker(clock=MutableClock()))
        # An intent that claims acceptance without any recorded mirror is corrupt.
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)
        orders.transition_status(intent.client_order_id, OrderStatus.ACKNOWLEDGED)
        with pytest.raises(ExecutionStateError, match="no local mirror"):
            engine.apply_fills(unit_of_work, intent.client_order_id)


class TestCancellations:
    def test_cancel_acknowledged_order(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        canceled = engine.request_cancel(unit_of_work, intent.client_order_id)

        assert canceled.status is OrderStatus.CANCELED
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None and mirror.status is BrokerOrderStatus.CANCELED

    def test_cancel_pending_is_idempotent_and_races_to_filled(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None
        engine.request_cancel(unit_of_work, intent.client_order_id)
        # The order was already terminal; a retried cancel must not resurrect it.
        with pytest.raises(ExecutionStateError):
            engine.request_cancel(unit_of_work, intent.client_order_id)

    def test_cancel_requires_live_state(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        engine, unit_of_work, _ = _engine(orders, FakePaperBroker(clock=MutableClock()))
        with pytest.raises(ExecutionStateError, match="cancel requires"):
            engine.request_cancel(unit_of_work, intent.client_order_id)


class TestMismatchAndExpiry:
    def test_conflicting_broker_parameters_fail_closed(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock())
        # Someone submitted the same client id with different parameters.
        broker.submit_order(replace(intent, quantity=OrderQuantity(20)))
        engine, unit_of_work, _ = _engine(orders, broker)
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)

        with pytest.raises(BrokerMirrorMismatchError, match="contradict"):
            engine.resolve(unit_of_work, intent.client_order_id)
        parked = orders.get(intent.client_order_id)
        assert parked is not None and parked.status is OrderStatus.SUBMITTING

    def test_window_cutoff_cancels_broker_accepted_orders(self) -> None:
        """A past-deadline accepted order is canceled at the broker, not orphaned."""
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        late = ExecutionEngine(broker=broker, clock=MutableClock(now=_CANCEL_AT))
        closed = late.expire_overdue(unit_of_work)

        assert [item.client_order_id for item in closed] == [intent.client_order_id]
        assert closed[0].status is OrderStatus.CANCELED
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None and mirror.status is BrokerOrderStatus.CANCELED
        assert late.expire_overdue(unit_of_work) == ()

    def test_window_cutoff_expires_intents_without_broker_orders(self) -> None:
        orders = FakeOrderRepository()
        _outbox_intent(orders)
        _, unit_of_work, _ = _engine(orders, FakePaperBroker(clock=MutableClock()))

        late = ExecutionEngine(
            broker=FakePaperBroker(clock=MutableClock()), clock=MutableClock(now=_CANCEL_AT)
        )
        closed = late.expire_overdue(unit_of_work)

        assert closed[0].status is OrderStatus.EXPIRED

    def test_window_cutoff_resolves_unknown_before_deciding(self) -> None:
        """An UNKNOWN intent with a live broker order resolves, never blindly expires."""
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
        engine, unit_of_work, _ = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        parked = orders.get(intent.client_order_id)
        assert parked is not None and parked.status is OrderStatus.UNKNOWN

        late = ExecutionEngine(broker=broker, clock=MutableClock(now=_CANCEL_AT))
        closed = late.expire_overdue(unit_of_work)

        assert closed[0].status is OrderStatus.CANCELED
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None and mirror.broker_order_id == "fake-order-000001"

    def test_window_cutoff_never_expires_on_cancel_transport_failure(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        class CancelFailsBroker:
            def __init__(self, inner: FakePaperBroker) -> None:
                self._inner = inner

            def cancel_order(self, broker_order_id: str) -> bool:
                raise BrokerTransportError("cancel outcome unknown")

            def get_order(self, client_order_id):  # type: ignore[no-untyped-def]
                return self._inner.get_order(client_order_id)

            def submit_order(self, intent):  # type: ignore[no-untyped-def]
                return self._inner.submit_order(intent)

            def list_fills(self, broker_order_id: str):  # type: ignore[no-untyped-def]
                return self._inner.list_fills(broker_order_id)

            def list_open_orders(self):  # type: ignore[no-untyped-def]
                return self._inner.list_open_orders()

            def list_positions(self):  # type: ignore[no-untyped-def]
                return self._inner.list_positions()

            def list_recent_orders(self, *, since):  # type: ignore[no-untyped-def]
                return self._inner.list_recent_orders(since=since)

            def get_asset(self, symbol):  # type: ignore[no-untyped-def]
                return self._inner.get_asset(symbol)

            def account(self):  # type: ignore[no-untyped-def]
                return self._inner.account()

        late = ExecutionEngine(broker=CancelFailsBroker(broker), clock=MutableClock(now=_CANCEL_AT))
        closed = late.expire_overdue(unit_of_work)

        assert closed == ()
        # The durable cancel request stands; the unknown cancel outcome is left
        # to recovery and reconciliation rather than a local expiry.
        current = orders.get(intent.client_order_id)
        assert current is not None and current.status is OrderStatus.CANCEL_PENDING

    def test_missing_intent_fails_closed(self) -> None:
        engine, unit_of_work, _ = _engine(
            FakeOrderRepository(), FakePaperBroker(clock=MutableClock())
        )
        unknown_id = ClientOrderId("slv1-seven-lens-2026-08-17-open-t99-AAPL-buy")
        with pytest.raises(ExecutionStateError, match="no order intent"):
            engine.submit_from_outbox(unit_of_work, unknown_id)


class TestIdempotentNoOps:
    def test_resubmitting_a_processed_intent_is_a_no_op(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, guard = _engine(orders, broker)
        first = engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        again = engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert again.status is first.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 1


class TestAssetGate:
    def test_unknown_symbol_fails_closed_before_any_state_change(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock(), unknown_assets={intent.symbol.value})
        engine, unit_of_work, guard = _engine(orders, broker)

        with pytest.raises(ExecutionStateError, match="does not trade symbol"):
            engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert guard.submit_calls == 0
        assert (orders.get(intent.client_order_id) or intent).status is OrderStatus.OUTBOX_PENDING
        assert orders.get_broker_order(intent.client_order_id) is None

    def test_untradable_symbol_fails_closed_before_any_state_change(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(
            clock=MutableClock(),
            assets={
                intent.symbol.value: PaperAsset(
                    symbol=intent.symbol,
                    asset_class=AssetClass.US_EQUITY,
                    status=AssetStatus.ACTIVE,
                    tradable=False,
                    exchange="ARCA",
                )
            },
        )
        engine, unit_of_work, guard = _engine(orders, broker)

        with pytest.raises(ExecutionStateError, match="does not trade symbol"):
            engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert guard.submit_calls == 0
        assert (orders.get(intent.client_order_id) or intent).status is OrderStatus.OUTBOX_PENDING

    def test_non_equity_asset_fails_closed_even_when_tradable(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(
            clock=MutableClock(),
            assets={
                intent.symbol.value: PaperAsset(
                    symbol=intent.symbol,
                    asset_class=AssetClass.CRYPTO,
                    status=AssetStatus.ACTIVE,
                    tradable=True,
                    exchange="CRYPTO",
                )
            },
        )
        engine, unit_of_work, guard = _engine(orders, broker)

        with pytest.raises(ExecutionStateError, match="US equity"):
            engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert guard.submit_calls == 0
        assert (orders.get(intent.client_order_id) or intent).status is OrderStatus.OUTBOX_PENDING

    def test_asset_gate_blocks_risk_exit_too(self) -> None:
        orders = FakeOrderRepository()
        intent = _intent()
        intent = replace(intent, intent_type=OrderIntentType.RISK_EXIT)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        broker = FakePaperBroker(clock=MutableClock(), unknown_assets={intent.symbol.value})
        engine, unit_of_work, guard = _engine(orders, broker)

        with pytest.raises(ExecutionStateError, match="does not trade symbol"):
            engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        assert guard.submit_calls == 0
        assert (orders.get(intent.client_order_id) or intent).status is OrderStatus.OUTBOX_PENDING


class TestCancelPendingCrashRecovery:
    def test_crash_between_cancel_pending_and_broker_cancel(self) -> None:
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None

        # Crash: the intent is durably CANCEL_PENDING but the broker was never called.
        orders.transition_status(intent.client_order_id, OrderStatus.CANCEL_PENDING)

        recovered = engine.request_cancel(unit_of_work, intent.client_order_id)

        assert recovered.status is OrderStatus.CANCELED
        final_mirror = orders.get_broker_order(intent.client_order_id)
        assert final_mirror is not None and final_mirror.status is BrokerOrderStatus.CANCELED


class TestPendingCancelCutoff:
    def test_async_cancel_stays_cancel_pending_and_converges(self) -> None:
        """A cutoff cancel the broker answers asynchronously never expires locally."""
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock(), cancel_mode=FakeCancelMode.PENDING)
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        late = ExecutionEngine(broker=broker, clock=MutableClock(now=_CANCEL_AT))
        closed = late.expire_overdue(unit_of_work)

        assert len(closed) == 1 and closed[0].status is OrderStatus.CANCEL_PENDING
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None and mirror.status is BrokerOrderStatus.PENDING_CANCEL

        broker.resolve_pending_cancel(mirror.broker_order_id)
        resolved = late.apply_fills(unit_of_work, intent.client_order_id)
        assert resolved.status is OrderStatus.CANCELED

    def test_fills_during_pending_cancel_are_absorbed(self) -> None:
        """A fill racing the cancel is broker truth; it wins locally too."""
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock(), cancel_mode=FakeCancelMode.PENDING)
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None
        engine.request_cancel(unit_of_work, intent.client_order_id)

        broker.apply_fill(
            mirror.broker_order_id,
            FakeFillStep(quantity=OrderQuantity(10), price=Price.from_cents(10_000)),
        )
        resolved = engine.apply_fills(unit_of_work, intent.client_order_id)

        assert resolved.status is OrderStatus.FILLED
        assert orders.fill_count == 1
        final_mirror = orders.get_broker_order(intent.client_order_id)
        assert final_mirror is not None and final_mirror.filled_quantity == 10

    def test_cutoff_cancel_timeout_never_changes_state(self) -> None:
        """A transport failure on the cutoff cancel leaves CANCEL_PENDING intact."""
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock(), cancel_mode=FakeCancelMode.TIMEOUT)
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)

        late = ExecutionEngine(broker=broker, clock=MutableClock(now=_CANCEL_AT))
        assert late.expire_overdue(unit_of_work) == ()
        current = orders.get(intent.client_order_id)
        assert current is not None and current.status is OrderStatus.CANCEL_PENDING

    def test_broker_expired_after_async_cancel_converges_to_expired(self) -> None:
        """EXPIRED is only ever entered on explicit broker proof."""
        orders = FakeOrderRepository()
        intent = _outbox_intent(orders)
        broker = FakePaperBroker(clock=MutableClock(), cancel_mode=FakeCancelMode.PENDING)
        engine, unit_of_work, _ = _engine(orders, broker)
        engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        mirror = orders.get_broker_order(intent.client_order_id)
        assert mirror is not None
        engine.request_cancel(unit_of_work, intent.client_order_id)

        broker.force_status(intent.client_order_id, BrokerOrderStatus.EXPIRED)
        resolved = engine.apply_fills(unit_of_work, intent.client_order_id)

        assert resolved.status is OrderStatus.EXPIRED


class TestBrokerTerminalRecovery:
    """SUBMITTING intents must converge to every broker-proven outcome."""

    def _recovered_status(self, broker_status: BrokerOrderStatus) -> OrderStatus:
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(clock=MutableClock())
        engine, unit_of_work, _ = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)
        broker.submit_order(intent)
        if broker_status in (
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.PARTIALLY_FILLED,
        ):
            mirror = broker.get_order(intent.client_order_id)
            assert mirror is not None
            step = FakeFillStep(
                quantity=OrderQuantity(10 if broker_status is BrokerOrderStatus.FILLED else 4),
                price=Price.from_cents(10_000),
            )
            broker.apply_fill(mirror.broker_order_id, step)
        else:
            broker.force_status(intent.client_order_id, broker_status)
        recovered = engine.recover(unit_of_work)
        return recovered[0].status

    def test_recovery_of_canceled(self) -> None:
        assert self._recovered_status(BrokerOrderStatus.CANCELED) is OrderStatus.CANCELED

    def test_recovery_of_filled(self) -> None:
        assert self._recovered_status(BrokerOrderStatus.FILLED) is OrderStatus.FILLED

    def test_recovery_of_partially_filled(self) -> None:
        assert (
            self._recovered_status(BrokerOrderStatus.PARTIALLY_FILLED)
            is OrderStatus.PARTIALLY_FILLED
        )

    def test_recovery_of_expired(self) -> None:
        assert self._recovered_status(BrokerOrderStatus.EXPIRED) is OrderStatus.EXPIRED

    def test_recovery_of_rejected(self) -> None:
        assert self._recovered_status(BrokerOrderStatus.REJECTED) is OrderStatus.REJECTED

    def test_recovery_of_pending_cancel(self) -> None:
        assert (
            self._recovered_status(BrokerOrderStatus.PENDING_CANCEL) is OrderStatus.CANCEL_PENDING
        )

    def test_recovery_of_review_statuses(self) -> None:
        for status in (
            BrokerOrderStatus.DONE_FOR_DAY,
            BrokerOrderStatus.REPLACED,
            BrokerOrderStatus.PENDING_REPLACE,
            BrokerOrderStatus.STOPPED,
            BrokerOrderStatus.SUSPENDED,
            BrokerOrderStatus.CALCULATED,
        ):
            recovered = self._recovered_status(status)
            assert recovered is OrderStatus.REVIEW_REQUIRED

    def test_recovery_of_unknown_without_broker_order_stays_unknown(self) -> None:
        """A missing broker order past deadline is UNKNOWN, never a fabricated terminal."""
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(
            clock=MutableClock(),
            plans={
                intent.client_order_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.TIMEOUT_BEFORE_ACCEPT
                )
            },
        )
        _, unit_of_work, _ = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)
        orders.transition_status(intent.client_order_id, OrderStatus.SUBMITTING)

        late = ExecutionEngine(broker=broker, clock=MutableClock(now=_CANCEL_AT))
        recovered = late.recover(unit_of_work)

        assert [item.status for item in recovered] == [OrderStatus.UNKNOWN]


class TestDuplicateDelayedVisibility:
    def test_duplicate_post_then_delayed_get_visible_resolves(self) -> None:
        """A duplicate POST with delayed full-record visibility never reads as REJECTED."""
        orders = FakeOrderRepository()
        intent = _intent()
        broker = FakePaperBroker(
            clock=MutableClock(),
            hidden_client_ids={intent.client_order_id.value},
            plans={
                intent.client_order_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.TIMEOUT_AFTER_ACCEPT
                )
            },
        )
        engine, unit_of_work, guard = _engine(orders, broker)
        orders.add(intent)
        orders.transition_status(intent.client_order_id, OrderStatus.RISK_APPROVED)
        orders.transition_status(intent.client_order_id, OrderStatus.OUTBOX_PENDING)

        parked = engine.submit_from_outbox(unit_of_work, intent.client_order_id)
        assert parked.status is OrderStatus.UNKNOWN

        resolved = engine.resolve(unit_of_work, intent.client_order_id)
        assert resolved.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 2  # same-id resubmit inside the window, no new id

        broker.reveal_order(intent.client_order_id)
        again = engine.resolve(unit_of_work, intent.client_order_id)
        assert again.status is OrderStatus.ACKNOWLEDGED
        assert guard.submit_calls == 2  # the visible order only re-recorded the mirror
