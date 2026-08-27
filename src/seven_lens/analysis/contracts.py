"""Immutable, bounded and canonical P3-A analysis wire contracts.

All decimal wire values are fixed-scale strings: money and prices use two decimal places,
signed weights use six, confidence uses four, and share quantities use six. Binary floats,
exponent notation, whitespace and negative zero are rejected. Every ``from_wire`` method first
applies the existing ``JsonObject`` resource budget and then requires an exact field set.

These objects describe analysis and portfolio *requests*. They have no approval, sizing,
execution, broker, network, credential, or ledger-write capability.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Final, Self, cast

from seven_lens.domain.json_values import JsonObject, JsonValue
from seven_lens.domain.value_objects import RunId, SchemaVersion, UtcTimestamp
from seven_lens.security.sanitized_text import validate_sanitized_text

__all__ = [
    "SCHEMA_VERSION",
    "AnalysisInput",
    "AnalysisStatus",
    "AnalysisWindow",
    "AnalystReport",
    "AnalystRole",
    "BorrowAvailability",
    "BorrowStatus",
    "ContractMeta",
    "InvestmentDebateState",
    "OpenOrderSummary",
    "PortfolioPosition",
    "PortfolioProposal",
    "PortfolioRequest",
    "PortfolioSnapshot",
    "PositionSide",
    "ProposalAction",
    "ProposalReasonCode",
    "RemainingLimits",
    "ResearchConclusion",
    "ResearchRating",
    "RiskDebateState",
    "RiskRejectionCode",
    "RiskRejectionFeedback",
    "SameDayExitReason",
    "SameDayFillSummary",
    "TraderPlan",
    "build_analysis_input",
    "build_portfolio_snapshot",
    "canonical_wire_json",
]

SCHEMA_VERSION: Final = SchemaVersion("1.0.0")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,95}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SIGNED_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
MAX_TEXT_BYTES: Final = 2_048
MAX_SEQUENCE_ITEMS: Final = 32


class AnalysisWindow(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    EMERGENCY = "EMERGENCY"


class AnalysisStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    ABSTAIN = "ABSTAIN"


class AnalystRole(StrEnum):
    TECHNICAL = "TECHNICAL"
    FUNDAMENTALS = "FUNDAMENTALS"
    NEWS = "NEWS"
    SENTIMENT = "SENTIMENT"


class ResearchRating(StrEnum):
    BUY = "BUY"
    OVERWEIGHT = "OVERWEIGHT"
    HOLD = "HOLD"
    UNDERWEIGHT = "UNDERWEIGHT"
    SELL = "SELL"


class ProposalAction(StrEnum):
    OPEN = "OPEN"
    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class SameDayExitReason(StrEnum):
    DOWNSIDE_BAND_EXCEEDED = "DOWNSIDE_BAND_EXCEEDED"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    MATERIAL_NEW_EVENT = "MATERIAL_NEW_EVENT"
    BORROW_LIQUIDITY_ANOMALY = "BORROW_LIQUIDITY_ANOMALY"
    HARD_RISK_TRIGGER = "HARD_RISK_TRIGGER"


class ProposalReasonCode(StrEnum):
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    NEWS = "NEWS"
    SENTIMENT = "SENTIMENT"
    VALUATION = "VALUATION"
    REBALANCE = "REBALANCE"


class RiskRejectionCode(StrEnum):
    CASH = "CASH"
    BUYING_POWER = "BUYING_POWER"
    MAX_SYMBOLS = "MAX_SYMBOLS"
    SINGLE_NAME = "SINGLE_NAME"
    LONG_GROSS = "LONG_GROSS"
    SHORT_GROSS = "SHORT_GROSS"
    TOTAL_GROSS = "TOTAL_GROSS"
    NET_EXPOSURE = "NET_EXPOSURE"
    TURNOVER = "TURNOVER"
    BORROW = "BORROW"
    OPEN_ORDER_CONFLICT = "OPEN_ORDER_CONFLICT"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    SAME_DAY_EXIT = "SAME_DAY_EXIT"
    DATA_CONFLICT = "DATA_CONFLICT"
    SCHEMA_INVALID = "SCHEMA_INVALID"


class BorrowAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


def _exact_enum(value: object, enum_type: type[StrEnum], field: str) -> StrEnum:
    if type(value) is not str:
        raise ValueError(f"{field} must be an exact string")
    try:
        result = enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field} is not a supported value") from error
    return result


def _text(value: object, field: str, *, maximum: int = MAX_TEXT_BYTES, empty: bool = False) -> str:
    return validate_sanitized_text(value, field, maximum=maximum, empty=empty)


def _symbol(value: object) -> str:
    result = _text(value, "symbol", maximum=10)
    if _SYMBOL.fullmatch(result) is None:
        raise ValueError("symbol must use canonical uppercase ticker format")
    return result


def _ref(value: object, field: str) -> str:
    result = validate_sanitized_text(
        value,
        field,
        maximum=96,
        allow_bare_host=True,
    )
    if _REF.fullmatch(result) is None:
        raise ValueError(f"{field} must use canonical reference format")
    return result


def _hash(value: object, field: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _version(value: object, field: str) -> str:
    result = validate_sanitized_text(
        value,
        field,
        maximum=64,
        allow_bare_host=True,
    )
    if _VERSION.fullmatch(result) is None:
        raise ValueError(f"{field} must use canonical version text")
    return result


def _integer(value: object, field: str, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an exact bounded integer")
    return value


def _decimal(
    value: object,
    field: str,
    *,
    scale: int,
    minimum: Decimal,
    maximum: Decimal,
) -> Decimal:
    if type(value) is not str:
        raise ValueError(f"{field} must be a canonical decimal string")
    pattern = _SIGNED_DECIMAL if minimum < 0 else _DECIMAL
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical decimal string")
    parts = value.split(".")
    if len(parts) != 2 or len(parts[1]) != scale:
        raise ValueError(f"{field} must use exactly {scale} decimal places")
    parsed = Decimal(value)
    if parsed.is_zero() and parsed.is_signed():
        raise ValueError(f"{field} must not use negative zero")
    if not parsed.is_finite() or not minimum <= parsed <= maximum:
        raise ValueError(f"{field} is outside its bounded range")
    return parsed


def _decimal_text(value: Decimal, scale: int) -> str:
    return format(value, f".{scale}f")


def _reject_negative_zero(value: Decimal, field: str) -> None:
    if value.is_zero() and value.is_signed():
        raise ValueError(f"{field} must not be negative zero")


def _sequence(
    value: object,
    field: str,
    parser: Callable[[object], object],
    *,
    maximum: int = MAX_SEQUENCE_ITEMS,
    nonempty: bool = False,
    unique: bool = True,
) -> tuple[object, ...]:
    if type(value) not in {list, tuple}:
        raise ValueError(f"{field} must be an exact sequence")
    raw = cast(list[object] | tuple[object, ...], value)
    if len(raw) > maximum or (nonempty and not raw):
        raise ValueError(f"{field} is outside its item bound")
    results = tuple(parser(item) for item in raw)
    if unique and len(set(results)) != len(results):
        raise ValueError(f"{field} must not contain duplicates")
    return results


def _strings(
    value: object, field: str, *, maximum: int = MAX_SEQUENCE_ITEMS, nonempty: bool = False
) -> tuple[str, ...]:
    return cast(
        tuple[str, ...],
        _sequence(
            value,
            field,
            lambda item: _text(item, f"{field} item"),
            maximum=maximum,
            nonempty=nonempty,
        ),
    )


def _refs(
    value: object, field: str, *, maximum: int = MAX_SEQUENCE_ITEMS, nonempty: bool = False
) -> tuple[str, ...]:
    return cast(
        tuple[str, ...],
        _sequence(
            value,
            field,
            lambda item: _ref(item, f"{field} item"),
            maximum=maximum,
            nonempty=nonempty,
        ),
    )


def _mapping(value: object, fields: frozenset[str]) -> dict[str, object]:
    # JsonObject provides depth/node/width/string/final-byte budgets and cycle rejection.
    safe = cast(dict[str, object], JsonObject.from_value(value).to_dict())
    if frozenset(safe) != fields:
        raise ValueError("wire object must contain the exact contract fields")
    return safe


def _run_id(value: object) -> RunId:
    return RunId.from_string(_text(value, "run_id", maximum=36))


def _timestamp(value: object) -> UtcTimestamp:
    return UtcTimestamp.from_isoformat(_text(value, "timestamp", maximum=27))


def _schema(value: object) -> SchemaVersion:
    parsed = SchemaVersion(_text(value, "schema_version", maximum=14))
    if parsed != SCHEMA_VERSION:
        raise ValueError("unsupported analysis schema version")
    return parsed


def _require_type(value: object, expected: type[object], field: str) -> None:
    if type(value) is not expected:
        raise ValueError(f"{field} requires an exact {expected.__name__} value")


@dataclass(frozen=True, slots=True)
class ContractMeta:
    schema_version: SchemaVersion
    run_id: RunId
    created_at: UtcTimestamp
    producer_version: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"schema_version", "run_id", "created_at", "producer_version"}
    )

    def __post_init__(self) -> None:
        _require_type(self.schema_version, SchemaVersion, "schema_version")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported analysis schema version")
        _require_type(self.run_id, RunId, "run_id")
        _require_type(self.created_at, UtcTimestamp, "created_at")
        object.__setattr__(
            self, "producer_version", _version(self.producer_version, "producer_version")
        )

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "schema_version": str(self.schema_version),
            "run_id": str(self.run_id),
            "created_at": str(self.created_at),
            "producer_version": self.producer_version,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        raw = _mapping(value, cls.FIELDS)
        return cls(
            schema_version=_schema(raw["schema_version"]),
            run_id=_run_id(raw["run_id"]),
            created_at=_timestamp(raw["created_at"]),
            producer_version=_version(raw["producer_version"], "producer_version"),
        )


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    symbol: str
    side: PositionSide
    quantity: Decimal
    signed_weight: Decimal
    average_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl_today: Decimal
    opened_at: UtcTimestamp
    same_day: bool

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "symbol",
            "side",
            "quantity",
            "signed_weight",
            "average_entry_price",
            "current_price",
            "market_value",
            "unrealized_pnl",
            "realized_pnl_today",
            "opened_at",
            "same_day",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _require_type(self.side, PositionSide, "side")
        for field, scale, low, high in (
            ("quantity", 6, Decimal("0.000001"), Decimal("1000000000")),
            ("signed_weight", 6, Decimal("-1"), Decimal("1")),
            ("average_entry_price", 2, Decimal("0.01"), Decimal("10000000")),
            ("current_price", 2, Decimal("0.01"), Decimal("10000000")),
            ("market_value", 2, Decimal("0"), Decimal("1000000000000")),
            ("unrealized_pnl", 2, Decimal("-1000000000000"), Decimal("1000000000000")),
            ("realized_pnl_today", 2, Decimal("-1000000000000"), Decimal("1000000000000")),
        ):
            current = getattr(self, field)
            if (
                type(current) is not Decimal
                or current.as_tuple().exponent != -scale
                or not current.is_finite()
                or not low <= current <= high
            ):
                raise ValueError(f"{field} must be an exact bounded Decimal with scale {scale}")
            if current.is_zero() and current.is_signed():
                raise ValueError(f"{field} must not be negative zero")
        if self.side is PositionSide.LONG and self.signed_weight <= 0:
            raise ValueError("LONG position requires positive signed_weight")
        if self.side is PositionSide.SHORT and self.signed_weight >= 0:
            raise ValueError("SHORT position requires negative signed_weight")
        if self.side is PositionSide.FLAT:
            raise ValueError("snapshot positions cannot be FLAT")
        _require_type(self.opened_at, UtcTimestamp, "opened_at")
        if type(self.same_day) is not bool:
            raise ValueError("same_day must be an exact bool")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": _decimal_text(self.quantity, 6),
            "signed_weight": _decimal_text(self.signed_weight, 6),
            "average_entry_price": _decimal_text(self.average_entry_price, 2),
            "current_price": _decimal_text(self.current_price, 2),
            "market_value": _decimal_text(self.market_value, 2),
            "unrealized_pnl": _decimal_text(self.unrealized_pnl, 2),
            "realized_pnl_today": _decimal_text(self.realized_pnl_today, 2),
            "opened_at": str(self.opened_at),
            "same_day": self.same_day,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        if type(r["same_day"]) is not bool:
            raise ValueError("same_day must be an exact bool")
        return cls(
            symbol=_symbol(r["symbol"]),
            side=cast(PositionSide, _exact_enum(r["side"], PositionSide, "side")),
            quantity=_decimal(
                r["quantity"],
                "quantity",
                scale=6,
                minimum=Decimal("0"),
                maximum=Decimal("1000000000"),
            ),
            signed_weight=_decimal(
                r["signed_weight"],
                "signed_weight",
                scale=6,
                minimum=Decimal("-1"),
                maximum=Decimal("1"),
            ),
            average_entry_price=_decimal(
                r["average_entry_price"],
                "average_entry_price",
                scale=2,
                minimum=Decimal("0.01"),
                maximum=Decimal("10000000"),
            ),
            current_price=_decimal(
                r["current_price"],
                "current_price",
                scale=2,
                minimum=Decimal("0.01"),
                maximum=Decimal("10000000"),
            ),
            market_value=_decimal(
                r["market_value"],
                "market_value",
                scale=2,
                minimum=Decimal("0"),
                maximum=Decimal("1000000000000"),
            ),
            unrealized_pnl=_decimal(
                r["unrealized_pnl"],
                "unrealized_pnl",
                scale=2,
                minimum=Decimal("-1000000000000"),
                maximum=Decimal("1000000000000"),
            ),
            realized_pnl_today=_decimal(
                r["realized_pnl_today"],
                "realized_pnl_today",
                scale=2,
                minimum=Decimal("-1000000000000"),
                maximum=Decimal("1000000000000"),
            ),
            opened_at=_timestamp(r["opened_at"]),
            same_day=r["same_day"],
        )


@dataclass(frozen=True, slots=True)
class OpenOrderSummary:
    reference_id: str
    symbol: str
    side: PositionSide
    remaining_quantity: Decimal

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"reference_id", "symbol", "side", "remaining_quantity"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_id", _ref(self.reference_id, "reference_id"))
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _require_type(self.side, PositionSide, "side")
        if self.side is PositionSide.FLAT:
            raise ValueError("open order side cannot be FLAT")
        if (
            type(self.remaining_quantity) is not Decimal
            or self.remaining_quantity.as_tuple().exponent != -6
            or not self.remaining_quantity.is_finite()
            or self.remaining_quantity <= 0
        ):
            raise ValueError("remaining_quantity must be a positive scale-6 Decimal")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "reference_id": self.reference_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "remaining_quantity": _decimal_text(self.remaining_quantity, 6),
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            _ref(r["reference_id"], "reference_id"),
            _symbol(r["symbol"]),
            cast(PositionSide, _exact_enum(r["side"], PositionSide, "side")),
            _decimal(
                r["remaining_quantity"],
                "remaining_quantity",
                scale=6,
                minimum=Decimal("0.000001"),
                maximum=Decimal("1000000000"),
            ),
        )


@dataclass(frozen=True, slots=True)
class SameDayFillSummary:
    reference_id: str
    symbol: str
    side: PositionSide
    quantity: Decimal
    price: Decimal
    occurred_at: UtcTimestamp

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"reference_id", "symbol", "side", "quantity", "price", "occurred_at"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_id", _ref(self.reference_id, "reference_id"))
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _require_type(self.side, PositionSide, "side")
        if self.side is PositionSide.FLAT:
            raise ValueError("fill side cannot be FLAT")
        if (
            type(self.quantity) is not Decimal
            or self.quantity.as_tuple().exponent != -6
            or not self.quantity.is_finite()
            or self.quantity <= 0
        ):
            raise ValueError("quantity must be a positive scale-6 Decimal")
        if (
            type(self.price) is not Decimal
            or self.price.as_tuple().exponent != -2
            or not self.price.is_finite()
            or self.price <= 0
        ):
            raise ValueError("price must be a positive scale-2 Decimal")
        _require_type(self.occurred_at, UtcTimestamp, "occurred_at")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "reference_id": self.reference_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": _decimal_text(self.quantity, 6),
            "price": _decimal_text(self.price, 2),
            "occurred_at": str(self.occurred_at),
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            _ref(r["reference_id"], "reference_id"),
            _symbol(r["symbol"]),
            cast(PositionSide, _exact_enum(r["side"], PositionSide, "side")),
            _decimal(
                r["quantity"],
                "quantity",
                scale=6,
                minimum=Decimal("0.000001"),
                maximum=Decimal("1000000000"),
            ),
            _decimal(
                r["price"], "price", scale=2, minimum=Decimal("0.01"), maximum=Decimal("10000000")
            ),
            _timestamp(r["occurred_at"]),
        )


@dataclass(frozen=True, slots=True)
class BorrowStatus:
    symbol: str
    availability: BorrowAvailability
    located_quantity: Decimal

    FIELDS: ClassVar[frozenset[str]] = frozenset({"symbol", "availability", "located_quantity"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _require_type(self.availability, BorrowAvailability, "availability")
        if (
            type(self.located_quantity) is not Decimal
            or self.located_quantity.as_tuple().exponent != -6
            or not self.located_quantity.is_finite()
            or self.located_quantity < 0
        ):
            raise ValueError("located_quantity must be a non-negative scale-6 Decimal")
        _reject_negative_zero(self.located_quantity, "located_quantity")
        if self.availability is not BorrowAvailability.AVAILABLE and self.located_quantity != 0:
            raise ValueError("unavailable or unknown borrow must have zero located_quantity")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "symbol": self.symbol,
            "availability": self.availability.value,
            "located_quantity": _decimal_text(self.located_quantity, 6),
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            _symbol(r["symbol"]),
            cast(
                BorrowAvailability,
                _exact_enum(r["availability"], BorrowAvailability, "availability"),
            ),
            _decimal(
                r["located_quantity"],
                "located_quantity",
                scale=6,
                minimum=Decimal("0"),
                maximum=Decimal("1000000000"),
            ),
        )


@dataclass(frozen=True, slots=True)
class RemainingLimits:
    remaining_slots: int
    long_gross_room: Decimal
    short_gross_room: Decimal
    total_gross_room: Decimal
    net_lower_room: Decimal
    net_upper_room: Decimal
    single_name_room: Decimal
    turnover_room: Decimal

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "remaining_slots",
            "long_gross_room",
            "short_gross_room",
            "total_gross_room",
            "net_lower_room",
            "net_upper_room",
            "single_name_room",
            "turnover_room",
        }
    )

    def __post_init__(self) -> None:
        _integer(self.remaining_slots, "remaining_slots", maximum=15)
        for name in self.FIELDS - {"remaining_slots"}:
            value = getattr(self, name)
            if (
                type(value) is not Decimal
                or value.as_tuple().exponent != -6
                or not value.is_finite()
                or not Decimal("-2") <= value <= Decimal("2")
                or (value.is_zero() and value.is_signed())
            ):
                raise ValueError(f"{name} must be a bounded scale-6 Decimal")

    def to_wire(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {"remaining_slots": self.remaining_slots}
        result.update(
            {
                name: _decimal_text(cast(Decimal, getattr(self, name)), 6)
                for name in self.FIELDS - {"remaining_slots"}
            }
        )
        return result

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        kw: dict[str, object] = {
            "remaining_slots": _integer(r["remaining_slots"], "remaining_slots", maximum=15)
        }
        for name in cls.FIELDS - {"remaining_slots"}:
            kw[name] = _decimal(r[name], name, scale=6, minimum=Decimal("-2"), maximum=Decimal("2"))
        return cls(**kw)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    as_of: UtcTimestamp
    nav: Decimal
    cash: Decimal
    buying_power: Decimal
    positions: tuple[PortfolioPosition, ...]
    open_orders: tuple[OpenOrderSummary, ...]
    same_day_fills: tuple[SameDayFillSummary, ...]
    borrow_statuses: tuple[BorrowStatus, ...]
    remaining_limits: RemainingLimits
    content_hash: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "as_of",
            "nav",
            "cash",
            "buying_power",
            "positions",
            "open_orders",
            "same_day_fills",
            "borrow_statuses",
            "remaining_limits",
            "content_hash",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.as_of, UtcTimestamp, "as_of")
        for name in ("nav", "cash", "buying_power"):
            value = getattr(self, name)
            if (
                type(value) is not Decimal
                or value.as_tuple().exponent != -2
                or not value.is_finite()
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative scale-2 Decimal")
        _reject_negative_zero(self.cash, "cash")
        _reject_negative_zero(self.buying_power, "buying_power")
        if self.nav <= 0:
            raise ValueError("nav must be positive")
        for name, item_type, maximum in (
            ("positions", PortfolioPosition, 15),
            ("open_orders", OpenOrderSummary, 64),
            ("same_day_fills", SameDayFillSummary, 128),
            ("borrow_statuses", BorrowStatus, 64),
        ):
            raw = getattr(self, name)
            if (
                type(raw) not in {list, tuple}
                or len(raw) > maximum
                or any(type(item) is not item_type for item in raw)
            ):
                raise ValueError(f"{name} must contain only bounded exact contract values")
            object.__setattr__(self, name, tuple(raw))
        _require_type(self.remaining_limits, RemainingLimits, "remaining_limits")
        for name in ("positions", "open_orders", "same_day_fills", "borrow_statuses"):
            values = getattr(self, name)
            key = "symbol" if name in {"positions", "borrow_statuses"} else "reference_id"
            identifiers = [getattr(item, key) for item in values]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{name} must have unique {key} values")
        _hash(self.content_hash, "content_hash")
        if self.content_hash != self.compute_content_hash():
            raise ValueError("portfolio snapshot content_hash does not match sanitized content")

    def validate_integrity(self) -> None:
        """Re-run nested and aggregate invariants on an already-built snapshot."""
        self.__post_init__()
        for position in self.positions:
            position.__post_init__()
        for order in self.open_orders:
            order.__post_init__()
        for fill in self.same_day_fills:
            fill.__post_init__()
        for borrow in self.borrow_statuses:
            borrow.__post_init__()
        self.remaining_limits.__post_init__()
        self.__post_init__()

    def _content_wire(self) -> dict[str, JsonValue]:
        return {
            "as_of": str(self.as_of),
            "nav": _decimal_text(self.nav, 2),
            "cash": _decimal_text(self.cash, 2),
            "buying_power": _decimal_text(self.buying_power, 2),
            "positions": [item.to_wire() for item in self.positions],
            "open_orders": [item.to_wire() for item in self.open_orders],
            "same_day_fills": [item.to_wire() for item in self.same_day_fills],
            "borrow_statuses": [item.to_wire() for item in self.borrow_statuses],
            "remaining_limits": self.remaining_limits.to_wire(),
        }

    def compute_content_hash(self) -> str:
        return hashlib.sha256(
            JsonObject.from_value(self._content_wire()).to_json().encode()
        ).hexdigest()

    def to_wire(self) -> dict[str, JsonValue]:
        return {**self._content_wire(), "content_hash": self.content_hash}

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)

        def parse_items(name: str, item_type: type[object], maximum: int) -> tuple[object, ...]:
            return _sequence(r[name], name, item_type.from_wire, maximum=maximum, unique=False)  # type: ignore[attr-defined]

        return cls(
            as_of=_timestamp(r["as_of"]),
            nav=_decimal(
                r["nav"], "nav", scale=2, minimum=Decimal("0.01"), maximum=Decimal("1000000000000")
            ),
            cash=_decimal(
                r["cash"], "cash", scale=2, minimum=Decimal("0"), maximum=Decimal("1000000000000")
            ),
            buying_power=_decimal(
                r["buying_power"],
                "buying_power",
                scale=2,
                minimum=Decimal("0"),
                maximum=Decimal("1000000000000"),
            ),
            positions=cast(
                tuple[PortfolioPosition, ...], parse_items("positions", PortfolioPosition, 15)
            ),
            open_orders=cast(
                tuple[OpenOrderSummary, ...], parse_items("open_orders", OpenOrderSummary, 64)
            ),
            same_day_fills=cast(
                tuple[SameDayFillSummary, ...],
                parse_items("same_day_fills", SameDayFillSummary, 128),
            ),
            borrow_statuses=cast(
                tuple[BorrowStatus, ...], parse_items("borrow_statuses", BorrowStatus, 64)
            ),
            remaining_limits=RemainingLimits.from_wire(r["remaining_limits"]),
            content_hash=_hash(r["content_hash"], "content_hash"),
        )


def build_portfolio_snapshot(
    *,
    as_of: UtcTimestamp,
    nav: Decimal,
    cash: Decimal,
    buying_power: Decimal,
    positions: Sequence[PortfolioPosition] = (),
    open_orders: Sequence[OpenOrderSummary] = (),
    same_day_fills: Sequence[SameDayFillSummary] = (),
    borrow_statuses: Sequence[BorrowStatus] = (),
    remaining_limits: RemainingLimits,
) -> PortfolioSnapshot:
    """Build a snapshot while deriving, never trusting, its sanitized content hash."""
    provisional = object.__new__(PortfolioSnapshot)
    for name, value in {
        "as_of": as_of,
        "nav": nav,
        "cash": cash,
        "buying_power": buying_power,
        "positions": tuple(positions),
        "open_orders": tuple(open_orders),
        "same_day_fills": tuple(same_day_fills),
        "borrow_statuses": tuple(borrow_statuses),
        "remaining_limits": remaining_limits,
        "content_hash": "0" * 64,
    }.items():
        object.__setattr__(provisional, name, value)
    content_hash = provisional.compute_content_hash()
    return PortfolioSnapshot(
        as_of,
        nav,
        cash,
        buying_power,
        tuple(positions),
        tuple(open_orders),
        tuple(same_day_fills),
        tuple(borrow_statuses),
        remaining_limits,
        content_hash,
    )


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    meta: ContractMeta
    input_id: RunId
    as_of: UtcTimestamp
    window: AnalysisWindow
    deadline: UtcTimestamp
    portfolio_snapshot: PortfolioSnapshot
    holding_symbols: tuple[str, ...]
    candidate_symbols: tuple[str, ...]
    focus_symbols: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    data_snapshot_refs: tuple[str, ...]
    universe_hash: str

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "input_id",
            "as_of",
            "window",
            "deadline",
            "portfolio_snapshot",
            "holding_symbols",
            "candidate_symbols",
            "focus_symbols",
            "evidence_refs",
            "data_snapshot_refs",
            "universe_hash",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        _require_type(self.input_id, RunId, "input_id")
        _require_type(self.as_of, UtcTimestamp, "as_of")
        _require_type(self.window, AnalysisWindow, "window")
        _require_type(self.deadline, UtcTimestamp, "deadline")
        _require_type(self.portfolio_snapshot, PortfolioSnapshot, "portfolio_snapshot")
        if self.as_of != self.portfolio_snapshot.as_of:
            raise ValueError("analysis input and portfolio snapshot must have the same as_of")
        for name, maximum in (
            ("holding_symbols", 15),
            ("candidate_symbols", 12),
            ("focus_symbols", 27),
        ):
            raw = getattr(self, name)
            parsed = cast(tuple[str, ...], _sequence(raw, name, _symbol, maximum=maximum))
            object.__setattr__(self, name, parsed)
        for name in ("evidence_refs", "data_snapshot_refs"):
            object.__setattr__(self, name, _refs(getattr(self, name), name))
        holdings = {position.symbol for position in self.portfolio_snapshot.positions}
        if set(self.holding_symbols) != holdings:
            raise ValueError(
                "holding_symbols must exactly equal portfolio snapshot position symbols"
            )
        if holdings & set(self.candidate_symbols):
            raise ValueError("candidate symbols must not overlap holdings")
        universe = holdings | set(self.candidate_symbols)
        if not set(self.focus_symbols) <= universe:
            raise ValueError("focus symbols must belong to holdings or candidates")
        elapsed = self.deadline.value - self.as_of.value
        if elapsed.total_seconds() <= 0:
            raise ValueError("deadline must be after as_of")
        if self.window is AnalysisWindow.EMERGENCY:
            if self.candidate_symbols:
                raise ValueError("emergency input must have zero candidates")
            if not self.focus_symbols or not set(self.focus_symbols) <= holdings:
                raise ValueError("emergency focus must be non-empty existing holdings")
            if elapsed.total_seconds() > 180:
                raise ValueError("emergency deadline must be at most three minutes")
        else:
            maximum = 12 if self.window is AnalysisWindow.PRIMARY else 5
            if len(self.candidate_symbols) > maximum:
                raise ValueError("candidate count exceeds the analysis window bound")
            if elapsed.total_seconds() > 900:
                raise ValueError("normal deadline must be at most fifteen minutes")
        _hash(self.universe_hash, "universe_hash")
        if self.universe_hash != self.compute_universe_hash():
            raise ValueError("universe_hash does not match the exact input universe")

    def validate_integrity(self) -> None:
        """Re-run all frozen input and nested snapshot invariants."""
        self.__post_init__()
        _require_type(self.meta, ContractMeta, "meta")
        self.meta.__post_init__()
        self.portfolio_snapshot.validate_integrity()
        self.__post_init__()

    def compute_universe_hash(self) -> str:
        payload = {
            "holdings": list(self.holding_symbols),
            "candidates": list(self.candidate_symbols),
        }
        return hashlib.sha256(JsonObject.from_value(payload).to_json().encode()).hexdigest()

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "input_id": str(self.input_id),
            "as_of": str(self.as_of),
            "window": self.window.value,
            "deadline": str(self.deadline),
            "portfolio_snapshot": self.portfolio_snapshot.to_wire(),
            "holding_symbols": list(self.holding_symbols),
            "candidate_symbols": list(self.candidate_symbols),
            "focus_symbols": list(self.focus_symbols),
            "evidence_refs": list(self.evidence_refs),
            "data_snapshot_refs": list(self.data_snapshot_refs),
            "universe_hash": self.universe_hash,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            meta=ContractMeta.from_wire(r["meta"]),
            input_id=_run_id(r["input_id"]),
            as_of=_timestamp(r["as_of"]),
            window=cast(AnalysisWindow, _exact_enum(r["window"], AnalysisWindow, "window")),
            deadline=_timestamp(r["deadline"]),
            portfolio_snapshot=PortfolioSnapshot.from_wire(r["portfolio_snapshot"]),
            holding_symbols=cast(
                tuple[str, ...],
                _sequence(r["holding_symbols"], "holding_symbols", _symbol, maximum=15),
            ),
            candidate_symbols=cast(
                tuple[str, ...],
                _sequence(r["candidate_symbols"], "candidate_symbols", _symbol, maximum=12),
            ),
            focus_symbols=cast(
                tuple[str, ...], _sequence(r["focus_symbols"], "focus_symbols", _symbol, maximum=27)
            ),
            evidence_refs=_refs(r["evidence_refs"], "evidence_refs"),
            data_snapshot_refs=_refs(r["data_snapshot_refs"], "data_snapshot_refs"),
            universe_hash=_hash(r["universe_hash"], "universe_hash"),
        )


def build_analysis_input(
    *,
    meta: ContractMeta,
    input_id: RunId,
    as_of: UtcTimestamp,
    window: AnalysisWindow,
    deadline: UtcTimestamp,
    portfolio_snapshot: PortfolioSnapshot,
    holding_symbols: Sequence[str],
    candidate_symbols: Sequence[str],
    focus_symbols: Sequence[str],
    evidence_refs: Sequence[str],
    data_snapshot_refs: Sequence[str],
) -> AnalysisInput:
    provisional = object.__new__(AnalysisInput)
    for name, value in {
        "meta": meta,
        "input_id": input_id,
        "as_of": as_of,
        "window": window,
        "deadline": deadline,
        "portfolio_snapshot": portfolio_snapshot,
        "holding_symbols": tuple(holding_symbols),
        "candidate_symbols": tuple(candidate_symbols),
        "focus_symbols": tuple(focus_symbols),
        "evidence_refs": tuple(evidence_refs),
        "data_snapshot_refs": tuple(data_snapshot_refs),
        "universe_hash": "0" * 64,
    }.items():
        object.__setattr__(provisional, name, value)
    return AnalysisInput(
        meta,
        input_id,
        as_of,
        window,
        deadline,
        portfolio_snapshot,
        tuple(holding_symbols),
        tuple(candidate_symbols),
        tuple(focus_symbols),
        tuple(evidence_refs),
        tuple(data_snapshot_refs),
        provisional.compute_universe_hash(),
    )


@dataclass(frozen=True, slots=True)
class AnalystReport:
    meta: ContractMeta
    report_id: RunId
    input_id: RunId
    role: AnalystRole
    symbol: str
    status: AnalysisStatus
    summary: str
    observations: tuple[str, ...]
    material_claims: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    counterevidence_refs: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    risks: tuple[str, ...]
    catalysts: tuple[str, ...]
    invalidators: tuple[str, ...]
    confidence: Decimal

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "report_id",
            "input_id",
            "role",
            "symbol",
            "status",
            "summary",
            "observations",
            "material_claims",
            "evidence_refs",
            "counterevidence_refs",
            "missing_evidence",
            "risks",
            "catalysts",
            "invalidators",
            "confidence",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        _require_type(self.report_id, RunId, "report_id")
        _require_type(self.input_id, RunId, "input_id")
        _require_type(self.role, AnalystRole, "role")
        _require_type(self.status, AnalysisStatus, "status")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        for name in (
            "observations",
            "material_claims",
            "missing_evidence",
            "risks",
            "catalysts",
            "invalidators",
        ):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        for name in ("evidence_refs", "counterevidence_refs"):
            object.__setattr__(self, name, _refs(getattr(self, name), name))
        if (
            type(self.confidence) is not Decimal
            or self.confidence.as_tuple().exponent != -4
            or not self.confidence.is_finite()
            or not Decimal("0") <= self.confidence <= Decimal("1")
        ):
            raise ValueError("confidence must be a scale-4 Decimal from zero to one")
        _reject_negative_zero(self.confidence, "confidence")
        if self.status is AnalysisStatus.VALID and not self.material_claims:
            raise ValueError("VALID analyst report requires material claims")
        if self.status is not AnalysisStatus.VALID and self.confidence != 0:
            raise ValueError("INVALID or ABSTAIN analyst report confidence must be zero")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "report_id": str(self.report_id),
            "input_id": str(self.input_id),
            "role": self.role.value,
            "symbol": self.symbol,
            "status": self.status.value,
            "summary": self.summary,
            "observations": list(self.observations),
            "material_claims": list(self.material_claims),
            "evidence_refs": list(self.evidence_refs),
            "counterevidence_refs": list(self.counterevidence_refs),
            "missing_evidence": list(self.missing_evidence),
            "risks": list(self.risks),
            "catalysts": list(self.catalysts),
            "invalidators": list(self.invalidators),
            "confidence": _decimal_text(self.confidence, 4),
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["report_id"]),
            _run_id(r["input_id"]),
            cast(AnalystRole, _exact_enum(r["role"], AnalystRole, "role")),
            _symbol(r["symbol"]),
            cast(AnalysisStatus, _exact_enum(r["status"], AnalysisStatus, "status")),
            _text(r["summary"], "summary"),
            _strings(r["observations"], "observations"),
            _strings(r["material_claims"], "material_claims"),
            _refs(r["evidence_refs"], "evidence_refs"),
            _refs(r["counterevidence_refs"], "counterevidence_refs"),
            _strings(r["missing_evidence"], "missing_evidence"),
            _strings(r["risks"], "risks"),
            _strings(r["catalysts"], "catalysts"),
            _strings(r["invalidators"], "invalidators"),
            _decimal(
                r["confidence"], "confidence", scale=4, minimum=Decimal("0"), maximum=Decimal("1")
            ),
        )


@dataclass(frozen=True, slots=True)
class InvestmentDebateState:
    meta: ContractMeta
    debate_id: RunId
    input_id: RunId
    symbol: str
    bull_arguments: tuple[str, ...]
    bear_arguments: tuple[str, ...]
    verified_claims: tuple[str, ...]
    disputed_claims: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    round_count: int
    complete: bool

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "debate_id",
            "input_id",
            "symbol",
            "bull_arguments",
            "bear_arguments",
            "verified_claims",
            "disputed_claims",
            "unresolved_conflicts",
            "round_count",
            "complete",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        _require_type(self.debate_id, RunId, "debate_id")
        _require_type(self.input_id, RunId, "input_id")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        for name in (
            "bull_arguments",
            "bear_arguments",
            "unresolved_conflicts",
        ):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        for name in ("verified_claims", "disputed_claims"):
            object.__setattr__(self, name, _refs(getattr(self, name), name))
        _integer(self.round_count, "round_count", maximum=2)
        if type(self.complete) is not bool:
            raise ValueError("complete must be an exact bool")
        if self.complete != (self.round_count == 2):
            raise ValueError("complete debate state requires exactly two rounds")
        if self.round_count > 0 and (not self.bull_arguments or not self.bear_arguments):
            raise ValueError("started investment debate requires bull and bear arguments")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "debate_id": str(self.debate_id),
            "input_id": str(self.input_id),
            "symbol": self.symbol,
            "bull_arguments": list(self.bull_arguments),
            "bear_arguments": list(self.bear_arguments),
            "verified_claims": list(self.verified_claims),
            "disputed_claims": list(self.disputed_claims),
            "unresolved_conflicts": list(self.unresolved_conflicts),
            "round_count": self.round_count,
            "complete": self.complete,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        if type(r["complete"]) is not bool:
            raise ValueError("complete must be an exact bool")
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["debate_id"]),
            _run_id(r["input_id"]),
            _symbol(r["symbol"]),
            _strings(r["bull_arguments"], "bull_arguments"),
            _strings(r["bear_arguments"], "bear_arguments"),
            _refs(r["verified_claims"], "verified_claims"),
            _refs(r["disputed_claims"], "disputed_claims"),
            _strings(r["unresolved_conflicts"], "unresolved_conflicts"),
            _integer(r["round_count"], "round_count", maximum=2),
            r["complete"],
        )


@dataclass(frozen=True, slots=True)
class ResearchConclusion:
    meta: ContractMeta
    conclusion_id: RunId
    input_id: RunId
    symbol: str
    rating: ResearchRating
    summary: str
    drivers: tuple[str, ...]
    risks: tuple[str, ...]
    invalidators: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    confidence: Decimal
    status: AnalysisStatus

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "conclusion_id",
            "input_id",
            "symbol",
            "rating",
            "summary",
            "drivers",
            "risks",
            "invalidators",
            "evidence_refs",
            "confidence",
            "status",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        _require_type(self.conclusion_id, RunId, "conclusion_id")
        _require_type(self.input_id, RunId, "input_id")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _require_type(self.rating, ResearchRating, "rating")
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        _require_type(self.status, AnalysisStatus, "status")
        for name in ("drivers", "risks", "invalidators"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))
        if (
            type(self.confidence) is not Decimal
            or self.confidence.as_tuple().exponent != -4
            or not self.confidence.is_finite()
            or not Decimal("0") <= self.confidence <= Decimal("1")
        ):
            raise ValueError("confidence must be a scale-4 Decimal from zero to one")
        _reject_negative_zero(self.confidence, "confidence")
        if self.status is not AnalysisStatus.VALID and self.confidence != 0:
            raise ValueError("non-VALID conclusion confidence must be zero")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "conclusion_id": str(self.conclusion_id),
            "input_id": str(self.input_id),
            "symbol": self.symbol,
            "rating": self.rating.value,
            "summary": self.summary,
            "drivers": list(self.drivers),
            "risks": list(self.risks),
            "invalidators": list(self.invalidators),
            "evidence_refs": list(self.evidence_refs),
            "confidence": _decimal_text(self.confidence, 4),
            "status": self.status.value,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["conclusion_id"]),
            _run_id(r["input_id"]),
            _symbol(r["symbol"]),
            cast(ResearchRating, _exact_enum(r["rating"], ResearchRating, "rating")),
            _text(r["summary"], "summary"),
            _strings(r["drivers"], "drivers"),
            _strings(r["risks"], "risks"),
            _strings(r["invalidators"], "invalidators"),
            _refs(r["evidence_refs"], "evidence_refs"),
            _decimal(
                r["confidence"], "confidence", scale=4, minimum=Decimal("0"), maximum=Decimal("1")
            ),
            cast(AnalysisStatus, _exact_enum(r["status"], AnalysisStatus, "status")),
        )


@dataclass(frozen=True, slots=True)
class TraderPlan:
    meta: ContractMeta
    plan_id: RunId
    input_id: RunId
    symbol: str
    rating: ResearchRating
    reason_codes: tuple[ProposalReasonCode, ...]
    evidence_refs: tuple[str, ...]
    entry_band_low: Decimal | None
    entry_band_high: Decimal | None
    downside_band: Decimal | None
    status: AnalysisStatus

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "plan_id",
            "input_id",
            "symbol",
            "rating",
            "reason_codes",
            "evidence_refs",
            "entry_band_low",
            "entry_band_high",
            "downside_band",
            "status",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        _require_type(self.plan_id, RunId, "plan_id")
        _require_type(self.input_id, RunId, "input_id")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _require_type(self.rating, ResearchRating, "rating")
        _require_type(self.status, AnalysisStatus, "status")
        parsed = cast(
            tuple[ProposalReasonCode, ...],
            _sequence(
                self.reason_codes,
                "reason_codes",
                lambda x: (
                    x
                    if type(x) is ProposalReasonCode
                    else (_ for _ in ()).throw(
                        ValueError("reason_codes requires exact enum values")
                    )
                ),
                maximum=6,
                nonempty=True,
            ),
        )
        object.__setattr__(self, "reason_codes", parsed)
        object.__setattr__(
            self,
            "evidence_refs",
            _refs(
                self.evidence_refs, "evidence_refs", nonempty=self.status is AnalysisStatus.VALID
            ),
        )
        for name in ("entry_band_low", "entry_band_high", "downside_band"):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not Decimal
                or value.as_tuple().exponent != -2
                or not value.is_finite()
                or value <= 0
            ):
                raise ValueError(f"{name} must be null or a positive scale-2 Decimal")
        if (self.entry_band_low is None) != (self.entry_band_high is None):
            raise ValueError("entry band low and high must appear together")
        if (
            self.entry_band_low is not None
            and self.entry_band_high is not None
            and self.entry_band_low > self.entry_band_high
        ):
            raise ValueError("entry band low must not exceed high")

    def to_wire(self) -> dict[str, JsonValue]:
        def price(value: Decimal | None) -> str | None:
            return None if value is None else _decimal_text(value, 2)

        return {
            "meta": self.meta.to_wire(),
            "plan_id": str(self.plan_id),
            "input_id": str(self.input_id),
            "symbol": self.symbol,
            "rating": self.rating.value,
            "reason_codes": [x.value for x in self.reason_codes],
            "evidence_refs": list(self.evidence_refs),
            "entry_band_low": price(self.entry_band_low),
            "entry_band_high": price(self.entry_band_high),
            "downside_band": price(self.downside_band),
            "status": self.status.value,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)

        def price(name: str) -> Decimal | None:
            return (
                None
                if r[name] is None
                else _decimal(
                    r[name], name, scale=2, minimum=Decimal("0.01"), maximum=Decimal("10000000")
                )
            )

        reasons = cast(
            tuple[ProposalReasonCode, ...],
            _sequence(
                r["reason_codes"],
                "reason_codes",
                lambda x: _exact_enum(x, ProposalReasonCode, "reason_code"),
                maximum=6,
                nonempty=True,
            ),
        )
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["plan_id"]),
            _run_id(r["input_id"]),
            _symbol(r["symbol"]),
            cast(ResearchRating, _exact_enum(r["rating"], ResearchRating, "rating")),
            reasons,
            _refs(r["evidence_refs"], "evidence_refs"),
            price("entry_band_low"),
            price("entry_band_high"),
            price("downside_band"),
            cast(AnalysisStatus, _exact_enum(r["status"], AnalysisStatus, "status")),
        )


@dataclass(frozen=True, slots=True)
class RiskDebateState:
    meta: ContractMeta
    debate_id: RunId
    input_id: RunId
    aggressive_arguments: tuple[str, ...]
    conservative_arguments: tuple[str, ...]
    neutral_arguments: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    round_count: int
    complete: bool

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "debate_id",
            "input_id",
            "aggressive_arguments",
            "conservative_arguments",
            "neutral_arguments",
            "unresolved_conflicts",
            "round_count",
            "complete",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        _require_type(self.debate_id, RunId, "debate_id")
        _require_type(self.input_id, RunId, "input_id")
        for name in (
            "aggressive_arguments",
            "conservative_arguments",
            "neutral_arguments",
            "unresolved_conflicts",
        ):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        _integer(self.round_count, "round_count", maximum=2)
        if type(self.complete) is not bool:
            raise ValueError("complete must be an exact bool")
        if self.complete != (self.round_count == 2):
            raise ValueError("complete debate state requires exactly two rounds")
        if self.round_count > 0 and (
            not self.aggressive_arguments
            or not self.conservative_arguments
            or not self.neutral_arguments
        ):
            raise ValueError("started risk debate requires all three viewpoints")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "debate_id": str(self.debate_id),
            "input_id": str(self.input_id),
            "aggressive_arguments": list(self.aggressive_arguments),
            "conservative_arguments": list(self.conservative_arguments),
            "neutral_arguments": list(self.neutral_arguments),
            "unresolved_conflicts": list(self.unresolved_conflicts),
            "round_count": self.round_count,
            "complete": self.complete,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        if type(r["complete"]) is not bool:
            raise ValueError("complete must be an exact bool")
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["debate_id"]),
            _run_id(r["input_id"]),
            _strings(r["aggressive_arguments"], "aggressive_arguments"),
            _strings(r["conservative_arguments"], "conservative_arguments"),
            _strings(r["neutral_arguments"], "neutral_arguments"),
            _strings(r["unresolved_conflicts"], "unresolved_conflicts"),
            _integer(r["round_count"], "round_count", maximum=2),
            r["complete"],
        )


@dataclass(frozen=True, slots=True)
class PortfolioRequest:
    symbol: str
    action: ProposalAction
    side: PositionSide
    target_weight: Decimal
    confidence: Decimal
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[ProposalReasonCode, ...]
    invalidators: tuple[str, ...]
    same_day_exit_reason: SameDayExitReason | None = None

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "symbol",
            "action",
            "side",
            "target_weight",
            "confidence",
            "evidence_refs",
            "reason_codes",
            "invalidators",
            "same_day_exit_reason",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        _require_type(self.action, ProposalAction, "action")
        _require_type(self.side, PositionSide, "side")
        if (
            type(self.target_weight) is not Decimal
            or self.target_weight.as_tuple().exponent != -6
            or not self.target_weight.is_finite()
            or not Decimal("-0.150000") <= self.target_weight <= Decimal("0.150000")
        ):
            raise ValueError("target_weight must be a canonical scale-6 Decimal within +/-0.15")
        if self.target_weight.is_zero() and self.target_weight.is_signed():
            raise ValueError("target_weight must not be negative zero")
        if (
            type(self.confidence) is not Decimal
            or self.confidence.as_tuple().exponent != -4
            or not self.confidence.is_finite()
            or not Decimal("0") <= self.confidence <= Decimal("1")
        ):
            raise ValueError("confidence must be a scale-4 Decimal from zero to one")
        _reject_negative_zero(self.confidence, "confidence")
        object.__setattr__(
            self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs", nonempty=True)
        )
        parsed = cast(
            tuple[ProposalReasonCode, ...],
            _sequence(
                self.reason_codes,
                "reason_codes",
                lambda x: (
                    x
                    if type(x) is ProposalReasonCode
                    else (_ for _ in ()).throw(
                        ValueError("reason_codes requires exact enum values")
                    )
                ),
                maximum=6,
                nonempty=True,
            ),
        )
        object.__setattr__(self, "reason_codes", parsed)
        object.__setattr__(self, "invalidators", _strings(self.invalidators, "invalidators"))
        if (
            self.same_day_exit_reason is not None
            and type(self.same_day_exit_reason) is not SameDayExitReason
        ):
            raise ValueError("same_day_exit_reason requires an exact enum or None")
        if self.side is PositionSide.LONG and self.target_weight <= 0:
            raise ValueError("LONG request requires positive target weight")
        if self.side is PositionSide.SHORT and self.target_weight >= 0:
            raise ValueError("SHORT request requires negative target weight")
        if self.side is PositionSide.FLAT and self.target_weight != 0:
            raise ValueError("FLAT request requires zero target weight")
        if self.action is ProposalAction.CLOSE and (
            self.side is not PositionSide.FLAT or self.target_weight != 0
        ):
            raise ValueError("CLOSE requires FLAT and zero target weight")
        if self.action in {ProposalAction.OPEN, ProposalAction.INCREASE} and (
            self.side is PositionSide.FLAT or self.target_weight == 0
        ):
            raise ValueError("OPEN or INCREASE requires directional non-zero target")
        if self.action is ProposalAction.HOLD and self.confidence < Decimal("0.6500"):
            pass
        elif self.confidence < Decimal("0.6500"):
            raise ValueError("confidence below 0.65 permits HOLD only")
        if self.same_day_exit_reason is not None and self.action not in {
            ProposalAction.REDUCE,
            ProposalAction.CLOSE,
        }:
            raise ValueError("same-day exit reason is valid only for REDUCE or CLOSE")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "symbol": self.symbol,
            "action": self.action.value,
            "side": self.side.value,
            "target_weight": _decimal_text(self.target_weight, 6),
            "confidence": _decimal_text(self.confidence, 4),
            "evidence_refs": list(self.evidence_refs),
            "reason_codes": [x.value for x in self.reason_codes],
            "invalidators": list(self.invalidators),
            "same_day_exit_reason": None
            if self.same_day_exit_reason is None
            else self.same_day_exit_reason.value,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        same_day = (
            None
            if r["same_day_exit_reason"] is None
            else cast(
                SameDayExitReason,
                _exact_enum(r["same_day_exit_reason"], SameDayExitReason, "same_day_exit_reason"),
            )
        )
        reasons = cast(
            tuple[ProposalReasonCode, ...],
            _sequence(
                r["reason_codes"],
                "reason_codes",
                lambda x: _exact_enum(x, ProposalReasonCode, "reason_code"),
                maximum=6,
                nonempty=True,
            ),
        )
        return cls(
            _symbol(r["symbol"]),
            cast(ProposalAction, _exact_enum(r["action"], ProposalAction, "action")),
            cast(PositionSide, _exact_enum(r["side"], PositionSide, "side")),
            _decimal(
                r["target_weight"],
                "target_weight",
                scale=6,
                minimum=Decimal("-0.15"),
                maximum=Decimal("0.15"),
            ),
            _decimal(
                r["confidence"], "confidence", scale=4, minimum=Decimal("0"), maximum=Decimal("1")
            ),
            _refs(r["evidence_refs"], "evidence_refs", nonempty=True),
            reasons,
            _strings(r["invalidators"], "invalidators"),
            same_day,
        )


@dataclass(frozen=True, slots=True)
class PortfolioProposal:
    meta: ContractMeta
    proposal_id: RunId
    attempt: int
    superseded_proposal_id: RunId | None
    analysis_input_id: RunId
    universe_hash: str
    snapshot_hash: str
    window: AnalysisWindow
    requests: tuple[PortfolioRequest, ...]
    graph_version: str
    prompt_version: str
    model_version: str
    provider_version: str
    data_version: str
    memory_version: str
    expiration_at: UtcTimestamp
    status: AnalysisStatus

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "proposal_id",
            "attempt",
            "superseded_proposal_id",
            "analysis_input_id",
            "universe_hash",
            "snapshot_hash",
            "window",
            "requests",
            "graph_version",
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
            "expiration_at",
            "status",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        _require_type(self.proposal_id, RunId, "proposal_id")
        _integer(self.attempt, "attempt", minimum=1, maximum=2)
        _require_type(self.analysis_input_id, RunId, "analysis_input_id")
        if self.superseded_proposal_id is not None:
            _require_type(self.superseded_proposal_id, RunId, "superseded_proposal_id")
        if (self.attempt == 1) != (self.superseded_proposal_id is None):
            raise ValueError("attempt 1 has no superseded id; attempt 2 requires one")
        if self.superseded_proposal_id == self.proposal_id:
            raise ValueError("proposal cannot supersede itself")
        _hash(self.universe_hash, "universe_hash")
        _hash(self.snapshot_hash, "snapshot_hash")
        _require_type(self.window, AnalysisWindow, "window")
        if (
            type(self.requests) not in {list, tuple}
            or len(self.requests) > 27
            or any(type(x) is not PortfolioRequest for x in self.requests)
        ):
            raise ValueError("requests must be bounded exact PortfolioRequest values")
        object.__setattr__(self, "requests", tuple(self.requests))
        symbols = [request.symbol for request in self.requests]
        if len(symbols) != len(set(symbols)):
            raise ValueError("proposal request symbols must be unique")
        for name in (
            "graph_version",
            "prompt_version",
            "model_version",
            "provider_version",
            "data_version",
            "memory_version",
        ):
            object.__setattr__(self, name, _version(getattr(self, name), name))
        _require_type(self.expiration_at, UtcTimestamp, "expiration_at")
        _require_type(self.status, AnalysisStatus, "status")
        if self.status is AnalysisStatus.VALID and not self.requests:
            raise ValueError("VALID proposal requires at least one request")
        if self.status is not AnalysisStatus.VALID and self.requests:
            raise ValueError("INVALID or ABSTAIN proposal must not contain requests")

    def validate_against(self, analysis_input: AnalysisInput) -> None:
        """Prove exact input identity, snapshot, window and request-universe membership."""
        _require_type(analysis_input, AnalysisInput, "analysis_input")
        if (
            self.analysis_input_id != analysis_input.input_id
            or self.universe_hash != analysis_input.universe_hash
            or self.snapshot_hash != analysis_input.portfolio_snapshot.content_hash
            or self.window is not analysis_input.window
        ):
            raise ValueError("proposal does not match the exact analysis input boundary")
        allowed = set(analysis_input.holding_symbols) | set(analysis_input.candidate_symbols)
        if any(request.symbol not in allowed for request in self.requests):
            raise ValueError("proposal request symbol is outside the analysis input universe")
        if analysis_input.window is AnalysisWindow.EMERGENCY and any(
            request.action in {ProposalAction.OPEN, ProposalAction.INCREASE}
            for request in self.requests
        ):
            raise ValueError("emergency proposal cannot open or increase exposure")
        if self.expiration_at.value > analysis_input.deadline.value:
            raise ValueError("proposal expiration cannot exceed analysis deadline")

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "proposal_id": str(self.proposal_id),
            "attempt": self.attempt,
            "superseded_proposal_id": None
            if self.superseded_proposal_id is None
            else str(self.superseded_proposal_id),
            "analysis_input_id": str(self.analysis_input_id),
            "universe_hash": self.universe_hash,
            "snapshot_hash": self.snapshot_hash,
            "window": self.window.value,
            "requests": [x.to_wire() for x in self.requests],
            "graph_version": self.graph_version,
            "prompt_version": self.prompt_version,
            "model_version": self.model_version,
            "provider_version": self.provider_version,
            "data_version": self.data_version,
            "memory_version": self.memory_version,
            "expiration_at": str(self.expiration_at),
            "status": self.status.value,
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        superseded = (
            None if r["superseded_proposal_id"] is None else _run_id(r["superseded_proposal_id"])
        )
        requests = cast(
            tuple[PortfolioRequest, ...],
            _sequence(
                r["requests"], "requests", PortfolioRequest.from_wire, maximum=27, unique=False
            ),
        )
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["proposal_id"]),
            _integer(r["attempt"], "attempt", minimum=1, maximum=2),
            superseded,
            _run_id(r["analysis_input_id"]),
            _hash(r["universe_hash"], "universe_hash"),
            _hash(r["snapshot_hash"], "snapshot_hash"),
            cast(AnalysisWindow, _exact_enum(r["window"], AnalysisWindow, "window")),
            requests,
            _version(r["graph_version"], "graph_version"),
            _version(r["prompt_version"], "prompt_version"),
            _version(r["model_version"], "model_version"),
            _version(r["provider_version"], "provider_version"),
            _version(r["data_version"], "data_version"),
            _version(r["memory_version"], "memory_version"),
            _timestamp(r["expiration_at"]),
            cast(AnalysisStatus, _exact_enum(r["status"], AnalysisStatus, "status")),
        )


@dataclass(frozen=True, slots=True)
class RiskRejectionFeedback:
    meta: ContractMeta
    rejected_proposal_id: RunId
    review_round: int
    rejection_codes: tuple[RiskRejectionCode, ...]
    rejected_symbols: tuple[str, ...]
    remaining_limits: RemainingLimits
    constraints_snapshot_hash: str
    reviewed_at: UtcTimestamp

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "meta",
            "rejected_proposal_id",
            "review_round",
            "rejection_codes",
            "rejected_symbols",
            "remaining_limits",
            "constraints_snapshot_hash",
            "reviewed_at",
        }
    )

    def __post_init__(self) -> None:
        _require_type(self.meta, ContractMeta, "meta")
        _require_type(self.rejected_proposal_id, RunId, "rejected_proposal_id")
        if type(self.review_round) is not int or self.review_round != 1:
            raise ValueError("Risk rejection feedback review_round is fixed at 1")
        codes = cast(
            tuple[RiskRejectionCode, ...],
            _sequence(
                self.rejection_codes,
                "rejection_codes",
                lambda x: (
                    x
                    if type(x) is RiskRejectionCode
                    else (_ for _ in ()).throw(
                        ValueError("rejection_codes requires exact enum values")
                    )
                ),
                maximum=15,
                nonempty=True,
            ),
        )
        object.__setattr__(self, "rejection_codes", codes)
        object.__setattr__(
            self,
            "rejected_symbols",
            cast(
                tuple[str, ...],
                _sequence(self.rejected_symbols, "rejected_symbols", _symbol, maximum=27),
            ),
        )
        _require_type(self.remaining_limits, RemainingLimits, "remaining_limits")
        _hash(self.constraints_snapshot_hash, "constraints_snapshot_hash")
        _require_type(self.reviewed_at, UtcTimestamp, "reviewed_at")

    def validate_integrity(self) -> None:
        """Re-run nested feedback invariants at an authority boundary."""
        _require_type(self.meta, ContractMeta, "meta")
        self.meta.__post_init__()
        self.remaining_limits.__post_init__()
        self.__post_init__()

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "meta": self.meta.to_wire(),
            "rejected_proposal_id": str(self.rejected_proposal_id),
            "review_round": self.review_round,
            "rejection_codes": [x.value for x in self.rejection_codes],
            "rejected_symbols": list(self.rejected_symbols),
            "remaining_limits": self.remaining_limits.to_wire(),
            "constraints_snapshot_hash": self.constraints_snapshot_hash,
            "reviewed_at": str(self.reviewed_at),
        }

    @classmethod
    def from_wire(cls, value: object) -> Self:
        r = _mapping(value, cls.FIELDS)
        codes = cast(
            tuple[RiskRejectionCode, ...],
            _sequence(
                r["rejection_codes"],
                "rejection_codes",
                lambda x: _exact_enum(x, RiskRejectionCode, "rejection_code"),
                maximum=15,
                nonempty=True,
            ),
        )
        return cls(
            ContractMeta.from_wire(r["meta"]),
            _run_id(r["rejected_proposal_id"]),
            _integer(r["review_round"], "review_round", minimum=1, maximum=1),
            codes,
            cast(
                tuple[str, ...],
                _sequence(r["rejected_symbols"], "rejected_symbols", _symbol, maximum=27),
            ),
            RemainingLimits.from_wire(r["remaining_limits"]),
            _hash(r["constraints_snapshot_hash"], "constraints_snapshot_hash"),
            _timestamp(r["reviewed_at"]),
        )


def canonical_wire_json(contract: object) -> str:
    """Return canonical, resource-bounded JSON for any supported P3-A contract."""
    method = getattr(contract, "to_wire", None)
    if method is None or not callable(method):
        raise ValueError("contract must provide to_wire")
    return JsonObject.from_value(method()).to_json()
