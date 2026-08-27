"""Strict execution-domain contracts for order intents and broker state.

The internal order lifecycle follows the roadmap state machine:

    CREATED -> RISK_APPROVED -> OUTBOX_PENDING -> SUBMITTING
            -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED
            -> CANCEL_PENDING -> CANCELED

with the closed failure statuses REJECTED, EXPIRED, and UNKNOWN.  A submit
timeout first moves the intent to UNKNOWN; resolution happens only by querying
the broker with the deterministic client order id.  The domain never encodes a
blind resubmit path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid4

from seven_lens.domain.value_objects import RunId, TradingDate, UtcTimestamp

_SYMBOL_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_STRATEGY_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_WINDOW_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CLIENT_ORDER_ID_PATTERN: Final = re.compile(
    r"^slv1-[a-z0-9][a-z0-9_-]{0,31}"
    r"-[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"-[a-z0-9][a-z0-9_-]{0,63}"
    r"-t[1-9][0-9]{0,9}"
    r"-[A-Z][A-Z0-9.\-]{0,9}"
    r"-(?:buy|sell)$"
)
_CLIENT_ORDER_ID_MAX_LENGTH: Final = 128
_CENTS: Final = Decimal("0.01")
_PRICE_MAX: Final = Decimal("10000000.00")
_USD_MAX: Final = Decimal("10000000000.00")
_MAX_TARGET_VERSION: Final = 10_000_000_000

# Provisional until walk-forward calibration produces an ADR-backed value.
COLLAR_OFFSET_BPS_MIN: Final = 1
COLLAR_OFFSET_BPS_MAX: Final = 500


class OrderSide(StrEnum):
    """The only two directions a Paper order may take."""

    BUY = "BUY"
    SELL = "SELL"


class OrderIntentType(StrEnum):
    """Closed classification of why an intent exists."""

    REBALANCE = "REBALANCE"
    RISK_EXIT = "RISK_EXIT"


class OrderStatus(StrEnum):
    """Authoritative internal lifecycle states for one order intent."""

    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    OUTBOX_PENDING = "OUTBOX_PENDING"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


TERMINAL_ORDER_STATUSES: Final[frozenset[OrderStatus]] = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
)

ORDER_STATUS_TRANSITIONS: Final[dict[OrderStatus, frozenset[OrderStatus]]] = {
    OrderStatus.CREATED: frozenset(
        {OrderStatus.RISK_APPROVED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
    ),
    OrderStatus.RISK_APPROVED: frozenset({OrderStatus.OUTBOX_PENDING, OrderStatus.EXPIRED}),
    OrderStatus.OUTBOX_PENDING: frozenset({OrderStatus.SUBMITTING, OrderStatus.EXPIRED}),
    OrderStatus.SUBMITTING: frozenset(
        {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.UNKNOWN,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELED,
            OrderStatus.REVIEW_REQUIRED,
        }
    ),
    OrderStatus.ACKNOWLEDGED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.EXPIRED,
            OrderStatus.REVIEW_REQUIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.EXPIRED,
            OrderStatus.REVIEW_REQUIRED,
        }
    ),
    OrderStatus.CANCEL_PENDING: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
            OrderStatus.REVIEW_REQUIRED,
        }
    ),
    OrderStatus.UNKNOWN: frozenset(
        {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.REVIEW_REQUIRED,
        }
    ),
    # A broker update can conflict after the local intent reached a terminal
    # state.  REVIEW_REQUIRED is the only safe escape hatch: it blocks new
    # entries while preserving the original terminal history for reconciliation.
    OrderStatus.FILLED: frozenset({OrderStatus.REVIEW_REQUIRED}),
    OrderStatus.CANCELED: frozenset({OrderStatus.REVIEW_REQUIRED}),
    OrderStatus.REJECTED: frozenset({OrderStatus.REVIEW_REQUIRED}),
    OrderStatus.EXPIRED: frozenset({OrderStatus.REVIEW_REQUIRED}),
    OrderStatus.REVIEW_REQUIRED: frozenset(),
}


class InvalidOrderTransitionError(ValueError):
    """Raised when a requested order status change is not in the closed map."""


def order_transition_allowed(current: OrderStatus, target: OrderStatus) -> bool:
    """Return whether the closed lifecycle map permits this status change."""
    if type(current) is not OrderStatus or type(target) is not OrderStatus:
        raise ValueError("order transition requires exact OrderStatus values")
    return target in ORDER_STATUS_TRANSITIONS[current]


def assert_order_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Fail closed unless the transition is explicitly allowed."""
    if not order_transition_allowed(current, target):
        raise InvalidOrderTransitionError(
            f"order status transition {current.value} -> {target.value} is not permitted"
        )


@dataclass(frozen=True, slots=True)
class Symbol:
    """A US-listed ticker in canonical uppercase broker form."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _SYMBOL_PATTERN.fullmatch(self.value) is None:
            raise ValueError("symbol must be uppercase letters, digits, '.' or '-' (max 10)")


@dataclass(frozen=True, slots=True)
class OrderQuantity:
    """A whole-share quantity; fractional and zero quantities are rejected."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value < 1:
            raise ValueError("quantity must be a positive whole number of shares")


@dataclass(frozen=True, slots=True)
class Price:
    """An exact two-decimal USD price; no floating point ever enters this domain."""

    value: Decimal

    def __post_init__(self) -> None:
        if type(self.value) is not Decimal:
            raise ValueError("price must be a Decimal")
        if not self.value.is_finite():
            raise ValueError("price must be finite")
        if self.value.as_tuple().exponent != -2:
            raise ValueError("price must use exactly two decimal places")
        if self.value <= 0 or self.value > _PRICE_MAX:
            raise ValueError("price must be greater than zero and at most 10,000,000.00")

    @classmethod
    def from_cents(cls, cents: int) -> Price:
        """Build a price from a positive whole number of cents."""
        if type(cents) is not int or cents < 1 or cents > 1_000_000_000:
            raise ValueError("cents must be a positive integer up to 1,000,000,000")
        return cls(Decimal(cents).scaleb(-2))

    @property
    def cents(self) -> int:
        return int(self.value.scaleb(2))


@dataclass(frozen=True, slots=True)
class UsdAmount:
    """An exact two-decimal non-negative USD amount for account balances."""

    value: Decimal

    def __post_init__(self) -> None:
        if type(self.value) is not Decimal:
            raise ValueError("amount must be a Decimal")
        if not self.value.is_finite():
            raise ValueError("amount must be finite")
        if self.value.as_tuple().exponent != -2:
            raise ValueError("amount must use exactly two decimal places")
        if self.value < 0 or self.value > _USD_MAX:
            raise ValueError("amount must be zero or positive and at most 10,000,000,000.00")

    @classmethod
    def from_cents(cls, cents: int) -> UsdAmount:
        """Build an amount from a non-negative whole number of cents."""
        if type(cents) is not int or cents < 0 or cents > 1_000_000_000_000:
            raise ValueError("cents must be a non-negative integer up to 1,000,000,000,000")
        return cls(Decimal(cents).scaleb(-2))

    @property
    def cents(self) -> int:
        return int(self.value.scaleb(2))


@dataclass(frozen=True, slots=True)
class PriceCollar:
    """Bounds a limit price may take around a reference quote.

    ``offset_bps`` is provisional pending walk-forward calibration; the closed
    range keeps the collar meaningful (never zero, never wider than five
    percent) without allowing an unbounded chase.
    """

    reference: Price
    offset_bps: int

    def __post_init__(self) -> None:
        if not isinstance(self.reference, Price):
            raise ValueError("collar reference must be a Price")
        if (
            type(self.offset_bps) is not int
            or self.offset_bps < COLLAR_OFFSET_BPS_MIN
            or self.offset_bps > COLLAR_OFFSET_BPS_MAX
        ):
            raise ValueError("collar offset must be between 1 and 500 basis points")

    @property
    def lower_limit(self) -> Price:
        floor = (self.reference.value * _bps_factor(-self.offset_bps)).quantize(
            _CENTS, rounding=ROUND_FLOOR
        )
        return Price(max(floor, _CENTS))

    @property
    def upper_limit(self) -> Price:
        ceiling = (self.reference.value * _bps_factor(self.offset_bps)).quantize(
            _CENTS, rounding=ROUND_CEILING
        )
        return Price(ceiling)

    def contains(self, price: Price) -> bool:
        if not isinstance(price, Price):
            raise ValueError("collar containment requires a Price")
        return self.lower_limit.value <= price.value <= self.upper_limit.value


def _bps_factor(bps: int) -> Decimal:
    return Decimal(1) + Decimal(bps) / Decimal(10_000)


@dataclass(frozen=True, slots=True)
class ClientOrderId:
    """The deterministic idempotency key for one order intent.

    Composition: strategy, trading date, execution window, target portfolio
    version, symbol, and side.  The same tuple always yields the same id, so a
    timeout can only ever be resolved by querying this id, never by resubmitting
    a fresh one.
    """

    value: str

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or len(self.value) > _CLIENT_ORDER_ID_MAX_LENGTH
            or _CLIENT_ORDER_ID_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError("client order id must use the canonical slv1 composition format")

    @classmethod
    def compose(
        cls,
        *,
        strategy: str,
        trading_date: TradingDate,
        window: str,
        target_version: int,
        symbol: Symbol,
        side: OrderSide,
    ) -> ClientOrderId:
        """Build the canonical id from its exact components."""
        if type(strategy) is not str or _STRATEGY_PATTERN.fullmatch(strategy) is None:
            raise ValueError("strategy must use lowercase letters, digits, '_' or '-' (max 32)")
        if not isinstance(trading_date, TradingDate):
            raise ValueError("trading_date must be a TradingDate")
        if type(window) is not str or _WINDOW_PATTERN.fullmatch(window) is None:
            raise ValueError("window must use lowercase letters, digits, '_' or '-' (max 64)")
        if (
            type(target_version) is not int
            or target_version < 1
            or target_version > _MAX_TARGET_VERSION
        ):
            raise ValueError("target_version must be a positive integer up to 10,000,000,000")
        if not isinstance(symbol, Symbol):
            raise ValueError("symbol must be a Symbol")
        if type(side) is not OrderSide:
            raise ValueError("side must be an OrderSide")
        return cls(
            f"slv1-{strategy}-{trading_date.value.isoformat()}-{window}"
            f"-t{target_version}-{symbol.value}-{side.value.lower()}"
        )


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """An approved-by-construction request that may become one Paper order."""

    intent_id: UUID
    client_order_id: ClientOrderId
    strategy: str
    trading_date: TradingDate
    window: str
    target_version: int
    symbol: Symbol
    side: OrderSide
    quantity: OrderQuantity
    intent_type: OrderIntentType
    limit_price: Price
    collar: PriceCollar
    earliest_submit_at: UtcTimestamp
    cancel_at: UtcTimestamp
    status: OrderStatus
    run_id: RunId
    created_at: UtcTimestamp

    def __post_init__(self) -> None:
        if not isinstance(self.intent_id, UUID) or self.intent_id.int == 0:
            raise ValueError("intent_id must be a non-nil UUID")
        if not isinstance(self.client_order_id, ClientOrderId):
            raise ValueError("client_order_id must be a ClientOrderId")
        if type(self.strategy) is not str or _STRATEGY_PATTERN.fullmatch(self.strategy) is None:
            raise ValueError("strategy must use lowercase letters, digits, '_' or '-' (max 32)")
        if not isinstance(self.trading_date, TradingDate):
            raise ValueError("trading_date must be a TradingDate")
        if type(self.window) is not str or _WINDOW_PATTERN.fullmatch(self.window) is None:
            raise ValueError("window must use lowercase letters, digits, '_' or '-' (max 64)")
        if (
            type(self.target_version) is not int
            or self.target_version < 1
            or self.target_version > _MAX_TARGET_VERSION
        ):
            raise ValueError("target_version must be a positive integer up to 10,000,000,000")
        if not isinstance(self.symbol, Symbol):
            raise ValueError("symbol must be a Symbol")
        if type(self.side) is not OrderSide:
            raise ValueError("side must be an OrderSide")
        if not isinstance(self.quantity, OrderQuantity):
            raise ValueError("quantity must be an OrderQuantity")
        if type(self.intent_type) is not OrderIntentType:
            raise ValueError("intent_type must be an OrderIntentType")
        if not isinstance(self.limit_price, Price):
            raise ValueError("limit_price must be a Price")
        if not isinstance(self.collar, PriceCollar):
            raise ValueError("collar must be a PriceCollar")
        if not isinstance(self.earliest_submit_at, UtcTimestamp):
            raise ValueError("earliest_submit_at must be a UtcTimestamp")
        if not isinstance(self.cancel_at, UtcTimestamp):
            raise ValueError("cancel_at must be a UtcTimestamp")
        if self.cancel_at.value <= self.earliest_submit_at.value:
            raise ValueError("cancel_at must be after earliest_submit_at")
        if type(self.status) is not OrderStatus:
            raise ValueError("status must be an OrderStatus")
        if not isinstance(self.run_id, RunId):
            raise ValueError("run_id must be a RunId")
        if not isinstance(self.created_at, UtcTimestamp):
            raise ValueError("created_at must be a UtcTimestamp")
        if not self.collar.contains(self.limit_price):
            raise ValueError("limit_price must lie inside the price collar")
        expected = ClientOrderId.compose(
            strategy=self.strategy,
            trading_date=self.trading_date,
            window=self.window,
            target_version=self.target_version,
            symbol=self.symbol,
            side=self.side,
        )
        if self.client_order_id != expected:
            raise ValueError("client_order_id must match its own composition components")

    @classmethod
    def create(
        cls,
        *,
        strategy: str,
        trading_date: TradingDate,
        window: str,
        target_version: int,
        symbol: Symbol,
        side: OrderSide,
        quantity: OrderQuantity,
        intent_type: OrderIntentType,
        limit_price: Price,
        collar: PriceCollar,
        earliest_submit_at: UtcTimestamp,
        cancel_at: UtcTimestamp,
        run_id: RunId,
        created_at: UtcTimestamp,
        intent_id: UUID | None = None,
    ) -> OrderIntent:
        """Create a fresh intent in CREATED; risk approval is a later transition."""
        return cls(
            intent_id=intent_id or uuid4(),
            client_order_id=ClientOrderId.compose(
                strategy=strategy,
                trading_date=trading_date,
                window=window,
                target_version=target_version,
                symbol=symbol,
                side=side,
            ),
            strategy=strategy,
            trading_date=trading_date,
            window=window,
            target_version=target_version,
            symbol=symbol,
            side=side,
            quantity=quantity,
            intent_type=intent_type,
            limit_price=limit_price,
            collar=collar,
            earliest_submit_at=earliest_submit_at,
            cancel_at=cancel_at,
            status=OrderStatus.CREATED,
            run_id=run_id,
            created_at=created_at,
        )

    def transition_to(self, target: OrderStatus) -> OrderIntent:
        """Return a new intent in ``target`` status; identity fields never change."""
        assert_order_transition(self.status, target)
        return replace(self, status=target)


class BrokerOrderStatus(StrEnum):
    """Closed mirror of broker-side order statuses this system records.

    Every official Alpaca order status has a typed representation: the mirror
    always preserves broker truth.  Statuses the local lifecycle cannot safely
    interpret (``DONE_FOR_DAY``, ``REPLACED``, ``PENDING_REPLACE``, ``STOPPED``,
    ``SUSPENDED``, ``CALCULATED``) map to a review-only intent state instead of
    being silently coerced into ACKNOWLEDGED/CANCELED/EXPIRED.
    """

    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    ACCEPTED_FOR_BIDDING = "ACCEPTED_FOR_BIDDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    DONE_FOR_DAY = "DONE_FOR_DAY"
    REPLACED = "REPLACED"
    PENDING_REPLACE = "PENDING_REPLACE"
    STOPPED = "STOPPED"
    SUSPENDED = "SUSPENDED"
    CALCULATED = "CALCULATED"


TERMINAL_BROKER_ORDER_STATUSES: Final[frozenset[BrokerOrderStatus]] = frozenset(
    {
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELED,
        BrokerOrderStatus.EXPIRED,
        BrokerOrderStatus.REJECTED,
    }
)

REVIEW_BROKER_ORDER_STATUSES: Final[frozenset[BrokerOrderStatus]] = frozenset(
    {
        BrokerOrderStatus.DONE_FOR_DAY,
        BrokerOrderStatus.REPLACED,
        BrokerOrderStatus.PENDING_REPLACE,
        BrokerOrderStatus.STOPPED,
        BrokerOrderStatus.SUSPENDED,
        BrokerOrderStatus.CALCULATED,
    }
)

BROKER_ORDER_STATUS_TRANSITIONS: Final[dict[BrokerOrderStatus, frozenset[BrokerOrderStatus]]] = {
    BrokerOrderStatus.RECEIVED: frozenset(
        {
            BrokerOrderStatus.ACCEPTED,
            BrokerOrderStatus.ACCEPTED_FOR_BIDDING,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
            *REVIEW_BROKER_ORDER_STATUSES,
        }
    ),
    BrokerOrderStatus.ACCEPTED: frozenset(
        {
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
            *REVIEW_BROKER_ORDER_STATUSES,
        }
    ),
    BrokerOrderStatus.ACCEPTED_FOR_BIDDING: frozenset(
        {
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
            *REVIEW_BROKER_ORDER_STATUSES,
        }
    ),
    BrokerOrderStatus.PARTIALLY_FILLED: frozenset(
        {
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
            *REVIEW_BROKER_ORDER_STATUSES,
        }
    ),
    BrokerOrderStatus.PENDING_CANCEL: frozenset(
        {
            BrokerOrderStatus.PENDING_CANCEL,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
            BrokerOrderStatus.REJECTED,
            *REVIEW_BROKER_ORDER_STATUSES,
        }
    ),
    BrokerOrderStatus.FILLED: frozenset(),
    BrokerOrderStatus.CANCELED: frozenset(),
    BrokerOrderStatus.EXPIRED: frozenset(),
    BrokerOrderStatus.REJECTED: frozenset(),
    BrokerOrderStatus.DONE_FOR_DAY: frozenset(),
    BrokerOrderStatus.REPLACED: frozenset(),
    BrokerOrderStatus.PENDING_REPLACE: frozenset(),
    BrokerOrderStatus.STOPPED: frozenset(),
    BrokerOrderStatus.SUSPENDED: frozenset(),
    BrokerOrderStatus.CALCULATED: frozenset(),
}


class InvalidBrokerOrderTransitionError(ValueError):
    """Raised when an observed broker status sequence is impossible."""


def broker_order_transition_allowed(current: BrokerOrderStatus, target: BrokerOrderStatus) -> bool:
    """Return whether the observed broker status change is representable."""
    if type(current) is not BrokerOrderStatus or type(target) is not BrokerOrderStatus:
        raise ValueError("broker order transition requires exact BrokerOrderStatus values")
    return target in BROKER_ORDER_STATUS_TRANSITIONS[current]


def assert_broker_order_transition(current: BrokerOrderStatus, target: BrokerOrderStatus) -> None:
    """Fail closed unless the observed broker transition is possible."""
    if not broker_order_transition_allowed(current, target):
        raise InvalidBrokerOrderTransitionError(
            f"broker order status {current.value} -> {target.value} is not representable"
        )


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """The local mirror of one broker-side Paper order."""

    broker_order_id: str
    client_order_id: ClientOrderId
    symbol: Symbol
    side: OrderSide
    quantity: OrderQuantity
    filled_quantity: int
    limit_price: Price
    status: BrokerOrderStatus
    submitted_at: UtcTimestamp
    updated_at: UtcTimestamp

    def __post_init__(self) -> None:
        if (
            type(self.broker_order_id) is not str
            or not self.broker_order_id.strip()
            or len(self.broker_order_id) > 100
            or "\x00" in self.broker_order_id
        ):
            raise ValueError("broker_order_id must be non-empty text up to 100 characters")
        if not isinstance(self.client_order_id, ClientOrderId):
            raise ValueError("client_order_id must be a ClientOrderId")
        if not isinstance(self.symbol, Symbol):
            raise ValueError("symbol must be a Symbol")
        if type(self.side) is not OrderSide:
            raise ValueError("side must be an OrderSide")
        if not isinstance(self.quantity, OrderQuantity):
            raise ValueError("quantity must be an OrderQuantity")
        if (
            type(self.filled_quantity) is not int
            or not 0 <= self.filled_quantity <= self.quantity.value
        ):
            raise ValueError("filled_quantity must be between zero and the order quantity")
        if not isinstance(self.limit_price, Price):
            raise ValueError("limit_price must be a Price")
        if type(self.status) is not BrokerOrderStatus:
            raise ValueError("status must be a BrokerOrderStatus")
        if not isinstance(self.submitted_at, UtcTimestamp):
            raise ValueError("submitted_at must be a UtcTimestamp")
        if not isinstance(self.updated_at, UtcTimestamp):
            raise ValueError("updated_at must be a UtcTimestamp")

    @property
    def is_open(self) -> bool:
        return (
            self.status not in TERMINAL_BROKER_ORDER_STATUSES
            and self.status not in REVIEW_BROKER_ORDER_STATUSES
        )


@dataclass(frozen=True, slots=True)
class Fill:
    """One broker execution against a broker order; immutable once recorded."""

    execution_id: str
    broker_order_id: str
    quantity: OrderQuantity
    price: Price
    occurred_at: UtcTimestamp

    def __post_init__(self) -> None:
        if (
            type(self.execution_id) is not str
            or not self.execution_id.strip()
            or len(self.execution_id) > 100
            or "\x00" in self.execution_id
        ):
            raise ValueError("execution_id must be non-empty text up to 100 characters")
        if (
            type(self.broker_order_id) is not str
            or not self.broker_order_id.strip()
            or len(self.broker_order_id) > 100
            or "\x00" in self.broker_order_id
        ):
            raise ValueError("broker_order_id must be non-empty text up to 100 characters")
        if not isinstance(self.quantity, OrderQuantity):
            raise ValueError("quantity must be an OrderQuantity")
        if not isinstance(self.price, Price):
            raise ValueError("price must be a Price")
        if not isinstance(self.occurred_at, UtcTimestamp):
            raise ValueError("occurred_at must be a UtcTimestamp")
