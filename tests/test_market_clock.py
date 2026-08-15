"""Deterministic market-session clock tests (no broker or wall clock)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seven_lens.clock.market_clock import (
    FakeMarketClock,
    MarketClockUnavailableError,
    MarketDayKind,
    MarketSession,
    RegularSessionWindow,
)
from seven_lens.domain.value_objects import TradingDate, UtcTimestamp


def timestamp(hour: int, minute: int = 0) -> UtcTimestamp:
    return UtcTimestamp(datetime(2026, 8, 14, hour, minute, tzinfo=UTC))


def trading_date(day: int) -> TradingDate:
    return TradingDate(datetime(2026, 8, day).date())


REGULAR = RegularSessionWindow(opens_at=timestamp(14, 30), closes_at=timestamp(21))
HALF_DAY = RegularSessionWindow(opens_at=timestamp(14, 30), closes_at=timestamp(17))


def test_regular_session_has_expected_open_close_and_half_open_boundaries() -> None:
    session = MarketSession(trading_date(14), MarketDayKind.REGULAR, REGULAR)

    assert session.day_kind is MarketDayKind.REGULAR
    assert session.is_closed is False
    assert session.regular_session == REGULAR
    assert session.is_regular_session(timestamp(14, 29)) is False
    assert session.is_regular_session(timestamp(14, 30)) is True
    assert session.is_regular_session(timestamp(20, 59)) is True
    assert session.is_regular_session(timestamp(21)) is False


def test_half_day_session_uses_early_close_without_hardcoded_clock_logic() -> None:
    session = MarketSession(trading_date(15), MarketDayKind.HALF_DAY, HALF_DAY)

    assert session.day_kind is MarketDayKind.HALF_DAY
    assert session.is_regular_session(timestamp(16, 59)) is True
    assert session.is_regular_session(timestamp(17)) is False


def test_holiday_or_closed_day_has_no_regular_session() -> None:
    session = MarketSession(trading_date(16), MarketDayKind.CLOSED, None)

    assert session.is_closed is True
    assert session.regular_session is None
    assert session.is_regular_session(timestamp(15)) is False


def test_fake_market_clock_returns_explicit_regular_half_day_and_closed_records() -> None:
    sessions = (
        MarketSession(trading_date(14), MarketDayKind.REGULAR, REGULAR),
        MarketSession(trading_date(15), MarketDayKind.HALF_DAY, HALF_DAY),
        MarketSession(trading_date(16), MarketDayKind.CLOSED, None),
    )
    clock = FakeMarketClock(sessions)

    assert clock.session_for(trading_date(14)).day_kind is MarketDayKind.REGULAR
    assert clock.session_for(trading_date(15)).day_kind is MarketDayKind.HALF_DAY
    assert clock.session_for(trading_date(16)).is_closed is True


def test_fake_market_clock_fails_closed_for_unrecorded_date() -> None:
    clock = FakeMarketClock((MarketSession(trading_date(14), MarketDayKind.REGULAR, REGULAR),))

    with pytest.raises(MarketClockUnavailableError, match="no deterministic market session"):
        clock.session_for(trading_date(17))


@pytest.mark.parametrize(
    "bad_session",
    [
        lambda: MarketSession(trading_date(14), MarketDayKind.CLOSED, REGULAR),
        lambda: MarketSession(trading_date(14), MarketDayKind.REGULAR, None),
        lambda: RegularSessionWindow(opens_at=timestamp(21), closes_at=timestamp(14, 30)),
        lambda: RegularSessionWindow(opens_at=timestamp(14, 30), closes_at=timestamp(14, 30)),
    ],
)
def test_market_session_rejects_inconsistent_or_zero_length_windows(bad_session: object) -> None:
    with pytest.raises(ValueError):
        bad_session()  # type: ignore[operator]


def test_fake_market_clock_rejects_duplicate_dates_and_invalid_query_type() -> None:
    session = MarketSession(trading_date(14), MarketDayKind.REGULAR, REGULAR)

    with pytest.raises(ValueError, match="duplicate"):
        FakeMarketClock((session, session))

    clock = FakeMarketClock((session,))
    with pytest.raises(ValueError, match="TradingDate"):
        clock.session_for("2026-08-14")  # type: ignore[arg-type]
