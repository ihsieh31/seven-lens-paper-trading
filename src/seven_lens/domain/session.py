"""Trading-session calendaring: sessions are identified by the New York day.

The broker and the daily operations run on ``America/New_York`` wall time, so a
UTC instant before 04:00 local (for example 03:00 UTC during EDT) belongs to
the previous trading session.  Holidays and half days are resolved by the
Alpaca market calendar at execution time, never hard-coded here.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from seven_lens.domain.value_objects import TradingDate, UtcTimestamp

_NEW_YORK = ZoneInfo("America/New_York")


def session_trading_date(now: UtcTimestamp) -> TradingDate:
    """Map a UTC instant to the New York trading session date."""
    if not isinstance(now, UtcTimestamp):
        raise ValueError("session_trading_date requires a UtcTimestamp")
    local = now.value.astimezone(_NEW_YORK)
    return TradingDate(local.date())
