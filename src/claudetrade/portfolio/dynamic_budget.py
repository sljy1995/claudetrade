"""Dynamic risk budget from the owner's two primitives (RISK.md §1) and drawdown
control (RISK.md §6)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy import stats

from claudetrade.backtest.metrics import PERIODS, sharpe, sharpe_se

KELLY_CAP = 0.5


@dataclass(frozen=True)
class OwnerPrimitives:
    dd_max: float = 0.15
    alpha: float = 0.10
    horizon: int = PERIODS  # trading days (12 months)


# --- bootstrap ------------------------------------------------------------------------------


def stationary_bootstrap(
    r: np.ndarray,
    length: int,
    n_paths: int,
    mean_block: float = 20,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Politis–Romano stationary bootstrap: geometric block lengths, wrapping indices."""
    rng = rng or np.random.default_rng(0)
    n = len(r)
    p = 1.0 / mean_block
    idx = np.empty((n_paths, length), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_paths)
    jumps = rng.random((n_paths, length)) < p
    starts = rng.integers(0, n, (n_paths, length))
    for t in range(1, length):
        idx[:, t] = np.where(jumps[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return r[idx]


def max_drawdowns(paths: np.ndarray) -> np.ndarray:
    wealth = np.cumprod(1 + paths, axis=1)
    peak = np.maximum.accumulate(
        np.concatenate([np.ones((len(paths), 1)), wealth], axis=1), axis=1
    )[:, 1:]
    return (1 - wealth / peak).max(axis=1)


def _t_paths(r: np.ndarray, length: int, n_paths: int, rng) -> np.ndarray:
    mu, sd = r.mean(), r.std(ddof=1)
    z = (r - mu) / sd
    nu, _, _ = stats.t.fit(z, floc=0.0)
    nu = max(float(nu), 3.0)
    draws = stats.t.rvs(nu, size=(n_paths, length), random_state=rng)
    draws *= np.sqrt((nu - 2) / nu)  # unit variance
    return mu + sd * draws


def _rescale(paths: np.ndarray, r: np.ndarray, sigma_ann: float, periods: int) -> np.ndarray:
    """Rescale returns to annual vol ``sigma_ann`` keeping the Sharpe ratio fixed."""
    return paths * (sigma_ann / (r.std(ddof=1) * np.sqrt(periods)))


def drawdown_quantiles(r, horizon: int = PERIODS, n_paths: int = 2000, seed: int = 0) -> dict:
    """DD₅₀/DD₉₅/DD₉₉ over ``horizon``: worse of block bootstrap and Student-t (RISK.md §6)."""
    a = np.asarray(r, float)
    a = a[np.isfinite(a)]
    rng = np.random.default_rng(seed)
    out = {}
    boot = max_drawdowns(stationary_bootstrap(a, horizon, n_paths, rng=rng))
    tdd = max_drawdowns(_t_paths(a, horizon, n_paths, rng))
    for q in (50, 95, 99):
        out[f"dd{q}"] = float(max(np.percentile(boot, q), np.percentile(tdd, q)))
    return out


def sigma_dd(
    r,
    owner: OwnerPrimitives,
    n_paths: int = 2000,
    seed: int = 0,
    periods: int = PERIODS,
    lo: float = 0.005,
    hi: float = 0.60,
    iters: int = 18,
) -> dict:
    """Largest annual vol σ with P(max DD over H ≥ DD_max) ≤ α, under both the block
    bootstrap and a fitted Student-t; returns both and the binding (smaller) one."""
    a = np.asarray(r, float)
    a = a[np.isfinite(a)]
    rng = np.random.default_rng(seed)
    base = {
        "bootstrap": stationary_bootstrap(a, owner.horizon, n_paths, rng=rng),
        "student_t": _t_paths(a, owner.horizon, n_paths, rng),
    }
    res = {}
    for name, paths in base.items():  # common random numbers across σ → monotone search

        def prob(s, paths=paths):
            return float(np.mean(max_drawdowns(_rescale(paths, a, s, periods)) >= owner.dd_max))

        if prob(lo) > owner.alpha:
            res[name] = lo  # even tiny risk breaches: floor (effectively flat)
            continue
        if prob(hi) <= owner.alpha:
            res[name] = hi
            continue
        a_, b_ = lo, hi
        for _ in range(iters):
            mid = 0.5 * (a_ + b_)
            a_, b_ = (mid, b_) if prob(mid) <= owner.alpha else (a_, mid)
        res[name] = a_
    res["binding"] = min(res["bootstrap"], res["student_t"])
    return res


# --- target -----------------------------------------------------------------------------


def kelly_fraction(sr_ann: float, se_ann: float, cap: float = KELLY_CAP) -> float:
    """Bayesian uncertainty-adjusted Kelly fraction f = SR² / (SR² + SE²), capped."""
    if not np.isfinite(sr_ann) or sr_ann <= 0:
        return 0.0
    if not np.isfinite(se_ann):
        return 0.0
    return float(min(cap, sr_ann**2 / (sr_ann**2 + se_ann**2)))


@dataclass
class Budget:
    dd_max: float
    alpha: float
    horizon: int
    sr: float
    sr_se: float
    kelly_f: float
    sigma_opp: float
    sigma_dd_bootstrap: float
    sigma_dd_t: float
    sigma_dd: float
    sigma_target: float
    binding: str

    def as_dict(self) -> dict:
        return asdict(self)


def target_budget(
    oos_returns,
    owner: OwnerPrimitives,
    deflated_sr_ann: float | None = None,
    periods: int = PERIODS,
    seed: int = 0,
) -> Budget:
    """σ*_t = min(f_t · SR̂_t, σ_DD,t) from the portfolio's walk-forward OOS returns.

    ``deflated_sr_ann`` should be the multiple-testing-deflated Sharpe (RESEARCH.md); if
    omitted, the raw OOS Sharpe is used, which is only acceptable before any search.
    """
    a = np.asarray(oos_returns, float)
    a = a[np.isfinite(a)]
    sr = deflated_sr_ann if deflated_sr_ann is not None else sharpe(a, periods)
    se = sharpe_se(a, periods)
    f = kelly_fraction(sr, se)
    s_opp = max(0.0, f * sr)
    dd = sigma_dd(a, owner, seed=seed, periods=periods)
    target = min(s_opp, dd["binding"])
    binding = "edge (Kelly)" if s_opp <= dd["binding"] else "loss capacity (drawdown)"
    return Budget(
        owner.dd_max,
        owner.alpha,
        owner.horizon,
        float(sr),
        float(se),
        f,
        s_opp,
        dd["bootstrap"],
        dd["student_t"],
        dd["binding"],
        target,
        binding,
    )


def apply_hysteresis(
    prev: float | None,
    new: float,
    threshold: float = 0.10,
    max_step: float = 0.25,
    force_decrease: bool = False,
) -> float:
    """RISK.md §1 update rules: ignore < 10% changes, step ≤ 25%, decreases forced by a
    REDUCE/REVIEW state apply immediately."""
    if prev is None or prev <= 0:
        return new
    if force_decrease and new < prev:
        return new
    change = new / prev - 1
    if abs(change) <= threshold:
        return prev
    return prev * (1 + np.clip(change, -max_step, max_step))


def drawdown_scaler(dd_current: float, dd50: float, dd_max: float) -> float:
    """CPPI-style brake (RISK.md §6): 1 until DD₅₀, linear to 0 at DD_max."""
    if dd_current <= dd50:
        return 1.0
    if dd_max <= dd50:
        return 0.0
    return float(max(0.0, 1 - (dd_current - dd50) / (dd_max - dd50)))
