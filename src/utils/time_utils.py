"""WIB time helpers and market status utilities."""

from datetime import datetime, timezone, timedelta

# WIB = UTC+7
WIB = timezone(timedelta(hours=7))


def now_wib() -> datetime:
    """Get current time in WIB (Western Indonesia Time, UTC+7)."""
    return datetime.now(WIB)


def format_wib_iso(dt: datetime | None = None) -> str:
    """Format a datetime as ISO 8601 string in WIB timezone."""
    if dt is None:
        dt = now_wib()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return dt.astimezone(WIB).isoformat()


def get_market_status() -> str:
    """Determine IDX market status based on current WIB time.

    IDX trading hours (WIB):
    - Session 1: 09:00 - 11:30
    - Lunch break: 11:30 - 13:30
    - Session 2: 13:30 - 15:00
    - Pre-closing: 14:50 - 15:00 (part of session 2)
    - Weekend/holidays: closed

    Returns one of: "open", "lunch_break", "pre_open", "closed"
    """
    now = now_wib()

    # Weekend check
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return "closed"

    hour_min = now.hour * 60 + now.minute

    # Pre-open: 08:45 - 09:00
    if 525 <= hour_min < 540:
        return "pre_open"
    # Session 1: 09:00 - 11:30
    elif 540 <= hour_min < 690:
        return "open"
    # Lunch break: 11:30 - 13:30
    elif 690 <= hour_min < 810:
        return "lunch_break"
    # Session 2: 13:30 - 15:00
    elif 810 <= hour_min < 900:
        return "open"
    else:
        return "closed"
