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
from contextlib import AbstractContextManager, contextmanager, suppress
from typing import Protocol
from uuid import UUID, uuid5

from seven_lens.application.ports.broker import (
    AssetClass,
    AssetStatus,
    BrokerConflictError,
    BrokerTransportError,
    PaperAsset,
    PaperBrokerPort,
    PaperPosition,
    SubmitAccepted,
    SubmitResult,
)
from seven_lens.application.ports.persistence import (
    BrokerOrderIdentityConflictError,
    OrderRepository,
)
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.control import ControlCommand, ControlCommandRecord, ControlStateSnapshot
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    OrderIntent,
    OrderIntentType,
    OrderStatus,
    Symbol,
    order_transition_allowed,
)
from seven_lens.observability.structured_logging import WARNING_LEVEL, log_named_event

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

_EXECUTION_CONFLICT_COMMAND_NAMESPACE = UUID("a4f5d73e-85f1-4dbb-a9c9-3e06f078a10c")


def _execution_conflict_command_id(client_order_id: ClientOrderId, conflict: str) -> UUID:
    """Derive a stable audit identity for one execution conflict."""
    return uuid5(
        _EXECUTION_CONFLICT_COMMAND_NAMESPACE,
        f"{client_order_id.value}\x1f{conflict}",
    )


class ExecutionStateError(RuntimeError):
    """Raised when an operation is invoked against an impossible intent state."""


class BrokerMirrorMismatchError(RuntimeError):
    """Raised when the broker order for our id contradicts the intent parameters."""


class ExecutionPausedError(RuntimeError):
    """Raised when a new entry submission is attempted while entries are paused.

    Pausing only stops *new* exposure: risk exits, cancels, expiries, fills,
    and resolutions stay available while paused.
    """


class ControlPersistenceError(RuntimeError):
    """Raised when the durable pause/audit cannot be persisted."""


class _ControlStateSource(Protocol):
    def state(self) -> ControlStateSnapshot: ...

    def submission_guard(self) -> AbstractContextManager[ControlStateSnapshot]: ...

    def set_entries_paused(self, paused: bool, reason: str | None) -> ControlStateSnapshot: ...

    def add_command(self, record: ControlCommandRecord) -> UtcTimestamp | None: ...


class _OrderUnitOfWork(Protocol):
    @property
    def orders(self) -> OrderRepository: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


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
        if (
            asset is None
            or asset.status is not AssetStatus.ACTIVE
            or not asset.tradable
            or asset.asset_class is not AssetClass.US_EQUITY
        ):
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
        self._assert_mirror_matches(intent, order, unit_of_work=unit_of_work)
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
        self._assert_mirror_matches(intent, order, unit_of_work=unit_of_work)
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
        self._assert_mirror_matches(intent, order, unit_of_work=unit_of_work)
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

        Pre-broker expiry transitions are one transaction sweep: an unexpected
        exception before the final commit leaves them uncommitted so the UoW
        rolls the whole sweep back. Per-intent durability is intentionally not
        promised here.
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
        handled_client_order_ids: set[str] = set()
        for status in (
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_PENDING,
        ):
            for intent in unit_of_work.orders.list_by_status(status):
                if intent.client_order_id.value in handled_client_order_ids:
                    continue
                handled_client_order_ids.add(intent.client_order_id.value)
                if intent.cancel_at.value <= now.value:
                    try:
                        result = self.request_cancel(unit_of_work, intent.client_order_id)
                    except BrokerTransportError:
                        # The cancel outcome is unknown; the order may still
                        # fill.  Leave it for recovery and reconciliation.
                        log_named_event(
                            __name__,
                            "expire_overdue_cancel_transport_failure",
                            level=WARNING_LEVEL,
                            client_order_id=intent.client_order_id.value,
                            status=intent.status.value,
                        )
                        continue
                    except (ExecutionStateError, BrokerMirrorMismatchError):
                        # No local change is safe here: reconciliation must
                        # arbitrate the broker-truth conflict.
                        log_named_event(
                            __name__,
                            "expire_overdue_cancel_reconciliation_required",
                            level=WARNING_LEVEL,
                            client_order_id=intent.client_order_id.value,
                            status=intent.status.value,
                        )
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
            with self._entry_submission_guard(submitting, unit_of_work):
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
                self._pause_for_reconciliation_required(submitting, unit_of_work)
            return submitting

    @contextmanager
    def _entry_submission_guard(
        self, intent: OrderIntent, unit_of_work: _OrderUnitOfWork | None = None
    ) -> Iterator[None]:
        """Linearize a new entry before a concurrent pause becomes visible.

        The guard and the unresolved-intent check must be on the same
        PostgreSQL connection as the order write to avoid cross-connection
        self-deadlock.  When the caller's unit of work exposes a control
        repository (PostgresUnitOfWork), we use that; otherwise we fall back
        to the construction-time control.
        """
        if intent.intent_type is OrderIntentType.RISK_EXIT:
            yield
            return
        # Prefer the caller's control (same DB connection) to avoid A-waits-B / B-waits-A.
        control = None
        if unit_of_work is not None:
            control = getattr(unit_of_work, "control", None)
        if control is None:
            control = self._control
        if control is None:
            yield
            return
        with control.submission_guard() as state:
            if state.entries_paused:
                raise ExecutionPausedError(
                    "entries are paused; new intents cannot submit until a CLEAN "
                    "reconciliation resumes them"
                )
            if unit_of_work is not None and self._has_unresolved_intents(unit_of_work):
                raise ExecutionPausedError(
                    "reconciliation required; unresolved broker truth blocks new entries"
                )
            yield

    def _has_unresolved_intents(self, unit_of_work: _OrderUnitOfWork) -> bool:
        """Return whether any durable UNKNOWN or REVIEW_REQUIRED blocks new exposure."""
        for status in (OrderStatus.UNKNOWN, OrderStatus.REVIEW_REQUIRED):
            if unit_of_work.orders.list_by_status(status):
                return True
        return False

    def _pause_for_reconciliation_required(
        self,
        intent: OrderIntent,
        unit_of_work: _OrderUnitOfWork | None = None,
        *,
        conflict: str = "ambiguous broker outcome",
    ) -> None:
        """Durably block new entries after an ambiguous broker outcome.

        Failures are not swallowed: a persistence failure raises
        ControlPersistenceError so the caller can observe that the operator
        mirror is not durable, even though the primary UNKNOWN gate remains.
        """
        if intent.intent_type is OrderIntentType.RISK_EXIT:
            return
        control = None
        uow_for_commit = None
        if unit_of_work is not None and hasattr(unit_of_work, "control"):
            # Use the same DB connection as the order write.
            control = getattr(unit_of_work, "control", None)
            uow_for_commit = unit_of_work
        if control is None:
            control = self._control
        if control is None:
            return
        try:
            control.set_entries_paused(True, f"reconciliation required; {conflict}")
            if uow_for_commit is not None and hasattr(uow_for_commit, "commit"):
                # UNKNOWN was committed before entering this helper.  Commit
                # the global safety blocker before attempting non-essential audit.
                uow_for_commit.commit()
        except Exception as exc:
            if uow_for_commit is not None and hasattr(uow_for_commit, "rollback"):
                with suppress(Exception):
                    uow_for_commit.rollback()
            raise ControlPersistenceError("failed to persist entries_paused") from exc
        try:
            now = self._clock()
            control.add_command(
                ControlCommandRecord(
                    command_id=_execution_conflict_command_id(intent.client_order_id, conflict),
                    command=ControlCommand.PAUSE_ENTRIES,
                    reason=f"automatic pause on {conflict}",
                    actor="execution_engine",
                    run_id=None,
                    requested_at=now,
                    applied_at=now,
                )
            )
        except Exception as exc:
            if uow_for_commit is not None and hasattr(uow_for_commit, "rollback"):
                with suppress(Exception):
                    uow_for_commit.rollback()
            raise ControlPersistenceError(
                "failed to persist pause audit command; durable pause remains"
            ) from exc
        if uow_for_commit is not None and hasattr(uow_for_commit, "commit"):
            try:
                uow_for_commit.commit()
            except Exception as exc:
                if hasattr(uow_for_commit, "rollback"):
                    with suppress(Exception):
                        uow_for_commit.rollback()
                raise ControlPersistenceError(
                    "failed to commit pause audit; durable pause remains"
                ) from exc

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
            self._pause_for_reconciliation_required(submitting, unit_of_work)
            return unknown
        except BrokerConflictError as error:
            unit_of_work.orders.transition_status(submitting.client_order_id, OrderStatus.UNKNOWN)
            unit_of_work.commit()
            self._pause_for_reconciliation_required(submitting, unit_of_work)
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
        self._assert_mirror_matches(intent, order, unit_of_work=unit_of_work)
        return self._refresh_from_broker_order(unit_of_work, intent, order)

    def _refresh_from_broker_order(
        self, unit_of_work: _OrderUnitOfWork, intent: OrderIntent, order: BrokerOrder
    ) -> OrderIntent:
        fills = tuple(self._broker.list_fills(order.broker_order_id))
        # Check the whole response before mutating the broker mirror.  An equal
        # execution id is idempotent only when every immutable fill field,
        # including broker_order_id, is identical.  Keep the payload in the
        # bounded map: a set alone would hide a conflicting duplicate in this
        # broker response.
        seen_by_execution_id: dict[str, Fill] = {}
        for fill in fills:
            seen = seen_by_execution_id.get(fill.execution_id)
            if seen is not None:
                if seen != fill:
                    self._persist_review_and_pause(unit_of_work, intent, "conflicting fill")
                    raise BrokerMirrorMismatchError(
                        "broker response contains conflicting fills for one execution id; "
                        "reconciliation required"
                    )
                # Exact duplicate events in one broker response have the same
                # idempotent meaning as an exact replay already in storage.
                continue
            seen_by_execution_id[fill.execution_id] = fill
            if fill.broker_order_id != order.broker_order_id:
                self._persist_review_and_pause(unit_of_work, intent, "conflicting fill")
                raise BrokerMirrorMismatchError(
                    "broker fill is bound to a different order; reconciliation required"
                )
            existing = unit_of_work.orders.get_fill_by_execution_id(fill.execution_id)
            if existing is not None and existing != fill:
                self._persist_review_and_pause(unit_of_work, intent, "conflicting fill")
                raise BrokerMirrorMismatchError(
                    "broker fill conflicts with an existing execution id; reconciliation required"
                )
        try:
            unit_of_work.orders.record_broker_order(order)
        except BrokerOrderIdentityConflictError as error:
            # A concurrent process may bind this broker id after the preflight
            # read.  Roll back any transaction-local work, then make the
            # conflict durable before pausing new entries.
            with suppress(Exception):
                unit_of_work.rollback()
            self._persist_review_and_pause(
                unit_of_work,
                intent,
                "broker order identity conflict",
            )
            raise BrokerMirrorMismatchError(
                "broker order identity is already bound to another local order; "
                "reconciliation required"
            ) from error
        for fill in fills:
            inserted = unit_of_work.orders.add_fill(fill)
            if not inserted:
                existing = unit_of_work.orders.get_fill_by_execution_id(fill.execution_id)
                if existing != fill:
                    self._persist_review_and_pause(unit_of_work, intent, "conflicting fill")
                    raise BrokerMirrorMismatchError(
                        "broker fill conflicts with an existing execution id; "
                        "reconciliation required"
                    )
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

    def _persist_review_and_pause(
        self, unit_of_work: _OrderUnitOfWork, intent: OrderIntent, conflict: str
    ) -> OrderIntent:
        """Persist an unresolved marker before the shared entry pause.

        The marker is committed first so a pause or audit failure cannot leave
        the intent in a submit-capable state.  The broker observation itself is
        never overwritten; reconciliation remains the authority for resolving
        the conflict.
        """
        current = unit_of_work.orders.get(intent.client_order_id)
        if current is None:
            raise ControlPersistenceError(
                "cannot persist unresolved broker conflict: order intent is missing"
            )
        if current.status is not OrderStatus.REVIEW_REQUIRED:
            try:
                current = unit_of_work.orders.transition_status(
                    intent.client_order_id, OrderStatus.REVIEW_REQUIRED
                )
                unit_of_work.commit()
            except Exception as exc:
                if hasattr(unit_of_work, "rollback"):
                    with suppress(Exception):
                        unit_of_work.rollback()
                raise ControlPersistenceError(
                    "failed to persist REVIEW_REQUIRED after broker conflict"
                ) from exc
        self._pause_for_reconciliation_required(
            current,
            unit_of_work,
            conflict=conflict,
        )
        return current

    def _assert_mirror_matches(
        self,
        intent: OrderIntent,
        order: BrokerOrder,
        *,
        unit_of_work: _OrderUnitOfWork | None = None,
    ) -> None:
        mismatch_fields: list[str] = []
        if order.client_order_id != intent.client_order_id:
            mismatch_fields.append("client_order_id")
        if order.symbol != intent.symbol:
            mismatch_fields.append("symbol")
        if order.side != intent.side:
            mismatch_fields.append("side")
        if order.quantity != intent.quantity:
            mismatch_fields.append("quantity")
        if order.limit_price != intent.limit_price:
            mismatch_fields.append("limit_price")
        if unit_of_work is not None:
            local_mirror = unit_of_work.orders.get_broker_order(intent.client_order_id)
            if local_mirror is not None and local_mirror.broker_order_id != order.broker_order_id:
                mismatch_fields.append("broker_order_id")
            local_by_broker_id = unit_of_work.orders.get_broker_order_by_id(order.broker_order_id)
            if (
                local_by_broker_id is not None
                and local_by_broker_id.client_order_id != intent.client_order_id
                and "broker_order_id" not in mismatch_fields
            ):
                mismatch_fields.append("broker_order_id")
        if not mismatch_fields:
            return
        if unit_of_work is not None:
            self._persist_review_and_pause(unit_of_work, intent, "broker mirror mismatch")
        raise BrokerMirrorMismatchError(
            "broker order parameters contradict the order intent; "
            f"mismatch_fields={','.join(mismatch_fields)}"
        )
