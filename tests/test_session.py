"""Unit tests for New-York trading-session date mapping."""

from __future__ import annotations

import pytest

from seven_lens.domain.session import session_trading_date
from seven_lens.domain.value_objects import TradingDate, UtcTimestamp


class TestSessionTradingDate:
    def test_utc_after_new_york_midnight_maps_to_the_same_day(self) -> None:
        now = UtcTimestamp.from_isoformat("2026-08-17T13:35:00.000000Z")

        assert session_trading_date(now) == TradingDate.from_isoformat("2026-08-17")

    def test_late_utc_evening_maps_to_the_new_york_day(self) -> None:
        now = UtcTimestamp.from_isoformat("2026-08-18T03:00:00.000000Z")

        assert session_trading_date(now) == TradingDate.from_isoformat("2026-08-17")

    def test_winter_time_shift_still_uses_new_york_wall_clock(self) -> None:
        now = UtcTimestamp.from_isoformat("2026-01-18T03:30:00.000000Z")

        assert session_trading_date(now) == TradingDate.from_isoformat("2026-01-17")

    def test_mid_day_is_unambiguous(self) -> None:
        now = UtcTimestamp.from_isoformat("2026-08-18T19:00:00.000000Z")

        assert session_trading_date(now) == TradingDate.from_isoformat("2026-08-18")

    def test_rejects_non_timestamp(self) -> None:
        with pytest.raises(ValueError, match="UtcTimestamp"):
            session_trading_date("2026-08-17T13:35:00Z")  # type: ignore[arg-type]
