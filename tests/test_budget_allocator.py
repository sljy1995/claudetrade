import numpy as np
import pandas as pd
import pytest

from claudetrade.portfolio import dynamic_budget as bd
from claudetrade.portfolio import risk_model as rm
from claudetrade.portfolio.allocator import (
    AllocationInputs,
    allocate,
    regime_scaler,
    risk_budget_weights,
)
from tests.test_risk_model import rets


def test_kelly_fraction_shrinks_with_uncertainty():
    assert bd.kelly_fraction(1.0, np.inf) == 0.0
    assert bd.kelly_fraction(-0.5, 0.1) == 0.0
    assert bd.kelly_fraction(1.0, 1.0) == pytest.approx(0.5)
    assert bd.kelly_fraction(1.0, 2.0) == pytest.approx(0.2)
    assert bd.kelly_fraction(3.0, 0.1) == bd.KELLY_CAP


def test_sigma_dd_hits_alpha():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0004, 0.01, 2500)
    owner = bd.OwnerPrimitives(dd_max=0.15, alpha=0.10)
    res = bd.sigma_dd(r, owner, n_paths=1500)
    s = res["bootstrap"]
    # independent check with fresh paths
    paths = bd.stationary_bootstrap(r, owner.horizon, 3000, rng=np.random.default_rng(99))
    p = np.mean(bd.max_drawdowns(bd._rescale(paths, r, s, 252)) >= owner.dd_max)
    assert p == pytest.approx(owner.alpha, abs=0.03)
    assert res["binding"] == min(res["bootstrap"], res["student_t"])


def test_sigma_dd_increases_with_loss_capacity():
    r = np.random.default_rng(1).normal(0.0003, 0.01, 2000)
    lo = bd.sigma_dd(r, bd.OwnerPrimitives(0.10, 0.10), n_paths=800)["binding"]
    hi = bd.sigma_dd(r, bd.OwnerPrimitives(0.25, 0.10), n_paths=800)["binding"]
    assert hi > lo


def test_target_budget_cold_start_is_small():
    z = np.random.default_rng(2).normal(0, 1, 60)
    z = (z - z.mean()) / z.std(ddof=1)
    r = 0.01 * z + 0.01 * 0.8 / np.sqrt(252)  # exactly SR 0.8 over 3 months: SE ≈ 2
    b = bd.target_budget(r, bd.OwnerPrimitives())
    assert b.kelly_f < 0.2
    assert b.sigma_target == min(b.sigma_opp, b.sigma_dd)


def test_hysteresis_rules():
    assert bd.apply_hysteresis(0.10, 0.105) == 0.10
    assert bd.apply_hysteresis(0.10, 0.20) == pytest.approx(0.125)
    assert bd.apply_hysteresis(0.10, 0.05) == pytest.approx(0.075)
    assert bd.apply_hysteresis(0.10, 0.05, force_decrease=True) == 0.05


def test_drawdown_scaler_linear():
    assert bd.drawdown_scaler(0.03, 0.05, 0.15) == 1.0
    assert bd.drawdown_scaler(0.10, 0.05, 0.15) == pytest.approx(0.5)
    assert bd.drawdown_scaler(0.16, 0.05, 0.15) == 0.0


def test_drawdown_quantiles_ordered():
    q = bd.drawdown_quantiles(np.random.default_rng(3).normal(0.0003, 0.01, 1500), n_paths=500)
    assert q["dd50"] < q["dd95"] < q["dd99"]


def test_risk_budget_equal_scores_diag_is_inverse_vol():
    vols = np.array([0.1, 0.2, 0.4])
    cov = pd.DataFrame(np.diag(vols**2), list("abc"), list("abc"))
    w = risk_budget_weights(pd.Series(1.0, index=list("abc")), cov)
    expected = (1 / vols) / (1 / vols).sum()
    np.testing.assert_allclose(w.to_numpy(), expected, rtol=1e-6)


def test_risk_budget_contributions_follow_scores():
    r = rets(k=4, corr=0.4)
    est = rm.estimate(r)
    b = pd.Series([1, 2, 3, 4], index=est.ids, dtype=float)
    w = risk_budget_weights(b, est.cov)
    np.testing.assert_allclose(
        rm.risk_contributions(w, est.cov).to_numpy(), (b / b.sum()).to_numpy(), atol=1e-6
    )


def _inputs(r, **kw):
    est = rm.estimate(r)
    base = dict(
        scores=pd.Series(1.0, index=est.ids),
        risk=est,
        returns=r,
        sigma_target=0.10,
        es_budget=None,
        kelly_f=0.25,
    )
    base.update(kw)
    return AllocationInputs(**base)


def test_allocation_hits_vol_target_and_no_leverage():
    r = rets(k=10, corr=0.2, seed=4)
    a = allocate(_inputs(r))
    assert a.summary["ex_ante_vol"] == pytest.approx(0.10, rel=1e-3)
    assert a.weights.sum() <= 1.0 + 1e-12 and (a.weights >= 0).all()
    hi = allocate(_inputs(r, sigma_target=5.0))  # unreachable without leverage
    assert hi.weights.sum() == pytest.approx(1.0)


def test_group_risk_share_cap():
    r = rets(k=10, corr=0.2, seed=5)
    sectors = pd.Series(["tech"] * 6 + ["a", "b", "c", "d"], index=[f"S{i}" for i in range(10)])
    a = allocate(_inputs(r, groups={"sector": sectors}))
    share = a.diagnostics["risk_share"].groupby(sectors).sum()
    assert share["tech"] <= 0.30 + 1e-6


def test_stop_and_kelly_caps_bind():
    r = rets(k=6, seed=6)
    ids = [f"S{i}" for i in range(6)]
    prices = pd.Series(100.0, index=ids)
    atr = pd.Series(1.0, index=ids)
    atr["S0"] = 20.0  # very wide stop → small size
    mu = pd.Series(0.10, index=ids)
    mu["S1"] = 0.0  # no expected edge → Kelly cap 0
    a = allocate(_inputs(r, prices=prices, atr=atr, mu=mu))
    assert "S1" not in a.weights.index
    assert a.diagnostics.loc["S0", "binding_cap"] == "stop_risk"


def test_explicit_es_budget_is_respected():
    r = rets(k=8, seed=7)
    a = allocate(_inputs(r, es_budget=0.004))
    assert a.summary["es_binding"] <= 0.004 * (1 + 1e-6)


def test_scalers_reduce_exposure():
    r = rets(k=8, seed=8)
    full = allocate(_inputs(r)).weights.sum()
    half = allocate(_inputs(r, drawdown_scaler=0.5)).weights.sum()
    assert half == pytest.approx(full * 0.5, rel=1e-6)
    assert regime_scaler(0.5, start=0.8) == 1.0
    assert regime_scaler(1.0, start=0.8) == pytest.approx(0.25)
