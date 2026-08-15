"""Pure market-clock port and deterministic fake used by tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seven_lens.domain.value_objects import TradingDate, UtcTimestamp


class MarketClockUnavailableError(LookupError):
    """Raised when a date has no explicit calendar record (fail closed)."""


class MarketDayKind(StrEnum):
    REGULAR = "REGULAR"
    HALF_DAY = "HALF_DAY"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class RegularSessionWindow:
    opens_at: UtcTimestamp
    closes_at: UtcTimestamp

    def __post_init__(self) -> None:
        if not isinstance(self.opens_at, UtcTimestamp) or not isinstance(
            self.closes_at, UtcTimestamp
        ):
            raise ValueError("regular-session boundaries must be UtcTimestamp values")
        if self.opens_at.value >= self.closes_at.value:
            raise ValueError("regular session must close after it opens")

    def contains(self, instant: UtcTimestamp) -> bool:
        """Use the conventional half-open interval ``[open, close)``."""
        if not isinstance(instant, UtcTimestamp):
            raise ValueError("instant must be a UtcTimestamp")
        return self.opens_at.value <= instant.value < self.closes_at.value


@dataclass(frozen=True, slots=True)
class MarketSession:
    trading_date: TradingDate
    day_kind: MarketDayKind
    regular_session: RegularSessionWindow | None

    def __post_init__(self) -> None:
        if not isinstance(self.trading_date, TradingDate):
            raise ValueError("trading_date must be a TradingDate")
        if not isinstance(self.day_kind, MarketDayKind):
            raise ValueError("day_kind must be a MarketDayKind")
        if self.day_kind is MarketDayKind.CLOSED:
            if self.regular_session is not None:
                raise ValueError("a closed day cannot have a regular-session window")
        elif not isinstance(self.regular_session, RegularSessionWindow):
            raise ValueError("an open market day requires a regular-session window")

    @property
    def is_closed(self) -> bool:
        return self.day_kind is MarketDayKind.CLOSED

    def is_regular_session(self, instant: UtcTimestamp) -> bool:
        return self.regular_session is not None and self.regular_session.contains(instant)


class MarketClock(Protocol):
    """Application port for an authoritative exchange calendar implementation."""

    def session_for(self, trading_date: TradingDate) -> MarketSession: ...


class FakeMarketClock:
    """Deterministic fake requiring an explicit record for every queried date."""

    def __init__(self, sessions: tuple[MarketSession, ...]) -> None:
        by_date: dict[TradingDate, MarketSession] = {}
        for session in sessions:
            if session.trading_date in by_date:
                raise ValueError("fake market clock contains a duplicate trading date")
            by_date[session.trading_date] = session
        self._sessions = by_date

    def session_for(self, trading_date: TradingDate) -> MarketSession:
        if not isinstance(trading_date, TradingDate):
            raise ValueError("trading_date must be a TradingDate")
        try:
            return self._sessions[trading_date]
        except KeyError as error:
            raise MarketClockUnavailableError(
                f"no deterministic market session for {trading_date}"
            ) from error
