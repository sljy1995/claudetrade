"""Position sizing and portfolio construction (RISK.md §3–§5).

Pipeline for one rebalance:

1. **Risk budgeting.** Each name's share of portfolio variance ∝ its signal strength.
2. **Per-name caps.** min(risk-budget weight, stop-risk weight, fractional-Kelly weight).
3. **Concentration.** Trim names above 2/N_eff of variance and groups (sector, cluster,
   issuer) above 30% of variance.
4. **Exposure.** Scale gross to σ*_t × regime × drawdown scalers, never above 1.0.
5. **Tail.** Trim the largest marginal-ES contributors until binding ES ≤ ES*_t (after
   100 trims, scale the whole book down uniformly).

Every cap that binds is recorded in the diagnostics table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from claudetrade.portfolio.risk_model import (
    PERIODS,
    RiskEstimate,
    binding_es,
    effective_bets,
    portfolio_vol,
    risk_contributions,
)

GROUP_VARIANCE_CAP = 0.30
MIN_EFFECTIVE_BETS = 8


class AllocationError(Exception):
    """No valid allocation. Callers must fail closed (no new risk)."""


def risk_budget_weights(
    budgets: pd.Series, cov: pd.DataFrame, iters: int = 500, tol: float = 1e-10
) -> pd.Series:
    """Long-only weights whose variance contributions are proportional to ``budgets``
    (Spinu 2013 / cyclical Newton). Returned weights sum to 1."""
    b = budgets[budgets > 0]
    if b.empty:
        return pd.Series(dtype=float)
    b = b / b.sum()
    s = cov.loc[b.index, b.index].to_numpy()
    bv = b.to_numpy()
    x = 1.0 / np.sqrt(np.diag(s))
    x = x / x.sum()
    for _ in range(iters):
        x_old = x.copy()
        for i in range(len(x)):
            c = s[i] @ x - s[i, i] * x[i]
            a = s[i, i]
            # solve a·x² + c·x − b_i·(xᵀΣx) = 0 for x_i, holding the total risk fixed
            total = x @ s @ x
            x[i] = (-c + np.sqrt(c * c + 4 * a * bv[i] * total)) / (2 * a)
        x = x / x.sum()
        if np.max(np.abs(x - x_old)) < tol:
            break
    return pd.Series(x, index=b.index)


@dataclass
class AllocationInputs:
    scores: pd.Series  # ≥ 0 signal strength per name
    risk: RiskEstimate
    returns: pd.DataFrame  # PIT daily returns for tail estimates
    sigma_target: float  # σ*_t (annualised)
    es_budget: float | None  # ES*_t (1-day); None → derived from σ*_t and the tail model
    kelly_f: float
    prices: pd.Series | None = None
    atr: pd.Series | None = None
    stop_k: float = 2.5
    n_target: int | None = None
    mu: pd.Series | None = None  # OOS expected annual return per name (shrunk)
    groups: dict[str, pd.Series] = field(default_factory=dict)  # name → id→group label
    regime_scaler: float = 1.0
    drawdown_scaler: float = 1.0


@dataclass
class Allocation:
    weights: pd.Series
    diagnostics: pd.DataFrame
    summary: dict


def allocate(inp: AllocationInputs) -> Allocation:
    risk = inp.risk
    scores = inp.scores.reindex(risk.ids).fillna(0.0).clip(lower=0.0)
    half = set(risk.half_budget)
    scores = scores.where(~scores.index.isin(half), scores * 0.5)
    scores = scores[scores > 0]
    if scores.empty:
        return Allocation(pd.Series(dtype=float), pd.DataFrame(), {"reason": "no positive scores"})
    cov = risk.cov.loc[scores.index, scores.index]

    # 1. risk-budget shape, scaled to the vol target (gross may be < 1)
    shape = risk_budget_weights(scores, cov)
    vol_shape = portfolio_vol(shape, cov)
    w = shape * (inp.sigma_target / vol_shape) if vol_shape > 0 else shape * 0
    binding = pd.Series("risk_budget", index=w.index, dtype=object)

    # ES budget derived from σ* (RISK.md §1.5) on the target-scaled shape
    es_star = inp.es_budget
    if es_star is None:
        es_star = binding_es(w, inp.returns, risk)["binding"] if w.sum() > 0 else 0.0

    # 2. per-name caps
    n_target = inp.n_target or len(w)
    if inp.atr is not None and inp.prices is not None and es_star > 0:
        atr_frac = (inp.atr / inp.prices).reindex(w.index)
        stop_cap = (es_star / n_target) / (inp.stop_k * atr_frac)
        hit = stop_cap < w
        w = w.where(~hit, stop_cap)
        binding[hit] = "stop_risk"
    if inp.mu is not None:
        var_i = risk.vol.reindex(w.index) ** 2
        kelly_cap = (inp.kelly_f * inp.mu.reindex(w.index).clip(lower=0.0) / var_i).fillna(0.0)
        hit = kelly_cap < w
        w = w.where(~hit, kelly_cap)
        binding[hit] = "kelly"

    # 3. concentration by risk contribution
    w, conc_hits = _cap_concentration(w, cov, inp.groups)
    for name, label in conc_hits.items():
        binding[name] = label

    # 4. exposure scaling (never above the vol target or gross 1, no leverage)
    pv = portfolio_vol(w, cov)
    scale = min(1.0, inp.sigma_target / pv) if pv > 0 else 0.0
    scale *= inp.regime_scaler * inp.drawdown_scaler
    w = w * scale
    if w.sum() > 1.0:
        w = w / w.sum()
        binding[:] = binding.where(binding != "risk_budget", "gross_1")

    # 5. tail budget
    es = (
        binding_es(w, inp.returns, risk)
        if w.sum() > 0
        else {"binding": 0.0, "fhs": 0.0, "stress": {}}
    )
    for _ in range(100):
        if es["binding"] <= es_star * (1 + 1e-9) or w.sum() == 0:
            break
        worst = _marginal_es(w, inp.returns, risk).idxmax()
        w[worst] *= 0.9
        binding[worst] = "es_budget"
        es = binding_es(w, inp.returns, risk)
    if es["binding"] > es_star * (1 + 1e-9) and es["binding"] > 0:
        # ES is positively homogeneous in w: a uniform scale-down meets the budget exactly.
        w = w * (es_star / es["binding"])
        binding[:] = binding.where(binding == "es_budget", "es_budget_uniform")
        es = binding_es(w, inp.returns, risk)
    if es["binding"] > es_star * (1 + 1e-6):
        raise AllocationError(f"Could not meet ES budget: {es['binding']:.4f} > {es_star:.4f}")

    w = w[w > 1e-8]
    rc = risk_contributions(w, cov.loc[w.index, w.index]) if len(w) else pd.Series(dtype=float)
    n_eff = effective_bets(w, cov.loc[w.index, w.index]) if len(w) else 0.0
    diag = pd.DataFrame(
        {
            "weight": w,
            "risk_share": rc,
            "vol": risk.vol.reindex(w.index),
            "binding_cap": binding.reindex(w.index),
        }
    )
    flags = []
    if len(w) and n_eff < MIN_EFFECTIVE_BETS:
        flags.append(f"effective bets {n_eff:.1f} < {MIN_EFFECTIVE_BETS}")
    summary = {
        "gross": float(w.sum()),
        "ex_ante_vol": portfolio_vol(w, cov),
        "sigma_target": inp.sigma_target,
        "es_binding": es["binding"],
        "es_fhs": es["fhs"],
        "es_budget": es_star,
        "effective_bets": n_eff,
        "n_names": int(len(w)),
        "flags": flags,
        "regime_scaler": inp.regime_scaler,
        "drawdown_scaler": inp.drawdown_scaler,
    }
    return Allocation(w, diag, summary)


def _cap_concentration(
    w: pd.Series, cov: pd.DataFrame, groups: dict[str, pd.Series], iters: int = 200
) -> tuple[pd.Series, dict]:
    hits = {}
    w = w.copy()
    for _ in range(iters):
        changed = False
        rc = risk_contributions(w, cov)
        n_eff = max(effective_bets(w, cov), 1.0)
        cap = 2.0 / n_eff
        over = rc[rc > cap + 1e-9]
        for name in over.index:
            w[name] *= np.sqrt(cap / rc[name]) * 0.99
            hits[name] = "name_risk_share"
            changed = True
        for gname, labels in groups.items():
            lab = labels.reindex(w.index)
            share = rc.groupby(lab).sum()
            for g, s in share[share > GROUP_VARIANCE_CAP + 1e-9].items():
                members = lab[lab == g].index
                w[members] *= np.sqrt(GROUP_VARIANCE_CAP / s) * 0.99
                for m in members:
                    hits[m] = f"{gname}_risk_share"
                changed = True
        if not changed:
            break
    return w, hits


def _marginal_es(w: pd.Series, returns: pd.DataFrame, risk: RiskEstimate) -> pd.Series:
    """Component ES on the FHS tail days: each name's average loss contribution."""
    from claudetrade.portfolio.risk_model import ES_LEVEL

    ids = list(w.index)
    r = returns[ids].dropna(how="all").fillna(0.0)
    ewma = r.ewm(halflife=20, min_periods=10).std().shift(1)
    scaled = ((r / ewma).dropna() * (risk.vol[ids] / np.sqrt(PERIODS))).to_numpy()
    port = scaled @ w.to_numpy()
    tail = -port >= np.quantile(-port, ES_LEVEL)
    return pd.Series(-(scaled[tail] * w.to_numpy()).mean(axis=0), index=ids)


def regime_scaler(indicator_percentile: float, start: float, floor: float = 0.25) -> float:
    """Predeclared piecewise-linear de-risking: 1.0 below ``start`` percentile, falling
    linearly to ``floor`` at the 100th. ``start`` must be calibrated walk-forward and
    recorded in the run config (RISK.md §5)."""
    if indicator_percentile <= start:
        return 1.0
    return float(max(floor, 1 - (1 - floor) * (indicator_percentile - start) / (1 - start)))
