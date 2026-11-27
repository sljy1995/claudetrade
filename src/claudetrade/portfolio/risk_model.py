"""Risk estimation (RISK.md §2): volatility, shrunk covariance, tail risk, fallbacks.

All inputs are point-in-time daily returns (wide: date × security_id) that the caller
obtained from ``PITStore.total_returns(as_of)``. Outputs are annualised unless named
``_daily``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

PERIODS = 252
MIN_OBS_BLOCK = 60  # fewer → block the name
MIN_OBS_FULL = 252  # fewer → haircut and half budget
SHORT_HISTORY_HAIRCUT = 1.5
MAX_CONDITION = 1e4
ES_LEVEL = 0.975


class RiskModelError(Exception):
    """Risk inputs are invalid. Callers must fail closed: no new risk."""


@dataclass
class RiskEstimate:
    ids: list[str]
    vol: pd.Series  # annualised, conservative
    cov: pd.DataFrame  # annualised, shrunk, vol-consistent
    blocked: list[str] = field(default_factory=list)
    half_budget: list[str] = field(default_factory=list)  # short-history names
    shrinkage: float = np.nan
    condition_number: float = np.nan
    notes: list[str] = field(default_factory=list)


def conservative_vol(
    returns: pd.DataFrame, halflife: int = 20, long_window: int = 120, periods: int = PERIODS
) -> pd.Series:
    """Per-asset max(EWMA vol, long-window vol), annualised (RISK.md §2)."""
    ewma = returns.ewm(halflife=halflife, min_periods=10).std().iloc[-1]
    long = returns.tail(long_window).std()
    return np.sqrt(periods) * pd.concat([ewma, long], axis=1).max(axis=1)


def ledoit_wolf(x: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit–Wolf (2004) shrinkage toward a scaled identity. ``x`` is T × N demeaned."""
    t, n = x.shape
    s = x.T @ x / t
    mu = np.trace(s) / n
    target = mu * np.eye(n)
    d2 = np.sum((s - target) ** 2) / n
    b2_bar = sum(np.sum((np.outer(r, r) - s) ** 2) for r in x) / t**2 / n
    b2 = min(b2_bar, d2)
    delta = b2 / d2 if d2 > 0 else 1.0
    return delta * target + (1 - delta) * s, float(delta)


def estimate(returns: pd.DataFrame, window: int = PERIODS, periods: int = PERIODS) -> RiskEstimate:
    """Build the risk estimate, applying the RISK.md §2 fallbacks."""
    counts = returns.notna().sum()
    blocked = sorted(counts[counts < MIN_OBS_BLOCK].index)
    usable = [c for c in returns.columns if c not in blocked]
    if not usable:
        raise RiskModelError("No names with enough history")
    half = sorted(counts[(counts >= MIN_OBS_BLOCK) & (counts < MIN_OBS_FULL)].index)

    r = returns[usable].tail(window)
    vol = conservative_vol(returns[usable], periods=periods)
    vol.loc[half] *= SHORT_HISTORY_HAIRCUT

    # Correlation from the window (missing days count as zero return, then shrunk);
    # the vol scale comes from the conservative estimate above.
    filled = r.fillna(0.0)
    x = (filled - filled.mean()).to_numpy()
    sd = x.std(axis=0)
    if (sd <= 0).any():
        raise RiskModelError(f"Zero variance for {list(np.array(usable)[sd <= 0])}")
    z = x / sd
    corr, delta = ledoit_wolf(z)
    dcorr = np.sqrt(np.diag(corr))
    corr = corr / np.outer(dcorr, dcorr)
    cov = np.outer(vol.to_numpy(), vol.to_numpy()) * corr

    cond = float(np.linalg.cond(cov))
    if not np.isfinite(cond) or cond > MAX_CONDITION:
        raise RiskModelError(f"Covariance ill-conditioned (κ = {cond:.3g})")
    notes = []
    if half:
        notes.append(f"short history (×{SHORT_HISTORY_HAIRCUT} vol, half budget): {half}")
    if blocked:
        notes.append(f"blocked (< {MIN_OBS_BLOCK} obs): {blocked}")
    return RiskEstimate(
        usable, vol, pd.DataFrame(cov, usable, usable), blocked, half, delta, cond, notes
    )


def portfolio_vol(w: pd.Series, cov: pd.DataFrame) -> float:
    w = w.reindex(cov.index).fillna(0.0).to_numpy()
    return float(np.sqrt(max(w @ cov.to_numpy() @ w, 0.0)))


def risk_contributions(w: pd.Series, cov: pd.DataFrame) -> pd.Series:
    """Component contributions to portfolio variance, as shares summing to 1."""
    wv = w.reindex(cov.index).fillna(0.0)
    var = float(wv @ cov @ wv)
    if var <= 0:
        return wv * 0.0
    return wv * (cov @ wv) / var


def effective_bets(w: pd.Series, cov: pd.DataFrame) -> float:
    """1 / Σ(risk share²): equals N for an equal-risk portfolio of N names."""
    rc = risk_contributions(w, cov)
    rc = rc[rc > 0]
    return float(1.0 / np.sum(rc**2)) if len(rc) else 0.0


# --- tail risk ----------------------------------------------------------------------------


def _es(losses: np.ndarray, level: float = ES_LEVEL) -> float:
    q = np.quantile(losses, level)
    return float(losses[losses >= q].mean())


def fhs_es(
    w: pd.Series,
    returns: pd.DataFrame,
    current_vol_ann: pd.Series,
    level: float = ES_LEVEL,
    halflife: int = 20,
    periods: int = PERIODS,
) -> float:
    """1-day filtered-historical-simulation ES of portfolio ``w`` (fraction of NAV).

    Each asset's history is standardised by its own EWMA vol at the time, then rescaled to
    its current vol, so the tail shape is historical but the scale is today's.
    """
    ids = [i for i in w.index if w[i] != 0]
    r = returns[ids].dropna(how="all").fillna(0.0)
    ewma = r.ewm(halflife=halflife, min_periods=10).std().shift(1)
    std = (r / ewma).dropna()
    scaled = std * (current_vol_ann[ids] / np.sqrt(periods))
    port = scaled.to_numpy() @ w[ids].to_numpy()
    return _es(-port, level)


def parametric_es(sigma_daily: float, level: float = ES_LEVEL) -> float:
    z = stats.norm.ppf(level)
    return float(sigma_daily * stats.norm.pdf(z) / (1 - level))


def stress_es(
    w: pd.Series,
    risk: RiskEstimate,
    returns: pd.DataFrame | None = None,
    windows: dict[str, tuple[str, str]] | None = None,
    level: float = ES_LEVEL,
    periods: int = PERIODS,
) -> dict[str, float]:
    """1-day stress ES under the RISK.md §2 synthetic shocks, plus historical windows that
    the data covers. Returns each scenario's ES (fraction of NAV)."""
    wv = w.reindex(risk.cov.index).fillna(0.0)
    vol_d = risk.vol / np.sqrt(periods)
    x = wv.to_numpy()
    v = vol_d.to_numpy()
    corr = risk.cov.to_numpy() / np.outer(risk.vol.to_numpy(), risk.vol.to_numpy())
    out = {}
    sv = v * 2.5
    out["vol_spike_x2.5"] = parametric_es(float(np.sqrt(x @ (corr * np.outer(sv, sv)) @ x)), level)
    conv = np.full_like(corr, 0.8)
    np.fill_diagonal(conv, 1.0)
    sigma_conv = float(np.sqrt(x @ (conv * np.outer(v, v)) @ x))
    out["corr_to_0.8"] = parametric_es(sigma_conv, level)
    out["gap_-3sigma"] = 3 * sigma_conv
    if returns is not None and windows:
        for name, (a, b) in windows.items():
            sub = returns.loc[a:b, wv.index].dropna(how="all")
            if len(sub) >= 20:
                out[f"hist_{name}"] = _es(-(sub.fillna(0.0).to_numpy() @ wv.to_numpy()), level)
    return out


HISTORICAL_STRESS_WINDOWS = {
    "2008": ("2008-09-01", "2009-03-31"),
    "2020": ("2020-02-15", "2020-04-30"),
    "2022": ("2022-01-01", "2022-10-31"),
}


def binding_es(w: pd.Series, returns: pd.DataFrame, risk: RiskEstimate) -> dict:
    """RISK.md §2: binding tail estimate = max(FHS ES, 0.5 × worst stress ES)."""
    fhs = fhs_es(w, returns, risk.vol)
    stress = stress_es(w, risk, returns, HISTORICAL_STRESS_WINDOWS)
    worst = max(stress.values())
    return {"fhs": fhs, "stress": stress, "binding": max(fhs, 0.5 * worst)}
