"""Fetch source data into immutable snapshots.

uv run python -m claudetrade.data.fetch yahoo --tickers AAPL MSFT --start 2005-01-01
uv run python -m claudetrade.data.fetch fred --series DGS10 CPIAUCSL UNRATE
"""

from __future__ import annotations

import argparse
import json

from claudetrade.data.snapshots import SnapshotStore

DEFAULT_ROOT = "data/snapshots"


def main(argv=None):
    p = argparse.ArgumentParser(prog="claudetrade.data.fetch")
    p.add_argument("--root", default=DEFAULT_ROOT)
    sub = p.add_subparsers(dest="source", required=True)
    y = sub.add_parser("yahoo")
    y.add_argument("--tickers", nargs="+", required=True)
    y.add_argument("--start", required=True)
    y.add_argument("--end")
    y.add_argument("--name", default="yahoo_us_equities")
    f = sub.add_parser("fred")
    f.add_argument("--series", nargs="+", required=True)
    f.add_argument("--name", default="alfred_macro")
    args = p.parse_args(argv)

    store = SnapshotStore(args.root)
    out = {}
    if args.source == "yahoo":
        from claudetrade.data.sources import yahoo

        bars, actions = yahoo.fetch(args.tickers, args.start, args.end)
        meta = {
            "source": "yahoo",
            "tickers": sorted(args.tickers),
            "start": args.start,
            "end": args.end,
            "flags": sorted(yahoo.FLAGS),
        }
        out["bars"] = store.write(bars, "bars", args.name, meta).content_hash
        out["actions"] = store.write(actions, "actions", args.name, meta).content_hash
    else:
        from claudetrade.data.sources import fred

        obs = fred.fetch(args.series)
        out["observations"] = store.write(
            obs, "observations", args.name, {"source": "alfred", "series": sorted(args.series)}
        ).content_hash
    print(json.dumps({"name": args.name, **out}, indent=2))


if __name__ == "__main__":
    main()
