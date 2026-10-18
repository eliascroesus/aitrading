"""Performance statistics from an equity curve and a trade list."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from hive.signals.indicators import drawdown


def equity_metrics(equity: pd.Series, periods_per_year: int = 252) -> dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return {"total_return": 0.0, "cagr": 0.0, "ann_vol": 0.0, "sharpe": 0.0, "sortino": 0.0,
                "max_drawdown": 0.0, "calmar": 0.0, "days": int(len(equity))}
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1.0
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    vol = float(rets.std(ddof=1) * math.sqrt(periods_per_year)) if len(rets) > 1 else 0.0
    mean_ann = float(rets.mean() * periods_per_year)
    sharpe = mean_ann / vol if vol > 0 else 0.0
    downside = rets[rets < 0]
    dvol = float(downside.std(ddof=1) * math.sqrt(periods_per_year)) if len(downside) > 1 else 0.0
    sortino = mean_ann / dvol if dvol > 0 else 0.0
    mdd = float(drawdown(equity).min())
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    return {
        "total_return": float(total),
        "cagr": float(cagr),
        "ann_vol": vol,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": mdd,
        "calmar": float(calmar),
        "days": int(len(equity)),
    }


def trade_metrics(trades: pd.DataFrame) -> dict[str, float]:
    if trades is None or trades.empty:
        return {"n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0, "avg_hold_days": 0.0, "expectancy_pct": 0.0, "fees_paid": 0.0}
    pnl = trades["pnl"].astype(float)
    ret = trades["return_pct"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())
    return {
        "n_trades": int(len(trades)),
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
        "avg_win_pct": float(ret[pnl > 0].mean()) if len(wins) else 0.0,
        "avg_loss_pct": float(ret[pnl <= 0].mean()) if len(losses) else 0.0,
        "avg_hold_days": float(trades["hold_days"].mean()),
        "expectancy_pct": float(ret.mean()),
        "fees_paid": float(trades["fees"].sum()),
    }


def exposure_metrics(exposure: pd.Series) -> dict[str, float]:
    if exposure is None or exposure.empty:
        return {"avg_exposure": 0.0, "time_in_market": 0.0}
    return {
        "avg_exposure": float(exposure.mean()),
        "time_in_market": float((exposure > 0).mean()),
    }


def buy_and_hold(equity_start: float, bars: pd.DataFrame) -> pd.Series:
    """Benchmark: everything into one asset at the first close, held."""
    px = bars["adj_close"].dropna()
    return (px / px.iloc[0]) * equity_start


def summarize(equity: pd.Series, trades: pd.DataFrame, exposure: pd.Series | None = None) -> dict[str, float]:
    out = equity_metrics(equity)
    out.update(trade_metrics(trades))
    if exposure is not None:
        out.update(exposure_metrics(exposure))
    return {k: (v if not isinstance(v, float) or np.isfinite(v) else 0.0) for k, v in out.items()}
