"""Event-driven daily backtest engine.

Timeline for each trading day ``d``:

1. **Before the open:** apply corporate actions with ex-date ``d``. Splits multiply share
   counts first; dividends (per post-split share) are then credited in cash on shares held
   at the previous close.
2. **At the open:** execute orders decided at the previous close. Target weights become
   target shares using the open price. Fills are capped by a participation limit on ADV
   (the rest is not filled); a name with no bar on ``d`` is not traded (rejected).
3. **At the close:** mark to market. Stale names (no bar) keep their last close.
4. **After the close:** if ``d`` is a rebalance date, ask the strategy for target weights
   using ``as_of = eod_available_at(d)``. They execute at the next open, never same-bar.

Each day the accounting identity is checked:
    ΔNAV = price P&L + dividends − costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

from claudetrade.backtest.costs import CostModel
from claudetrade.data.pit import PITStore
from claudetrade.data.schema import eod_available_at


class Strategy(Protocol):
    def target(self, as_of: pd.Timestamp, pit: PITStore) -> pd.Series:
        """Target weights (security_id → weight of NAV), long-only, sum ≤ 1."""
        ...


class AccountingError(AssertionError):
    pass


@dataclass
class BacktestResult:
    daily: pd.DataFrame  # nav, ret, cash, gross, turnover, costs, dividends
    trades: pd.DataFrame
    flags: set[str] = field(default_factory=set)

    @property
    def returns(self) -> pd.Series:
        return self.daily["ret"]


@dataclass
class Engine:
    pit: PITStore
    costs: CostModel = field(default_factory=CostModel)
    participation: float = 0.05  # max fraction of ADV per fill (RISK.md §3)
    adv_window: int = 20
    initial_nav: float = 1_000_000.0
    rebalance: str | int = 1  # every N trading days, or "W"/"M" (last trading day of period)
    tol: float = 1e-6

    def run(self, strategy: Strategy, start, end, ids=None) -> BacktestResult:
        bars = self.pit.bars
        if ids is not None:
            bars = bars[bars["security_id"].isin(ids)]
        bars = bars[(bars["date"] >= pd.Timestamp(start)) & (bars["date"] <= pd.Timestamp(end))]
        dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
        if len(dates) < 2:
            raise ValueError("Need at least two trading days")

        opens = bars.pivot_table(index="date", columns="security_id", values="open", aggfunc="last")
        closes = bars.pivot_table(
            index="date", columns="security_id", values="close", aggfunc="last"
        )
        vols = bars.pivot_table(
            index="date", columns="security_id", values="volume", aggfunc="last"
        )
        adv = vols.rolling(self.adv_window, min_periods=1).mean().shift(1)  # known at prior close
        sigma = closes.pct_change().rolling(self.adv_window, min_periods=5).std().shift(1)
        acts = self.pit.actions
        splits = _actions(acts, "split", dates)
        divs = _actions(acts, "dividend", dates)
        rebal = _rebalance_mask(dates, self.rebalance)

        cash = self.initial_nav
        shares = pd.Series(dtype=float)
        last_close = pd.Series(dtype=float)
        pending: pd.Series | None = None
        rows, trades = [], []
        prev_nav = self.initial_nav

        for i, d in enumerate(dates):
            # 1. corporate actions (before the open)
            if d in splits and len(shares):
                sp = splits[d].reindex(shares.index).fillna(1.0)
                shares = shares * sp
                last_close = last_close / sp.reindex(last_close.index).fillna(1.0)
            div_cash = 0.0
            if d in divs and len(shares):
                dv = divs[d].reindex(shares.index).fillna(0.0)
                div_cash = float((shares * dv).sum())
                cash += div_cash

            # P&L reference: previous close marks, adjusted for splits
            ref = last_close.reindex(shares.index)
            day_costs = 0.0
            traded_notional = 0.0
            trade_pnl = 0.0  # (close - fill) × traded shares, part of price P&L

            # 2. execute pending orders at the open
            if pending is not None:
                o = opens.loc[d]
                held_px = o.reindex(shares.index).fillna(ref)
                nav_open = cash + float((shares * held_px).sum())
                names = list(pending.index.union(shares.index))
                # sells first, so buys are funded; buys never take cash below zero
                names.sort(
                    key=lambda n: (
                        float(pending.get(n, 0.0)) * nav_open
                        - float(shares.get(n, 0.0))
                        * float(o.get(n, np.nan) if np.isfinite(o.get(n, np.nan)) else 0.0)
                    )
                )
                for sid in names:
                    px = o.get(sid, np.nan)
                    cur = float(shares.get(sid, 0.0))
                    if not np.isfinite(px):
                        if pending.get(sid, 0.0) != 0.0 or cur != 0.0:
                            trades.append((d, sid, 0.0, np.nan, 0.0, "rejected_no_bar"))
                        continue
                    want = float(pending.get(sid, 0.0)) * nav_open / px
                    delta = want - cur
                    if abs(delta) * px < 1.0:  # ignore dust
                        continue
                    cap = self.participation * float(adv.loc[d].get(sid, 0.0) or 0.0)
                    status = "filled"
                    if abs(delta) > cap:
                        delta = np.sign(delta) * cap
                        status = "partial" if cap > 0 else "rejected_no_liquidity"
                        if cap == 0:
                            trades.append((d, sid, 0.0, px, 0.0, status))
                            continue
                    adv_i = float(adv.loc[d].get(sid, 0.0) or 0.0)
                    sig_i = float(sigma.loc[d].get(sid, np.nan))
                    c = self.costs.cost(delta, px, adv_i, sig_i)
                    if delta > 0 and delta * px + c > cash:
                        if cash <= 0:
                            continue
                        delta = cash / (px * (1 + c / (delta * px)))
                        c = self.costs.cost(delta, px, adv_i, sig_i)
                        while delta * px + c > cash and delta > 0:
                            delta *= 0.999
                            c = self.costs.cost(delta, px, adv_i, sig_i)
                        status = "partial_cash"
                        if abs(delta) * px < 1.0:
                            continue
                    cash -= delta * px + c
                    day_costs += c
                    traded_notional += abs(delta) * px
                    shares.loc[sid] = cur + delta
                    close_px = closes.loc[d].get(sid, np.nan)
                    close_px = close_px if np.isfinite(close_px) else px
                    trade_pnl += delta * (close_px - px)
                    trades.append((d, sid, delta, px, c, status))
                shares = shares[shares.abs() > 1e-12]
                pending = None

            # 3. mark at the close
            c_today = closes.loc[d]
            marks = c_today.reindex(shares.index)
            stale = marks.isna()
            marks = marks.fillna(last_close.reindex(shares.index))
            if marks.isna().any():
                raise AccountingError(
                    f"{d.date()}: no price ever seen for {list(marks[marks.isna()].index)}"
                )
            last_close = pd.concat([last_close.drop(marks.index, errors="ignore"), marks])
            nav = cash + float((shares * marks).sum())

            # accounting identity: pre-trade holdings move from ref to close; traded shares
            # from fill to close.
            pre = ref.dropna()
            held_before = _shares_before(shares, trades, d).reindex(pre.index).fillna(0.0)
            price_pnl = float((held_before * (marks.reindex(pre.index).fillna(pre) - pre)).sum())
            expected = prev_nav + price_pnl + trade_pnl + div_cash - day_costs
            if i > 0 and abs(nav - expected) > self.tol * max(1.0, abs(prev_nav)):
                raise AccountingError(f"{d.date()}: NAV {nav:.6f} != expected {expected:.6f}")

            gross = float((shares * marks).abs().sum()) / nav if nav else 0.0
            rows.append(
                {
                    "date": d,
                    "nav": nav,
                    "cash": cash,
                    "gross": gross,
                    "turnover": traded_notional / prev_nav if prev_nav else 0.0,
                    "costs": day_costs,
                    "dividends": div_cash,
                    "stale": int(stale.sum()),
                }
            )
            prev_nav = nav

            # 4. decide after the close (executes next open)
            if rebal[i] and i < len(dates) - 1:
                w = strategy.target(eod_available_at(d), self.pit)
                pending = _check_weights(w, d)

        daily = pd.DataFrame(rows).set_index("date")
        daily["ret"] = daily["nav"].pct_change().fillna(daily["nav"].iloc[0] / self.initial_nav - 1)
        tr = pd.DataFrame(
            trades, columns=["date", "security_id", "shares", "price", "cost", "status"]
        )
        return BacktestResult(daily, tr, set(self.pit.flags))


def _shares_before(shares_after: pd.Series, trades: list, d) -> pd.Series:
    traded = pd.Series({sid: q for (td, sid, q, *_rest) in trades if td == d and q}, dtype=float)
    traded = traded.groupby(level=0).sum() if len(traded) else traded
    before = shares_after.sub(traded, fill_value=0.0)
    return before[before.abs() > 1e-12]


def _check_weights(w: pd.Series, d) -> pd.Series:
    w = pd.Series(w, dtype=float).fillna(0.0)
    if (w < -1e-12).any():
        raise ValueError(f"{d.date()}: negative weights (long-only, RISK.md §9)")
    if w.sum() > 1 + 1e-9:
        raise ValueError(f"{d.date()}: gross weight {w.sum():.4f} > 1 (no leverage, RISK.md §8)")
    return w


def _actions(acts: pd.DataFrame, kind: str, dates: pd.DatetimeIndex) -> dict:
    sub = acts[acts["kind"] == kind]
    out = {}
    agg = "prod" if kind == "split" else "sum"
    for d, g in sub.groupby(pd.to_datetime(sub["ex_date"])):
        if d in dates:
            out[d] = g.groupby("security_id")["value"].agg(agg)
    return out


def _rebalance_mask(dates: pd.DatetimeIndex, rule) -> np.ndarray:
    if isinstance(rule, int):
        return np.arange(len(dates)) % rule == 0
    period = dates.to_period(rule)
    s = pd.Series(period, index=dates)
    return (s != s.shift(-1)).to_numpy()
