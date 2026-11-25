import numpy as np
import pandas as pd
import pytest

from claudetrade.data.sources import fred, yahoo
from claudetrade.data.validate import raise_on_errors, validate_actions, validate_bars


def _yf_frame():
    # Raw closes 100, 102, then a 2:1 split → 51.5, 52. yfinance reports split-adjusted.
    idx = pd.DatetimeIndex(pd.bdate_range("2024-03-01", periods=4)).tz_localize("America/New_York")
    raw = np.array([100.0, 102.0, 51.5, 52.0])
    adj = raw / np.array([2, 2, 1, 1])
    return pd.DataFrame(
        {
            "Open": adj,
            "High": adj * 1.01,
            "Low": adj * 0.99,
            "Close": adj,
            "Volume": [1000 * 2, 1000 * 2, 2000, 2000],  # adjusted volume
            "Dividends": [0.0, 0.25, 0.0, 0.0],  # split-adjusted: raw was 0.50
            "Stock Splits": [0.0, 0.0, 2.0, 0.0],
        },
        index=idx,
    )


def test_yahoo_reconstructs_raw_prices_and_actions():
    bars, actions = yahoo.from_yfinance_history("abc", _yf_frame())
    assert bars["security_id"].unique().tolist() == ["YF:ABC"]
    np.testing.assert_allclose(bars["close"], [100.0, 102.0, 51.5, 52.0])
    assert bars["volume"].tolist() == [1000, 1000, 2000, 2000]
    split = actions[actions["kind"] == "split"]
    div = actions[actions["kind"] == "dividend"]
    assert split["value"].item() == 2.0
    assert div["value"].item() == pytest.approx(0.50)
    raise_on_errors(validate_bars(bars, actions))
    raise_on_errors(validate_actions(actions))


def test_alfred_vintages_and_missing_marker():
    payload = {
        "observations": [
            {
                "date": "2024-01-01",
                "value": "2.0",
                "realtime_start": "2024-04-25",
                "realtime_end": "2024-05-29",
            },
            {
                "date": "2024-01-01",
                "value": "3.5",
                "realtime_start": "2024-05-30",
                "realtime_end": "9999-12-31",
            },
            {
                "date": "2024-04-01",
                "value": ".",
                "realtime_start": "2024-07-25",
                "realtime_end": "9999-12-31",
            },
        ]
    }
    obs = fred.from_alfred_json("GDP", payload)
    assert len(obs) == 2
    assert obs["available_at"].min() > pd.Timestamp("2024-04-25 23:00", tz="America/New_York")


def test_fred_requires_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FRED_API_KEY"):
        fred.fetch(["DGS10"])


@pytest.mark.network
def test_yahoo_live_fetch():
    bars, actions = yahoo.fetch(["AAPL"], "2020-08-20", "2020-09-10")  # 4:1 split 2020-08-31
    assert (actions["kind"] == "split").any()
    raise_on_errors(validate_bars(bars, actions))
