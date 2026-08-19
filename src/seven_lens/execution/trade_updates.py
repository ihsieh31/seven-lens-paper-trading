"""Idempotent consumption of broker trade-update events.

The consumer is transport-neutral: a WebSocket bridge (P6/P7 runtime) or a
replayed test stream feeds it typed updates.  Safety rules:

* duplicate fill events are absorbed by the append-only ledger's execution ids;
* out-of-order status updates never regress the intent - a strictly older
  observation is reported STALE and changes nothing;
* an update for an order we never recorded is UNKNOWN_ORDER, never a guess;
* any state the closed lifecycle cannot represent surfaces as a typed error
  for reconciliation, with nothing committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    OrderIntent,
    OrderQuantity,
    OrderStatus,
    Price,
    assert_broker_order_transition,
    order_transition_allowed,
)


class TradeUpdateError(RuntimeError):
    """Raised when an update cannot be applied fail-safely."""


class TradeUpdateOutcome(StrEnum):
    """The closed set of consumer results."""

    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    STALE = "STALE"
    UNKNOWN_ORDER = "UNKNOWN_ORDER"


@dataclass(frozen=True, slots=True)
class OrderStatusUpdate:
    """One broker observation of an order, ordered by the broker timestamp."""

    client_order_id: ClientOrderId
    broker_order_id: str
    status: BrokerOrderStatus
    filled_quantity: int
    observed_at: UtcTimestamp

    def __post_init__(self) -> None:
        if not isinstance(self.client_order_id, ClientOrderId):
            raise ValueError("status update requires a ClientOrderId")
        if (
            type(self.broker_order_id) is not str
            or not self.broker_order_id.strip()
            or len(self.broker_order_id) > 100
        ):
            raise ValueError("status update requires a bounded broker order id")
        if type(self.status) is not BrokerOrderStatus:
            raise ValueError("status update requires a BrokerOrderStatus")
        if type(self.filled_quantity) is not int or self.filled_quantity < 0:
            raise ValueError("status update requires a non-negative filled quantity")
        if not isinstance(self.observed_at, UtcTimestamp):
            raise ValueError("status update requires a UtcTimestamp")


@dataclass(frozen=True, slots=True)
class FillUpdate:
    """One broker execution event; identity is the execution id."""

    fill: Fill

    def __post_init__(self) -> None:
        if not isinstance(self.fill, Fill):
            raise ValueError("fill update requires a Fill")


type TradeUpdate = OrderStatusUpdate | FillUpdate


class _ConsumerOrders(Protocol):
    def get(self, client_order_id: ClientOrderId) -> OrderIntent | None: ...

    def get_broker_order(self, client_order_id: ClientOrderId) -> BrokerOrder | None: ...

    def get_broker_order_by_id(self, broker_order_id: str) -> BrokerOrder | None: ...

    def add_fill(self, fill: Fill) -> bool: ...

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]: ...

    def update_broker_order_status(
        self,
        broker_order_id: str,
        status: BrokerOrderStatus,
        filled_quantity: int,
        *,
        broker_observed_at: UtcTimestamp | None = None,
    ) -> BrokerOrder: ...

    def transition_status(
        self, client_order_id: ClientOrderId, target: OrderStatus
    ) -> OrderIntent: ...


class _ConsumerUnitOfWork(Protocol):
    @property
    def orders(self) -> _ConsumerOrders: ...

    def commit(self) -> None: ...


class TradeUpdateConsumer:
    """Applies one update at a time; every outcome is explicit and auditable."""

    def apply(self, unit_of_work: _ConsumerUnitOfWork, update: TradeUpdate) -> TradeUpdateOutcome:
        if type(update) is FillUpdate:
            return self._apply_fill(unit_of_work, update.fill)
        if type(update) is OrderStatusUpdate:
            return self._apply_status(unit_of_work, update)
        raise TradeUpdateError("unknown trade update type")

    def _apply_fill(self, unit_of_work: _ConsumerUnitOfWork, fill: Fill) -> TradeUpdateOutcome:
        mirror = unit_of_work.orders.get_broker_order_by_id(fill.broker_order_id)
        if mirror is None:
            return TradeUpdateOutcome.UNKNOWN_ORDER
        inserted = unit_of_work.orders.add_fill(fill)
        if not inserted:
            unit_of_work.commit()
            return TradeUpdateOutcome.DUPLICATE
        total = sum(
            item.quantity.value for item in unit_of_work.orders.list_fills(mirror.broker_order_id)
        )
        if total > mirror.quantity.value:
            raise TradeUpdateError("trade update fills exceed the recorded order quantity")
        broker_target = mirror.status
        if mirror.status is not BrokerOrderStatus.FILLED:
            broker_target = (
                BrokerOrderStatus.FILLED
                if total == mirror.quantity.value
                else BrokerOrderStatus.PARTIALLY_FILLED
            )
            assert_broker_order_transition(mirror.status, broker_target)
        unit_of_work.orders.update_broker_order_status(
            mirror.broker_order_id,
            broker_target,
            total,
            broker_observed_at=fill.occurred_at,
        )
        intent = unit_of_work.orders.get(mirror.client_order_id)
        if intent is not None:
            intent_target = (
                OrderStatus.FILLED
                if broker_target is BrokerOrderStatus.FILLED
                else OrderStatus.PARTIALLY_FILLED
            )
            if intent_target is not intent.status:
                if not order_transition_allowed(intent.status, intent_target):
                    raise TradeUpdateError(
                        "fill update has no legal intent transition; reconciliation must arbitrate"
                    )
                unit_of_work.orders.transition_status(mirror.client_order_id, intent_target)
        unit_of_work.commit()
        return TradeUpdateOutcome.APPLIED

    def _apply_status(
        self, unit_of_work: _ConsumerUnitOfWork, update: OrderStatusUpdate
    ) -> TradeUpdateOutcome:
        intent = unit_of_work.orders.get(update.client_order_id)
        if intent is None:
            return TradeUpdateOutcome.UNKNOWN_ORDER
        mirror = unit_of_work.orders.get_broker_order(update.client_order_id)
        if mirror is None or mirror.broker_order_id != update.broker_order_id:
            return TradeUpdateOutcome.UNKNOWN_ORDER
        if update.observed_at.value < mirror.updated_at.value:
            return TradeUpdateOutcome.STALE
        if update.status is mirror.status and update.filled_quantity == mirror.filled_quantity:
            # A replayed observation that changes nothing is a duplicate, not a write.
            return TradeUpdateOutcome.DUPLICATE
        if update.observed_at.value == mirror.updated_at.value:
            raise TradeUpdateError(
                "equal broker timestamp with a conflicting payload; ordering is ambiguous"
            )
        if update.status is not mirror.status:
            assert_broker_order_transition(mirror.status, update.status)
        unit_of_work.orders.update_broker_order_status(
            mirror.broker_order_id,
            update.status,
            update.filled_quantity,
            broker_observed_at=update.observed_at,
        )
        intent_target = _INTENT_STATUS_BY_BROKER[update.status]
        if intent_target is OrderStatus.ACKNOWLEDGED and intent.status in (
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_PENDING,
        ):
            intent_target = intent.status
        if intent_target is not intent.status:
            _transition_intent(unit_of_work, update.client_order_id, intent.status, intent_target)
        unit_of_work.commit()
        return TradeUpdateOutcome.APPLIED


def _transition_intent(
    unit_of_work: _ConsumerUnitOfWork,
    client_order_id: ClientOrderId,
    current: OrderStatus,
    target: OrderStatus,
) -> None:
    """Transition the intent, routing an externally canceled order legally."""
    if not order_transition_allowed(current, target):
        if target is OrderStatus.CANCELED and order_transition_allowed(
            current, OrderStatus.CANCEL_PENDING
        ):
            unit_of_work.orders.transition_status(client_order_id, OrderStatus.CANCEL_PENDING)
            current = OrderStatus.CANCEL_PENDING
        else:
            raise TradeUpdateError(
                "trade update state has no legal intent transition; reconciliation must arbitrate"
            )
    unit_of_work.orders.transition_status(client_order_id, target)


_INTENT_STATUS_BY_BROKER: dict[BrokerOrderStatus, OrderStatus] = {
    BrokerOrderStatus.RECEIVED: OrderStatus.ACKNOWLEDGED,
    BrokerOrderStatus.ACCEPTED: OrderStatus.ACKNOWLEDGED,
    BrokerOrderStatus.ACCEPTED_FOR_BIDDING: OrderStatus.ACKNOWLEDGED,
    BrokerOrderStatus.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
    BrokerOrderStatus.FILLED: OrderStatus.FILLED,
    BrokerOrderStatus.PENDING_CANCEL: OrderStatus.CANCEL_PENDING,
    BrokerOrderStatus.CANCELED: OrderStatus.CANCELED,
    BrokerOrderStatus.EXPIRED: OrderStatus.EXPIRED,
    BrokerOrderStatus.REJECTED: OrderStatus.REJECTED,
    BrokerOrderStatus.DONE_FOR_DAY: OrderStatus.REVIEW_REQUIRED,
    BrokerOrderStatus.REPLACED: OrderStatus.REVIEW_REQUIRED,
    BrokerOrderStatus.PENDING_REPLACE: OrderStatus.REVIEW_REQUIRED,
    BrokerOrderStatus.STOPPED: OrderStatus.REVIEW_REQUIRED,
    BrokerOrderStatus.SUSPENDED: OrderStatus.REVIEW_REQUIRED,
    BrokerOrderStatus.CALCULATED: OrderStatus.REVIEW_REQUIRED,
}


def fill_update(
    *,
    execution_id: str,
    broker_order_id: str,
    quantity: int,
    price_cents: int,
    occurred_at: UtcTimestamp,
) -> FillUpdate:
    """Convenience constructor mirroring the broker's execution event shape."""
    return FillUpdate(
        fill=Fill(
            execution_id=execution_id,
            broker_order_id=broker_order_id,
            quantity=OrderQuantity(quantity),
            price=Price.from_cents(price_cents),
            occurred_at=occurred_at,
        )
    )
