"""Idempotent consumption of broker trade-update events.

The consumer is transport-neutral: a WebSocket bridge (P6/P7 runtime) or a
replayed test stream feeds it typed updates.  Safety rules:

* duplicate fill events are absorbed by the append-only ledger's execution ids;
* out-of-order status updates never regress the intent - a strictly older
  observation is reported STALE and changes nothing;
* an update for an order we never recorded is UNKNOWN_ORDER, never a guess;
* any state the closed lifecycle cannot represent surfaces as a typed error
  for reconciliation; the execution fact (fill) is durable, but derived
  mirror/intent mutations are rolled back on conflict.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.control import ControlCommand, ControlCommandRecord, ControlStateSnapshot
from seven_lens.execution.orders import (
    REVIEW_BROKER_ORDER_STATUSES,
    TERMINAL_BROKER_ORDER_STATUSES,
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    OrderIntent,
    OrderQuantity,
    OrderStatus,
    Price,
    assert_broker_order_transition,
    broker_order_transition_allowed,
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

    @property
    def control(self) -> _ConsumerControl: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class _ConsumerControl(Protocol):
    def set_entries_paused(self, paused: bool, reason: str | None) -> object: ...

    def add_command(self, record: ControlCommandRecord) -> object: ...

    def state(self) -> ControlStateSnapshot: ...


class TradeUpdateConsumer:
    """Applies one update at a time; every outcome is explicit and auditable."""

    def apply(self, unit_of_work: _ConsumerUnitOfWork, update: TradeUpdate) -> TradeUpdateOutcome:
        if type(update) is FillUpdate:
            return self._apply_fill(unit_of_work, update.fill)
        if type(update) is OrderStatusUpdate:
            return self._apply_status(unit_of_work, update)
        raise TradeUpdateError("unknown trade update type")

    def _persist_conflict_pause(self, unit_of_work: _ConsumerUnitOfWork, conflict: str) -> None:
        """Persist entries_paused and an audit command after an unrepresentable update."""
        # Use the same connection as the accepted fact when possible; rollback
        # has already cleared derived state, so this is a fresh transaction.
        try:
            unit_of_work.control.set_entries_paused(True, f"reconciliation required; {conflict}")
        except Exception as exc:
            raise TradeUpdateError(f"failed to persist entries_paused after {conflict}") from exc
        try:
            from uuid import uuid4

            now = unit_of_work.control.state().updated_at
            unit_of_work.control.add_command(
                ControlCommandRecord(
                    command_id=uuid4(),
                    command=ControlCommand.PAUSE_ENTRIES,
                    reason=f"automatic pause on {conflict}",
                    actor="trade_update_consumer",
                    run_id=None,
                    requested_at=now,
                    applied_at=now,
                )
            )
        except TradeUpdateError:
            raise
        except Exception as exc:
            raise TradeUpdateError(f"failed to persist pause audit after {conflict}") from exc
        try:
            unit_of_work.commit()
        except Exception as exc:
            raise TradeUpdateError(f"failed to commit pause after {conflict}") from exc

    def _apply_fill(self, unit_of_work: _ConsumerUnitOfWork, fill: Fill) -> TradeUpdateOutcome:
        mirror = unit_of_work.orders.get_broker_order_by_id(fill.broker_order_id)
        if mirror is None:
            return TradeUpdateOutcome.UNKNOWN_ORDER
        inserted = unit_of_work.orders.add_fill(fill)
        if not inserted:
            unit_of_work.commit()
            return TradeUpdateOutcome.DUPLICATE
        # Make the execution fact durable before attempting derived state.
        # If the commit itself fails, the fill is not durable and we propagate.
        unit_of_work.commit()
        try:
            total = sum(
                item.quantity.value
                for item in unit_of_work.orders.list_fills(mirror.broker_order_id)
            )
            # Never allow the derived filled_quantity to move backwards: broker may have
            # reported a cumulative quantity already larger than our locally summed fills.
            new_filled = mirror.filled_quantity if mirror.filled_quantity > total else total
            if new_filled > mirror.quantity.value:
                raise TradeUpdateError("trade update fills exceed the recorded order quantity")
            # Broker watermark must never move backwards.  A late fill does not regress it.
            if fill.occurred_at.value > mirror.updated_at.value:
                new_observed: UtcTimestamp | None = fill.occurred_at
            else:
                new_observed = None
            # Terminal and review mirrors must never regress due to a late fill.
            if (
                mirror.status in TERMINAL_BROKER_ORDER_STATUSES
                or mirror.status in REVIEW_BROKER_ORDER_STATUSES
            ):
                broker_target = mirror.status
            elif mirror.status is BrokerOrderStatus.PENDING_CANCEL:
                broker_target = BrokerOrderStatus.PENDING_CANCEL
            else:
                if new_filled == mirror.quantity.value:
                    candidate = BrokerOrderStatus.FILLED
                elif new_filled > 0:
                    candidate = BrokerOrderStatus.PARTIALLY_FILLED
                else:
                    candidate = mirror.status
                if candidate is not mirror.status:
                    if not broker_order_transition_allowed(mirror.status, candidate):
                        raise TradeUpdateError(
                            "fill update has no legal broker transition; "
                            "reconciliation must arbitrate"
                        )
                    broker_target = candidate
                else:
                    broker_target = mirror.status
            # If nothing about the mirror would change, the fill is already durable.
            if (
                broker_target is mirror.status
                and new_filled == mirror.filled_quantity
                and new_observed is None
            ):
                return TradeUpdateOutcome.APPLIED
            unit_of_work.orders.update_broker_order_status(
                mirror.broker_order_id,
                broker_target,
                new_filled,
                broker_observed_at=new_observed,
            )
            intent = unit_of_work.orders.get(mirror.client_order_id)
            if intent is not None:
                if broker_target is BrokerOrderStatus.FILLED:
                    intent_target = OrderStatus.FILLED
                elif broker_target is BrokerOrderStatus.PARTIALLY_FILLED:
                    intent_target = OrderStatus.PARTIALLY_FILLED
                elif broker_target is BrokerOrderStatus.PENDING_CANCEL:
                    intent_target = OrderStatus.CANCEL_PENDING
                else:
                    intent_target = intent.status
                if intent_target is not intent.status:
                    if not order_transition_allowed(intent.status, intent_target):
                        raise TradeUpdateError(
                            "fill update has no legal intent transition; "
                            "reconciliation must arbitrate"
                        )
                    unit_of_work.orders.transition_status(mirror.client_order_id, intent_target)
            unit_of_work.commit()
            return TradeUpdateOutcome.APPLIED
        except TradeUpdateError:
            # Roll back only the derived mirror/intent mutations; the fill
            # itself was already committed and must survive.
            with contextlib.suppress(Exception):
                unit_of_work.rollback()
            # Durably pause for reconciliation; an exception is not a durable gate.
            self._persist_conflict_pause(unit_of_work, "conflicting fill")
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                unit_of_work.rollback()
            # Preserve fill fact, persist pause, then surface typed error.
            self._persist_conflict_pause(unit_of_work, "conflicting fill")
            raise TradeUpdateError(
                "trade update cannot be applied fail-safely; reconciliation required"
            ) from exc

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
        try:
            if update.observed_at.value == mirror.updated_at.value:
                raise TradeUpdateError(
                    "equal broker timestamp with a conflicting payload; ordering is ambiguous"
                )
            # Defensive re-check of the database guards at the mutation boundary;
            # the trigger stays the last authority, but a rejected application
            # update must never surface as a raw persistence exception.
            if update.filled_quantity < mirror.filled_quantity:
                raise TradeUpdateError(
                    "status update would regress filled quantity; reconciliation required"
                )
            if update.filled_quantity > mirror.quantity.value:
                raise TradeUpdateError(
                    "status update exceeds the recorded order quantity; reconciliation required"
                )
            if update.status is BrokerOrderStatus.FILLED and (
                update.filled_quantity != mirror.quantity.value
            ):
                raise TradeUpdateError(
                    "a filled status must carry exactly the full order quantity; "
                    "reconciliation required"
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
                _transition_intent(
                    unit_of_work, update.client_order_id, intent.status, intent_target
                )
            unit_of_work.commit()
            return TradeUpdateOutcome.APPLIED
        except TradeUpdateError:
            # Nothing here is an append-only fact: roll the whole attempt back.
            with contextlib.suppress(Exception):
                unit_of_work.rollback()
            # Durably pause for reconciliation; an exception is not a durable gate.
            self._persist_conflict_pause(unit_of_work, "conflicting status")
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                unit_of_work.rollback()
            self._persist_conflict_pause(unit_of_work, "conflicting status")
            raise TradeUpdateError(
                "trade update cannot be applied fail-safely; reconciliation required"
            ) from exc


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
