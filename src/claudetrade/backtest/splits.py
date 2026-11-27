"""Data partitioning (RESEARCH.md §2): holdout sizing and Combinatorial Purged CV."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from claudetrade.backtest.metrics import PERIODS, min_track_record_length

MIN_HOLDOUT_PERIODS = int(1.5 * PERIODS)  # 18 months floor


def holdout_periods(dev_returns, alpha: float = 0.05, periods: int = PERIODS) -> int:
    """Holdout length: MinTRL to reject SR ≤ 0 at 1 − alpha given development returns, with
    an 18-month floor. ``inf`` MinTRL (no positive edge) returns the floor; such a strategy
    should not reach the holdout anyway."""
    mtrl = min_track_record_length(dev_returns, 0.0, alpha, periods)
    if not np.isfinite(mtrl):
        return MIN_HOLDOUT_PERIODS
    return max(MIN_HOLDOUT_PERIODS, int(np.ceil(mtrl)))


@dataclass(frozen=True)
class Split:
    train: np.ndarray  # integer positions into the date index
    test_groups: tuple[int, ...]
    test: np.ndarray


class CPCV:
    """Combinatorial Purged Cross-Validation (López de Prado, AFML ch. 12).

    The ``n`` observations are cut into ``n_groups`` contiguous groups. Each split uses
    ``k_test`` groups as test. Training observations within ``label_horizon`` periods
    before a test block (their labels overlap it) are purged, and ``embargo`` periods
    after each test block are dropped.

    Produces C(N, k) splits and C(N−1, k−1) complete backtest paths.
    """

    def __init__(
        self, n_groups: int = 6, k_test: int = 2, label_horizon: int = 1, embargo: int | None = None
    ):
        if not 1 <= k_test < n_groups:
            raise ValueError("need 1 <= k_test < n_groups")
        self.n_groups, self.k_test = n_groups, k_test
        self.label_horizon = label_horizon
        self.embargo = label_horizon if embargo is None else embargo

    def groups(self, n: int) -> list[np.ndarray]:
        return np.array_split(np.arange(n), self.n_groups)

    def splits(self, n: int) -> list[Split]:
        groups = self.groups(n)
        out = []
        for combo in itertools.combinations(range(self.n_groups), self.k_test):
            test = np.concatenate([groups[g] for g in combo])
            keep = np.ones(n, dtype=bool)
            keep[test] = False
            for g in combo:
                lo, hi = groups[g][0], groups[g][-1]
                keep[max(0, lo - self.label_horizon) : lo] = False  # purge
                keep[hi + 1 : hi + 1 + self.embargo] = False  # embargo
            out.append(Split(np.flatnonzero(keep), combo, test))
        return out

    @property
    def n_paths(self) -> int:
        from math import comb

        return comb(self.n_groups - 1, self.k_test - 1)

    def path_assignment(self) -> list[dict[int, int]]:
        """For each path, which split index supplies each group's test returns."""
        combos = list(itertools.combinations(range(self.n_groups), self.k_test))
        seen = {g: [i for i, c in enumerate(combos) if g in c] for g in range(self.n_groups)}
        return [{g: seen[g][p] for g in range(self.n_groups)} for p in range(self.n_paths)]


def assemble_paths(
    cv: CPCV, index: pd.DatetimeIndex, split_returns: list[dict[int, pd.Series]]
) -> pd.DataFrame:
    """Stitch per-split test-group returns into full out-of-sample paths.

    ``split_returns[i][g]`` is the return series of split ``i`` over test group ``g``.
    """
    paths = {}
    for p, assign in enumerate(cv.path_assignment()):
        parts = [split_returns[assign[g]][g] for g in range(cv.n_groups)]
        paths[f"path_{p}"] = pd.concat(parts).reindex(index)
    return pd.DataFrame(paths)
