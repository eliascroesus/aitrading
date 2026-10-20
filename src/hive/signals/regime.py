"""Benchmark regime: only add risk when the index itself is healthy.

Evidence: Faber (2007) MA timing cuts drawdowns; Cooper-Gutierrez-Hameed (2004)
momentum pays after UP markets and loses after DOWN markets; Daniel & Moskowitz
(2016) momentum crashes in "panic" states (after declines, with high vol).

States
------
GREEN   close > long MA and the MA rising                -> normal risk
YELLOW  above the MA but MA falling, or VIX > threshold   -> half risk
RED     close < long MA                                   -> no new stock longs
crash_guard  24m return < 0 and 21d vol > 30%, or 36m return < 0 -> no new momentum entries
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

from hive.config import RegimeRules
from hive.signals import indicators as ind


class RegimeState(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


REGIME_COLUMNS = ["state", "crash_guard", "risk_multiplier", "allow_new_stock", "allow_new_etf"]


def regime_frame(
    benchmark_bars: pd.DataFrame, rules: RegimeRules, vix: pd.Series | None = None
) -> pd.DataFrame:
    close = benchmark_bars["adj_close"]
    ma = ind.sma(close, rules.ma_long)
    rising = ma > ma.shift(rules.slope_lookback)
    above = close > ma

    state = pd.Series(RegimeState.RED.value, index=close.index, dtype="object")
    state[above & rising] = RegimeState.GREEN.value
    state[above & ~rising] = RegimeState.YELLOW.value
    if vix is not None and rules.vix_yellow_threshold is not None:
        v = vix.reindex(close.index).ffill()
        state[(state == RegimeState.GREEN.value) & (v > rules.vix_yellow_threshold)] = (
            RegimeState.YELLOW.value
        )

    log_ret = np.log(close / close.shift(1))
    long_ret = close / close.shift(rules.crash_long_return_bars) - 1.0
    very_long_ret = close / close.shift(rules.crash_very_long_return_bars) - 1.0
    vol = log_ret.rolling(rules.crash_vol_bars).std() * np.sqrt(252)
    crash = ((long_ret < 0) & (vol > rules.crash_vol_ann_threshold)) | (very_long_ret < 0)
    crash = crash.fillna(False).astype(bool)

    out = pd.DataFrame(index=close.index)
    out["state"] = state
    out["crash_guard"] = crash
    out["risk_multiplier"] = np.select(
        [state == RegimeState.GREEN.value, state == RegimeState.YELLOW.value],
        [1.0, rules.yellow_risk_multiplier], default=0.0,
    )
    out["allow_new_stock"] = (state != RegimeState.RED.value) & ~crash
    # ETFs may still enter in RED if they pass their own template; crash guard still binds.
    out["allow_new_etf"] = ~crash
    # Warm-up: no MA yet -> treat as RED/blocked.
    warm = ma.isna()
    out.loc[warm, ["allow_new_stock", "allow_new_etf"]] = False
    out.loc[warm, "risk_multiplier"] = 0.0
    out.loc[warm, "state"] = RegimeState.RED.value
    return out[REGIME_COLUMNS]


def class_multipliers(reg: pd.Series | None, *, use_filter: bool = True) -> dict[str, float]:
    """Per-asset-class risk multiplier for new entries (0 = blocked).

    RED blocks stocks and crypto outright; ETFs passing their own template may
    still enter at full size. YELLOW halves everything. The crash guard blocks
    all. Shared by the backtester and the live run so the two cannot diverge.
    """
    if not use_filter or reg is None:
        return {"stock": 1.0, "etf": 1.0, "crypto": 1.0, "macro": 1.0}
    state = str(reg["state"])
    base = float(reg["risk_multiplier"])
    allow_stock, allow_etf = bool(reg["allow_new_stock"]), bool(reg["allow_new_etf"])
    out = {"stock": base if allow_stock else 0.0, "crypto": base if allow_stock else 0.0,
           "etf": base if allow_etf else 0.0, "macro": 1.0}   # currencies and metals have no equity regime
    if state == RegimeState.RED.value and allow_etf:
        out["etf"] = 1.0
    return out


def short_multipliers(reg: pd.Series | None, green: dict[str, float], *, use_filter: bool = True) -> dict[str, float]:
    """The regime for new shorts, the long side's mirror: full size when the benchmark is RED,
    the yellow multiplier when YELLOW, ``green[asset class]`` (a lever, default 0) in a GREEN
    bull market. The crash guard (and the warm-up before the benchmark has a long MA) blocks new
    shorts as it blocks longs: panic states are when shorts get squeezed. Currencies and metals
    are not gated by the equity regime."""
    if not use_filter or reg is None:
        return {"stock": 1.0, "etf": 1.0, "crypto": 1.0, "macro": 1.0}
    state = str(reg["state"])
    if not bool(reg["allow_new_etf"]):
        m = dict.fromkeys(("stock", "etf", "crypto"), 0.0)
    elif state == RegimeState.RED.value:
        m = {c: 1.0 for c in ("stock", "etf", "crypto")}
    elif state == RegimeState.YELLOW.value:
        m = {c: float(reg["risk_multiplier"]) for c in ("stock", "etf", "crypto")}
    else:
        m = {c: float(green.get(c, 0.0)) for c in ("stock", "etf", "crypto")}
    return {**m, "macro": 1.0}


def regime_at(frame: pd.DataFrame, when: pd.Timestamp | str) -> pd.Series:
    sub = frame.loc[: pd.Timestamp(when)]
    if sub.empty:
        return pd.Series({"state": RegimeState.RED.value, "crash_guard": False,
                          "risk_multiplier": 0.0, "allow_new_stock": False, "allow_new_etf": False})
    return sub.iloc[-1]
