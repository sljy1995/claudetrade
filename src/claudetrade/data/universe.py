"""Universe membership as dated intervals, queried point in time."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml


@dataclass
class Universe:
    name: str
    members_df: pd.DataFrame  # columns: security_id, start, end (end NaT = still a member)
    point_in_time: bool  # False → membership reflects today's list: survivorship bias.

    def members(self, as_of) -> list[str]:
        d = pd.Timestamp(as_of)
        d = (
            d.tz_convert("America/New_York").tz_localize(None).normalize()
            if d.tzinfo
            else d.normalize()
        )
        m = self.members_df
        mask = (m["start"] <= d) & (m["end"].isna() | (m["end"] > d))
        return sorted(m.loc[mask, "security_id"].unique())

    @property
    def flags(self) -> set[str]:
        return set() if self.point_in_time else {"SURVIVORSHIP_BIASED"}

    @classmethod
    def from_yaml(cls, path: str | Path) -> Universe:
        """Format:

        name: us_large_cap_static
        point_in_time: false
        members:
          - {security_id: "YF:AAPL", start: 2000-01-01}
          - {security_id: "YF:XYZ", start: 2001-01-01, end: 2015-06-30}
        """
        cfg = yaml.safe_load(Path(path).read_text())
        df = pd.DataFrame(cfg["members"])
        df["start"] = pd.to_datetime(df["start"])
        df["end"] = pd.to_datetime(df.get("end"))
        return cls(
            cfg["name"], df[["security_id", "start", "end"]], bool(cfg.get("point_in_time", False))
        )
