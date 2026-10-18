"""Data-quality guards.

Public, Yahoo-derived histories contain corrupted prints — typically around
delistings and acquisitions (e.g. a x1000 close on a company's last days).
One such row turns a backtest into fiction, so every series is truncated at
its first implausible move before any signal sees it. The same guard runs in
the live cycle: a corrupted feed excludes the symbol rather than trading it.

What counts as implausible (deliberately conservative, but it must keep
genuine events such as Bitcoin's +55% day in 2010 or a financial's +100%
rebound in 2008):

* a one-day move larger than ``max_abs_return`` **that reverts** — within the
  next ``confirm_bars`` bars the price comes back to within ``revert_band`` of
  the pre-move price (a spike), or
* such a move within the last ``confirm_bars`` bars of the series, where it
  cannot be verified (the delisting-day case), or
* an OHLC row that is internally inconsistent or non-positive.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MAX_ABS_DAILY_RETURN = 0.50
CONFIRM_BARS = 5
REVERT_BAND = 0.30
# Crypto's early history has genuine 50%+ days that also partly retrace.
MAX_ABS_RETURN_BY_CLASS = {"crypto": 0.75}


def max_abs_return_for(asset_class: str) -> float:
    return MAX_ABS_RETURN_BY_CLASS.get(asset_class, MAX_ABS_DAILY_RETURN)


@dataclass(frozen=True)
class Anomaly:
    date: pd.Timestamp
    kind: str
    value: float


def find_first_anomaly(
    bars: pd.DataFrame,
    *,
    max_abs_return: float = MAX_ABS_DAILY_RETURN,
    confirm_bars: int = CONFIRM_BARS,
    revert_band: float = REVERT_BAND,
) -> Anomaly | None:
    if bars.empty:
        return None
    px = bars["adj_close"].to_numpy(dtype=float)
    n = len(px)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.r_[np.nan, px[1:] / px[:-1] - 1.0]
    candidates: list[Anomaly] = []
    for i in np.flatnonzero(np.abs(ret) > max_abs_return):
        prev = px[i - 1]
        if i + confirm_bars >= n:
            candidates.append(Anomaly(bars.index[i], "unverifiable_move", float(ret[i])))
            break
        window = px[i + 1 : i + 1 + confirm_bars]
        if np.nanmin(np.abs(window / prev - 1.0)) < revert_band:
            candidates.append(Anomaly(bars.index[i], "spike_reverted", float(ret[i])))
            break
    bad = (bars["high"] < bars["low"]) | (bars["close"] > bars["high"] * 1.5) | (
        bars["close"] < bars["low"] * 0.5) | (bars["close"] <= 0)
    if bad.any():
        d = bars.index[bad.to_numpy()][0]
        candidates.append(Anomaly(d, "ohlc", float(bars.loc[d, "close"])))
    return min(candidates, key=lambda a: a.date) if candidates else None


def truncate_at_first_anomaly(bars: pd.DataFrame, **kw) -> tuple[pd.DataFrame, Anomaly | None]:
    """Return the series up to (excluding) the first anomalous row."""
    a = find_first_anomaly(bars, **kw)
    if a is None:
        return bars, None
    return bars.loc[: a.date - pd.Timedelta(days=1)], a


def clean_universe(
    bars_by_symbol: dict[str, pd.DataFrame], *, min_bars: int = 1,
    asset_classes: dict[str, str] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Anomaly]]:
    cleaned: dict[str, pd.DataFrame] = {}
    anomalies: dict[str, Anomaly] = {}
    for sym, b in bars_by_symbol.items():
        ac = (asset_classes or {}).get(sym, "stock")
        c, a = truncate_at_first_anomaly(b, max_abs_return=max_abs_return_for(ac))
        if a is not None:
            anomalies[sym] = a
        if len(c) >= min_bars:
            cleaned[sym] = c
    return cleaned, anomalies
