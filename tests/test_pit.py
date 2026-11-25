"""Point-in-time guarantees: nothing published after as_of is ever visible."""

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from claudetrade.data import PITStore
from claudetrade.data.pit import _empty
from claudetrade.data.schema import eod_available_at, market_close_utc
from tests.conftest import empty_actions, make_bars

BARS = make_bars(periods=60)
DATES = sorted(BARS["date"].unique())


def store(bars=BARS, actions=None, obs=None):
    return PITStore(
        bars,
        actions if actions is not None else empty_actions(),
        obs if obs is not None else _empty("observations"),
    )


def test_bar_not_visible_at_its_own_close():
    d = DATES[10]
    s = store()
    assert d not in set(s.get_bars(market_close_utc(d))["date"])
    assert d in set(s.get_bars(eod_available_at(d))["date"])


@settings(max_examples=50, deadline=None)
@given(st.integers(min_value=0, max_value=59), st.integers(min_value=-600, max_value=600))
def test_never_returns_future_data(i, minutes):
    as_of = eod_available_at(DATES[i]) + pd.Timedelta(minutes=minutes)
    got = store().get_bars(as_of)
    assert (got["available_at"] <= as_of).all()


@settings(max_examples=30, deadline=None)
@given(st.integers(min_value=5, max_value=40))
def test_future_append_invariance(i):
    """Appending data after as_of must not change anything seen at as_of."""
    as_of = eod_available_at(DATES[i])
    past = BARS[BARS["date"] <= DATES[i]]
    a = store(bars=past).total_returns(as_of)
    b = store(bars=BARS).total_returns(as_of)
    pd.testing.assert_frame_equal(a, b)


def test_vintages_return_first_release_before_revision():
    obs = pd.DataFrame(
        {
            "security_id": ["MACRO:GDP"] * 2,
            "field": ["value"] * 2,
            "effective_at": pd.to_datetime(["2024-01-01"] * 2),
            "value": [2.0, 3.5],
            "available_at": pd.to_datetime(["2024-04-25 23:59", "2024-05-30 23:59"]).tz_localize(
                "UTC"
            ),
            "source": ["test"] * 2,
        }
    )
    s = store(obs=obs)
    assert s.get_observations(pd.Timestamp("2024-04-24", tz="UTC")).empty
    assert s.get_observations(pd.Timestamp("2024-05-01", tz="UTC"))["value"].item() == 2.0
    assert s.get_observations(pd.Timestamp("2024-06-01", tz="UTC"))["value"].item() == 3.5


def test_split_and_dividend_give_correct_total_return():
    d = pd.bdate_range("2024-01-02", periods=3)
    bars = pd.DataFrame(
        {
            "security_id": "A",
            "date": d,
            "open": [100, 100, 51],
            "high": [101, 101, 52],
            "low": [99, 99, 50],
            "close": [100.0, 100.0, 51.0],
            "volume": 1000,
            "available_at": [eod_available_at(x) for x in d],
            "source": "t",
        }
    )
    # 2:1 split and a $0.50 (post-split) dividend on day 3: 2 × (51 + 0.5) / 100 − 1 = 3%
    actions = pd.DataFrame(
        {
            "security_id": ["A", "A"],
            "ex_date": [d[2], d[2]],
            "kind": ["split", "dividend"],
            "value": [2.0, 0.5],
            "available_at": [eod_available_at(d[2])] * 2,
            "source": ["t"] * 2,
        }
    )
    r = store(bars=bars, actions=actions).total_returns(eod_available_at(d[2]))["A"]
    assert abs(r.iloc[0]) < 1e-12
    assert abs(r.iloc[1] - 0.03) < 1e-12


def test_naive_as_of_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        store().get_bars(pd.Timestamp("2024-02-01"))
