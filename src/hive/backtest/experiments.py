"""Pre-registered validation experiments: robustness grid, null test, walk-forward.

Rules of the road (docs/STRATEGY.md §10): the grid is reported in full, the
null test must be beaten, and anything adopted from exploration is validated on
the held-out period. Each experiment writes a CSV so the documentary can show
every cell, not just the winner.
"""

from __future__ import annotations

import itertools
import json
import logging
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from hive.backtest.costs import CostModel
from hive.backtest.engine import Backtester
from hive.backtest.metrics import equity_metrics
from hive.config import StrategyConfig
from hive.data.universe import PointInTimeUniverse

log = logging.getLogger(__name__)

KEEP = ["total_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar", "n_trades",
        "win_rate", "profit_factor", "avg_hold_days", "expectancy_pct", "avg_exposure", "n_drawdown_halts"]


@dataclass
class ExperimentInputs:
    bars: dict[str, pd.DataFrame]
    classes: dict[str, str]
    bench: pd.DataFrame
    cost: CostModel
    universe: PointInTimeUniverse | None
    vix: pd.Series | None
    equity: float
    start: str
    end: str | None


def _apply_overrides(cfg: StrategyConfig, overrides: dict[str, Any]) -> StrategyConfig:
    d = cfg.model_dump()
    for dotted, value in overrides.items():
        node = d
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return StrategyConfig.model_validate(d)


def _run_one(args: tuple[ExperimentInputs, StrategyConfig, dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
    inputs, cfg, overrides, extra = args
    c = _apply_overrides(cfg, overrides)
    bt = Backtester(
        inputs.bars, inputs.bench, c, inputs.cost, asset_class_by_symbol=inputs.classes,
        initial_equity=inputs.equity, start=inputs.start, end=inputs.end, universe=inputs.universe,
        vix=inputs.vix, **extra,
    )
    res = bt.run()
    row = {**overrides, **{k: res.metrics.get(k) for k in KEEP}}
    row["extra"] = json.dumps(extra, default=str)
    return row


def run_grid(
    inputs: ExperimentInputs, cfg: StrategyConfig, grid: dict[str, list[Any]], *,
    out_csv: Path, n_jobs: int = 3, extra: dict[str, Any] | None = None,
) -> pd.DataFrame:
    keys = list(grid)
    cells = [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*grid.values())]
    jobs = [(inputs, cfg, cell, extra or {}) for cell in cells]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=n_jobs) as ex:
        for row in ex.map(_run_one, jobs):
            rows.append(row)
            log.info("grid cell done: %s", {k: row[k] for k in keys} | {"cagr": row["cagr"]})
    df = pd.DataFrame(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    return df


def run_oat(
    inputs: ExperimentInputs, cfg: StrategyConfig, variations: dict[str, list[Any]], *,
    out_csv: Path, n_jobs: int = 3,
) -> pd.DataFrame:
    """One-at-a-time sensitivity: vary each parameter alone around the base config."""
    cells: list[dict[str, Any]] = [{}]
    for k, vals in variations.items():
        cells += [{k: v} for v in vals]
    jobs = [(inputs, cfg, cell, {}) for cell in cells]
    rows = []
    with ProcessPoolExecutor(max_workers=n_jobs) as ex:
        for cell, row in zip(cells, ex.map(_run_one, jobs), strict=True):
            row["param"] = next(iter(cell), "BASE")
            row["value"] = next(iter(cell.values()), None)
            rows.append(row)
    df = pd.DataFrame(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    return df


def run_null_test(
    inputs: ExperimentInputs, cfg: StrategyConfig, *, seeds: list[int], out_csv: Path, n_jobs: int = 3,
) -> pd.DataFrame:
    """Same template, sizing and exits with random entry days (entry_mode=random_template)."""
    jobs = [(inputs, cfg, {}, {"entry_mode": "random_template", "seed": s}) for s in seeds]
    rows = []
    with ProcessPoolExecutor(max_workers=n_jobs) as ex:
        for s, row in zip(seeds, ex.map(_run_one, jobs), strict=True):
            row["seed"] = s
            rows.append(row)
    df = pd.DataFrame(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    return df


def benchmark_row(inputs: ExperimentInputs) -> dict[str, float]:
    b = inputs.bench.loc[inputs.start: inputs.end or None]
    eq = b["adj_close"] / b["adj_close"].iloc[0] * inputs.equity
    return equity_metrics(eq)


def summarize_null(df: pd.DataFrame, actual_cagr: float) -> dict[str, float]:
    arr = df["cagr"].to_numpy(dtype=float)
    return {
        "null_mean_cagr": float(np.mean(arr)), "null_std_cagr": float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
        "null_max_cagr": float(np.max(arr)), "actual_cagr": actual_cagr,
        "pct_null_runs_beating_actual": float(np.mean(arr >= actual_cagr)),
    }
