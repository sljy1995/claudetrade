"""Immutable, content-addressed snapshots.

A snapshot is a parquet file named by the SHA-256 of its canonical content, plus a JSON
manifest. Writing identical content is a no-op; nothing is ever overwritten. Revisions
produce a new snapshot. Reads verify the hash, so silent corruption or edits are caught.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from claudetrade.data.errors import SnapshotIntegrityError
from claudetrade.data.validate import (
    raise_on_errors,
    validate_actions,
    validate_bars,
    validate_observations,
)

KINDS = {"bars", "actions", "observations"}
_SORT_KEYS = {
    "bars": ["security_id", "date"],
    "actions": ["security_id", "ex_date", "kind"],
    "observations": ["security_id", "field", "effective_at", "available_at"],
}


@dataclass
class Manifest:
    kind: str
    name: str
    content_hash: str
    rows: int
    created_at: str
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _canonical_bytes(df: pd.DataFrame, kind: str) -> bytes:
    df = df.sort_values(_SORT_KEYS[kind]).reset_index(drop=True)
    df = df[sorted(df.columns)]
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression=None)
    return buf.getvalue()


def content_hash(df: pd.DataFrame, kind: str) -> str:
    # Hash a stable CSV rendering: parquet bytes can embed library versions.
    df = df.sort_values(_SORT_KEYS[kind]).reset_index(drop=True)
    df = df[sorted(df.columns)]
    return hashlib.sha256(
        df.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S.%f%z").encode()
    ).hexdigest()


class SnapshotStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _dir(self, kind: str, name: str) -> Path:
        return self.root / kind / name

    def write(self, df: pd.DataFrame, kind: str, name: str, meta: dict | None = None) -> Manifest:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        validator = {
            "bars": validate_bars,
            "actions": validate_actions,
            "observations": validate_observations,
        }[kind]
        warnings = raise_on_errors(validator(df))

        h = content_hash(df, kind)
        d = self._dir(kind, name)
        d.mkdir(parents=True, exist_ok=True)
        data_path, man_path = d / f"{h}.parquet", d / f"{h}.json"
        if data_path.exists() and man_path.exists():
            return Manifest(**json.loads(man_path.read_text()))

        manifest = Manifest(
            kind=kind,
            name=name,
            content_hash=h,
            rows=len(df),
            created_at=pd.Timestamp.now(tz="UTC").isoformat(),
            meta=meta or {},
            warnings=[f"{w.check}: {w.detail}" for w in warnings],
        )
        tmp = data_path.with_suffix(".tmp")
        tmp.write_bytes(_canonical_bytes(df, kind))
        tmp.rename(data_path)
        man_path.write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True))
        with (d / "history.jsonl").open("a") as f:
            f.write(json.dumps({"content_hash": h, "created_at": manifest.created_at}) + "\n")
        return manifest

    def read(self, kind: str, name: str, content_hash_: str) -> pd.DataFrame:
        d = self._dir(kind, name)
        df = pd.read_parquet(d / f"{content_hash_}.parquet")
        actual = content_hash(df, kind)
        if actual != content_hash_:
            raise SnapshotIntegrityError(
                f"{kind}/{name}: expected {content_hash_[:12]}, file hashes to {actual[:12]}"
            )
        return df

    def manifest(self, kind: str, name: str, content_hash_: str) -> Manifest:
        return Manifest(**json.loads((self._dir(kind, name) / f"{content_hash_}.json").read_text()))

    def latest(self, kind: str, name: str) -> str:
        """Hash of the most recently written snapshot. Runs should record the hash they used
        rather than rely on 'latest', so they stay reproducible."""
        hist = self._dir(kind, name) / "history.jsonl"
        if not hist.exists():
            raise FileNotFoundError(f"No snapshots for {kind}/{name}")
        return json.loads(hist.read_text().strip().splitlines()[-1])["content_hash"]
