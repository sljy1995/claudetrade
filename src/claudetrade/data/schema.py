"""Table schemas and time conventions.

Three table kinds:

``bars``          one row per (security_id, date): raw (unadjusted) OHLCV.
``actions``       corporate actions: splits (ratio) and cash dividends (amount, raw per share
                  on the ex-date share basis), keyed by ex-date.
``observations``  long format for fundamentals and macro: (security_id, field, effective_at,
                  value). Multiple rows for the same key are vintages; each has its own
                  ``available_at``.

All tables carry ``available_at`` (tz-aware UTC) and ``source``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)
# End-of-day data is treated as known a little after the close, never at the close itself.
EOD_PUBLICATION_LAG = timedelta(minutes=15)

BAR_COLUMNS = [
    "security_id",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "available_at",
    "source",
]
ACTION_COLUMNS = ["security_id", "ex_date", "kind", "value", "available_at", "source"]
OBSERVATION_COLUMNS = [
    "security_id",
    "field",
    "effective_at",
    "value",
    "available_at",
    "source",
]

ACTION_KINDS = {"split", "dividend"}


def market_close_utc(d: date | pd.Timestamp) -> pd.Timestamp:
    """The US equity close on calendar date ``d``, as a UTC timestamp."""
    d = pd.Timestamp(d).date()
    return pd.Timestamp(datetime.combine(d, MARKET_CLOSE, tzinfo=MARKET_TZ)).tz_convert("UTC")


def eod_available_at(d: date | pd.Timestamp) -> pd.Timestamp:
    """When an end-of-day bar for date ``d`` is first usable."""
    return market_close_utc(d) + EOD_PUBLICATION_LAG


def end_of_day_utc(d: date | pd.Timestamp) -> pd.Timestamp:
    """23:59:59 New York time on ``d``. Conservative availability for values that are
    published at an unknown time during that day (e.g. macro releases)."""
    d = pd.Timestamp(d).date()
    return pd.Timestamp(datetime.combine(d, time(23, 59, 59), tzinfo=MARKET_TZ)).tz_convert("UTC")


def to_utc(ts) -> pd.Timestamp:
    """Coerce to a tz-aware UTC timestamp. Naive input is rejected, so callers can't
    silently mix time zones."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        raise ValueError(f"Naive timestamp {ts!r}: pass a tz-aware time (e.g. UTC).")
    return ts.tz_convert("UTC")
