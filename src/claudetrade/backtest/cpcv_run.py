"""Run a strategy through CPCV with the event-driven engine."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from claudetrade.backtest.engine import Engine
from claudetrade.backtest.splits import CPCV, assemble_paths


def run_cpcv(
    engine: Engine,
    strategy_factory: Callable[[], object],
    dates: pd.DatetimeIndex,
    cv: CPCV,
    ids=None,
) -> pd.DataFrame:
    """Out-of-sample return paths (columns ``path_0..``) over ``dates``.

    For each split, a fresh strategy is created. If it has ``fit(pit, train_dates)``, it is
    fit on the purged/embargoed training dates only. Each test group is then simulated
    separately, starting flat (so costs of entering positions are always paid).
    """
    dates = pd.DatetimeIndex(dates)
    groups = cv.groups(len(dates))
    split_returns = []
    for split in cv.splits(len(dates)):
        strat = strategy_factory()
        if hasattr(strat, "fit"):
            strat.fit(engine.pit, dates[split.train])
        per_group = {}
        for g in split.test_groups:
            gd = dates[groups[g]]
            res = engine.run(strat, gd[0], gd[-1], ids)
            per_group[g] = res.returns.reindex(gd)
        split_returns.append(per_group)
    return assemble_paths(cv, dates, split_returns)
