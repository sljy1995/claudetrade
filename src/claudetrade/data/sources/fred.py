"""FRED / ALFRED macro series with full vintage history.

ALFRED returns every vintage of every observation with its ``realtime_start`` (the date
that value was first published). Using it means a backtest sees GDP, payrolls, CPI etc.
as first reported, not as later revised.
"""

from __future__ import annotations

import os

import pandas as pd
import requests

from claudetrade.data.schema import OBSERVATION_COLUMNS, end_of_day_utc

SOURCE = "alfred"
API = "https://api.stlouisfed.org/fred/series/observations"


def security_id(series_id: str) -> str:
    return f"MACRO:{series_id}"


def from_alfred_json(series_id: str, payload: dict) -> pd.DataFrame:
    obs = pd.DataFrame(payload["observations"])
    obs = obs[obs["value"] != "."]  # FRED's missing-value marker
    out = pd.DataFrame(
        {
            "security_id": security_id(series_id),
            "field": "value",
            "effective_at": pd.to_datetime(obs["date"]),
            "value": pd.to_numeric(obs["value"]),
            # Release time of day is not given; treat as known only at the end of that day.
            "available_at": pd.to_datetime(obs["realtime_start"]).map(end_of_day_utc),
            "source": SOURCE,
        }
    )
    return out[OBSERVATION_COLUMNS].reset_index(drop=True)


def fetch(series_ids: list[str], api_key: str | None = None) -> pd.DataFrame:
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY is not set. Add it as an environment secret.")
    frames = []
    for sid in series_ids:
        r = requests.get(
            API,
            params={
                "series_id": sid,
                "api_key": key,
                "file_type": "json",
                "realtime_start": "1776-07-04",
                "realtime_end": "9999-12-31",
            },
            timeout=60,
        )
        r.raise_for_status()
        frames.append(from_alfred_json(sid, r.json()))
    return pd.concat(frames, ignore_index=True)
