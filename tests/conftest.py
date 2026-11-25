import numpy as np
import pandas as pd
import pytest

from claudetrade.data.schema import eod_available_at


def make_bars(ids=("A", "B"), start="2024-01-02", periods=30, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=periods)
    rows = []
    for sid in ids:
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, periods)))
        open_ = close * (1 + rng.normal(0, 0.002, periods))
        high = np.maximum(open_, close) * 1.005
        low = np.minimum(open_, close) * 0.995
        for d, o, h, lo, c in zip(dates, open_, high, low, close, strict=True):
            rows.append((sid, d, o, h, lo, c, 1_000_000, eod_available_at(d), "test"))
    return pd.DataFrame(
        rows,
        columns=[
            "security_id",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "available_at",
            "source",
        ],
    )


def empty_actions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "security_id": pd.Series(dtype=str),
            "ex_date": pd.Series(dtype="datetime64[ns]"),
            "kind": pd.Series(dtype=str),
            "value": pd.Series(dtype=float),
            "available_at": pd.Series(dtype="datetime64[ns, UTC]"),
            "source": pd.Series(dtype=str),
        }
    )


@pytest.fixture
def bars():
    return make_bars()
