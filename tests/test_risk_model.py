import numpy as np
import pandas as pd
import pytest

from claudetrade.portfolio import risk_model as rm


def rets(n=500, k=5, seed=0, vols=None, corr=0.3):
    rng = np.random.default_rng(seed)
    vols = np.array(vols or [0.01 * (1 + i / 2) for i in range(k)])
    c = np.full((k, k), corr)
    np.fill_diagonal(c, 1.0)
    x = rng.multivariate_normal(np.zeros(k), c * np.outer(vols, vols), n)
    return pd.DataFrame(
        x, index=pd.bdate_range("2015-01-01", periods=n), columns=[f"S{i}" for i in range(k)]
    )


def test_conservative_vol_takes_the_max_when_vol_rises():
    r = rets(k=1)
    r.iloc[-20:] *= 3
    v = rm.conservative_vol(r)["S0"]
    assert v > r.tail(120).std().item() * np.sqrt(252) * 0.999


def test_ledoit_wolf_improves_conditioning():
    r = rets(n=60, k=40, seed=1)
    x = (r - r.mean()).to_numpy()
    sample = x.T @ x / len(x)
    shrunk, delta = rm.ledoit_wolf(x)
    assert 0 < delta <= 1
    assert np.linalg.cond(shrunk) < np.linalg.cond(sample)
    assert np.all(np.linalg.eigvalsh(shrunk) > 0)


def test_fallbacks_block_and_haircut():
    r = rets(n=400, k=3)
    r.iloc[:-50, 0] = np.nan  # 50 obs → blocked
    r.iloc[:-150, 1] = np.nan  # 150 obs → haircut + half budget
    est = rm.estimate(r)
    assert est.blocked == ["S0"]
    assert est.half_budget == ["S1"]
    full = rm.conservative_vol(r[["S1"]])["S1"]
    assert est.vol["S1"] == pytest.approx(full * 1.5)


def test_ill_conditioned_fails_closed(monkeypatch):
    r = rets(k=3)
    monkeypatch.setattr(rm, "MAX_CONDITION", 1.0001)  # any real covariance now exceeds it
    with pytest.raises(rm.RiskModelError, match="ill-conditioned"):
        rm.estimate(r)


def test_zero_variance_fails_closed():
    r = rets(k=2)
    r["flat"] = 0.0
    with pytest.raises(rm.RiskModelError, match="Zero variance"):
        rm.estimate(r)


def test_fhs_es_matches_normal_for_gaussian_data():
    r = rets(n=4000, k=1, vols=[0.01])
    est = rm.estimate(r)
    es = rm.fhs_es(pd.Series({"S0": 1.0}), r, est.vol)
    assert es == pytest.approx(rm.parametric_es(est.vol["S0"] / np.sqrt(252)), rel=0.15)


def test_stress_es_exceeds_normal_es():
    r = rets()
    est = rm.estimate(r)
    w = pd.Series(0.2, index=est.ids)
    s = rm.stress_es(w, est)
    base = rm.parametric_es(rm.portfolio_vol(w, est.cov) / np.sqrt(252))
    assert s["vol_spike_x2.5"] > base and s["corr_to_0.8"] > base


def test_effective_bets_equal_risk():
    cov = pd.DataFrame(np.eye(4) * 0.04, index=list("abcd"), columns=list("abcd"))
    assert rm.effective_bets(pd.Series(0.25, index=list("abcd")), cov) == pytest.approx(4.0)
