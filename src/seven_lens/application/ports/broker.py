"""Network-neutral Paper broker port shared by the engine and future adapters.

Only a Paper broker can implement this port.  The account snapshot asserts the
broker environment explicitly so every caller can fail closed when the broker's
own view of the account is not ``PAPER``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seven_lens.config.broker import BrokerEnvironment
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.orders import (
    BrokerOrder,
    ClientOrderId,
    Fill,
    OrderIntent,
    Price,
    Symbol,
    UsdAmount,
)


class BrokerTransportError(RuntimeError):
    """A timeout or connection failure whose order outcome is unknown.

    The caller must move the intent to UNKNOWN and resolve it by querying the
    deterministic client order id.  This error never licenses a fresh submit.
    """


class BrokerQueryError(RuntimeError):
    """A read against the broker failed; no state may be inferred from it."""


class BrokerConflictError(RuntimeError):
    """The broker reports this client order id with different parameters.

    This is a fail-closed invariant breach: the id is deterministic, so a
    parameter mismatch means local and broker state disagree structurally.
    """


class DuplicateClientOrderIdUnknown(BrokerTransportError):
    """A duplicate-client-order-id rejection could not be resolved to an order.

    The broker stated the id is already known, so a deterministic rejection
    cannot be concluded, yet the follow-up query found nothing.  The submission
    outcome stays ambiguous: the caller must keep the intent UNKNOWN and let
    recovery and reconciliation converge on broker truth.
    """


class RejectionReason(StrEnum):
    """Closed broker-side rejection causes for a submitted intent."""

    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    SYMBOL_NOT_TRADEABLE = "SYMBOL_NOT_TRADEABLE"
    OUTSIDE_TRADING_WINDOW = "OUTSIDE_TRADING_WINDOW"
    ORDER_PARAMETERS_REJECTED = "ORDER_PARAMETERS_REJECTED"


class AssetClass(StrEnum):
    """Closed classification of a broker asset with a Paper-account orientation."""

    US_EQUITY = "US_EQUITY"
    US_OPTION = "US_OPTION"
    CRYPTO = "CRYPTO"
    FUTURE = "FUTURE"


class AssetStatus(StrEnum):
    """The broker's own tradability gate for an asset."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True, slots=True)
class PaperAsset:
    """Typed, read-only tradability of one symbol on the Paper account."""

    symbol: Symbol
    asset_class: AssetClass
    status: AssetStatus
    tradable: bool
    exchange: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise ValueError("asset symbol must be a Symbol")
        if type(self.asset_class) is not AssetClass:
            raise ValueError("asset class must be an AssetClass")
        if type(self.status) is not AssetStatus:
            raise ValueError("asset status must be an AssetStatus")
        if type(self.tradable) is not bool:
            raise ValueError("asset tradability must be a boolean")
        if (
            type(self.exchange) is not str
            or not self.exchange.strip()
            or len(self.exchange) > 20
            or "\x00" in self.exchange
        ):
            raise ValueError("asset exchange must be bounded text")


@dataclass(frozen=True, slots=True)
class PaperAccount:
    """The broker's own view of the Paper account used for Paper assertions."""

    account_id: str
    environment: BrokerEnvironment
    cash: UsdAmount
    equity: UsdAmount

    def __post_init__(self) -> None:
        if (
            type(self.account_id) is not str
            or not self.account_id.strip()
            or len(self.account_id) > 100
        ):
            raise ValueError("account_id must be non-empty text up to 100 characters")
        if self.environment is not BrokerEnvironment.PAPER:
            raise ValueError("account snapshot must assert the PAPER environment")
        if not isinstance(self.cash, UsdAmount):
            raise ValueError("cash must be a UsdAmount")
        if not isinstance(self.equity, UsdAmount):
            raise ValueError("equity must be a UsdAmount")


@dataclass(frozen=True, slots=True)
class PaperPosition:
    """The broker's own view of one open position."""

    symbol: Symbol
    quantity: int
    average_entry_price: Price

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise ValueError("position symbol must be a Symbol")
        if type(self.quantity) is not int or self.quantity < 1:
            raise ValueError("position quantity must be a positive whole number of shares")
        if not isinstance(self.average_entry_price, Price):
            raise ValueError("position average_entry_price must be a Price")


@dataclass(frozen=True, slots=True)
class SubmitAccepted:
    """The broker accepted the order under our deterministic client id."""

    order: BrokerOrder

    def __post_init__(self) -> None:
        if not isinstance(self.order, BrokerOrder):
            raise ValueError("accepted submission requires a BrokerOrder")


@dataclass(frozen=True, slots=True)
class SubmitRejected:
    """The broker deterministically refused the order; no order exists."""

    reason: RejectionReason

    def __post_init__(self) -> None:
        if type(self.reason) is not RejectionReason:
            raise ValueError("rejection requires a closed RejectionReason")


type SubmitResult = SubmitAccepted | SubmitRejected


class PaperBrokerPort(Protocol):
    """The only broker surface the execution engine may call."""

    def account(self) -> PaperAccount:
        """Return the Paper account snapshot; callers must re-check PAPER."""
        ...

    def submit_order(self, intent: OrderIntent) -> SubmitResult:
        """Submit exactly one intent under its deterministic client order id.

        Raises BrokerTransportError when the outcome is unknown and
        BrokerConflictError on an id/parameter mismatch at the broker.
        """
        ...

    def get_order(self, client_order_id: ClientOrderId) -> BrokerOrder | None:
        """Return the broker's order for this client id, or None if absent."""
        ...

    def list_recent_orders(self, *, since: UtcTimestamp) -> tuple[BrokerOrder, ...]:
        """Return broker orders updated since the bounded reconciliation horizon.

        Includes terminal orders: reconciliation must compare broker history,
        not only open orders.  The horizon keeps the fetch deterministic and
        bounded; callers must never request unbounded history.
        """
        ...

    def list_open_orders(self) -> tuple[BrokerOrder, ...]:
        """Return every non-terminal order known to the broker."""
        ...

    def list_positions(self) -> tuple[PaperPosition, ...]:
        """Return the broker's own position view for ledger reconciliation."""
        ...

    def list_fills(self, broker_order_id: str) -> tuple[Fill, ...]:
        """Return fills for one broker order in broker-reported order."""
        ...

    def get_asset(self, symbol: Symbol) -> PaperAsset | None:
        """Return typed tradability for one symbol, or None when unknown."""
        ...

    def cancel_order(self, broker_order_id: str) -> bool:
        """Request cancellation; False means the order was already terminal."""
        ...
