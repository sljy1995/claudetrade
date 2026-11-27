"""Performance and validation statistics (RESEARCH.md §3–§5).

Sharpe ratios inside the PSR/DSR/MinTRL formulas are *per period* (non-annualised), as
in Bailey & López de Prado (2012, 2014). Annualised values use ``periods`` (252 for daily).

References
- Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier" (2012): PSR, MinTRL.
- Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014): DSR, E[max SR].
- Bailey, Borwein, López de Prado & Zhu, "The Probability of Backtest Overfitting" (2017).
- Lo, "The Statistics of Sharpe Ratios" (2002): autocorrelation adjustment.
"""

from __future__ import annotations

import itertools
from math import comb

import numpy as np
import pandas as pd
from scipy import stats

EULER_GAMMA = 0.5772156649015329
PERIODS = 252


def _arr(r) -> np.ndarray:
    a = np.asarray(r, dtype=float)
    return a[np.isfinite(a)]


# --- basic performance ------------------------------------------------------------------


def sharpe(r, periods: int = PERIODS) -> float:
    a = _arr(r)
    sd = a.std(ddof=1)
    return float(a.mean() / sd * np.sqrt(periods)) if sd > 0 else np.nan


def sortino(r, periods: int = PERIODS) -> float:
    a = _arr(r)
    downside = np.sqrt(np.mean(np.minimum(a, 0.0) ** 2))
    return float(a.mean() / downside * np.sqrt(periods)) if downside > 0 else np.nan


def volatility(r, periods: int = PERIODS) -> float:
    return float(_arr(r).std(ddof=1) * np.sqrt(periods))


def cagr(r, periods: int = PERIODS) -> float:
    a = _arr(r)
    growth = np.prod(1 + a)
    return float(growth ** (periods / len(a)) - 1) if len(a) else np.nan


def drawdown_series(r) -> pd.Series:
    r = pd.Series(r).fillna(0.0)
    wealth = (1 + r).cumprod()
    return wealth / wealth.cummax() - 1


def max_drawdown(r) -> float:
    """Maximum drawdown as a positive fraction."""
    return float(-drawdown_series(r).min())


def calmar(r, periods: int = PERIODS) -> float:
    mdd = max_drawdown(r)
    return cagr(r, periods) / mdd if mdd > 0 else np.nan


def summary(r, periods: int = PERIODS) -> dict:
    return {
        "cagr": cagr(r, periods),
        "vol": volatility(r, periods),
        "sharpe": sharpe(r, periods),
        "sortino": sortino(r, periods),
        "max_dd": max_drawdown(r),
        "calmar": calmar(r, periods),
        "n": int(len(_arr(r))),
    }


# --- Sharpe inference ---------------------------------------------------------------------


def _moments(a: np.ndarray) -> tuple[float, float, float]:
    sr = a.mean() / a.std(ddof=1)
    skew = float(stats.skew(a, bias=False))
    kurt = float(stats.kurtosis(a, fisher=False, bias=False))  # non-excess
    return float(sr), skew, kurt


def _sr_var_factor(sr: float, skew: float, kurt: float) -> float:
    return 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2


def autocorr_inflation(a: np.ndarray, max_lag: int | None = None) -> float:
    """Variance inflation of the mean from serial correlation (Newey–West/Bartlett weights).
    1.0 for i.i.d. returns; > 1 when positively autocorrelated."""
    n = len(a)
    q = max_lag or int(np.floor(4 * (n / 100) ** (2 / 9)))
    x = a - a.mean()
    var = np.dot(x, x) / n
    if var == 0:
        return 1.0
    infl = 1.0
    for k in range(1, q + 1):
        rho = np.dot(x[k:], x[:-k]) / n / var
        infl += 2 * (1 - k / (q + 1)) * rho
    return max(infl, 1e-6)


def sharpe_se(r, periods: int = PERIODS, autocorr: bool = True) -> float:
    """Standard error of the annualised Sharpe: non-normal (skew/kurtosis) and, optionally,
    serial-correlation adjusted (Lo 2002 spirit)."""
    a = _arr(r)
    sr, skew, kurt = _moments(a)
    var = _sr_var_factor(sr, skew, kurt) / (len(a) - 1)
    if autocorr:
        var *= autocorr_inflation(a)
    return float(np.sqrt(max(var, 0.0)) * np.sqrt(periods))


def psr(r, sr_benchmark_ann: float = 0.0, periods: int = PERIODS) -> float:
    """Probabilistic Sharpe Ratio: P(true SR > benchmark)."""
    a = _arr(r)
    sr, skew, kurt = _moments(a)
    sr_star = sr_benchmark_ann / np.sqrt(periods)
    denom = np.sqrt(max(_sr_var_factor(sr, skew, kurt), 1e-12))
    return float(stats.norm.cdf((sr - sr_star) * np.sqrt(len(a) - 1) / denom))


def expected_max_sharpe(n_trials: float, var_sr: float) -> float:
    """E[max SR] of ``n_trials`` independent zero-skill trials whose Sharpe estimates have
    variance ``var_sr`` (same units as the Sharpes; annualised in → annualised out)."""
    if n_trials <= 1:
        return 0.0
    z1 = stats.norm.ppf(1 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
    return float(np.sqrt(var_sr) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def deflated_sharpe_ratio(
    r, n_trials: float, var_trials_sr_ann: float, periods: int = PERIODS
) -> float:
    """DSR: PSR against the Sharpe that the best of ``n_trials`` lucky trials would show."""
    return psr(r, expected_max_sharpe(n_trials, var_trials_sr_ann), periods)


def deflated_sharpe_estimate(sr_ann: float, n_trials: float, var_trials_sr_ann: float) -> float:
    """Sharpe after subtracting the expected maximum under the null (RESEARCH.md §5a)."""
    return sr_ann - expected_max_sharpe(n_trials, var_trials_sr_ann)


def min_track_record_length(
    r, sr_benchmark_ann: float = 0.0, alpha: float = 0.05, periods: int = PERIODS
) -> float:
    """Minimum number of periods for the observed Sharpe to reject SR ≤ benchmark at
    confidence 1 − alpha. ``inf`` if the observed Sharpe does not exceed the benchmark."""
    a = _arr(r)
    sr, skew, kurt = _moments(a)
    sr_star = sr_benchmark_ann / np.sqrt(periods)
    if sr <= sr_star:
        return float("inf")
    z = stats.norm.ppf(1 - alpha)
    return float(1 + _sr_var_factor(sr, skew, kurt) * (z / (sr - sr_star)) ** 2)


# --- multiple testing ---------------------------------------------------------------------


def effective_trials(trial_returns: pd.DataFrame, corr_threshold: float = 0.7) -> int:
    """Number of clusters among trial return series (average-linkage on 1 − ρ)."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    m = trial_returns.dropna(axis=1, how="all")
    n = m.shape[1]
    if n <= 1:
        return n
    dist = np.clip(1 - m.corr().fillna(0.0).to_numpy(), 0, 2)
    np.fill_diagonal(dist, 0.0)
    z = linkage(squareform(dist, checks=False), method="average")
    return int(fcluster(z, t=1 - corr_threshold, criterion="distance").max())


def pbo(
    trial_returns: pd.DataFrame, n_blocks: int = 16, max_combinations: int = 5000, seed: int = 0
) -> dict:
    """Probability of Backtest Overfitting via CSCV.

    Rows are periods, columns are trial configurations. Returns PBO and the logit
    distribution. Uses a random subset of block combinations when there are more than
    ``max_combinations``.
    """
    m = trial_returns.to_numpy(dtype=float)
    t, n = m.shape
    if n < 2:
        raise ValueError("PBO needs at least two trials")
    n_blocks -= n_blocks % 2
    blocks = np.array_split(np.arange(t), n_blocks)
    all_combos = comb(n_blocks, n_blocks // 2)
    if all_combos <= max_combinations:
        combos = list(itertools.combinations(range(n_blocks), n_blocks // 2))
    else:
        rng = np.random.default_rng(seed)
        combos = {
            tuple(sorted(rng.choice(n_blocks, n_blocks // 2, replace=False)))
            for _ in range(max_combinations * 2)
        }
        combos = list(combos)[:max_combinations]

    def sr_cols(x):
        sd = x.std(axis=0, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(sd > 0, x.mean(axis=0) / sd, -np.inf)

    logits = []
    for is_blocks in combos:
        is_idx = np.concatenate([blocks[b] for b in is_blocks])
        oos_idx = np.concatenate([blocks[b] for b in range(n_blocks) if b not in is_blocks])
        best = int(np.argmax(sr_cols(m[is_idx])))
        oos = sr_cols(m[oos_idx])
        rank = stats.rankdata(oos)[best]  # 1 = worst
        w = rank / (n + 1)
        logits.append(np.log(w / (1 - w)))
    logits = np.array(logits)
    return {"pbo": float(np.mean(logits <= 0)), "logits": logits, "n_combinations": len(combos)}
