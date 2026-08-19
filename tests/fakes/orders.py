"""An in-memory OrderRepository double that mirrors the database guard semantics."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.orders import (
    TERMINAL_BROKER_ORDER_STATUSES,
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    OrderIntent,
    OrderStatus,
    assert_broker_order_transition,
    assert_order_transition,
)


class FakeOrderRepository:
    """Type-exact in-memory persistence with the same closed transition maps."""

    def __init__(self) -> None:
        self._intents: dict[str, OrderIntent] = {}
        self._mirrors: dict[str, BrokerOrder] = {}
        self._mirror_by_client: dict[str, str] = {}
        self._fills: list[Fill] = []
        self._execution_ids: set[str] = set()

    def add(self, intent: OrderIntent) -> OrderIntent:
        existing = self._intents.get(intent.client_order_id.value)
        if existing is None:
            self._intents[intent.client_order_id.value] = intent
            return intent
        if existing.intent_id != intent.intent_id:
            raise ValueError("client order id is bound to a different order intent")
        return existing

    def get(self, client_order_id: ClientOrderId) -> OrderIntent | None:
        return self._intents.get(client_order_id.value)

    def list_by_status(self, status: OrderStatus) -> tuple[OrderIntent, ...]:
        return tuple(
            intent for _, intent in sorted(self._intents.items()) if intent.status is status
        )

    def transition_status(self, client_order_id: ClientOrderId, target: OrderStatus) -> OrderIntent:
        current = self._intents.get(client_order_id.value)
        if current is None:
            raise RuntimeError("order intent disappeared during transition")
        if current.status is not target:
            assert_order_transition(current.status, target)
            current = replace(current, status=target)
            self._intents[client_order_id.value] = current
        return current

    def record_broker_order(self, order: BrokerOrder) -> BrokerOrder:
        existing = self._mirrors.get(order.broker_order_id)
        if existing is not None:
            if (
                existing.client_order_id != order.client_order_id
                or existing.symbol != order.symbol
                or existing.side != order.side
                or existing.quantity != order.quantity
                or existing.limit_price != order.limit_price
                or existing.submitted_at != order.submitted_at
            ):
                raise ValueError("broker order identity fields are immutable")
            if existing.status is not order.status:
                assert_broker_order_transition(existing.status, order.status)
            if order.updated_at.value < existing.updated_at.value:
                raise ValueError("broker_updated_at must never move backwards")
            if order.filled_quantity < existing.filled_quantity:
                raise ValueError("filled_quantity must never move backwards")
            if order.status is BrokerOrderStatus.FILLED and (
                order.filled_quantity != order.quantity.value
            ):
                raise ValueError("a filled mirror must be exactly filled")
            updated = replace(
                existing,
                status=order.status,
                filled_quantity=order.filled_quantity,
                updated_at=order.updated_at,
            )
            self._mirrors[order.broker_order_id] = updated
            return updated
        if (
            order.status is BrokerOrderStatus.FILLED
            and order.filled_quantity != order.quantity.value
        ):
            raise ValueError("a filled mirror must be exactly filled")
        self._mirrors[order.broker_order_id] = order
        self._mirror_by_client[order.client_order_id.value] = order.broker_order_id
        return order

    def get_broker_order(self, client_order_id: ClientOrderId) -> BrokerOrder | None:
        broker_order_id = self._mirror_by_client.get(client_order_id.value)
        if broker_order_id is None:
            return None
        return self._mirrors[broker_order_id]

    def get_broker_order_by_id(self, broker_order_id: str) -> BrokerOrder | None:
        return self._mirrors.get(broker_order_id)

    def update_broker_order_status(
        self,
        broker_order_id: str,
        status: BrokerOrderStatus,
        filled_quantity: int,
        *,
        broker_observed_at: UtcTimestamp | None = None,
    ) -> BrokerOrder:
        existing = self._mirrors.get(broker_order_id)
        if existing is None:
            raise RuntimeError("broker order disappeared during refresh")
        if existing.status is not status:
            assert_broker_order_transition(existing.status, status)
        if filled_quantity < existing.filled_quantity:
            raise ValueError("filled_quantity must never move backwards")
        if status is BrokerOrderStatus.FILLED and filled_quantity != existing.quantity.value:
            raise ValueError("a filled mirror must be exactly filled")
        if broker_observed_at is not None:
            if broker_observed_at.value < existing.updated_at.value:
                raise ValueError("broker_updated_at must never move backwards")
            updated_at = broker_observed_at
        else:
            # Mirror the database guard trigger: every mutation advances the
            # local record clock by one microsecond.
            updated_at = UtcTimestamp(existing.updated_at.value + timedelta(microseconds=1))
        updated = replace(
            existing,
            status=status,
            filled_quantity=filled_quantity,
            updated_at=updated_at,
        )
        self._mirrors[broker_order_id] = updated
        return updated

    def add_fill(self, fill: Fill) -> bool:
        if fill.execution_id in self._execution_ids:
            return False
        self._execution_ids.add(fill.execution_id)
        self._fills.append(fill)
        return True

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]:
        return tuple(fill for fill in self._fills if fill.broker_order_id == broker_order_id)

    def list_open_broker_orders(self) -> tuple[BrokerOrder, ...]:
        return tuple(
            order
            for _, order in sorted(self._mirrors.items())
            if order.status not in TERMINAL_BROKER_ORDER_STATUSES
        )

    def list_all_broker_orders(self) -> tuple[BrokerOrder, ...]:
        return tuple(order for _, order in sorted(self._mirrors.items()))

    def list_all_fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)

    @property
    def fill_count(self) -> int:
        return len(self._fills)
