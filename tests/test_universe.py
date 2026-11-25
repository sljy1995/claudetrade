import pandas as pd

from claudetrade.data import Universe


def test_members_point_in_time(tmp_path):
    p = tmp_path / "u.yaml"
    p.write_text("""
name: t
point_in_time: true
members:
  - {security_id: "YF:A", start: 2000-01-01}
  - {security_id: "YF:B", start: 2005-01-01, end: 2010-06-30}
""")
    u = Universe.from_yaml(p)
    assert u.members("2004-12-31") == ["YF:A"]
    assert u.members(pd.Timestamp("2008-01-01", tz="UTC")) == ["YF:A", "YF:B"]
    assert u.members("2010-06-30") == ["YF:A"]
    assert u.flags == set()


def test_static_universe_is_flagged(tmp_path):
    p = tmp_path / "u.yaml"
    p.write_text("name: s\nmembers:\n  - {security_id: 'YF:A', start: 2000-01-01}\n")
    assert Universe.from_yaml(p).flags == {"SURVIVORSHIP_BIASED"}
