# mypy: ignore-errors
"""Trade-update consumer tests: duplicates, out-of-order, and fail-closed edges."""

from __future__ import annotations

import logging

import pytest

from fakes.control import FakeControlRepository
from fakes.orders import FakeOrderRepository
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.control import ControlCommand
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
_T3 = UtcTimestamp.from_isoformat("2026-08-17T13:35:03.000000Z")
_T4 = UtcTimestamp.from_isoformat("2026-08-17T13:35:04.000000Z")
_CANCEL_AT = UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z")
_TRADING_DATE = TradingDate.from_isoformat("2026-08-17")


class _UnitOfWork:
    def __init__(self, orders: FakeOrderRepository) -> None:
        self.orders = orders
        self.control = FakeControlRepository(_T0)
        self.commit_count = 0
        self.rollback_count = 0

    def rollback(self) -> None:
        self.rollback_count += 1
        self.orders.rollback()

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


class TestStatusConflictConvergence:
    """Unrepresentable status updates converge to TradeUpdateError + durable pause."""

    def _partially_filled_state(self) -> tuple[_UnitOfWork, OrderIntent]:
        unit_of_work, intent, _ = _setup()
        outcome = TradeUpdateConsumer().apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.PARTIALLY_FILLED, 5, _T2)
        )
        assert outcome is TradeUpdateOutcome.APPLIED
        return unit_of_work, intent

    def _assert_typed_conflict_result(
        self,
        unit_of_work: _UnitOfWork,
        intent: OrderIntent,
        conflict: pytest.ExceptionInfo[TradeUpdateError],
        commits_before: int,
        rollbacks_before: int,
        expected_status: BrokerOrderStatus,
        expected_filled: int,
        expected_intent_status: OrderStatus,
    ) -> None:
        del intent
        assert type(conflict.value) is TradeUpdateError
        mirror = unit_of_work.orders.get_broker_order_by_id("b-1")
        assert mirror is not None and mirror.filled_quantity == expected_filled
        assert mirror.status is expected_status
        current = unit_of_work.orders.get(
            unit_of_work.orders.get_broker_order_by_id("b-1").client_order_id
        )
        assert current is not None and current.status is expected_intent_status
        assert unit_of_work.rollback_count == rollbacks_before + 1
        # Rollback happens first; the safety pause and its audit are separate commits.
        assert unit_of_work.commit_count == commits_before + 2
        state = unit_of_work.control.state()
        assert state.entries_paused is True
        assert state.paused_reason == "reconciliation required; conflicting status"
        pause_commands = [
            command
            for command in unit_of_work.control.commands
            if command.command is ControlCommand.PAUSE_ENTRIES
        ]
        assert len(pause_commands) == 1
        assert pause_commands[0].reason == "automatic pause on conflicting status"
        assert pause_commands[0].actor == "trade_update_consumer"

    def test_filled_quantity_regression_is_typed_rolled_back_and_paused(self) -> None:
        unit_of_work, intent = self._partially_filled_state()
        consumer = TradeUpdateConsumer()
        commits_before = unit_of_work.commit_count
        rollbacks_before = unit_of_work.rollback_count

        with pytest.raises(TradeUpdateError) as conflict:
            consumer.apply(
                unit_of_work,
                _status_update(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T3),
            )

        assert "b-1" not in str(conflict.value)
        self._assert_typed_conflict_result(
            unit_of_work,
            intent,
            conflict,
            commits_before,
            rollbacks_before,
            BrokerOrderStatus.PARTIALLY_FILLED,
            5,
            OrderStatus.PARTIALLY_FILLED,
        )

    def test_filled_status_with_mismatched_quantity_is_typed_rolled_back_and_paused(
        self,
    ) -> None:
        unit_of_work, intent, _mirror = _setup()
        consumer = TradeUpdateConsumer()
        commits_before = unit_of_work.commit_count
        rollbacks_before = unit_of_work.rollback_count

        with pytest.raises(TradeUpdateError) as conflict:
            consumer.apply(unit_of_work, _status_update(intent, BrokerOrderStatus.FILLED, 4, _T2))

        self._assert_typed_conflict_result(
            unit_of_work,
            intent,
            conflict,
            commits_before,
            rollbacks_before,
            BrokerOrderStatus.ACCEPTED,
            0,
            OrderStatus.ACKNOWLEDGED,
        )

    def test_overfill_beyond_order_quantity_is_typed_rolled_back_and_paused(self) -> None:
        unit_of_work, intent, _mirror = _setup()
        consumer = TradeUpdateConsumer()
        commits_before = unit_of_work.commit_count
        rollbacks_before = unit_of_work.rollback_count

        with pytest.raises(TradeUpdateError) as conflict:
            consumer.apply(
                unit_of_work,
                _status_update(intent, BrokerOrderStatus.PARTIALLY_FILLED, 12, _T2),
            )

        self._assert_typed_conflict_result(
            unit_of_work,
            intent,
            conflict,
            commits_before,
            rollbacks_before,
            BrokerOrderStatus.ACCEPTED,
            0,
            OrderStatus.ACKNOWLEDGED,
        )

    def test_illegal_broker_transition_is_typed_rolled_back_and_paused(self) -> None:
        unit_of_work, intent = self._partially_filled_state()
        consumer = TradeUpdateConsumer()
        canceled = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.CANCELED, 5, _T3)
        )
        assert canceled is TradeUpdateOutcome.APPLIED
        commits_before = unit_of_work.commit_count
        rollbacks_before = unit_of_work.rollback_count

        with pytest.raises(TradeUpdateError) as conflict:
            consumer.apply(unit_of_work, _status_update(intent, BrokerOrderStatus.ACCEPTED, 5, _T4))

        self._assert_typed_conflict_result(
            unit_of_work,
            intent,
            conflict,
            commits_before,
            rollbacks_before,
            BrokerOrderStatus.CANCELED,
            5,
            OrderStatus.CANCELED,
        )

    def test_equal_timestamp_conflicting_payload_is_typed_rolled_back_and_paused(
        self,
    ) -> None:
        unit_of_work, intent, _mirror = _setup()
        consumer = TradeUpdateConsumer()
        commits_before = unit_of_work.commit_count
        rollbacks_before = unit_of_work.rollback_count

        with pytest.raises(TradeUpdateError) as conflict:
            consumer.apply(
                unit_of_work, _status_update(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T1)
            )

        assert type(conflict.value) is TradeUpdateError
        assert "b-1" not in str(conflict.value)
        self._assert_typed_conflict_result(
            unit_of_work,
            intent,
            conflict,
            commits_before,
            rollbacks_before,
            BrokerOrderStatus.ACCEPTED,
            0,
            OrderStatus.ACKNOWLEDGED,
        )

    def test_equal_timestamp_identical_payload_stays_duplicate_without_pause(self) -> None:
        unit_of_work, intent, _mirror = _setup()
        consumer = TradeUpdateConsumer()

        outcome = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.ACCEPTED, 0, _T1)
        )

        assert outcome is TradeUpdateOutcome.DUPLICATE
        state = unit_of_work.control.state()
        assert state.entries_paused is False
        assert state.paused_reason is None
        assert unit_of_work.control.commands == []
        assert unit_of_work.rollback_count == 0
        assert unit_of_work.commit_count == 0

    def test_audit_failure_leaves_conflict_pause_durable_and_observable(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        unit_of_work, intent, _mirror = _setup()

        def fail_audit(_record: object) -> object:
            raise RuntimeError("injected audit repository failure")

        monkeypatch.setattr(unit_of_work.control, "add_command", fail_audit)
        with (
            caplog.at_level(logging.ERROR, logger="seven_lens.execution.trade_updates"),
            pytest.raises(TradeUpdateError, match="audit"),
        ):
            TradeUpdateConsumer().apply(
                unit_of_work,
                _status_update(intent, BrokerOrderStatus.FILLED, 4, _T2),
            )

        state = unit_of_work.control.state()
        assert state.entries_paused is True
        assert state.paused_reason == "reconciliation required; conflicting status"
        assert unit_of_work.commit_count == 1
        assert "trade_update_conflict_pause_audit_failed" in caplog.text

    def test_stale_unknown_and_monotonic_paths_never_pause(self) -> None:
        unit_of_work, intent = self._partially_filled_state()
        consumer = TradeUpdateConsumer()

        stale = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.PARTIALLY_FILLED, 4, _T1)
        )
        assert stale is TradeUpdateOutcome.STALE
        assert unit_of_work.control.state().entries_paused is False
        assert unit_of_work.control.commands == []

        forged = OrderStatusUpdate(
            client_order_id=intent.client_order_id,
            broker_order_id="b-other",
            status=BrokerOrderStatus.FILLED,
            filled_quantity=10,
            observed_at=_T3,
        )
        assert consumer.apply(unit_of_work, forged) is TradeUpdateOutcome.UNKNOWN_ORDER
        assert unit_of_work.control.state().entries_paused is False

        applied = consumer.apply(
            unit_of_work, _status_update(intent, BrokerOrderStatus.FILLED, 10, _T3)
        )
        assert applied is TradeUpdateOutcome.APPLIED
        final = unit_of_work.orders.get(intent.client_order_id)
        assert final is not None and final.status is OrderStatus.FILLED
        assert unit_of_work.control.state().entries_paused is False
        assert unit_of_work.control.commands == []
