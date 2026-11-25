"""Point-in-time reads. Nothing with ``available_at > as_of`` is ever returned."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from claudetrade.data.schema import to_utc
from claudetrade.data.snapshots import SnapshotStore


@dataclass
class PITStore:
    """A read-only view over specific snapshot hashes. Pinning hashes (not 'latest') keeps
    every run reproducible; ``snapshot_ids`` goes into the run metadata."""

    bars: pd.DataFrame
    actions: pd.DataFrame
    observations: pd.DataFrame
    snapshot_ids: dict[str, str] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)

    @classmethod
    def from_snapshots(
        cls, store: SnapshotStore, refs: dict[str, tuple[str, str]], flags: set[str] | None = None
    ) -> PITStore:
        """``refs`` maps kind → (name, content_hash). Missing kinds are empty."""
        frames, ids = {}, {}
        for kind in ("bars", "actions", "observations"):
            if kind in refs:
                name, h = refs[kind]
                frames[kind] = store.read(kind, name, h)
                ids[kind] = f"{name}@{h}"
            else:
                frames[kind] = _empty(kind)
        return cls(frames["bars"], frames["actions"], frames["observations"], ids, set(flags or ()))

    # --- raw point-in-time access -------------------------------------------------------

    def get_bars(self, as_of, ids=None, start=None) -> pd.DataFrame:
        as_of = to_utc(as_of)
        b = self.bars[self.bars["available_at"] <= as_of]
        if ids is not None:
            b = b[b["security_id"].isin(ids)]
        if start is not None:
            b = b[b["date"] >= pd.Timestamp(start)]
        return b.sort_values(["security_id", "date"]).reset_index(drop=True)

    def get_actions(self, as_of, ids=None) -> pd.DataFrame:
        as_of = to_utc(as_of)
        a = self.actions[self.actions["available_at"] <= as_of]
        if ids is not None:
            a = a[a["security_id"].isin(ids)]
        return a.reset_index(drop=True)

    def get_observations(self, as_of, fields=None, ids=None) -> pd.DataFrame:
        """Latest known vintage of each (security_id, field, effective_at) as of ``as_of``.
        A value revised after ``as_of`` shows its earlier, then-known figure."""
        as_of = to_utc(as_of)
        o = self.observations[self.observations["available_at"] <= as_of]
        if fields is not None:
            o = o[o["field"].isin(fields)]
        if ids is not None:
            o = o[o["security_id"].isin(ids)]
        o = o.sort_values("available_at")
        return (
            o.drop_duplicates(["security_id", "field", "effective_at"], keep="last")
            .sort_values(["security_id", "field", "effective_at"])
            .reset_index(drop=True)
        )

    # --- derived series -----------------------------------------------------------------

    def total_returns(self, as_of, ids=None, start=None) -> pd.DataFrame:
        """Daily close-to-close total returns (wide: date × security_id), computed from raw
        prices plus only the splits and dividends known as of ``as_of``.

        On an ex-date with split ratio s and dividend D (per post-split share):
            r_t = s · (P_t + D) / P_{t-1} − 1
        """
        b = self.get_bars(as_of, ids, start)
        if b.empty:
            return pd.DataFrame()
        a = self.get_actions(as_of, ids)
        close = b.pivot_table(index="date", columns="security_id", values="close", aggfunc="last")
        split = _action_wide(a, "split", close, fill=1.0, agg="prod")
        div = _action_wide(a, "dividend", close, fill=0.0, agg="sum")
        r = split * (close + div) / close.shift(1) - 1.0
        return r.iloc[1:]

    def adv_dollars(self, as_of, window: int = 20, ids=None) -> pd.Series:
        """Average daily dollar volume over the last ``window`` known bars."""
        b = self.get_bars(as_of, ids)
        b = b.assign(dv=b["close"] * b["volume"])
        return b.groupby("security_id")["dv"].apply(lambda s: s.tail(window).mean())


def _action_wide(
    a: pd.DataFrame, kind: str, like: pd.DataFrame, fill: float, agg: str
) -> pd.DataFrame:
    out = pd.DataFrame(fill, index=like.index, columns=like.columns)
    sub = a[a["kind"] == kind]
    if sub.empty:
        return out
    w = sub.pivot_table(index="ex_date", columns="security_id", values="value", aggfunc=agg)
    w.index = pd.to_datetime(w.index)
    w = w.reindex(index=like.index, columns=like.columns)
    return w.fillna(fill).astype(np.float64)


def _empty(kind: str) -> pd.DataFrame:
    from claudetrade.data.schema import ACTION_COLUMNS, BAR_COLUMNS, OBSERVATION_COLUMNS

    cols = {"bars": BAR_COLUMNS, "actions": ACTION_COLUMNS, "observations": OBSERVATION_COLUMNS}[
        kind
    ]
    df = pd.DataFrame({c: [] for c in cols})
    df["available_at"] = pd.to_datetime(df["available_at"], utc=True)
    return df
