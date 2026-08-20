"""Broker-versus-ledger reconciliation with fail-closed mismatch reporting.

The reconciler never repairs state and never places orders.  It compares the
broker's own account, order, fill, and position views against the local
authoritative tables and returns the result; persistence of the run and any
pause decision belong to the caller's transaction and control plane.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Protocol
from uuid import uuid4

from seven_lens.application.ports.broker import BrokerTransportError, PaperBrokerPort
from seven_lens.application.ports.persistence import (
    ControlRepository,
    OrderRepository,
    ReconciliationRepository,
)
from seven_lens.config.broker import BrokerEnvironment
from seven_lens.domain.value_objects import TradingDate, UtcTimestamp
from seven_lens.execution.control import ControlCommand, ControlCommandRecord
from seven_lens.execution.ledger import LedgerInvariantError, account_valuation, project_ledger
from seven_lens.execution.orders import BrokerOrder, OrderStatus, Price, Symbol
from seven_lens.execution.reconciliation import (
    MismatchKind,
    ReconciliationMismatch,
    ReconciliationResult,
    ReconciliationStatus,
)


class AccountReconciliationPolicy:
    """Closed policy for expected account identity and tolerances."""

    def __init__(
        self,
        *,
        expected_account_id: str,
        cash_tolerance_cents: int = 100,
        nav_tolerance_cents: int = 100,
    ) -> None:
        if (
            type(expected_account_id) is not str
            or not expected_account_id.strip()
            or len(expected_account_id) > 100
        ):
            raise ValueError("expected_account_id must be non-empty text up to 100 characters")
        if type(cash_tolerance_cents) is not int or cash_tolerance_cents < 0:
            raise ValueError("cash_tolerance_cents must be a non-negative integer")
        if type(nav_tolerance_cents) is not int or nav_tolerance_cents < 0:
            raise ValueError("nav_tolerance_cents must be a non-negative integer")
        self.expected_account_id = expected_account_id
        self.cash_tolerance_cents = cash_tolerance_cents
        self.nav_tolerance_cents = nav_tolerance_cents


class ReconciliationMarkPriceProvider(Protocol):
    """Seam for NAV mark prices; implementations must not call the broker."""

    def current_price(self, symbol: Symbol) -> Price: ...


class _AccountBaselineRepository(Protocol):
    def get_baseline(self, account_id: str) -> object | None: ...

    def set_baseline(
        self, account_id: str, opening_cash_cents: int, effective_at: UtcTimestamp
    ) -> object: ...


class _ReconciliationUnitOfWork(Protocol):
    @property
    def orders(self) -> OrderRepository: ...

    @property
    def reconciliations(self) -> ReconciliationRepository: ...

    @property
    def control(self) -> ControlRepository: ...

    def commit(self) -> None: ...


_TERMINAL_INTENT_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
        OrderStatus.REJECTED,
    }
)


class Reconciler:
    """Compare the broker's views with local authority; report, never repair."""

    def __init__(
        self,
        *,
        broker: PaperBrokerPort,
        clock: Callable[[], UtcTimestamp],
        account_policy: AccountReconciliationPolicy | None = None,
        price_provider: ReconciliationMarkPriceProvider | None = None,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._account_policy = account_policy
        self._price_provider = price_provider

    def run(
        self, unit_of_work: _ReconciliationUnitOfWork, trading_date: TradingDate
    ) -> ReconciliationResult:
        """Collect, persist the run, and auto-pause entries on any mismatch."""
        try:
            result = self.collect(unit_of_work, trading_date)
        except BrokerTransportError:
            # A reconciliation that cannot observe broker truth is itself
            # durable mismatch evidence.  Never let a transport failure leave
            # entries enabled or disappear without an append-only run.
            result = ReconciliationResult.create(
                trading_date=trading_date,
                mismatches=(
                    ReconciliationMismatch(
                        kind=MismatchKind.BROKER_QUERY_FAILURE,
                        detail="broker query failed during reconciliation",
                    ),
                ),
                checked_orders=0,
                checked_fills=0,
                observed_at=self._clock(),
            )
        except LedgerInvariantError as error:
            detail = str(error)[:200] if str(error).strip() else "ledger invariant failure"
            # Bound detail without leaking secrets.
            detail = detail.replace("\x00", "")[:200].strip() or "ledger invariant failure"
            result = ReconciliationResult.create(
                trading_date=trading_date,
                mismatches=(
                    ReconciliationMismatch(
                        kind=MismatchKind.LOCAL_LEDGER_INVARIANT,
                        detail=detail,
                    ),
                ),
                checked_orders=0,
                checked_fills=0,
                observed_at=self._clock(),
            )
        unit_of_work.reconciliations.add(result)
        if result.status is ReconciliationStatus.MISMATCH:
            unit_of_work.control.set_entries_paused(True, "reconciliation mismatch")
            now = self._clock()
            unit_of_work.control.add_command(
                ControlCommandRecord(
                    command_id=uuid4(),
                    command=ControlCommand.PAUSE_ENTRIES,
                    reason="automatic pause on reconciliation mismatch",
                    actor="reconciler",
                    run_id=None,
                    requested_at=now,
                    applied_at=now,
                )
            )
        unit_of_work.commit()
        return result

    def _history_horizon(
        self, unit_of_work: _ReconciliationUnitOfWork, trading_date: TradingDate
    ) -> UtcTimestamp:
        """The earliest broker update time this run must re-examine.

        Every order updated after the previous run's observed_at must be
        compared; with no previous evidence, the trading date's start is the
        wall of the record.
        """
        latest = unit_of_work.reconciliations.latest()
        if latest is not None:
            return latest.observed_at
        return UtcTimestamp(datetime.combine(trading_date.value, time.min, tzinfo=UTC))

    def collect(
        self, unit_of_work: _ReconciliationUnitOfWork, trading_date: TradingDate
    ) -> ReconciliationResult:
        """Build the reconciliation result for this trading date."""
        mismatches: list[ReconciliationMismatch] = []
        account = self._broker.account()
        if account.environment is not BrokerEnvironment.PAPER:
            mismatches.append(
                ReconciliationMismatch(
                    kind=MismatchKind.NON_PAPER_ACCOUNT,
                    detail="broker account does not assert PAPER",
                )
            )
        # Account identity, buying power, and cash/NAV are checked later after
        # ledger projection, but a missing PAPER environment is already a mismatch.
        checked_orders = 0
        checked_fills = 0
        orders = unit_of_work.orders
        # REVIEW_REQUIRED is deliberately non-terminal in an operational sense:
        # automation cannot prove what the broker status means.  A later clean
        # run must never silently clear the pause while such an intent exists.
        for review_intent in orders.list_by_status(OrderStatus.REVIEW_REQUIRED):
            mismatches.append(
                ReconciliationMismatch(
                    kind=MismatchKind.INTENT_STATUS_MISMATCH,
                    detail=review_intent.client_order_id.value,
                )
            )
        # UNKNOWN is also an unresolved broker truth: a CLEAN reconciliation must
        # never silently clear the pause while such an intent exists.
        for unknown_intent in orders.list_by_status(OrderStatus.UNKNOWN):
            mismatches.append(
                ReconciliationMismatch(
                    kind=MismatchKind.INTENT_STATUS_MISMATCH,
                    detail=unknown_intent.client_order_id.value,
                )
            )
        local_mirrors = orders.list_open_broker_orders()
        for mirror in local_mirrors:
            checked_orders += 1
            broker_order = self._broker.get_order(mirror.client_order_id)
            if broker_order is None:
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.MISSING_BROKER_ORDER,
                        detail=mirror.broker_order_id,
                    )
                )
                continue
            intent = orders.get(mirror.client_order_id)
            if intent is not None and (
                broker_order.symbol != intent.symbol
                or broker_order.side != intent.side
                or broker_order.quantity != intent.quantity
                or broker_order.limit_price != intent.limit_price
            ):
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.PARAMETER_MISMATCH,
                        detail=mirror.broker_order_id,
                    )
                )
            if (
                intent is not None
                and intent.status in _TERMINAL_INTENT_STATUSES
                and broker_order.is_open
            ):
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.INTENT_STATUS_MISMATCH,
                        detail=mirror.broker_order_id,
                    )
                )
            if broker_order.status is not mirror.status:
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.STATUS_MISMATCH,
                        detail=mirror.broker_order_id,
                    )
                )
            local_fill_ids = {
                fill.execution_id for fill in orders.list_fills(mirror.broker_order_id)
            }
            for fill in self._broker.list_fills(mirror.broker_order_id):
                checked_fills += 1
                if fill.execution_id not in local_fill_ids:
                    mismatches.append(
                        ReconciliationMismatch(
                            kind=MismatchKind.MISSING_LOCAL_FILL,
                            detail=fill.execution_id,
                        )
                    )
        checked_mirror_ids = {mirror.broker_order_id for mirror in local_mirrors}
        for mirror in orders.list_all_broker_orders():
            if mirror.broker_order_id in checked_mirror_ids:
                continue
            broker_order = self._broker.get_order(mirror.client_order_id)
            if broker_order is None or not broker_order.is_open:
                continue
            # A terminal local mirror the broker still holds open is drift the
            # open-mirror pass can never see; report it explicitly.
            checked_orders += 1
            mismatches.append(
                ReconciliationMismatch(
                    kind=MismatchKind.STATUS_MISMATCH,
                    detail=mirror.broker_order_id,
                )
            )
        known_client_ids = {mirror.client_order_id for mirror in orders.list_all_broker_orders()}
        reported_orders: dict[str, BrokerOrder] = {}
        for broker_order in self._broker.list_open_orders():
            checked_orders += 1
            reported_orders[broker_order.broker_order_id] = broker_order
            if broker_order.client_order_id not in known_client_ids:
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.UNKNOWN_BROKER_ORDER,
                        detail=broker_order.broker_order_id,
                    )
                )
        # Closed-history pass: broker-terminal orders updated inside the horizon
        # are real evidence even though the open-orders list no longer shows them.
        # An order the broker closed that we never recorded is drift only the
        # terminal history can surface.
        horizon = self._history_horizon(unit_of_work, trading_date)
        for broker_order in self._broker.list_recent_orders(since=horizon):
            open_snapshot = reported_orders.get(broker_order.broker_order_id)
            if open_snapshot is not None and (
                broker_order.updated_at.value < open_snapshot.updated_at.value
                or (
                    broker_order.updated_at == open_snapshot.updated_at
                    and broker_order.status is open_snapshot.status
                )
            ):
                continue
            if broker_order.is_open:
                continue
            reported_orders[broker_order.broker_order_id] = broker_order
            checked_orders += 1
            closed_mirror = orders.get_broker_order(broker_order.client_order_id)
            if closed_mirror is None:
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.UNKNOWN_BROKER_ORDER,
                        detail=broker_order.broker_order_id,
                    )
                )
                continue
            if broker_order.status is not closed_mirror.status:
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.STATUS_MISMATCH,
                        detail=closed_mirror.broker_order_id,
                    )
                )
            local_fill_ids = {
                fill.execution_id for fill in orders.list_fills(closed_mirror.broker_order_id)
            }
            for fill in self._broker.list_fills(broker_order.broker_order_id):
                checked_fills += 1
                if fill.execution_id not in local_fill_ids:
                    mismatches.append(
                        ReconciliationMismatch(
                            kind=MismatchKind.MISSING_LOCAL_FILL,
                            detail=fill.execution_id,
                        )
                    )
        projection = project_ledger(
            orders.list_all_fills(),
            {order.broker_order_id: order for order in orders.list_all_broker_orders()},
        )
        projected = projection.positions
        broker_positions = {
            position.symbol: position.quantity for position in self._broker.list_positions()
        }
        for symbol in sorted(set(projected) | set(broker_positions), key=lambda item: item.value):
            if (symbol in projected) != (symbol in broker_positions):
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.POSITION_SYMBOL_MISMATCH,
                        detail=symbol.value,
                    )
                )
            elif projected[symbol] != broker_positions[symbol]:
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.POSITION_QUANTITY_MISMATCH,
                        detail=symbol.value,
                    )
                )
        # Account reconciliation: identity, cash, NAV, buying power.
        if self._account_policy is not None:
            if account.account_id != self._account_policy.expected_account_id:
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.ACCOUNT_ID_MISMATCH,
                        detail=account.account_id[:100],
                    )
                )
            # Buying power is typed and strictly parsed; a missing or malformed
            # value would have already raised BrokerTransportError and become
            # BROKER_QUERY_FAILURE.  Here we ensure it is at least present and
            # non-negative; a future ADR will define the portable expected-BP formula.
            try:
                buying_power_cents = account.buying_power.cents
                if buying_power_cents < 0:
                    mismatches.append(
                        ReconciliationMismatch(
                            kind=MismatchKind.BUYING_POWER_MISMATCH,
                            detail=str(buying_power_cents)[:200],
                        )
                    )
            except (ValueError, AttributeError, TypeError):
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE,
                        detail="buying power unavailable",
                    )
                )
            baseline = None
            baseline_error = False
            try:
                baseline_repo = getattr(unit_of_work, "account_baselines", None)
                if baseline_repo is not None:
                    baseline = baseline_repo.get_baseline(account.account_id)
            except (ValueError, TypeError, AttributeError):
                baseline_error = True
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE,
                        detail="baseline query failed",
                    )
                )
            if not baseline_error and baseline is None:
                mismatches.append(
                    ReconciliationMismatch(
                        kind=MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE,
                        detail="missing opening cash baseline",
                    )
                )
            elif baseline is not None:
                # Ledger cutoff: only post-baseline fills count toward expected cash/NAV.
                # For genesis (no cutoff) all fills are included.
                try:
                    cutoff_at = getattr(baseline, "cutoff_occurred_at", None)
                    cutoff_id = getattr(baseline, "cutoff_execution_id", None)
                    all_fills = orders.list_all_fills()
                    if cutoff_at is not None and cutoff_id is not None:
                        post_fills = tuple(
                            f
                            for f in all_fills
                            if f.occurred_at.value > cutoff_at.value
                            or (
                                f.occurred_at.value == cutoff_at.value
                                and f.execution_id > cutoff_id
                            )
                        )
                    elif cutoff_at is not None:
                        post_fills = tuple(
                            f for f in all_fills if f.occurred_at.value > cutoff_at.value
                        )
                    else:
                        # Fallback to effective_at if no explicit cutoff (old baselines)
                        effective_at = getattr(baseline, "effective_at", None)
                        if effective_at is not None:
                            post_fills = tuple(
                                f for f in all_fills if f.occurred_at.value >= effective_at.value
                            )
                        else:
                            post_fills = all_fills
                    broker_orders_map = {
                        o.broker_order_id: o for o in orders.list_all_broker_orders()
                    }
                    post_projection = project_ledger(post_fills, broker_orders_map)
                except (ValueError, TypeError, AttributeError) as exc:
                    mismatches.append(
                        ReconciliationMismatch(
                            kind=MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE,
                            detail=str(exc)[:200] if str(exc).strip() else "cutoff unavailable",
                        )
                    )
                    post_projection = projection
                try:
                    opening_cash_cents = int(baseline.opening_cash_cents)
                    expected_cash = opening_cash_cents + post_projection.cash_delta_cents
                    broker_cash = account.cash.cents
                    if abs(broker_cash - expected_cash) > self._account_policy.cash_tolerance_cents:
                        mismatches.append(
                            ReconciliationMismatch(
                                kind=MismatchKind.CASH_MISMATCH,
                                detail=f"expected {expected_cash} got {broker_cash}",
                            )
                        )
                except (ValueError, TypeError, AttributeError):
                    mismatches.append(
                        ReconciliationMismatch(
                            kind=MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE,
                            detail="cash reconciliation unavailable",
                        )
                    )
                # NAV reconciliation requires mark prices for every open lot.
                try:
                    if self._price_provider is None:
                        if post_projection.positions:
                            mismatches.append(
                                ReconciliationMismatch(
                                    kind=MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE,
                                    detail="missing mark price provider",
                                )
                            )
                        else:
                            # No positions: NAV is just expected cash; compare to equity.
                            opening_cash_cents = int(baseline.opening_cash_cents)
                            expected_nav = opening_cash_cents + post_projection.cash_delta_cents
                            broker_equity = account.equity.cents
                            if (
                                abs(broker_equity - expected_nav)
                                > self._account_policy.nav_tolerance_cents
                            ):
                                mismatches.append(
                                    ReconciliationMismatch(
                                        kind=MismatchKind.NAV_MISMATCH,
                                        detail=f"expected {expected_nav} got {broker_equity}",
                                    )
                                )
                    else:
                        prices: dict[Symbol, Price] = {}
                        for sym in post_projection.positions:
                            price = self._price_provider.current_price(sym)
                            if not isinstance(price, Price):
                                raise ValueError(f"missing price for {sym.value}")
                            prices[sym] = price
                        opening_cash_cents = int(baseline.opening_cash_cents)
                        nav = account_valuation(
                            post_projection,
                            opening_cash_cents=opening_cash_cents,
                            prices=prices,
                        )
                        broker_equity = account.equity.cents
                        if abs(broker_equity - nav) > self._account_policy.nav_tolerance_cents:
                            mismatches.append(
                                ReconciliationMismatch(
                                    kind=MismatchKind.NAV_MISMATCH,
                                    detail=f"expected {nav} got {broker_equity}",
                                )
                            )
                except ValueError as ve:
                    # Missing price or valuation input is explicitly unavailable, not CLEAN.
                    detail = str(ve)[:200] if str(ve).strip() else "valuation input missing"
                    mismatches.append(
                        ReconciliationMismatch(
                            kind=MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE,
                            detail=detail,
                        )
                    )
                except (TypeError, AttributeError):
                    mismatches.append(
                        ReconciliationMismatch(
                            kind=MismatchKind.ACCOUNT_RECONCILIATION_UNAVAILABLE,
                            detail="nav reconciliation unavailable",
                        )
                    )
        return ReconciliationResult.create(
            trading_date=trading_date,
            mismatches=tuple(mismatches),
            checked_orders=checked_orders,
            checked_fills=checked_fills,
            observed_at=self._clock(),
        )
