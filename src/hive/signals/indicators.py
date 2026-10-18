"""Vectorised technical indicators on daily bars.

All functions take pandas Series/DataFrames indexed by date (ascending) and
return Series aligned to the same index. They never look forward: every value
at row *t* depends only on rows ``<= t``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple moving average; NaN until ``period`` observations exist."""
    return close.rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def slope_pct(series: pd.Series, lookback: int) -> pd.Series:
    """Percent change of a series over ``lookback`` bars (used for MA slope)."""
    return (series / series.shift(lookback) - 1.0) * 100.0


def roc_pct(close: pd.Series, period: int) -> pd.Series:
    """Rate of change: percent return over the last ``period`` bars."""
    return (close / close.shift(period) - 1.0) * 100.0


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Average True Range (RMA smoothing)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rolling_high(series: pd.Series, lookback: int) -> pd.Series:
    return series.rolling(lookback, min_periods=1).max()


def pct_from_high(close: pd.Series, lookback: int = 252) -> pd.Series:
    """Distance from the trailing high in percent (<= 0)."""
    return (close / rolling_high(close, lookback) - 1.0) * 100.0


def realized_vol_annualized(close: pd.Series, lookback: int = 20, periods_per_year: int = 252) -> pd.Series:
    """Annualised standard deviation of daily log returns."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(lookback, min_periods=lookback).std() * np.sqrt(periods_per_year)


def drawdown(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak (<= 0)."""
    peak = equity.cummax()
    return equity / peak - 1.0


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI, 0-100."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # When there were no losses at all, RSI is 100 by convention.
    return out.where(avg_loss != 0.0, 100.0).where(avg_gain.notna())
