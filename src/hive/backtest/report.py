"""Write a backtest report: metrics JSON, trades/equity CSVs, and a PNG chart."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from hive.backtest.engine import BacktestResult
from hive.backtest.metrics import buy_and_hold, equity_metrics


def write_report(
    res: BacktestResult, out_dir: Path, *, benchmark_bars: pd.DataFrame | None = None,
    title: str = "backtest",
) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    metrics = dict(res.metrics)
    bh = None
    if benchmark_bars is not None and len(res.equity):
        bench = benchmark_bars.loc[res.equity.index[0]: res.equity.index[-1]]
        if len(bench):
            bh = buy_and_hold(float(res.equity.iloc[0]), bench)
            metrics["benchmark"] = equity_metrics(bh)
    metrics["config"] = res.config.model_dump()
    paths["metrics"] = out_dir / "metrics.json"
    paths["metrics"].write_text(json.dumps(metrics, indent=2, default=str))

    paths["equity"] = out_dir / "equity.csv"
    eq = pd.DataFrame({"equity": res.equity, "exposure": res.exposure})
    if bh is not None:
        eq["benchmark_buy_hold"] = bh.reindex(eq.index).ffill()
    eq.to_csv(paths["equity"])

    paths["trades"] = out_dir / "trades.csv"
    res.trades.to_csv(paths["trades"], index=False)

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 1]})
        axes[0].plot(res.equity.index, res.equity.values, label="strategy", lw=1.4)
        if bh is not None:
            axes[0].plot(bh.index, bh.values, label="benchmark buy & hold", lw=1.0, alpha=0.8)
        axes[0].set_yscale("log")
        axes[0].set_title(title)
        axes[0].legend(loc="upper left")
        axes[0].grid(alpha=0.3)
        dd = res.equity / res.equity.cummax() - 1.0
        axes[1].fill_between(dd.index, dd.values, 0, alpha=0.4)
        axes[1].set_ylabel("drawdown")
        axes[1].grid(alpha=0.3)
        fig.tight_layout()
        paths["chart"] = out_dir / "equity.png"
        fig.savefig(paths["chart"], dpi=120)
        plt.close(fig)
    except Exception:  # matplotlib is optional at runtime
        pass
    return paths
