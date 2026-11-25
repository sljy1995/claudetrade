"""Yahoo Finance via yfinance. **Prototype data only**: no delisted securities (so every
result built on it is SURVIVORSHIP_BIASED) and occasional adjustment errors.

yfinance's ``Close`` (with ``auto_adjust=False``) is split-adjusted but not dividend-
adjusted, and its dividends are split-adjusted too. Back-adjusted levels embed future
information (splits not yet known), so we reconstruct **raw** as-traded prices and store
splits and dividends as separate actions; returns are then rebuilt point in time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from claudetrade.data.schema import ACTION_COLUMNS, BAR_COLUMNS, eod_available_at

SOURCE = "yahoo"
FLAGS = {"SURVIVORSHIP_BIASED"}


def security_id(ticker: str) -> str:
    # Yahoo has no permanent identifier; tickers can be reused. Replace when a vendor
    # with permanent IDs is added.
    return f"YF:{ticker.upper()}"


def from_yfinance_history(ticker: str, hist: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert one ticker's ``Ticker.history(auto_adjust=False, actions=True)`` frame into
    (bars, actions) tables with raw prices."""
    h = hist.copy()
    idx = pd.DatetimeIndex(h.index)
    h.index = (
        (idx.tz_convert("America/New_York") if idx.tz is not None else idx)
        .tz_localize(None)
        .normalize()
    )
    h = h[~h.index.duplicated(keep="last")].sort_index()

    splits = h.get("Stock Splits", pd.Series(0.0, index=h.index)).fillna(0.0)
    splits = splits.where(splits > 0, 1.0)
    # Factor converting split-adjusted values on date t to raw: product of ratios of all
    # splits with ex-date strictly after t.
    future = splits[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    raw_factor = future.to_numpy()

    sid = security_id(ticker)
    bars = pd.DataFrame(
        {
            "security_id": sid,
            "date": h.index,
            "open": h["Open"].to_numpy() * raw_factor,
            "high": h["High"].to_numpy() * raw_factor,
            "low": h["Low"].to_numpy() * raw_factor,
            "close": h["Close"].to_numpy() * raw_factor,
            "volume": h["Volume"].to_numpy() / raw_factor,
        }
    )
    bars = bars.dropna(subset=["open", "high", "low", "close"])
    bars["available_at"] = bars["date"].map(eod_available_at)
    bars["source"] = SOURCE

    actions = []
    for d, s in splits[splits != 1.0].items():
        actions.append((sid, d, "split", float(s)))
    divs = h.get("Dividends", pd.Series(0.0, index=h.index)).fillna(0.0)
    for d, v in divs[divs > 0].items():
        actions.append((sid, d, "dividend", float(v) * float(future.loc[d])))
    act = pd.DataFrame(actions, columns=["security_id", "ex_date", "kind", "value"])
    # Conservative: an action is usable from its ex-date's open at the earliest; we use
    # the ex-date close, consistent with EOD bars.
    act["available_at"] = (
        pd.to_datetime(act["ex_date"]).map(eod_available_at)
        if len(act)
        else pd.Series(dtype="datetime64[ns, UTC]")
    )
    act["source"] = SOURCE
    bars["volume"] = np.round(bars["volume"]).astype("int64")
    return bars[BAR_COLUMNS].reset_index(drop=True), act[ACTION_COLUMNS].reset_index(drop=True)


def fetch(
    tickers: list[str], start: str, end: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf

    all_bars, all_actions, failed = [], [], []
    for t in tickers:
        hist = yf.Ticker(t).history(start=start, end=end, auto_adjust=False, actions=True)
        if hist.empty:
            failed.append(t)
            continue
        b, a = from_yfinance_history(t, hist)
        all_bars.append(b)
        all_actions.append(a)
    if failed:
        # Fail closed: a silently missing name changes the universe.
        raise RuntimeError(f"No data returned for {failed}")
    return pd.concat(all_bars, ignore_index=True), pd.concat(all_actions, ignore_index=True)
