from math import comb

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from claudetrade.backtest import metrics as m
from claudetrade.backtest.splits import CPCV, MIN_HOLDOUT_PERIODS, assemble_paths, holdout_periods


def test_max_drawdown_known():
    r = pd.Series([0.1, -0.5, 0.2, 1.0])
    assert m.max_drawdown(r) == pytest.approx(0.5)


def test_psr_matches_closed_form_for_normal_returns():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, 2000)
    sr = r.mean() / r.std(ddof=1)
    skew, kurt = stats.skew(r, bias=False), stats.kurtosis(r, fisher=False, bias=False)
    z = sr * np.sqrt(len(r) - 1) / np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)
    assert m.psr(r) == pytest.approx(stats.norm.cdf(z))


def test_dsr_falls_with_more_trials():
    r = np.random.default_rng(1).normal(0.0008, 0.01, 1500)
    vals = [m.deflated_sharpe_ratio(r, n, 0.25) for n in (1, 10, 100, 1000)]
    assert vals == sorted(vals, reverse=True)
    assert m.expected_max_sharpe(1, 0.25) == 0.0


def test_expected_max_sharpe_matches_simulation():
    rng = np.random.default_rng(2)
    n, sd = 50, 0.5
    sim = rng.normal(0, sd, (20000, n)).max(axis=1).mean()
    assert m.expected_max_sharpe(n, sd**2) == pytest.approx(sim, rel=0.05)


def test_min_trl_consistent_with_psr():
    r = np.random.default_rng(3).normal(0.001, 0.01, 3000)
    n = m.min_track_record_length(r, 0.0, 0.05)
    assert np.isfinite(n) and n < len(r)
    assert m.min_track_record_length(-np.abs(r), 0.0) == float("inf")


def test_sharpe_se_close_to_iid_formula():
    r = np.random.default_rng(4).normal(0.0005, 0.01, 5000)
    sr = r.mean() / r.std(ddof=1)
    iid = np.sqrt((1 + 0.5 * sr**2) / len(r)) * np.sqrt(252)
    assert m.sharpe_se(r, autocorr=False) == pytest.approx(iid, rel=0.05)


def test_pbo_noise_near_half_and_skill_low():
    rng = np.random.default_rng(5)
    noise = pd.DataFrame(rng.normal(0, 0.01, (1000, 30)))
    assert 0.3 < m.pbo(noise, n_blocks=10)["pbo"] < 0.7
    skilled = noise.copy()
    skilled[0] += 0.003
    assert m.pbo(skilled, n_blocks=10)["pbo"] < 0.1


def test_effective_trials_collapses_duplicates():
    rng = np.random.default_rng(6)
    base = rng.normal(0, 0.01, (500, 3))
    cols = {f"v{i}{j}": base[:, i] + rng.normal(0, 0.001, 500) for i in range(3) for j in range(4)}
    assert m.effective_trials(pd.DataFrame(cols)) == 3


@pytest.mark.parametrize("n_groups,k", [(6, 2), (8, 3), (5, 1)])
def test_cpcv_split_and_path_counts(n_groups, k):
    cv = CPCV(n_groups, k, label_horizon=5)
    sp = cv.splits(600)
    assert len(sp) == comb(n_groups, k)
    assert cv.n_paths == comb(n_groups - 1, k - 1)
    for s in sp:
        assert not set(s.train) & set(s.test)


def test_cpcv_purge_and_embargo():
    cv = CPCV(6, 1, label_horizon=5, embargo=3)
    groups = cv.groups(600)
    s = cv.splits(600)[2]  # test group 2
    lo, hi = groups[2][0], groups[2][-1]
    train = set(s.train)
    assert not any(t in train for t in range(lo - 5, lo))
    assert not any(t in train for t in range(hi + 1, hi + 4))
    assert lo - 6 in train and hi + 4 in train


def test_paths_cover_every_date_once():
    cv = CPCV(6, 2)
    idx = pd.bdate_range("2020-01-01", periods=120)
    groups = cv.groups(len(idx))
    split_returns = [
        {g: pd.Series(float(i), index=idx[groups[g]]) for g in s.test_groups}
        for i, s in enumerate(cv.splits(len(idx)))
    ]
    paths = assemble_paths(cv, idx, split_returns)
    assert paths.shape == (120, cv.n_paths)
    assert paths.notna().all().all()
    # each path uses a different split for at least one group
    assert paths.T.drop_duplicates().shape[0] == cv.n_paths


def test_holdout_floor_and_mintrl():
    rng = np.random.default_rng(7)
    weak = rng.normal(0.0001, 0.01, 1000)
    strong = rng.normal(0.003, 0.01, 1000)
    assert holdout_periods(strong) == MIN_HOLDOUT_PERIODS
    assert holdout_periods(weak) >= MIN_HOLDOUT_PERIODS
