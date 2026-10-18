"""Per-symbol feature frame and the deterministic entry/exit rules.

The rule set (docs/STRATEGY.md), evaluated on the close of each bar:

1. **Trend template** — close above the long MA; the long MA rising over every
   configured lookback; mid MA above long MA; close within ``min_pct_of_high``
   of the trailing high; long (12-1 style) momentum positive.
2. **Setup "was higher, then reset"** — the vol-normalised impulse ROC reached
   ``impulse_min_z`` recently (excluding the last ``impulse_skip`` bars); the
   short ROC has decayed into ``[reset_z_min, reset_z_max]``; the pullback from
   the recent closing high is between ``pullback_min_atr`` and
   ``pullback_max_atr`` ATRs; and volume has dried up.
3. **Trigger** — within ``setup_valid_bars`` of a setup: close above the prior
   two highs, above a short EMA, and the very short ROC positive.

Every value at row *t* depends only on rows ``<= t``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from hive.config import TrendRules
from hive.data.bars import adjust_ohlc, has_true_ohlc
from hive.signals import indicators as ind

FEATURE_COLUMNS = [
    "close", "raw_close", "ma_long", "ma_mid", "ema_trig", "atr", "atr_pct", "sigma_d", "vol_ann",
    "pct_of_high", "mom_long", "mom_vol_adj", "z_impulse", "impulse_max", "z_reset",
    "pullback_atr", "volume_ratio", "swing_low",
    "trend_ok", "template_ok", "impulse_ok", "reset_ok", "depth_ok", "volume_ok",
    "setup", "setup_recent", "trigger", "entry_signal",
    "below_mid_streak", "below_long",
]


def _atr_with_fallback(px: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ATR; for close-only sources use the RMA of |close - prev close|."""
    if has_true_ohlc(px):
        return ind.atr(px["high"], px["low"], px["close"], period)
    cc = px["close"].diff().abs()
    return cc.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def zroc(close: pd.Series, period: int, sigma_d: pd.Series) -> pd.Series:
    """ln(C_t / C_{t-N}) / (sigma_d * sqrt(N)) — volatility-normalised ROC."""
    lr = np.log(close / close.shift(period))
    return lr / (sigma_d * np.sqrt(period))


def _streak(flag: pd.Series) -> pd.Series:
    """Length of the current run of True values (0 when False)."""
    f = flag.fillna(False).astype(int)
    groups = (f != f.shift()).cumsum()
    return f.groupby(groups).cumsum() * f


def compute_features(bars: pd.DataFrame, rules: TrendRules) -> pd.DataFrame:
    px = adjust_ohlc(bars)
    close, high, low = px["close"], px["high"], px["low"]
    f = pd.DataFrame(index=px.index)
    f["close"] = close
    # Unadjusted close, for dollar-denominated filters (min price, lot maths).
    f["raw_close"] = bars["close"]

    # --- volatility & ranges --------------------------------------------------
    log_ret = np.log(close / close.shift(1))
    f["sigma_d"] = log_ret.rolling(rules.sigma_period, min_periods=rules.sigma_period).std()
    f["vol_ann"] = f["sigma_d"] * np.sqrt(rules.bars_per_year)
    f["atr"] = _atr_with_fallback(px, rules.atr_period)
    f["atr_pct"] = f["atr"] / close * 100.0

    # --- trend template ---------------------------------------------------------
    f["ma_long"] = ind.sma(close, rules.ma_long)
    f["ma_mid"] = ind.sma(close, rules.ma_mid)
    slope_ok = pd.Series(True, index=px.index)
    for lb in rules.ma_slope_lookbacks:
        slope_ok &= f["ma_long"] > f["ma_long"].shift(lb)
    f["pct_of_high"] = close / ind.rolling_high(high, rules.high_lookback)
    f["mom_long"] = close.shift(rules.mom_skip) / close.shift(rules.mom_period) - 1.0
    ann_sigma = log_ret.rolling(rules.mom_period, min_periods=rules.mom_period // 2).std() * np.sqrt(
        rules.bars_per_year
    )
    f["mom_vol_adj"] = f["mom_long"] / ann_sigma.replace(0.0, np.nan)
    f["trend_ok"] = (close > f["ma_long"]) & slope_ok
    template = (
        f["trend_ok"]
        & (f["ma_mid"] > f["ma_long"])
        & (f["pct_of_high"] >= rules.min_pct_of_high)
    )
    if rules.require_long_momentum:
        template &= f["mom_long"] > 0
    f["template_ok"] = template

    # --- setup ------------------------------------------------------------------
    f["z_impulse"] = zroc(close, rules.impulse_period, f["sigma_d"])
    f["impulse_max"] = (
        f["z_impulse"].shift(rules.impulse_skip)
        .rolling(rules.impulse_window, min_periods=max(5, rules.impulse_window // 4)).max()
    )
    f["impulse_ok"] = f["impulse_max"] >= rules.impulse_min_z
    f["z_reset"] = zroc(close, rules.reset_period, f["sigma_d"])
    f["reset_ok"] = (f["z_reset"] >= rules.reset_z_min) & (f["z_reset"] <= rules.reset_z_max)
    recent_high = close.rolling(rules.pullback_high_lookback, min_periods=5).max()
    f["pullback_atr"] = (recent_high - close) / f["atr"]
    f["depth_ok"] = (f["pullback_atr"] >= rules.pullback_min_atr) & (
        f["pullback_atr"] <= rules.pullback_max_atr
    )
    vol = px["volume"]
    if rules.require_volume and vol.gt(0).any():
        f["volume_ratio"] = ind.sma(vol, rules.volume_short) / ind.sma(vol, rules.volume_long)
        f["volume_ok"] = f["volume_ratio"] <= rules.volume_max_ratio
    else:
        f["volume_ratio"] = np.nan
        f["volume_ok"] = True
    f["setup"] = f["impulse_ok"] & f["reset_ok"] & f["depth_ok"] & f["volume_ok"]
    f["setup_recent"] = f["setup"].astype(int).rolling(rules.setup_valid_bars, min_periods=1).max() > 0

    # --- trigger ------------------------------------------------------------------
    f["ema_trig"] = ind.ema(close, rules.trigger_ema)
    prior_two_high = pd.concat([high.shift(1), high.shift(2)], axis=1).max(axis=1)
    f["trigger"] = (
        (close > prior_two_high)
        & (close > f["ema_trig"])
        & (ind.roc_pct(close, rules.trigger_roc) > 0)
    )
    f["entry_signal"] = f["template_ok"] & f["setup_recent"] & f["trigger"]

    # --- stop reference & exit helpers ---------------------------------------------
    f["swing_low"] = low.rolling(rules.stop_swing_low_bars, min_periods=1).min()
    f["below_mid_streak"] = _streak(close < f["ma_mid"])
    f["below_long"] = close < f["ma_long"]

    bool_cols = ["trend_ok", "template_ok", "impulse_ok", "reset_ok", "depth_ok", "volume_ok",
                 "setup", "setup_recent", "trigger", "entry_signal", "below_long"]
    for col in bool_cols:
        f[col] = f[col].fillna(False).astype(bool)
    return f[FEATURE_COLUMNS]


def initial_stop(entry_price: float, row: pd.Series, rules: TrendRules) -> float:
    """``max(swing low - pad*ATR, entry - k*ATR)`` — the wider-of-two protective stop."""
    atr = float(row["atr"])
    swing = float(row["swing_low"]) if pd.notna(row["swing_low"]) else entry_price - rules.stop_initial_atr * atr
    return max(swing - rules.stop_swing_low_atr_pad * atr, entry_price - rules.stop_initial_atr * atr)


def rank_candidates(cands: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional score: 0.6 * rank(vol-adjusted long momentum) + 0.4 * rank(closeness to high).

    Revision and quality terms from the spec need fundamentals and are added
    by the orchestrator when available.
    """
    if cands.empty:
        return cands
    out = cands.copy()
    mom = out["mom_vol_adj"].fillna(out["mom_vol_adj"].min())
    out["score"] = 0.6 * mom.rank(pct=True) + 0.4 * out["pct_of_high"].rank(pct=True)
    return out.sort_values("score", ascending=False).reset_index(drop=True)


def latest_candidates(
    features_by_symbol: dict[str, pd.DataFrame], when: pd.Timestamp | str
) -> pd.DataFrame:
    """Entry signals that fire on ``when``, ranked. One row per symbol."""
    ts = pd.Timestamp(when)
    rows = []
    for sym, f in features_by_symbol.items():
        if ts not in f.index:
            continue
        row = f.loc[ts]
        if bool(row["entry_signal"]):
            rows.append({"symbol": sym, **row.to_dict()})
    if not rows:
        return pd.DataFrame(columns=["symbol", "score", *FEATURE_COLUMNS])
    return rank_candidates(pd.DataFrame(rows))
