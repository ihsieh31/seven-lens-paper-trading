"""Strict, dependency-free value objects used by the domain layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

_SCHEMA_VERSION_COMPONENT_MAX = 9_999
_SCHEMA_VERSION_COMPONENT = r"(?:0|[1-9][0-9]{0,3})"
_SCHEMA_VERSION_PATTERN = re.compile(
    rf"^{_SCHEMA_VERSION_COMPONENT}\.{_SCHEMA_VERSION_COMPONENT}\.{_SCHEMA_VERSION_COMPONENT}$"
)
_TRADING_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass(frozen=True, slots=True)
class RunId:
    """A non-nil UUID that identifies one immutable processing run."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise ValueError("RunId value must be a UUID")
        if self.value.int == 0:
            raise ValueError("RunId must not be the nil UUID")

    @classmethod
    def new(cls) -> RunId:
        """Create a new random UUID-backed run identifier."""
        return cls(uuid4())

    @classmethod
    def from_string(cls, value: str) -> RunId:
        """Parse only canonical UUID text, rejecting ambiguous representations."""
        if not isinstance(value, str):
            raise ValueError("RunId text must be a string")
        try:
            parsed = UUID(value)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("RunId text must be a valid UUID") from error
        if str(parsed) != value:
            raise ValueError("RunId text must use canonical lowercase UUID format")
        return cls(parsed)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class TradingDate:
    """A calendar date used for trading-session identity, never a datetime."""

    value: date

    def __post_init__(self) -> None:
        if not isinstance(self.value, date) or isinstance(self.value, datetime):
            raise ValueError("TradingDate value must be a date, not a datetime")

    @classmethod
    def from_isoformat(cls, value: str) -> TradingDate:
        """Parse a complete ISO-8601 calendar date."""
        if not isinstance(value, str) or _TRADING_DATE_PATTERN.fullmatch(value) is None:
            raise ValueError("TradingDate text must use YYYY-MM-DD format")
        try:
            return cls(date.fromisoformat(value))
        except ValueError as error:
            raise ValueError("TradingDate text must be a valid calendar date") from error

    def __str__(self) -> str:
        return self.value.isoformat()


@dataclass(frozen=True, slots=True)
class UtcTimestamp:
    """A timezone-aware datetime whose domain and storage semantics are UTC."""

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):
            raise ValueError("UtcTimestamp value must be a datetime")
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("UtcTimestamp must be timezone-aware")
        if self.value.utcoffset() != timedelta(0):
            raise ValueError("UtcTimestamp must use UTC")

    @classmethod
    def from_isoformat(cls, value: str) -> UtcTimestamp:
        """Parse only the canonical UTC wire format with fixed microseconds and ``Z``."""
        if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
            raise ValueError("UtcTimestamp text must use YYYY-MM-DDTHH:MM:SS.ffffffZ")
        try:
            return cls(datetime.strptime(value, _UTC_TIMESTAMP_FORMAT).replace(tzinfo=UTC))
        except ValueError as error:
            raise ValueError("UtcTimestamp text must be a valid canonical UTC timestamp") from error

    @classmethod
    def now(cls) -> UtcTimestamp:
        """Return the current timezone-aware UTC timestamp."""
        return cls(datetime.now(UTC))

    def __str__(self) -> str:
        return self.value.astimezone(UTC).strftime(_UTC_TIMESTAMP_FORMAT)


@dataclass(frozen=True, slots=True)
class SchemaVersion:
    """A strict MAJOR.MINOR.PATCH schema contract version."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or _SCHEMA_VERSION_PATTERN.fullmatch(self.value) is None:
            raise ValueError(
                "SchemaVersion must use MAJOR.MINOR.PATCH with components from 0 to "
                f"{_SCHEMA_VERSION_COMPONENT_MAX}"
            )

    @property
    def major(self) -> int:
        return int(self.value.split(".")[0])

    @property
    def minor(self) -> int:
        return int(self.value.split(".")[1])

    @property
    def patch(self) -> int:
        return int(self.value.split(".")[2])

    def __str__(self) -> str:
        return self.value
