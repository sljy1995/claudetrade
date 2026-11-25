import pandas as pd
import pytest

from claudetrade.data import DataValidationError, SnapshotIntegrityError, SnapshotStore
from claudetrade.data.validate import validate_bars


def test_identical_content_same_hash_no_overwrite(tmp_path, bars):
    s = SnapshotStore(tmp_path)
    m1 = s.write(bars, "bars", "t")
    m2 = s.write(bars.sample(frac=1, random_state=1), "bars", "t")  # row order irrelevant
    assert m1.content_hash == m2.content_hash
    assert m1.created_at == m2.created_at


def test_revision_creates_new_snapshot(tmp_path, bars):
    s = SnapshotStore(tmp_path)
    h1 = s.write(bars, "bars", "t").content_hash
    revised = bars.copy()
    revised.loc[0, ["close", "high"]] = revised.loc[0, ["close", "high"]] * 1.01
    h2 = s.write(revised, "bars", "t").content_hash
    assert h1 != h2
    assert s.latest("bars", "t") == h2
    pd.testing.assert_frame_equal(
        s.read("bars", "t", h1).sort_values(["security_id", "date"]).reset_index(drop=True),
        bars.sort_values(["security_id", "date"]).reset_index(drop=True)[sorted(bars.columns)],
        check_like=True,
    )


def test_tampering_detected(tmp_path, bars):
    s = SnapshotStore(tmp_path)
    h = s.write(bars, "bars", "t").content_hash
    path = tmp_path / "bars" / "t" / f"{h}.parquet"
    df = pd.read_parquet(path)
    df.loc[0, "close"] *= 2
    df.to_parquet(path, index=False)
    with pytest.raises(SnapshotIntegrityError):
        s.read("bars", "t", h)


@pytest.mark.parametrize(
    "mutate,check",
    [
        (lambda b: pd.concat([b, b.iloc[[0]]]), "duplicates"),
        (lambda b: b.assign(high=b["low"] * 0.9), "ohlc_inconsistent"),
        (lambda b: b.assign(close=-1.0), "non_positive_price"),
        (
            lambda b: b.assign(available_at=b["available_at"] - pd.Timedelta(hours=2)),
            "available_before_close",
        ),
    ],
)
def test_bad_bars_fail_closed(tmp_path, bars, mutate, check):
    with pytest.raises(DataValidationError) as e:
        SnapshotStore(tmp_path).write(mutate(bars), "bars", "t")
    assert any(i.check == check for i in e.value.issues)


def test_unexplained_jump_is_warned(bars):
    b = bars.copy()
    idx = b.index[(b["security_id"] == "A")][10:]
    b.loc[idx, ["open", "high", "low", "close"]] *= 3
    issues = validate_bars(b)
    assert any(i.check == "extreme_return" and i.severity == "warning" for i in issues)
