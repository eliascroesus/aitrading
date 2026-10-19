"""The strategy lab: every backtest the firm runs is kept and shown.

Each evaluation (a sweep cell, an operator proposal, the parent reference) is
written as one JSON record under ``reports/lab/`` with its in-sample and
out-of-sample metrics and downsampled equity curves. The store is append-only
and code-only: the dashboard draws the whole map of what was tested, not just
the winners, and the search never retests a cell it has a record for (the
registry keeps only its last 200 iterations; the lab keeps everything).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from hive.config import Settings, StrategyConfig

if TYPE_CHECKING:  # pragma: no cover
    from hive.operator.iterate import EvalResult

log = logging.getLogger(__name__)

LAB_DIR = "lab"
CURVE_POINTS = 160
PARENT_PREFIX = "parent-"


def lab_dir(settings: Settings) -> Path:
    return settings.hive_reports_dir / LAB_DIR


def cell_key(sleeve: str, overrides: dict[str, Any]) -> str:
    """One string per (sleeve, overrides) cell; the dedup key of the whole search."""
    return f"{sleeve}|" + json.dumps(overrides or {}, sort_keys=True, default=str)


def cell_id(sleeve: str, overrides: dict[str, Any]) -> str:
    return hashlib.sha1(cell_key(sleeve, overrides).encode()).hexdigest()[:10]


def downsample(series: pd.Series, n: int = CURVE_POINTS) -> dict[str, list]:
    """An equity series -> {dates, equity} with at most ``n`` points, first and last kept."""
    s = series.dropna()
    if s.empty:
        return {"dates": [], "equity": []}
    if len(s) > n:
        idx = sorted(set(np.linspace(0, len(s) - 1, n).round().astype(int).tolist()))
        s = s.iloc[idx]
    return {"dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in s.index],
            "equity": [round(float(v), 2) for v in s.values]}


def benchmark_on(dates: list[str], bench: pd.DataFrame | None, equity_start: float) -> list[float | None]:
    """Buy & hold of the benchmark, indexed to the same start, sampled on ``dates``."""
    if bench is None or not dates or "adj_close" not in bench:
        return [None] * len(dates)
    px = bench["adj_close"].dropna()
    px = px.loc[pd.Timestamp(dates[0]):pd.Timestamp(dates[-1])]
    if px.empty:
        return [None] * len(dates)
    bh = (px / px.iloc[0]) * equity_start
    vals = bh.reindex(pd.DatetimeIndex(pd.to_datetime(dates)), method="ffill").values
    return [None if (v is None or not np.isfinite(v)) else round(float(v), 2) for v in vals]


def data_fingerprint(inputs: dict[str, Any]) -> dict[str, Any]:
    bars = inputs.get("bars") or {}
    firsts = [df.index[0] for df in bars.values() if len(df)]
    lasts = [df.index[-1] for df in bars.values() if len(df)]
    return {"n_symbols": len(bars),
            "first_bar": pd.Timestamp(min(firsts)).strftime("%Y-%m-%d") if firsts else None,
            "last_bar": pd.Timestamp(max(lasts)).strftime("%Y-%m-%d") if lasts else None}


def record_test(ldir: Path, rec: dict[str, Any]) -> Path:
    """Append one test record (never rewritten). Returns its path."""
    ldir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    rec["ts"] = rec.get("ts") or now.isoformat(timespec="seconds")
    # the file name carries the write time to the microsecond, so a sorted listing is the write order
    rec.setdefault("id", f"{now.strftime('%Y%m%dT%H%M%S%f')}-{rec.get('sleeve') or 'x'}-"
                         f"{cell_id(rec.get('sleeve') or '', rec.get('overrides') or {})}")
    p = ldir / f"{rec['id']}.json"
    while p.exists():  # never overwrite a record
        rec["id"] += "x"
        p = ldir / f"{rec['id']}.json"
    p.write_text(json.dumps(rec, default=str))
    return p


def read_tests(ldir: Path, *, with_curves: bool = True) -> list[dict[str, Any]]:
    """Every test record, oldest first."""
    if not ldir.exists():
        return []
    out = []
    for p in sorted(ldir.glob("*.json")):
        if p.name.startswith(PARENT_PREFIX):
            continue
        try:
            d = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        if not with_curves:
            d.pop("curves", None)
        out.append(d)
    return out


def recorded_keys(ldir: Path) -> set[str]:
    """Cells with an evaluation on record (seat records are bookkeeping, not evaluations)."""
    return {cell_key(r.get("sleeve") or "", r["overrides"]) for r in read_tests(ldir, with_curves=False)
            if r.get("overrides") and r.get("by") != "seat"}


def lab_record(*, by: str, sleeve: str, name: str, overrides: dict[str, Any], ev: EvalResult | None,
                parent: EvalResult | None, verdict: str, on: str, hypothesis: str = "", child_id: str | None = None,
                elapsed_s: float | None = None, cfg: StrategyConfig | None = None,
                data: dict[str, Any] | None = None) -> dict[str, Any]:
    """The common shape of a lab record (metrics flat at the top, curves nested)."""
    rec: dict[str, Any] = {"date": on, "by": by, "sleeve": sleeve, "name": name, "overrides": overrides,
                           "hypothesis": hypothesis, "verdict": verdict, "child_id": child_id,
                           "passed": None if by == "parent" else verdict.startswith(("spawned", "passed", "seated")),
                           "config_version": cfg.version if cfg else None, "data": data or {}, "elapsed_s": elapsed_s}
    if ev is not None:
        rec["insample"], rec["outofsample"] = ev.insample, ev.outofsample
        rec["is_cagr"], rec["oos_cagr"] = ev.insample.get("cagr"), ev.outofsample.get("cagr")
        rec["is_dd"], rec["oos_dd"] = ev.insample.get("max_drawdown"), ev.outofsample.get("max_drawdown")
        rec["curves"] = ev.curves
    if parent is not None:
        rec["parent_oos_cagr"] = parent.outofsample.get("cagr")
        rec["parent_oos_dd"] = parent.outofsample.get("max_drawdown")
    return rec


# --- the parent reference, cached per data snapshot --------------------------------------------

def parent_key(cfg: StrategyConfig, sleeve: str, fp: dict[str, Any]) -> str:
    o = cfg.operator
    return "|".join(str(x) for x in (cfg.version, sleeve, fp.get("first_bar"), fp.get("last_bar"), fp.get("n_symbols"),
                                     o.insample_start, o.insample_end, o.outofsample_start, o.outofsample_end))


def parent_path(ldir: Path, sleeve: str) -> Path:
    return ldir / f"{PARENT_PREFIX}{sleeve}.json"


def load_parent(ldir: Path, sleeve: str, key: str) -> EvalResult | None:
    from hive.operator.iterate import EvalResult

    p = parent_path(ldir, sleeve)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except (ValueError, OSError):
        return None
    if d.get("key") != key:
        return None
    return EvalResult({}, d["insample"], d["outofsample"], d.get("curves") or {})


def parent_eval(cfg: StrategyConfig, sleeve: str, inputs: dict[str, Any], ldir: Path | None,
                *, on: str | None = None) -> EvalResult:
    """The pre-registered rules on this data snapshot: from the cache when the snapshot is the
    same, else backtested afresh, cached and recorded as a lab test of its own."""
    from hive.operator.iterate import evaluate

    fp = data_fingerprint(inputs)
    key = parent_key(cfg, sleeve, fp)
    if ldir is not None:
        cached = load_parent(ldir, sleeve, key)
        if cached is not None:
            return cached
    t0 = datetime.now(UTC)
    ev = evaluate(cfg, {}, inputs, keep_curves=ldir is not None)
    elapsed = (datetime.now(UTC) - t0).total_seconds()
    if ldir is not None:
        ldir.mkdir(parents=True, exist_ok=True)
        parent_path(ldir, sleeve).write_text(json.dumps({
            "key": key, "ts": t0.isoformat(timespec="seconds"), "sleeve": sleeve, "data": fp,
            "insample": ev.insample, "outofsample": ev.outofsample, "curves": ev.curves}, default=str))
        record_test(ldir, lab_record(by="parent", sleeve=sleeve, name=f"parent ({cfg.name} v{cfg.version})",
                                      overrides={}, ev=ev, parent=None, verdict="reference",
                                      on=on or t0.date().isoformat(), elapsed_s=round(elapsed, 1), cfg=cfg, data=fp))
        log.info("lab: parent %s backtested in %.0fs (OOS CAGR %.2f%%)", sleeve, elapsed, 100 * ev.outofsample["cagr"])
    return ev


def summary(ldir: Path, *, today: str | None = None) -> dict[str, Any]:
    """Counts for the dashboard header: tests, passes, today's, last timestamp."""
    tests = read_tests(ldir, with_curves=False)
    today = today or datetime.now(UTC).date().isoformat()
    cells = [t for t in tests if t.get("by") not in ("parent", "seat")]
    return {"n_tests": len(cells), "n_passed": sum(1 for t in cells if t.get("passed")),
            "n_today": sum(1 for t in cells if (t.get("ts") or "")[:10] == today),
            "last_ts": tests[-1]["ts"] if tests else None}


STATUS_FILE = "lab_run.json"


def write_status(state_dir: Path, info: dict[str, Any]) -> Path:
    """Where the hourly lab stands (round, started, finished, cells); shown on the dashboard, not committed."""
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_dir / STATUS_FILE
    p.write_text(json.dumps(info, default=str))
    return p


def read_status(state_dir: Path) -> dict[str, Any]:
    p = state_dir / STATUS_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return {}
