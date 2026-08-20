"""A deterministic in-memory Paper broker used by tests and fault injection.

The fake is the P2 acceptance surface for execution safety: every submit
outcome - acknowledgement, deterministic rejection, timeout before broker
accept, and timeout after broker accept - is scripted per deterministic client
order id.  Fault plans are one-shot: they are consumed by the first submit
attempt so a retry after a timeout-before-accept follows the default plan,
which keeps harness scenarios deterministic.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum

from seven_lens.application.ports.broker import (
    AssetClass,
    AssetStatus,
    BrokerConflictError,
    BrokerTransportError,
    PaperAccount,
    PaperAsset,
    PaperPosition,
    RejectionReason,
    SubmitAccepted,
    SubmitRejected,
    SubmitResult,
)
from seven_lens.config.broker import BrokerEnvironment
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.orders import (
    BrokerOrder,
    BrokerOrderStatus,
    ClientOrderId,
    Fill,
    OrderIntent,
    OrderQuantity,
    OrderSide,
    Price,
    Symbol,
    UsdAmount,
    assert_broker_order_transition,
)

ExactClock = Callable[[], UtcTimestamp]
SubmitParams = tuple[str, str, int, Decimal]


class FakeSubmitOutcome(StrEnum):
    """The closed set of scripted submit outcomes."""

    ACKNOWLEDGE = "ACKNOWLEDGE"
    REJECT = "REJECT"
    TIMEOUT_BEFORE_ACCEPT = "TIMEOUT_BEFORE_ACCEPT"
    TIMEOUT_AFTER_ACCEPT = "TIMEOUT_AFTER_ACCEPT"


class FakeCancelMode(StrEnum):
    """How the fake broker handles a cancel request.

    Real brokers cancel asynchronously: a DELETE can succeed while the order
    still works or fills.  The fake must be able to reproduce that instead of
    always answering with an instant terminal CANCELED.
    """

    IMMEDIATE = "IMMEDIATE"
    PENDING = "PENDING"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True, slots=True)
class FakeFillStep:
    """One scripted execution against a submitted order."""

    quantity: OrderQuantity
    price: Price


@dataclass(frozen=True, slots=True)
class FakeSubmitPlan:
    """The scripted behavior for one deterministic client order id."""

    outcome: FakeSubmitOutcome
    first_fill: FakeFillStep | None = None
    rejection_reason: RejectionReason | None = None

    def __post_init__(self) -> None:
        if type(self.outcome) is not FakeSubmitOutcome:
            raise ValueError("fake submit outcome must be a FakeSubmitOutcome")
        if self.outcome is FakeSubmitOutcome.REJECT:
            if type(self.rejection_reason) is not RejectionReason:
                raise ValueError("REJECT plans require a closed RejectionReason")
            if self.first_fill is not None:
                raise ValueError("REJECT plans cannot include a fill step")
        elif self.rejection_reason is not None:
            raise ValueError("only REJECT plans carry a rejection reason")


@dataclass(slots=True)
class _OrderState:
    order: BrokerOrder
    fills: list[Fill] = field(default_factory=list)


class FakePaperBroker:
    """Deterministic Paper broker implementing ``PaperBrokerPort``."""

    def __init__(
        self,
        *,
        clock: ExactClock,
        plans: Mapping[str, FakeSubmitPlan] | None = None,
        default_plan: FakeSubmitPlan | None = None,
        cash: UsdAmount | None = None,
        equity: UsdAmount | None = None,
        buying_power: UsdAmount | None = None,
        account_id: str = "fake-paper-primary",
        cash_mismatch: bool = False,
        equity_mismatch: bool = False,
        buying_power_mismatch: bool = False,
        cancel_mode: FakeCancelMode = FakeCancelMode.IMMEDIATE,
        hidden_client_ids: set[str] | None = None,
        assets: Mapping[str, PaperAsset] | None = None,
        unknown_assets: set[str] | None = None,
    ) -> None:
        self._clock = clock
        self._plans: dict[str, FakeSubmitPlan] = dict(plans or {})
        self._default_plan = default_plan or FakeSubmitPlan(outcome=FakeSubmitOutcome.ACKNOWLEDGE)
        self._cash = cash if cash is not None else UsdAmount.from_cents(100_000_000)
        self._equity = equity if equity is not None else UsdAmount.from_cents(100_000_000)
        self._buying_power = (
            buying_power if buying_power is not None else UsdAmount.from_cents(100_000_000)
        )
        self._account_id = account_id
        self._cash_mismatch = cash_mismatch
        self._equity_mismatch = equity_mismatch
        self._buying_power_mismatch = buying_power_mismatch
        self._cancel_mode = cancel_mode
        self._hidden_client_ids: set[str] = set(hidden_client_ids or ())
        self._assets: dict[str, PaperAsset] = dict(assets or {})
        self._unknown_assets: set[str] = set(unknown_assets or ())
        self._orders: dict[str, _OrderState] = {}
        self._by_client: dict[str, str] = {}
        self._submit_params: dict[str, SubmitParams] = {}
        self._results: dict[str, SubmitResult] = {}
        self._sequence = 0

    def account(self) -> PaperAccount:
        return PaperAccount(
            account_id=self._account_id,
            environment=BrokerEnvironment.PAPER,
            cash=self._cash,
            equity=self._equity,
            buying_power=self._buying_power,
        )

    def submit_order(self, intent: OrderIntent) -> SubmitResult:
        if type(intent) is not OrderIntent:
            raise ValueError("submit requires an exact OrderIntent")
        client_id = intent.client_order_id.value
        params: SubmitParams = (
            intent.symbol.value,
            intent.side.value,
            intent.quantity.value,
            intent.limit_price.value,
        )
        previous = self._submit_params.get(client_id)
        if previous is not None:
            if previous != params:
                raise BrokerConflictError(
                    "client order id was already submitted with different parameters"
                )
            result = self._results.get(client_id)
            if isinstance(result, SubmitRejected):
                return result
            broker_order_id = self._by_client.get(client_id)
            if broker_order_id is None:
                raise BrokerTransportError(
                    "fake broker submit outcome remains unknown for this client order id"
                )
            return SubmitAccepted(order=self._orders[broker_order_id].order)

        plan = self._plans.pop(client_id, self._default_plan)
        if plan.outcome is FakeSubmitOutcome.TIMEOUT_BEFORE_ACCEPT:
            # The submit never reached the broker: no parameters are recorded,
            # so a same-id retry is a genuinely fresh broker-side attempt.
            raise BrokerTransportError("fake broker timed out before accepting the order")
        self._submit_params[client_id] = params
        if plan.outcome is FakeSubmitOutcome.REJECT:
            assert plan.rejection_reason is not None
            result = SubmitRejected(reason=plan.rejection_reason)
            self._results[client_id] = result
            return result

        state = self._record_accepted_order(intent, plan)
        if plan.outcome is FakeSubmitOutcome.TIMEOUT_AFTER_ACCEPT:
            raise BrokerTransportError("fake broker timed out after accepting the order")
        result = SubmitAccepted(order=state.order)
        self._results[client_id] = result
        return result

    def get_order(self, client_order_id: ClientOrderId) -> BrokerOrder | None:
        if client_order_id.value in self._hidden_client_ids:
            return None
        broker_order_id = self._by_client.get(client_order_id.value)
        if broker_order_id is None:
            return None
        return self._orders[broker_order_id].order

    def reveal_order(self, client_order_id: ClientOrderId) -> None:
        """End a scripted REST visibility gap for one client order id."""
        self._hidden_client_ids.discard(client_order_id.value)

    def list_recent_orders(self, *, since: UtcTimestamp) -> tuple[BrokerOrder, ...]:
        """Return every recorded order whose broker timestamp is inside the horizon."""
        return tuple(
            state.order
            for _, state in sorted(self._orders.items())
            if state.order.updated_at.value >= since.value
        )

    def list_open_orders(self) -> tuple[BrokerOrder, ...]:
        return tuple(
            state.order for _, state in sorted(self._orders.items()) if state.order.is_open
        )

    def get_asset(self, symbol: Symbol) -> PaperAsset | None:
        if symbol.value in self._unknown_assets:
            return None
        existing = self._assets.get(symbol.value)
        if existing is not None:
            return existing
        return PaperAsset(
            symbol=symbol,
            asset_class=AssetClass.US_EQUITY,
            status=AssetStatus.ACTIVE,
            tradable=True,
            exchange="ARCA",
        )

    def list_positions(self) -> tuple[PaperPosition, ...]:
        """Derive the position view deterministically from recorded fills."""
        quantities: dict[str, int] = {}
        cost_cents: dict[str, int] = {}
        for _, state in sorted(self._orders.items()):
            for fill in state.fills:
                symbol = state.order.symbol.value
                quantity = quantities.get(symbol, 0)
                cost = cost_cents.get(symbol, 0)
                if state.order.side is OrderSide.BUY:
                    quantities[symbol] = quantity + fill.quantity.value
                    cost_cents[symbol] = cost + fill.quantity.value * fill.price.cents
                    continue
                if fill.quantity.value > quantity:
                    raise BrokerConflictError("fake broker recorded an oversell")
                average = cost // quantity if quantity else 0
                quantities[symbol] = quantity - fill.quantity.value
                cost_cents[symbol] = cost - average * fill.quantity.value
        positions: list[PaperPosition] = []
        for symbol_value, quantity in sorted(quantities.items()):
            if quantity < 1:
                continue
            invested = cost_cents.get(symbol_value, 0)
            average_cents = invested // quantity
            positions.append(
                PaperPosition(
                    symbol=Symbol(symbol_value),
                    quantity=quantity,
                    average_entry_price=Price.from_cents(max(average_cents, 1)),
                )
            )
        return tuple(positions)

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]:
        state = self._orders.get(broker_order_id)
        if state is None:
            return ()
        return tuple(state.fills)

    def cancel_order(self, broker_order_id: str) -> bool:
        state = self._orders.get(broker_order_id)
        if state is None or not state.order.is_open:
            return False
        if self._cancel_mode is FakeCancelMode.TIMEOUT:
            raise BrokerTransportError("fake broker cancel outcome is unknown")
        if self._cancel_mode is FakeCancelMode.PENDING:
            self._transition(state, BrokerOrderStatus.PENDING_CANCEL)
            return True
        self._transition(state, BrokerOrderStatus.CANCELED)
        return True

    def resolve_pending_cancel(self, broker_order_id: str) -> None:
        """Finish an async cancel: the broker now reports CANCELED."""
        state = self._orders.get(broker_order_id)
        if state is None or state.order.status is not BrokerOrderStatus.PENDING_CANCEL:
            raise ValueError("resolve_pending_cancel requires a PENDING_CANCEL order")
        self._transition(state, BrokerOrderStatus.CANCELED)

    def apply_fill(self, broker_order_id: str, step: FakeFillStep) -> Fill:
        """Advance a partial-fill scenario by one deterministic execution."""
        if type(step) is not FakeFillStep:
            raise ValueError("fill step must be a FakeFillStep")
        state = self._orders.get(broker_order_id)
        if state is None:
            raise ValueError("unknown broker order id")
        if not state.order.is_open:
            raise ValueError("terminal broker orders cannot receive fills")
        return self._apply_fill_step(state, step)

    def expire_order(self, broker_order_id: str) -> bool:
        """Simulate broker-side expiry of an open order."""
        state = self._orders.get(broker_order_id)
        if state is None or not state.order.is_open:
            return False
        self._transition(state, BrokerOrderStatus.EXPIRED)
        return True

    def force_status(self, client_order_id: ClientOrderId, status: BrokerOrderStatus) -> None:
        """Script an observed broker status change (including review statuses)."""
        if type(status) is not BrokerOrderStatus:
            raise ValueError("status must be a BrokerOrderStatus")
        broker_order_id = self._by_client.get(client_order_id.value)
        if broker_order_id is None:
            raise ValueError("no recorded order for this client order id")
        self._transition(self._orders[broker_order_id], status)

    def _record_accepted_order(self, intent: OrderIntent, plan: FakeSubmitPlan) -> _OrderState:
        self._sequence += 1
        broker_order_id = f"fake-order-{self._sequence:06d}"
        now = self._clock()
        initial_status = (
            BrokerOrderStatus.RECEIVED
            if plan.outcome is FakeSubmitOutcome.TIMEOUT_AFTER_ACCEPT
            else BrokerOrderStatus.ACCEPTED
        )
        order = BrokerOrder(
            broker_order_id=broker_order_id,
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            filled_quantity=0,
            limit_price=intent.limit_price,
            status=initial_status,
            submitted_at=now,
            updated_at=now,
        )
        state = _OrderState(order=order)
        self._orders[broker_order_id] = state
        self._by_client[intent.client_order_id.value] = broker_order_id
        if plan.first_fill is not None:
            self._apply_fill_step(state, plan.first_fill)
        return state

    def _apply_fill_step(self, state: _OrderState, step: FakeFillStep) -> Fill:
        order = state.order
        new_filled = order.filled_quantity + step.quantity.value
        if new_filled > order.quantity.value:
            raise ValueError("scripted fills exceed the submitted quantity")
        self._sequence += 1
        fill = Fill(
            execution_id=f"fake-exec-{self._sequence:06d}",
            broker_order_id=order.broker_order_id,
            quantity=step.quantity,
            price=step.price,
            occurred_at=self._clock(),
        )
        target = (
            BrokerOrderStatus.FILLED
            if new_filled == order.quantity.value
            else BrokerOrderStatus.PARTIALLY_FILLED
        )
        self._transition(state, target)
        state.order = replace(state.order, filled_quantity=new_filled)
        state.fills.append(fill)
        return fill

    def _transition(self, state: _OrderState, target: BrokerOrderStatus) -> None:
        order = state.order
        assert_broker_order_transition(order.status, target)
        state.order = replace(order, status=target, updated_at=self._clock())
