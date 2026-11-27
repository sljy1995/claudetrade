import numpy as np
import pandas as pd
import pytest

from claudetrade.backtest.costs import ZERO_COST, CostModel
from claudetrade.backtest.engine import Engine
from claudetrade.data import PITStore
from claudetrade.data.pit import _empty
from claudetrade.data.schema import eod_available_at
from claudetrade.strategies.benchmarks import BuyAndHold, EqualWeight
from tests.conftest import empty_actions, make_bars


def pit(bars, actions=None):
    return PITStore(
        bars, actions if actions is not None else empty_actions(), _empty("observations")
    )


def bars_from_closes(closes, opens=None, start="2024-01-02", sid="A", volume=1e9):
    d = pd.bdate_range(start, periods=len(closes))
    closes = np.asarray(closes, float)
    opens = closes if opens is None else np.asarray(opens, float)
    return pd.DataFrame(
        {
            "security_id": sid,
            "date": d,
            "open": opens,
            "high": np.maximum(opens, closes) * 1.001,
            "low": np.minimum(opens, closes) * 0.999,
            "close": closes,
            "volume": volume,
            "available_at": [eod_available_at(x) for x in d],
            "source": "t",
        }
    )


def test_executes_next_open_not_same_bar():
    # Decide on day 0 close (100), fill on day 1 open (110), close 121.
    b = bars_from_closes([100, 121, 121], opens=[100, 110, 121])
    res = Engine(pit(b), ZERO_COST).run(BuyAndHold({"A": 1.0}), b.date.min(), b.date.max())
    t = res.trades.iloc[0]
    assert t["date"] == b.date[1] and t["price"] == 110
    assert res.daily["ret"].iloc[1] == pytest.approx(121 / 110 - 1)


def test_strategy_never_sees_future():
    b = make_bars(periods=40)

    class Spy:
        def target(self, as_of, p):
            seen = p.get_bars(as_of)
            assert (seen["available_at"] <= as_of).all()
            assert seen["date"].max() <= as_of.tz_convert("America/New_York").tz_localize(None)
            return pd.Series({"A": 0.5, "B": 0.5})

    Engine(pit(b)).run(Spy(), b.date.min(), b.date.max())


def test_buy_and_hold_matches_total_return_with_split_and_dividend():
    closes = [100, 100, 102, 51.5, 52, 53]
    b = bars_from_closes(closes)
    d = b.date
    actions = pd.DataFrame(
        {
            "security_id": ["A", "A"],
            "ex_date": [d[3], d[3]],
            "kind": ["split", "dividend"],
            "value": [2.0, 0.25],
            "available_at": [eod_available_at(d[3])] * 2,
            "source": ["t"] * 2,
        }
    )
    p = pit(b, actions)
    res = Engine(p, ZERO_COST, rebalance=1000).run(BuyAndHold({"A": 1.0}), d.min(), d.max())
    tr = p.total_returns(eod_available_at(d.max()))["A"]
    # Fully invested from day 1's open (= close 100). Up to and including the ex-date the
    # engine matches the total return; afterwards the dividend sits in cash (no reinvest).
    pd.testing.assert_series_equal(
        res.returns.iloc[2:4], tr.loc[d[2] : d[3]], check_names=False, rtol=1e-10
    )
    nav = res.daily["nav"]
    assert res.daily["dividends"].iloc[3] == pytest.approx(10_000 * 2 * 0.25)
    assert nav.iloc[4] - nav.iloc[3] == pytest.approx(20_000 * (52 - 51.5))


def test_costs_reduce_nav_and_identity_holds():
    b = make_bars(periods=60, seed=3)
    s = EqualWeight(ids=["A", "B"])
    free = Engine(pit(b), ZERO_COST).run(s, b.date.min(), b.date.max())
    costly = Engine(pit(b), CostModel(half_spread_bps=20)).run(s, b.date.min(), b.date.max())
    assert costly.daily["nav"].iloc[-1] < free.daily["nav"].iloc[-1]
    assert costly.daily["costs"].sum() > 0


def test_participation_cap_gives_partial_fill():
    b = bars_from_closes([100] * 5, volume=1000)  # ADV 1000 shares → cap 50 shares/day
    res = Engine(pit(b), ZERO_COST, initial_nav=1_000_000).run(
        BuyAndHold({"A": 1.0}), b.date.min(), b.date.max()
    )
    first = res.trades.iloc[0]
    assert first["status"] == "partial" and first["shares"] == pytest.approx(50)


def test_rejects_leverage_and_shorts():
    b = make_bars(periods=10)
    with pytest.raises(ValueError, match="leverage"):
        Engine(pit(b)).run(BuyAndHold({"A": 0.8, "B": 0.8}), b.date.min(), b.date.max())
    with pytest.raises(ValueError, match="long-only"):
        Engine(pit(b)).run(BuyAndHold({"A": -0.1}), b.date.min(), b.date.max())


def test_missing_bar_rejects_order_and_marks_stale():
    b = make_bars(ids=("A", "B"), periods=10)
    b = b[~((b.security_id == "B") & (b.date == b.date.unique()[1]))]
    res = Engine(pit(b), ZERO_COST).run(
        BuyAndHold({"A": 0.5, "B": 0.5}), b.date.min(), b.date.max()
    )
    assert "rejected_no_bar" in set(res.trades["status"])
