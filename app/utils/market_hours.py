"""Market hours utilities — timezone-aware NYSE schedule helpers.

Provides functions to check if the US stock market is currently open,
calculate countdowns to next open/close, and get full market status.
Uses stdlib zoneinfo (no pytz dependency).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def now_et() -> datetime:
    """Current time in US Eastern."""
    return datetime.now(ET)


def is_market_open(dt: datetime | None = None) -> bool:
    """Check if NYSE is currently open (Mon-Fri 9:30-16:00 ET).

    Does NOT account for NYSE holidays — that requires `exchange_calendars`.
    """
    now = dt or now_et()
    if now.weekday() > 4:  # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= now.time() < MARKET_CLOSE


def is_weekday(dt: datetime | None = None) -> bool:
    """True if the given time is a weekday."""
    now = dt or now_et()
    return now.weekday() <= 4


def next_market_open(dt: datetime | None = None) -> datetime:
    """Return the next market open datetime in ET.

    If market is currently open, returns the *next day's* open.
    """
    now = dt or now_et()
    candidate = now.replace(
        hour=MARKET_OPEN.hour,
        minute=MARKET_OPEN.minute,
        second=0,
        microsecond=0,
    )

    # If we haven't passed today's open yet and it's a weekday, use today
    if now.time() < MARKET_OPEN and now.weekday() <= 4:
        return candidate

    # Otherwise advance to next weekday
    candidate += timedelta(days=1)
    while candidate.weekday() > 4:
        candidate += timedelta(days=1)
    return candidate


def next_market_close(dt: datetime | None = None) -> datetime:
    """Return the next market close datetime in ET."""
    now = dt or now_et()
    candidate = now.replace(
        hour=MARKET_CLOSE.hour,
        minute=MARKET_CLOSE.minute,
        second=0,
        microsecond=0,
    )

    if is_market_open(now):
        return candidate

    # Market is closed — find next close after next open
    nxt_open = next_market_open(now)
    return nxt_open.replace(
        hour=MARKET_CLOSE.hour,
        minute=MARKET_CLOSE.minute,
        second=0,
        microsecond=0,
    )


def market_status(dt: datetime | None = None) -> dict:
    """Full market status for frontend display."""
    now = dt or now_et()
    is_open = is_market_open(now)

    if is_open:
        closes_at = next_market_close(now)
        time_remaining = closes_at - now
        next_label = "Closes"
        next_time = closes_at
    else:
        opens_at = next_market_open(now)
        time_remaining = opens_at - now
        next_label = "Opens"
        next_time = opens_at

    return {
        "is_open": is_open,
        "current_time_et": now.strftime("%Y-%m-%d %H:%M:%S ET"),
        "next_event": next_label,
        "next_event_time": next_time.strftime("%Y-%m-%d %H:%M ET"),
        "time_remaining_seconds": int(time_remaining.total_seconds()),
        "day_of_week": now.strftime("%A"),
    }
