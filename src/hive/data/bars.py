"""Canonical daily-bar frame and point-in-time helpers."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]

_DATE_CANDIDATES = ("date", "time", "timestamp", "datetime", "t")
_COLUMN_ALIASES = {
    "adj close": "adj_close",
    "adjclose": "adj_close",
    "adjusted close": "adj_close",
    "adjusted_close": "adj_close",
    "priceusd": "close",
    "price": "close",
    "vol": "volume",
    "v": "volume",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
}


def normalize_bars(df: pd.DataFrame, *, date_col: str | None = None) -> pd.DataFrame:
    """Coerce any reasonable OHLCV table into the canonical bars frame.

    * Finds the date column (or uses a DatetimeIndex), strips tz, floors to day.
    * Lower-cases / aliases column names (``Adj Close`` -> ``adj_close`` ...).
    * Fills ``adj_close`` from ``close``, and ``open/high/low`` from ``close``
      when a source is close-only (e.g. CoinMetrics). ``volume`` defaults to 0.
    * Sorts ascending, drops duplicate dates (keeps last) and rows without a close.
    """
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    out = out.rename(columns={c: _COLUMN_ALIASES.get(c, c.replace(" ", "_")) for c in out.columns})

    if date_col is not None:
        date_col = date_col.strip().lower()
    elif not isinstance(out.index, pd.DatetimeIndex):
        date_col = next((c for c in _DATE_CANDIDATES if c in out.columns), None)
        if date_col is None:
            raise ValueError("normalize_bars: no DatetimeIndex and no recognisable date column")

    if date_col is not None:
        idx = pd.to_datetime(out[date_col], utc=True, errors="coerce")
        out = out.drop(columns=[date_col])
        out.index = idx
    elif out.index.tz is None:
        out.index = out.index.tz_localize("UTC")

    out = out[out.index.notna()]
    out.index = out.index.tz_convert("UTC").tz_localize(None).normalize()
    out.index.name = "date"

    if "close" not in out.columns:
        raise ValueError("normalize_bars: a 'close' column is required")
    for col in ("open", "high", "low"):
        if col not in out.columns:
            out[col] = out["close"]
    if "adj_close" not in out.columns:
        out["adj_close"] = out["close"]
    if "volume" not in out.columns:
        out["volume"] = 0.0

    out = out[BAR_COLUMNS].apply(pd.to_numeric, errors="coerce").astype("float64")
    out = out.dropna(subset=["close"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def adjust_ohlc(bars: pd.DataFrame) -> pd.DataFrame:
    """Return bars with open/high/low/close scaled to the total-return series.

    The factor ``adj_close / close`` folds dividends and splits into all four
    price columns, so indicators and P&L both see one consistent series.
    """
    factor = (bars["adj_close"] / bars["close"]).fillna(1.0)
    out = bars.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = bars[col] * factor
    return out


def as_of(bars: pd.DataFrame, when: date | datetime | str | pd.Timestamp) -> pd.DataFrame:
    """Rows with index <= ``when`` (inclusive). The only sanctioned way to slice time."""
    ts = pd.Timestamp(when)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return bars.loc[: ts.normalize()]


def has_true_ohlc(bars: pd.DataFrame) -> bool:
    """False when a source was close-only (high == low == close everywhere)."""
    return bool(((bars["high"] != bars["close"]) | (bars["low"] != bars["close"])).any())
