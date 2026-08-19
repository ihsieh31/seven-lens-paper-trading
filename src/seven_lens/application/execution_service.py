"""The deterministic execution engine that moves intents through the broker.

Safety contract implemented here:

* ``SUBMITTING`` is durably persisted before any broker call, so a crash never
  duplicates an order: recovery resolves by the deterministic client order id.
* A transport timeout moves the intent to ``UNKNOWN``; resolution is always a
  query by client order id.  A fresh submission with the same id happens only
  when the broker proves the id is unknown *and* the cancel window is open.
* Rejected submissions, partial fills, cancels, expiries, and duplicate broker
  events are all idempotent: fills are deduplicated by execution id and mirror
  refreshes are no-ops when nothing changed.
* A broker order whose parameters differ from the intent is a structural
  mismatch: the engine refuses to record it and raises for reconciliation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from seven_lens.application.ports.broker import (
    AssetClass,
    BrokerConflictError,
    BrokerTransportError,
    PaperAsset,
    PaperBrokerPort,
    PaperPosition,
    SubmitAccepted,
    SubmitResult,
)
from seven_lens.application.ports.persistence import OrderRepository
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.control import ControlStateSnapshot
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    OrderIntent,
    OrderIntentType,
    OrderStatus,
    Symbol,
    order_transition_allowed,
)

_LIVE_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.CREATED,
        OrderStatus.RISK_APPROVED,
        OrderStatus.OUTBOX_PENDING,
        OrderStatus.SUBMITTING,
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.UNKNOWN,
        OrderStatus.REVIEW_REQUIRED,
    }
)

_INTENT_STATUS_BY_BROKER_STATUS: dict[BrokerOrderStatus, OrderStatus] = {
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


class ExecutionStateError(RuntimeError):
    """Raised when an operation is invoked against an impossible intent state."""


class BrokerMirrorMismatchError(RuntimeError):
    """Raised when the broker order for our id contradicts the intent parameters."""


class ExecutionPausedError(RuntimeError):
    """Raised when a new entry submission is attempted while entries are paused.

    Pausing only stops *new* exposure: risk exits, cancels, expiries, fills,
    and resolutions stay available while paused.
    """


class _ControlStateSource(Protocol):
    def state(self) -> ControlStateSnapshot: ...

    def submission_guard(self) -> AbstractContextManager[ControlStateSnapshot]: ...


class _OrderUnitOfWork(Protocol):
    @property
    def orders(self) -> OrderRepository: ...

    def commit(self) -> None: ...


class ExecutionEngine:
    """Drives one intent at a time; every broker call is bracketed by durable state."""

    def __init__(
        self,
        *,
        broker: PaperBrokerPort,
        clock: Callable[[], UtcTimestamp],
        control: _ControlStateSource | None = None,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._control = control

    def list_positions(self) -> tuple[PaperPosition, ...]:
        """Read the broker's own position view; never writes."""
        return self._broker.list_positions()

    def get_asset(self, symbol: Symbol) -> PaperAsset | None:
        """Read the broker's own asset view; never writes."""
        return self._broker.get_asset(symbol)

    def submit_from_outbox(
        self, unit_of_work: _OrderUnitOfWork, client_order_id: ClientOrderId
    ) -> OrderIntent:
        """Submit one OUTBOX_PENDING intent; any other state is an idempotent no-op."""
        intent = self._require_intent(unit_of_work, client_order_id)
        if intent.status is not OrderStatus.OUTBOX_PENDING:
            return intent
        self._assert_entries_allowed(intent)
        self._assert_asset_tradable(intent)
        # The asset lookup is a remote call.  A pause may be applied while it is
        # in flight, so re-check immediately before reserving SUBMITTING.
        self._assert_entries_allowed(intent)
        submitting = unit_of_work.orders.transition_status(client_order_id, OrderStatus.SUBMITTING)
        unit_of_work.commit()
        return self._complete_submission(unit_of_work, submitting)

    def _assert_asset_tradable(self, intent: OrderIntent) -> None:
        """Fail closed before any state change when the broker cannot trade the symbol.

        The broker's own asset view is authoritative: an unknown or untradable
        symbol can never be executed, so creating a SUBMITTING in-flight state
        would only leave durable garbage for recovery.  The intent stays
        OUTBOX_PENDING and every retry re-checks the gate for free.
        """
        asset = self._broker.get_asset(intent.symbol)
        if asset is None or not asset.tradable or asset.asset_class is not AssetClass.US_EQUITY:
            raise ExecutionStateError(
                "asset gate: the broker does not trade symbol as a US equity "
                f"{intent.symbol.value}; the intent was not submitted"
            )

    def _assert_entries_allowed(self, intent: OrderIntent) -> None:
        """Gate new entries before any state change or broker call.

        Risk-exit intents are urgent exposure reductions and bypass the gate;
        every other OUTBOX_PENDING intent is blocked while paused.
        """
        if self._control is None or not self._control.state().entries_paused:
            return
        if intent.intent_type is OrderIntentType.RISK_EXIT:
            return
        raise ExecutionPausedError(
            "entries are paused; new intents cannot submit until a CLEAN "
            "reconciliation resumes them"
        )

    def resolve(
        self, unit_of_work: _OrderUnitOfWork, client_order_id: ClientOrderId
    ) -> OrderIntent:
        """Resolve a SUBMITTING or UNKNOWN intent strictly by querying the broker.

        The cancel deadline closes the right to create new exposure; it is not
        proof that the broker holds no order.  A past-deadline query that
        returns nothing leaves the intent UNKNOWN, never EXPIRED: broker
        replication and REST visibility lag can hide an accepted order, and
        only reconciliation or a later recovery may converge it.
        """
        intent = self._require_intent(unit_of_work, client_order_id)
        if intent.status not in (OrderStatus.SUBMITTING, OrderStatus.UNKNOWN):
            return intent
        order = self._broker.get_order(client_order_id)
        if order is None:
            if self._clock().value >= intent.cancel_at.value:
                if intent.status is OrderStatus.SUBMITTING:
                    unknown = unit_of_work.orders.transition_status(
                        client_order_id, OrderStatus.UNKNOWN
                    )
                else:
                    unknown = intent
                unit_of_work.commit()
                return unknown
            return self._complete_submission(unit_of_work, intent)
        self._assert_mirror_matches(intent, order)
        return self._record_accepted(unit_of_work, intent, order)

    def apply_fills(
        self, unit_of_work: _OrderUnitOfWork, client_order_id: ClientOrderId
    ) -> OrderIntent:
        """Idempotently absorb broker fills and status for a live intent."""
        intent = self._require_intent(unit_of_work, client_order_id)
        if intent.status not in (
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_PENDING,
        ):
            return intent
        mirror = unit_of_work.orders.get_broker_order(client_order_id)
        if mirror is None:
            raise ExecutionStateError("intent claims broker acceptance but no local mirror exists")
        order = self._broker.get_order(client_order_id)
        if order is None:
            raise BrokerMirrorMismatchError(
                "broker no longer knows an order we recorded as accepted"
            )
        self._assert_mirror_matches(intent, order)
        return self._refresh_from_broker_order(unit_of_work, intent, order)

    def request_cancel(
        self, unit_of_work: _OrderUnitOfWork, client_order_id: ClientOrderId
    ) -> OrderIntent:
        """Cancel an open intent; safe to retry while it remains CANCEL_PENDING."""
        intent = self._require_intent(unit_of_work, client_order_id)
        if intent.status is OrderStatus.CANCEL_PENDING:
            pass
        elif intent.status in (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED):
            intent = unit_of_work.orders.transition_status(
                client_order_id, OrderStatus.CANCEL_PENDING
            )
            unit_of_work.commit()
        else:
            raise ExecutionStateError(
                "cancel requires an acknowledged, partially filled, or cancel-pending intent"
            )
        mirror = unit_of_work.orders.get_broker_order(client_order_id)
        if mirror is None:
            raise ExecutionStateError("intent claims broker acceptance but no local mirror exists")
        self._broker.cancel_order(mirror.broker_order_id)
        order = self._broker.get_order(client_order_id)
        if order is None:
            raise BrokerMirrorMismatchError(
                "broker no longer knows an order we recorded as accepted"
            )
        self._assert_mirror_matches(intent, order)
        return self._refresh_from_broker_order(unit_of_work, intent, order)

    def expire_overdue(self, unit_of_work: _OrderUnitOfWork) -> tuple[OrderIntent, ...]:
        """Enforce the window cutoff without contradicting the broker.

        Ambiguous states (SUBMITTING/UNKNOWN) are resolved by query first and
        never fabricate a terminal state: a past-deadline order the broker
        accepted is canceled at the broker, and an intent whose broker query
        returns nothing stays UNKNOWN for recovery.  Only intents that never
        reached the broker (CREATED/RISK_APPROVED/OUTBOX_PENDING) expire
        locally.  A canceled order stays CANCEL_PENDING until the broker proves
        CANCELED/EXPIRED/FILLED; transport failures and mirror mismatches
        change nothing locally so reconciliation can arbitrate.
        """
        now = self._clock()
        closed_by_id: dict[str, OrderIntent] = {}
        for status in (OrderStatus.SUBMITTING, OrderStatus.UNKNOWN):
            for intent in list(unit_of_work.orders.list_by_status(status)):
                if intent.cancel_at.value <= now.value:
                    resolved = self.resolve(unit_of_work, intent.client_order_id)
                    closed_by_id[resolved.client_order_id.value] = resolved
        for status in (
            OrderStatus.CREATED,
            OrderStatus.RISK_APPROVED,
            OrderStatus.OUTBOX_PENDING,
        ):
            for intent in unit_of_work.orders.list_by_status(status):
                if intent.cancel_at.value <= now.value:
                    expired = unit_of_work.orders.transition_status(
                        intent.client_order_id, OrderStatus.EXPIRED
                    )
                    closed_by_id[expired.client_order_id.value] = expired
        for status in (
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_PENDING,
        ):
            for intent in unit_of_work.orders.list_by_status(status):
                if intent.cancel_at.value <= now.value:
                    try:
                        result = self.request_cancel(unit_of_work, intent.client_order_id)
                    except BrokerTransportError:
                        # The cancel outcome is unknown; the order may still
                        # fill.  Leave it for recovery and reconciliation.
                        continue
                    except (ExecutionStateError, BrokerMirrorMismatchError):
                        # No local change is safe here: reconciliation must
                        # arbitrate the broker-truth conflict.
                        continue
                    closed_by_id[result.client_order_id.value] = result
        if closed_by_id:
            unit_of_work.commit()
        return tuple(closed_by_id.values())

    def recover(self, unit_of_work: _OrderUnitOfWork) -> tuple[OrderIntent, ...]:
        """After a crash or restart, resolve every SUBMITTING/UNKNOWN intent first.

        Resolution can move a SUBMITTING intent into UNKNOWN (missing broker
        order past the deadline), so the sweep snapshots both statuses up front
        and resolves each client order id exactly once.
        """
        by_id: dict[str, OrderIntent] = {}
        for status in (OrderStatus.SUBMITTING, OrderStatus.UNKNOWN):
            for intent in unit_of_work.orders.list_by_status(status):
                by_id[intent.client_order_id.value] = intent
        resolved: list[OrderIntent] = []
        for intent in by_id.values():
            resolved.append(self.resolve(unit_of_work, intent.client_order_id))
        return tuple(resolved)

    def _complete_submission(
        self, unit_of_work: _OrderUnitOfWork, submitting: OrderIntent
    ) -> OrderIntent:
        try:
            with self._entry_submission_guard(submitting):
                return self._submit_while_guarded(unit_of_work, submitting)
        except ExecutionPausedError:
            # A pause can race with the durable SUBMITTING reservation.  Do not
            # call the broker and do not claim the order is absent: another
            # process or a delayed prior request may still have used this id.
            if submitting.status is OrderStatus.SUBMITTING:
                submitting = unit_of_work.orders.transition_status(
                    submitting.client_order_id, OrderStatus.UNKNOWN
                )
                unit_of_work.commit()
            return submitting

    @contextmanager
    def _entry_submission_guard(self, intent: OrderIntent) -> Iterator[None]:
        """Linearize a new entry before a concurrent pause becomes visible."""
        if self._control is None or intent.intent_type is OrderIntentType.RISK_EXIT:
            yield
            return
        with self._control.submission_guard() as state:
            if state.entries_paused:
                raise ExecutionPausedError(
                    "entries are paused; new intents cannot submit until a CLEAN "
                    "reconciliation resumes them"
                )
            yield

    def _submit_while_guarded(
        self, unit_of_work: _OrderUnitOfWork, submitting: OrderIntent
    ) -> OrderIntent:
        try:
            result: SubmitResult = self._broker.submit_order(submitting)
        except BrokerTransportError:
            unknown = unit_of_work.orders.transition_status(
                submitting.client_order_id, OrderStatus.UNKNOWN
            )
            unit_of_work.commit()
            return unknown
        except BrokerConflictError as error:
            unit_of_work.orders.transition_status(submitting.client_order_id, OrderStatus.UNKNOWN)
            unit_of_work.commit()
            raise BrokerMirrorMismatchError(
                "broker reports a conflicting order for our deterministic client id"
            ) from error
        if type(result) is not SubmitAccepted:
            rejected = unit_of_work.orders.transition_status(
                submitting.client_order_id, OrderStatus.REJECTED
            )
            unit_of_work.commit()
            return rejected
        return self._record_accepted(unit_of_work, submitting, result.order)

    def _record_accepted(
        self, unit_of_work: _OrderUnitOfWork, intent: OrderIntent, order: BrokerOrder
    ) -> OrderIntent:
        self._assert_mirror_matches(intent, order)
        return self._refresh_from_broker_order(unit_of_work, intent, order)

    def _refresh_from_broker_order(
        self, unit_of_work: _OrderUnitOfWork, intent: OrderIntent, order: BrokerOrder
    ) -> OrderIntent:
        unit_of_work.orders.record_broker_order(order)
        for fill in self._broker.list_fills(order.broker_order_id):
            unit_of_work.orders.add_fill(fill)
        refreshed = intent
        target = _INTENT_STATUS_BY_BROKER_STATUS[order.status]
        if target is OrderStatus.ACKNOWLEDGED and intent.status in (
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_PENDING,
        ):
            # The intent never regresses: a pending cancel or an existing partial
            # fill stays put while the broker still reports a plain live order.
            target = intent.status
        if target is not intent.status:
            if not order_transition_allowed(intent.status, target):
                # e.g. a broker order that reached a terminal state our lifecycle
                # cannot represent from here: fail closed with no local change.
                raise ExecutionStateError(
                    "broker order state has no legal intent transition; "
                    "reconciliation must arbitrate"
                )
            refreshed = unit_of_work.orders.transition_status(intent.client_order_id, target)
        unit_of_work.commit()
        return refreshed

    def _require_intent(
        self, unit_of_work: _OrderUnitOfWork, client_order_id: ClientOrderId
    ) -> OrderIntent:
        intent = unit_of_work.orders.get(client_order_id)
        if intent is None:
            raise ExecutionStateError("client order id has no order intent")
        return intent

    def _assert_mirror_matches(self, intent: OrderIntent, order: BrokerOrder) -> None:
        if (
            order.client_order_id != intent.client_order_id
            or order.symbol != intent.symbol
            or order.side != intent.side
            or order.quantity != intent.quantity
            or order.limit_price != intent.limit_price
        ):
            raise BrokerMirrorMismatchError("broker order parameters contradict the order intent")
