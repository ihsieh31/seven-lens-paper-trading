"""Deterministic cash and position projections derived from the fill ledger.

The append-only fills plus their broker order mirrors are the only inputs.
Buys open FIFO lots and debit cash; sells consume lots in open order and
credit cash.  A sell that exceeds the projected position, an unknown broker
order, or a negative cash projection is a hard invariant failure - the ledger
never guesses and never clamps.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.execution.orders import (
    BrokerOrder,
    Fill,
    OrderSide,
    Price,
    Symbol,
)

_MAX_ABS_CENTS = 1_000_000_000_000


class LedgerInvariantError(RuntimeError):
    """Raised when fills cannot produce a consistent ledger."""


@dataclass(frozen=True, slots=True)
class OpenLot:
    """The remaining quantity and cost of one FIFO lot."""

    symbol: Symbol
    quantity: int
    price: Price
    opened_at: UtcTimestamp

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise ValueError("lot symbol must be a Symbol")
        if type(self.quantity) is not int or self.quantity < 1:
            raise ValueError("lot quantity must be a positive whole number of shares")
        if not isinstance(self.price, Price):
            raise ValueError("lot price must be a Price")
        if not isinstance(self.opened_at, UtcTimestamp):
            raise ValueError("lot opened_at must be a UtcTimestamp")


@dataclass(frozen=True, slots=True)
class LedgerProjection:
    """Signed cash delta and open lots after replaying the fill ledger.

    ``cash_delta_cents`` is the net ledger effect of fills; it is not an
    account balance, which would also require the opening cash position.
    """

    cash_delta_cents: int
    lots: tuple[OpenLot, ...]

    def __post_init__(self) -> None:
        if type(self.cash_delta_cents) is not int or abs(self.cash_delta_cents) > _MAX_ABS_CENTS:
            raise ValueError("cash delta must be a bounded integer number of cents")

    @property
    def positions(self) -> dict[Symbol, int]:
        quantities: dict[Symbol, int] = {}
        for lot in self.lots:
            quantities[lot.symbol] = quantities.get(lot.symbol, 0) + lot.quantity
        return quantities


def account_valuation(
    projection: LedgerProjection,
    *,
    opening_cash_cents: int,
    prices: Mapping[Symbol, Price],
) -> int:
    """Net account value in cents: opening cash, fill effects, marked positions.

    A symbol without a price fails closed rather than being valued at zero.
    """
    if type(opening_cash_cents) is not int or opening_cash_cents < 0:
        raise ValueError("opening cash must be a non-negative integer number of cents")
    market_value = 0
    for lot in projection.lots:
        price = prices.get(lot.symbol)
        if price is None:
            raise ValueError(f"missing price for symbol {lot.symbol.value}")
        market_value += lot.quantity * price.cents
    total = opening_cash_cents + projection.cash_delta_cents + market_value
    if abs(total) > _MAX_ABS_CENTS:
        raise ValueError("account valuation exceeds the allowed range")
    return total


def account_equity_from_cash_and_positions(
    expected_cash_cents: int,
    lots: tuple[OpenLot, ...],
    prices: Mapping[Symbol, Price],
) -> int:
    """NAV from checkpointed cash plus current FIFO lots at mark prices."""
    if type(expected_cash_cents) is not int or abs(expected_cash_cents) > _MAX_ABS_CENTS:
        raise ValueError("expected cash must be a bounded integer number of cents")
    market_value = 0
    for lot in lots:
        price = prices.get(lot.symbol)
        if price is None:
            raise ValueError(f"missing price for symbol {lot.symbol.value}")
        market_value += lot.quantity * price.cents
    total = expected_cash_cents + market_value
    if abs(total) > _MAX_ABS_CENTS:
        raise ValueError("account valuation exceeds the allowed range")
    return total


def project_ledger(
    fills: tuple[Fill, ...],
    broker_orders: Mapping[str, BrokerOrder],
) -> LedgerProjection:
    """Replay fills into cash and FIFO lots; every anomaly fails closed.

    Canonical replay order is the broker execution time ``occurred_at`` and,
    within the same timestamp, the deterministic ``execution_id``.  This makes
    the projection independent of database arrival order or caller iteration
    order.
    """
    canonical_fills = tuple(
        sorted(fills, key=lambda item: (item.occurred_at.value, item.execution_id))
    )
    cash_cents = 0
    lots: list[OpenLot] = []
    seen_executions: set[str] = set()
    filled_by_order: dict[str, int] = {}
    for fill in canonical_fills:
        if fill.execution_id in seen_executions:
            raise LedgerInvariantError("duplicate execution id in fill ledger")
        seen_executions.add(fill.execution_id)
        order = broker_orders.get(fill.broker_order_id)
        if order is None:
            raise LedgerInvariantError("fill references an unknown broker order")
        cumulative = filled_by_order.get(fill.broker_order_id, 0) + fill.quantity.value
        if cumulative > order.quantity.value:
            raise LedgerInvariantError("fill quantity exceeds the recorded order quantity")
        filled_by_order[fill.broker_order_id] = cumulative
        if order.side is OrderSide.BUY:
            cash_cents -= fill.quantity.value * fill.price.cents
            if abs(cash_cents) > _MAX_ABS_CENTS:
                raise LedgerInvariantError("projected cash delta exceeds the allowed range")
            lots.append(
                OpenLot(
                    symbol=order.symbol,
                    quantity=fill.quantity.value,
                    price=fill.price,
                    opened_at=fill.occurred_at,
                )
            )
            continue
        remaining = fill.quantity.value
        consumed: list[OpenLot] = []
        for lot in lots:
            if remaining == 0:
                consumed.append(lot)
                continue
            if lot.symbol != order.symbol:
                consumed.append(lot)
                continue
            if lot.quantity <= remaining:
                remaining -= lot.quantity
                cash_cents += lot.quantity * fill.price.cents
            else:
                cash_cents += remaining * fill.price.cents
                consumed.append(replace(lot, quantity=lot.quantity - remaining))
                remaining = 0
        if remaining != 0:
            raise LedgerInvariantError("sell fill exceeds the projected position")
        lots = consumed
    if abs(cash_cents) > _MAX_ABS_CENTS:
        raise LedgerInvariantError("projected cash delta exceeds the allowed range")
    ordered_lots = tuple(sorted(lots, key=lambda lot: (lot.symbol.value, lot.opened_at.value)))
    return LedgerProjection(cash_delta_cents=cash_cents, lots=ordered_lots)
