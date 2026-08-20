"""Control-plane service: audited pause, resume, cancel, and flatten levers.

Resume is deliberately hard: it fails closed unless the latest recorded
reconciliation run is CLEAN.  Flatten requires an explicit confirmation and a
prior pause, cancels every open order, and derives its sell intents from the
local fill ledger - never from the broker's position view alone.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Protocol
from uuid import uuid4

from seven_lens.application.execution_service import ExecutionEngine
from seven_lens.application.ports.broker import AssetClass
from seven_lens.application.ports.persistence import OrderRepository, ReconciliationRepository
from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp
from seven_lens.execution.control import (
    ControlCommand,
    ControlCommandRecord,
    ControlStateSnapshot,
)
from seven_lens.execution.ledger import LedgerProjection, project_ledger
from seven_lens.execution.orders import (
    OrderIntent,
    OrderIntentType,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    Price,
    PriceCollar,
    Symbol,
)
from seven_lens.execution.reconciliation import ReconciliationStatus

_FLATTEN_STRATEGY = "control-flatten"
_FLATTEN_WINDOW = "emergency"
_FLATTEN_CONFIRMATION = "FLATTEN_PAPER"
_FLATTEN_COLLAR_BPS = 500
_FLATTEN_CANCEL_HOURS = 4
_UNRESOLVED_FLATTEN_STATUSES = (
    OrderStatus.SUBMITTING,
    OrderStatus.UNKNOWN,
    OrderStatus.ACKNOWLEDGED,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.CANCEL_PENDING,
    OrderStatus.REVIEW_REQUIRED,
)


class ControlPlaneError(RuntimeError):
    """Raised when a control command cannot be applied fail-safely."""


class ResumeBlockedError(ControlPlaneError):
    """Raised when resume is attempted without a CLEAN reconciliation."""


class FlattenPriceProvider(Protocol):
    """Seam for the flatten sell reference price; must not write to the broker."""

    def current_price(self, symbol: Symbol) -> Price: ...


class LedgerFlattenPriceProvider:
    """Default reference price: the last recorded fill price on the local side.

    Deployed without any market data dependency, so a flatten can always be
    priced from durable local state when the broker view is untrusted.
    """

    def __init__(self, unit_of_work: _ControlUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def current_price(self, symbol: Symbol) -> Price:
        projection = project_ledger(
            self._unit_of_work.orders.list_all_fills(),
            {
                order.broker_order_id: order
                for order in self._unit_of_work.orders.list_all_broker_orders()
            },
        )
        for lot in projection.lots:
            if lot.symbol == symbol:
                return lot.price
        raise ControlPlaneError("flatten found no local fill price for a projected position")


class ControlRepositoryLike(Protocol):
    def state(self) -> ControlStateSnapshot: ...

    def set_entries_paused(self, paused: bool, reason: str | None) -> ControlStateSnapshot: ...

    def add_command(self, record: ControlCommandRecord) -> UtcTimestamp | None: ...

    def bump_flatten_generation(self) -> int: ...


class _ControlUnitOfWork(Protocol):
    @property
    def orders(self) -> OrderRepository: ...

    @property
    def reconciliations(self) -> ReconciliationRepository: ...

    @property
    def control(self) -> ControlRepositoryLike: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ControlPlane:
    """Applies operator commands transactionally with an append-only audit log."""

    def __init__(
        self,
        *,
        clock: Callable[[], UtcTimestamp],
        prices: FlattenPriceProvider | None = None,
    ) -> None:
        self._clock = clock
        self._prices = prices

    def state(self, unit_of_work: _ControlUnitOfWork) -> ControlStateSnapshot:
        return unit_of_work.control.state()

    def entries_allowed(self, unit_of_work: _ControlUnitOfWork) -> bool:
        return not unit_of_work.control.state().entries_paused

    def assert_entries_allowed(self, unit_of_work: _ControlUnitOfWork) -> None:
        snapshot = unit_of_work.control.state()
        if snapshot.entries_paused:
            raise ControlPlaneError("entries are paused; resume requires a CLEAN reconciliation")

    def pause_entries(
        self, unit_of_work: _ControlUnitOfWork, *, reason: str, actor: str
    ) -> ControlStateSnapshot:
        """Idempotently stop new entries; risk-reducing actions stay available."""
        snapshot = unit_of_work.control.set_entries_paused(True, reason)
        self._record(unit_of_work, ControlCommand.PAUSE_ENTRIES, reason, actor)
        unit_of_work.commit()
        return snapshot

    def resume_entries(
        self, unit_of_work: _ControlUnitOfWork, *, actor: str
    ) -> ControlStateSnapshot:
        """Fail closed unless latest reconciliation is CLEAN and no unresolved intent remains."""
        latest = unit_of_work.reconciliations.latest()
        if latest is None or latest.status is not ReconciliationStatus.CLEAN:
            raise ResumeBlockedError("resume requires a latest CLEAN reconciliation run")
        # Defense-in-depth: a CLEAN run must not mask durable UNKNOWN/REVIEW_REQUIRED.
        for status in (OrderStatus.UNKNOWN, OrderStatus.REVIEW_REQUIRED):
            if unit_of_work.orders.list_by_status(status):
                raise ResumeBlockedError(
                    f"resume blocked while {status.value} intents remain unresolved"
                )
        snapshot = unit_of_work.control.set_entries_paused(False, None)
        self._record(
            unit_of_work, ControlCommand.RESUME_ENTRIES, "resume after CLEAN reconciliation", actor
        )
        unit_of_work.commit()
        return snapshot

    def cancel_open_orders(
        self,
        unit_of_work: _ControlUnitOfWork,
        *,
        engine: ExecutionEngine,
        reason: str,
        actor: str,
    ) -> tuple[OrderIntent, ...]:
        """Request cancellation of every live acknowledged intent."""
        candidates = tuple(
            intent
            for status in (
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCEL_PENDING,
            )
            for intent in unit_of_work.orders.list_by_status(status)
        )
        canceled: list[OrderIntent] = []
        try:
            for intent in candidates:
                canceled.append(engine.request_cancel(unit_of_work, intent.client_order_id))
        except Exception as error:
            self._record_partial_failure(
                unit_of_work,
                ControlCommand.CANCEL_OPEN_ORDERS,
                reason,
                actor,
                completed=len(canceled),
                total=len(candidates),
                error=error,
                completed_ids=tuple(item.client_order_id.value for item in canceled),
            )
            unit_of_work.commit()
            raise ControlPlaneError(
                "cancel open orders partially failed; inspect the per-order ledger and retry"
            ) from error
        self._record(unit_of_work, ControlCommand.CANCEL_OPEN_ORDERS, reason, actor)
        unit_of_work.commit()
        return tuple(canceled)

    def flatten_paper(
        self,
        unit_of_work: _ControlUnitOfWork,
        *,
        engine: ExecutionEngine,
        trading_date: TradingDate,
        reason: str,
        actor: str,
        confirmation: str,
    ) -> tuple[OrderIntent, ...]:
        """Cancel open orders and sell every ledger position; explicit confirm only.

        The full sequence is: confirm the command, require a pause, resolve any
        SUBMITTING/UNKNOWN intent, request cancellation of every live intent,
        refresh their mirrors to the converged terminal state, reconcile the
        broker's position view against the local fill ledger, and only then
        price and submit SELL intents with a durable flatten generation.  Any
        disagreement with the broker aborts the flatten before a single new
        order is created.
        """
        if confirmation != _FLATTEN_CONFIRMATION:
            raise ControlPlaneError("flatten requires the explicit FLATTEN_PAPER confirmation")
        snapshot = unit_of_work.control.state()
        if not snapshot.entries_paused:
            raise ControlPlaneError("flatten requires entries to be paused first")
        resolution_candidates = tuple(
            intent
            for status in (OrderStatus.SUBMITTING, OrderStatus.UNKNOWN)
            for intent in unit_of_work.orders.list_by_status(status)
        )
        resolved_count = 0
        try:
            for intent in resolution_candidates:
                engine.resolve(unit_of_work, intent.client_order_id)
                resolved_count += 1
        except Exception as error:
            self._record_partial_failure(
                unit_of_work,
                ControlCommand.FLATTEN_PAPER,
                reason,
                actor,
                completed=resolved_count,
                total=len(resolution_candidates),
                error=error,
            )
            unit_of_work.commit()
            raise ControlPlaneError(
                "flatten partially failed while resolving ambiguous orders; entries remain paused"
            ) from error
        cancel_candidates = tuple(
            intent
            for status in (
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCEL_PENDING,
            )
            for intent in unit_of_work.orders.list_by_status(status)
        )
        canceled_count = 0
        canceled_ids: list[str] = []
        try:
            for intent in cancel_candidates:
                engine.request_cancel(unit_of_work, intent.client_order_id)
                canceled_count += 1
                canceled_ids.append(intent.client_order_id.value)
        except Exception as error:
            self._record_partial_failure(
                unit_of_work,
                ControlCommand.FLATTEN_PAPER,
                reason,
                actor,
                completed=canceled_count,
                total=len(cancel_candidates),
                error=error,
                completed_ids=tuple(canceled_ids),
            )
            unit_of_work.commit()
            raise ControlPlaneError(
                "flatten partially failed while canceling open orders; entries remain paused"
            ) from error
        refresh_candidates = tuple(
            intent
            for status in (
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCEL_PENDING,
            )
            for intent in unit_of_work.orders.list_by_status(status)
        )
        refreshed_count = 0
        try:
            for intent in refresh_candidates:
                engine.apply_fills(unit_of_work, intent.client_order_id)
                refreshed_count += 1
        except Exception as error:
            self._record_partial_failure(
                unit_of_work,
                ControlCommand.FLATTEN_PAPER,
                reason,
                actor,
                completed=refreshed_count,
                total=len(refresh_candidates),
                error=error,
            )
            unit_of_work.commit()
            raise ControlPlaneError(
                "flatten partially failed while refreshing canceled orders; entries remain paused"
            ) from error
        unresolved = [
            intent
            for status in _UNRESOLVED_FLATTEN_STATUSES
            for intent in unit_of_work.orders.list_by_status(status)
        ]
        if unresolved:
            unresolved_error = ControlPlaneError(
                "one or more broker orders remain unresolved after cancellation"
            )
            self._record_partial_failure(
                unit_of_work,
                ControlCommand.FLATTEN_PAPER,
                reason,
                actor,
                completed=0,
                total=len(unresolved),
                error=unresolved_error,
            )
            unit_of_work.commit()
            raise ControlPlaneError(f"flatten aborted: {unresolved_error}")
        mirrors = unit_of_work.orders.list_all_broker_orders()
        projection = project_ledger(
            unit_of_work.orders.list_all_fills(),
            {order.broker_order_id: order for order in mirrors},
        )
        self._assert_positions_agree(engine, projection)
        self._assert_flatten_assets_tradable(engine, projection)
        prices = (
            self._prices if self._prices is not None else LedgerFlattenPriceProvider(unit_of_work)
        )
        submitted: list[OrderIntent] = []
        now = self._clock()
        cancel_at = UtcTimestamp(now.value + timedelta(hours=_FLATTEN_CANCEL_HOURS))
        positions = sorted(projection.positions.items(), key=lambda item: item[0].value)
        priced_positions = [
            (
                symbol,
                quantity,
                PriceCollar(reference=prices.current_price(symbol), offset_bps=_FLATTEN_COLLAR_BPS),
            )
            for symbol, quantity in positions
        ]
        generation = unit_of_work.control.bump_flatten_generation()
        try:
            for symbol, quantity, collar in priced_positions:
                intent = OrderIntent.create(
                    strategy=_FLATTEN_STRATEGY,
                    trading_date=trading_date,
                    window=_FLATTEN_WINDOW,
                    target_version=generation,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=OrderQuantity(quantity),
                    intent_type=OrderIntentType.RISK_EXIT,
                    limit_price=collar.lower_limit,
                    collar=collar,
                    earliest_submit_at=now,
                    cancel_at=cancel_at,
                    run_id=RunId.new(),
                    created_at=now,
                )
                unit_of_work.orders.add(intent)
                unit_of_work.orders.transition_status(
                    intent.client_order_id, OrderStatus.RISK_APPROVED
                )
                unit_of_work.orders.transition_status(
                    intent.client_order_id, OrderStatus.OUTBOX_PENDING
                )
                result = engine.submit_from_outbox(unit_of_work, intent.client_order_id)
                if result.status not in (
                    OrderStatus.ACKNOWLEDGED,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.FILLED,
                ):
                    raise ControlPlaneError(
                        "flatten exit was not accepted by the broker "
                        f"for {result.symbol.value}: {result.status.value}"
                    )
                submitted.append(result)
        except Exception as error:
            self._record_partial_failure(
                unit_of_work,
                ControlCommand.FLATTEN_PAPER,
                reason,
                actor,
                completed=len(submitted),
                total=len(positions),
                error=error,
                completed_ids=tuple(item.client_order_id.value for item in submitted),
            )
            unit_of_work.commit()
            raise ControlPlaneError(
                "flatten partially failed while submitting exits; entries remain paused"
            ) from error
        self._record(unit_of_work, ControlCommand.FLATTEN_PAPER, reason, actor)
        unit_of_work.commit()
        return tuple(submitted)

    def _assert_positions_agree(
        self, engine: ExecutionEngine, projection: LedgerProjection
    ) -> None:
        broker_positions = {position.symbol: position for position in engine.list_positions()}
        local_positions = projection.positions
        if broker_positions.keys() != local_positions.keys():
            raise ControlPlaneError(
                "flatten aborted: the broker position view disagrees with the local ledger"
            )
        for symbol, quantity in local_positions.items():
            if broker_positions[symbol].quantity != quantity:
                raise ControlPlaneError(
                    "flatten aborted: the broker position view disagrees with the local ledger"
                )

    def _assert_flatten_assets_tradable(
        self, engine: ExecutionEngine, projection: LedgerProjection
    ) -> None:
        for symbol in projection.positions:
            asset = engine.get_asset(symbol)
            if asset is None or not asset.tradable or asset.asset_class is not AssetClass.US_EQUITY:
                raise ControlPlaneError(
                    "flatten aborted: the broker cannot trade a projected position as US equity "
                    f"{symbol.value}"
                )

    def shutdown_after_reconcile(
        self, unit_of_work: _ControlUnitOfWork, *, reason: str, actor: str
    ) -> None:
        """Record the intent to stop only after the next CLEAN reconciliation."""
        self._record(unit_of_work, ControlCommand.SHUTDOWN_AFTER_RECONCILE, reason, actor)
        unit_of_work.commit()

    def _record(
        self,
        unit_of_work: _ControlUnitOfWork,
        command: ControlCommand,
        reason: str,
        actor: str,
        *,
        applied: bool = True,
    ) -> None:
        now = self._clock()
        unit_of_work.control.add_command(
            ControlCommandRecord(
                command_id=uuid4(),
                command=command,
                reason=reason,
                actor=actor,
                run_id=None,
                requested_at=now,
                applied_at=now if applied else None,
            )
        )

    def _record_partial_failure(
        self,
        unit_of_work: _ControlUnitOfWork,
        command: ControlCommand,
        reason: str,
        actor: str,
        *,
        completed: int,
        total: int,
        error: Exception,
        completed_ids: tuple[str, ...] = (),
    ) -> None:
        ids = f" completed_ids={','.join(completed_ids)}" if completed_ids else ""
        detail = (
            f"PARTIAL_FAILURE {completed}/{total} {type(error).__name__}{ids}; {reason.strip()}"
        )[:200]
        self._record(unit_of_work, command, detail, actor, applied=False)
