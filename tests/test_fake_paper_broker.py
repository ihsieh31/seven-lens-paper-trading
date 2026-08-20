# mypy: ignore-errors
"""Behavioral tests for the deterministic fake Paper broker and its faults."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from seven_lens.application.ports.broker import (
    BrokerConflictError,
    BrokerTransportError,
    PaperAccount,
    RejectionReason,
    SubmitAccepted,
    SubmitRejected,
)
from seven_lens.config.broker import BrokerEnvironment
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.fake_broker import (
    FakeFillStep,
    FakePaperBroker,
    FakeSubmitOutcome,
    FakeSubmitPlan,
)
from seven_lens.execution.orders import (
    BrokerOrderStatus,
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    Price,
    PriceCollar,
    Symbol,
)


def _clock() -> Iterator[UtcTimestamp]:
    # A strictly increasing deterministic clock proves updated_at monotonicity.
    step = 0
    while True:
        step += 1
        yield UtcTimestamp.from_isoformat(f"2026-08-17T13:35:{step % 60:02d}.{step:06d}Z")


def _intent(*, target_version: int) -> OrderIntent:
    return OrderIntent.create(
        strategy="seven-lens",
        trading_date=TradingDate.from_isoformat("2026-08-17"),
        window="open",
        target_version=target_version,
        symbol=Symbol("AAPL"),
        side=OrderSide.BUY,
        quantity=OrderQuantity(10),
        intent_type=OrderIntentType.REBALANCE,
        limit_price=Price.from_cents(10_000),
        collar=PriceCollar(reference=Price.from_cents(10_000), offset_bps=100),
        earliest_submit_at=UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z"),
        cancel_at=UtcTimestamp.from_isoformat("2026-08-17T13:45:00.000000Z"),
        run_id=RunId.new(),
        created_at=UtcTimestamp.from_isoformat("2026-08-17T13:30:00.000000Z"),
    )


def _broker() -> FakePaperBroker:
    return FakePaperBroker(clock=lambda: next(_clock()))


class TestAccountSurface:
    def test_account_asserts_paper_environment(self) -> None:
        account = _broker().account()
        assert isinstance(account, PaperAccount)
        assert account.environment is BrokerEnvironment.PAPER
        assert account.account_id == "fake-paper-primary"
        assert account.cash.value.as_tuple().exponent == -2


class TestAcknowledgedSubmissions:
    def test_submit_acknowledges_and_mirrors_the_intent(self) -> None:
        broker = _broker()
        result = broker.submit_order(_intent(target_version=1))
        assert isinstance(result, SubmitAccepted)
        order = result.order
        assert order.broker_order_id == "fake-order-000001"
        assert order.status is BrokerOrderStatus.ACCEPTED
        assert order.filled_quantity == 0
        assert order.quantity.value == 10
        assert order.limit_price.cents == 10_000
        assert broker.list_open_orders() == (order,)

    def test_immediate_full_fill_closes_the_order(self) -> None:
        client_id = _intent(target_version=1).client_order_id
        broker = FakePaperBroker(
            clock=lambda: next(_clock()),
            plans={
                client_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.ACKNOWLEDGE,
                    first_fill=FakeFillStep(
                        quantity=OrderQuantity(10), price=Price.from_cents(10_000)
                    ),
                )
            },
        )
        result = broker.submit_order(_intent(target_version=1))
        assert isinstance(result, SubmitAccepted)
        order = result.order
        assert order.status is BrokerOrderStatus.FILLED
        assert order.filled_quantity == 10
        assert broker.list_open_orders() == ()
        fills = broker.list_fills(order.broker_order_id)
        assert len(fills) == 1
        assert fills[0].execution_id == "fake-exec-000002"
        assert fills[0].quantity.value == 10

    def _filled_broker(self) -> tuple[FakePaperBroker, str]:
        broker = FakePaperBroker(
            clock=lambda: next(_clock()),
            plans={
                "slv1-seven-lens-2026-08-17-open-t1-AAPL-buy": FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.ACKNOWLEDGE,
                    first_fill=FakeFillStep(
                        quantity=OrderQuantity(4), price=Price.from_cents(9_998)
                    ),
                )
            },
        )
        result = broker.submit_order(_intent(target_version=1))
        assert isinstance(result, SubmitAccepted)
        return broker, result.order.broker_order_id

    def test_partial_fills_progress_deterministically(self) -> None:
        broker, broker_order_id = self._filled_broker()
        order = broker.get_order(_intent(target_version=1).client_order_id)
        assert order is not None
        assert order.status is BrokerOrderStatus.PARTIALLY_FILLED
        assert order.filled_quantity == 4
        assert order in broker.list_open_orders()

        fill = broker.apply_fill(
            broker_order_id,
            FakeFillStep(quantity=OrderQuantity(6), price=Price.from_cents(9_999)),
        )
        assert fill.quantity.value == 6
        final = broker.get_order(_intent(target_version=1).client_order_id)
        assert final is not None
        assert final.status is BrokerOrderStatus.FILLED
        assert final.filled_quantity == 10
        assert len(broker.list_fills(broker_order_id)) == 2

    def test_overfill_and_terminal_fills_are_rejected(self) -> None:
        broker, broker_order_id = self._filled_broker()
        with pytest.raises(ValueError, match="exceed the submitted quantity"):
            broker.apply_fill(
                broker_order_id,
                FakeFillStep(quantity=OrderQuantity(7), price=Price.from_cents(9_999)),
            )
        broker.apply_fill(
            broker_order_id, FakeFillStep(quantity=OrderQuantity(6), price=Price.from_cents(9_999))
        )
        with pytest.raises(ValueError, match="terminal broker orders"):
            broker.apply_fill(
                broker_order_id,
                FakeFillStep(quantity=OrderQuantity(1), price=Price.from_cents(9_999)),
            )


class TestRejections:
    def test_deterministic_rejection_is_replayable(self) -> None:
        client_id = _intent(target_version=2).client_order_id
        broker = FakePaperBroker(
            clock=lambda: next(_clock()),
            plans={
                client_id.value: FakeSubmitPlan(
                    outcome=FakeSubmitOutcome.REJECT,
                    rejection_reason=RejectionReason.INSUFFICIENT_CASH,
                )
            },
        )
        result = broker.submit_order(_intent(target_version=2))
        assert result == SubmitRejected(reason=RejectionReason.INSUFFICIENT_CASH)
        again = broker.submit_order(_intent(target_version=2))
        assert again == SubmitRejected(reason=RejectionReason.INSUFFICIENT_CASH)
        assert broker.get_order(client_id) is None
        assert broker.list_open_orders() == ()

    def test_reject_plans_require_a_closed_reason(self) -> None:
        with pytest.raises(ValueError, match="REJECT plans require"):
            FakeSubmitPlan(outcome=FakeSubmitOutcome.REJECT)
        with pytest.raises(ValueError, match="cannot include a fill step"):
            FakeSubmitPlan(
                outcome=FakeSubmitOutcome.REJECT,
                rejection_reason=RejectionReason.SYMBOL_NOT_TRADEABLE,
                first_fill=FakeFillStep(quantity=OrderQuantity(1), price=Price.from_cents(1_000)),
            )
        with pytest.raises(ValueError, match="only REJECT plans"):
            FakeSubmitPlan(
                outcome=FakeSubmitOutcome.ACKNOWLEDGE,
                rejection_reason=RejectionReason.INSUFFICIENT_CASH,
            )


class TestTimeoutFaults:
    def test_timeout_before_accept_leaves_no_order(self) -> None:
        client_id = _intent(target_version=3).client_order_id
        broker = FakePaperBroker(
            clock=lambda: next(_clock()),
            plans={
                client_id.value: FakeSubmitPlan(outcome=FakeSubmitOutcome.TIMEOUT_BEFORE_ACCEPT)
            },
        )
        with pytest.raises(BrokerTransportError, match="before accepting"):
            broker.submit_order(_intent(target_version=3))
        assert broker.get_order(client_id) is None

        # The fault plan is one-shot: resolving via the same id retries cleanly.
        result = broker.submit_order(_intent(target_version=3))
        assert isinstance(result, SubmitAccepted)
        assert result.order.status is BrokerOrderStatus.ACCEPTED

    def test_timeout_after_accept_records_the_order(self) -> None:
        client_id = _intent(target_version=4).client_order_id
        broker = FakePaperBroker(
            clock=lambda: next(_clock()),
            plans={client_id.value: FakeSubmitPlan(outcome=FakeSubmitOutcome.TIMEOUT_AFTER_ACCEPT)},
        )
        with pytest.raises(BrokerTransportError, match="after accepting"):
            broker.submit_order(_intent(target_version=4))
        recorded = broker.get_order(client_id)
        assert recorded is not None
        assert recorded.status is BrokerOrderStatus.RECEIVED

        # Resubmitting the same id returns the recorded order; no duplicate.
        result = broker.submit_order(_intent(target_version=4))
        assert isinstance(result, SubmitAccepted)
        assert result.order.broker_order_id == recorded.broker_order_id


class TestIdempotencyGuards:
    def test_same_id_different_parameters_fails_closed(self) -> None:
        broker = _broker()
        broker.submit_order(_intent(target_version=5))
        heavier = replace(_intent(target_version=5), quantity=OrderQuantity(20))
        with pytest.raises(BrokerConflictError, match="different parameters"):
            broker.submit_order(heavier)

    def test_submit_requires_an_exact_intent(self) -> None:
        with pytest.raises(ValueError, match="exact OrderIntent"):
            _broker().submit_order("not-an-intent")  # type: ignore[arg-type]


class TestCancelAndExpiry:
    def test_cancel_only_open_orders(self) -> None:
        broker = _broker()
        result = broker.submit_order(_intent(target_version=6))
        assert isinstance(result, SubmitAccepted)
        broker_order_id = result.order.broker_order_id
        assert broker.cancel_order(broker_order_id) is True
        canceled = broker.get_order(_intent(target_version=6).client_order_id)
        assert canceled is not None
        assert canceled.status is BrokerOrderStatus.CANCELED
        assert broker.cancel_order(broker_order_id) is False

    def test_expire_only_open_orders(self) -> None:
        broker = _broker()
        result = broker.submit_order(_intent(target_version=7))
        assert isinstance(result, SubmitAccepted)
        broker_order_id = result.order.broker_order_id
        assert broker.expire_order(broker_order_id) is True
        assert broker.expire_order(broker_order_id) is False
        expired = broker.get_order(_intent(target_version=7).client_order_id)
        assert expired is not None
        assert expired.status is BrokerOrderStatus.EXPIRED

    def test_fills_for_unknown_orders_are_empty(self) -> None:
        assert _broker().list_fills("missing") == ()


class TestDeterministicIds:
    def test_sequences_are_stable_across_operations(self) -> None:
        broker = _broker()
        first = broker.submit_order(_intent(target_version=8))
        second = broker.submit_order(_intent(target_version=9))
        assert isinstance(first, SubmitAccepted)
        assert isinstance(second, SubmitAccepted)
        assert first.order.broker_order_id == "fake-order-000001"
        assert second.order.broker_order_id == "fake-order-000002"
