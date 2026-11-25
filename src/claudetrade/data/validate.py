"""Validation. Errors block use of the data (fail closed); warnings are recorded."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from claudetrade.data.errors import DataValidationError
from claudetrade.data.schema import (
    ACTION_COLUMNS,
    ACTION_KINDS,
    BAR_COLUMNS,
    OBSERVATION_COLUMNS,
    market_close_utc,
)

# A one-day move this large with no corporate action is almost always a data error.
EXTREME_RETURN = 0.5


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    check: str
    detail: str


def _missing_columns(df: pd.DataFrame, required: list[str]) -> list[Issue]:
    missing = [c for c in required if c not in df.columns]
    return [Issue("error", "columns", f"missing columns {missing}")] if missing else []


def _check_available_at(df: pd.DataFrame) -> list[Issue]:
    col = df["available_at"]
    if not isinstance(col.dtype, pd.DatetimeTZDtype):
        return [Issue("error", "available_at", "must be tz-aware datetimes")]
    if col.isna().any():
        return [Issue("error", "available_at", f"{int(col.isna().sum())} null values")]
    return []


def validate_bars(df: pd.DataFrame, actions: pd.DataFrame | None = None) -> list[Issue]:
    issues = _missing_columns(df, BAR_COLUMNS)
    if issues:
        return issues
    issues += _check_available_at(df)

    dup = df.duplicated(["security_id", "date"])
    if dup.any():
        issues.append(Issue("error", "duplicates", f"{int(dup.sum())} duplicate (id, date) rows"))

    px = df[["open", "high", "low", "close"]]
    if px.isna().any().any():
        issues.append(Issue("error", "missing_prices", f"{int(px.isna().any(axis=1).sum())} rows"))
    if (px <= 0).any().any():
        issues.append(
            Issue("error", "non_positive_price", f"{int((px <= 0).any(axis=1).sum())} rows")
        )
    bad_hl = (df["high"] < df[["open", "close"]].max(axis=1) - 1e-9) | (
        df["low"] > df[["open", "close"]].min(axis=1) + 1e-9
    )
    if bad_hl.any():
        issues.append(Issue("error", "ohlc_inconsistent", f"{int(bad_hl.sum())} rows"))
    if (df["volume"] < 0).any():
        issues.append(Issue("error", "negative_volume", f"{int((df['volume'] < 0).sum())} rows"))

    if not issues or all(i.severity != "error" for i in issues):
        close_utc = pd.to_datetime(df["date"]).map(market_close_utc)
        early = df["available_at"] < pd.to_datetime(close_utc, utc=True)
        if early.any():
            issues.append(
                Issue(
                    "error",
                    "available_before_close",
                    f"{int(early.sum())} bars usable before their close",
                )
            )

        # Extreme moves not explained by a split.
        s = df.sort_values(["security_id", "date"])
        ret = s.groupby("security_id")["close"].pct_change()
        flagged = s.loc[ret.abs() > EXTREME_RETURN, ["security_id", "date"]]
        if actions is not None and len(flagged):
            splits = actions.loc[actions["kind"] == "split", ["security_id", "ex_date"]]
            keys = set(zip(splits["security_id"], pd.to_datetime(splits["ex_date"]), strict=True))
            flagged = flagged[
                [
                    (i, pd.Timestamp(d)) not in keys
                    for i, d in zip(flagged["security_id"], flagged["date"], strict=True)
                ]
            ]
        if len(flagged):
            sample = ", ".join(
                f"{i}@{pd.Timestamp(d).date()}" for i, d in flagged.head(5).to_numpy()
            )
            issues.append(
                Issue(
                    "warning",
                    "extreme_return",
                    f"{len(flagged)} |1-day return| > {EXTREME_RETURN:.0%} "
                    f"without a split: {sample}",
                )
            )
    return issues


def validate_actions(df: pd.DataFrame) -> list[Issue]:
    issues = _missing_columns(df, ACTION_COLUMNS)
    if issues:
        return issues
    issues += _check_available_at(df)
    bad_kind = ~df["kind"].isin(ACTION_KINDS)
    if bad_kind.any():
        issues.append(
            Issue(
                "error", "action_kind", f"unknown kinds {sorted(df.loc[bad_kind, 'kind'].unique())}"
            )
        )
    if (df["value"] <= 0).any() or df["value"].isna().any():
        issues.append(Issue("error", "action_value", "split ratios and dividends must be > 0"))
    return issues


def validate_observations(df: pd.DataFrame) -> list[Issue]:
    issues = _missing_columns(df, OBSERVATION_COLUMNS)
    if issues:
        return issues
    issues += _check_available_at(df)
    dup = df.duplicated(["security_id", "field", "effective_at", "available_at"])
    if dup.any():
        issues.append(Issue("error", "duplicates", f"{int(dup.sum())} duplicate vintages"))
    if not np.isfinite(pd.to_numeric(df["value"], errors="coerce")).all():
        issues.append(Issue("warning", "non_finite", "non-numeric or non-finite values present"))
    return issues


def raise_on_errors(issues: list[Issue]) -> list[Issue]:
    """Raise if any issue is an error; otherwise return the warnings."""
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise DataValidationError(errors)
    return issues
