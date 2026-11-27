import pandas as pd

from claudetrade.backtest.costs import ZERO_COST
from claudetrade.backtest.cpcv_run import run_cpcv
from claudetrade.backtest.engine import Engine
from claudetrade.backtest.splits import CPCV
from claudetrade.data import PITStore
from claudetrade.data.pit import _empty
from claudetrade.strategies.benchmarks import EqualWeight
from tests.conftest import empty_actions, make_bars


def test_cpcv_end_to_end_and_fit_sees_only_train():
    b = make_bars(ids=("A", "B", "C"), periods=180, seed=9)
    p = PITStore(b, empty_actions(), _empty("observations"))
    dates = pd.DatetimeIndex(sorted(b.date.unique()))
    fits = []

    class Fitted(EqualWeight):
        def fit(self, pit, train_dates):
            fits.append(set(train_dates))

    cv = CPCV(6, 2, label_horizon=3)
    paths = run_cpcv(Engine(p, ZERO_COST), lambda: Fitted(ids=["A", "B", "C"]), dates, cv)
    assert paths.shape == (180, cv.n_paths)
    for f, s in zip(fits, cv.splits(len(dates)), strict=True):
        assert not f & set(dates[s.test])
