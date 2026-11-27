"""Benchmark strategies used for comparison in every report (RESEARCH.md §4)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from claudetrade.data.pit import PITStore
from claudetrade.data.universe import Universe


@dataclass
class EqualWeight:
    """Equal weight across the point-in-time universe members with a known price."""

    universe: Universe | None = None
    ids: list[str] | None = None

    def target(self, as_of: pd.Timestamp, pit: PITStore) -> pd.Series:
        members = self.universe.members(as_of) if self.universe else self.ids
        lookback = as_of.tz_convert("America/New_York").tz_localize(None) - pd.Timedelta(days=10)
        known = pit.get_bars(as_of, members, start=lookback.normalize())
        names = sorted(known["security_id"].unique())
        if not names:
            return pd.Series(dtype=float)
        return pd.Series(1.0 / len(names), index=names)


@dataclass
class BuyAndHold:
    weights: dict[str, float]

    def target(self, as_of: pd.Timestamp, pit: PITStore) -> pd.Series:
        return pd.Series(self.weights, dtype=float)
