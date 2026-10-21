"""The strategy lab: every backtest the firm runs is kept and shown.

Each evaluation (a sweep cell, an operator proposal, the parent reference) is
written as one JSON record under ``reports/lab/`` with its in-sample and
out-of-sample metrics and downsampled equity curves. The store is append-only
and code-only: the dashboard draws the whole map of what was tested, not just
the winners, and the search never retests a cell it has a record for (the
registry keeps only its last 200 iterations; the lab keeps everything).

Storage is sized for a lab that runs around the clock (thousands of cells a
day): a curve stores only its equity values and points at a shared calendar
(``cal/<id>.json``: the dates and the benchmark, identical for every cell on
the same data snapshot); a rejected cell keeps a lean out-of-sample curve, a
winner keeps both windows at full resolution; and each finished UTC day is
rolled from one file per record into ``days/<day>.jsonl`` (metrics) and
``days/<day>.curves.jsonl`` (curves), so listings stay small and reading the
metrics never touches a curve. Rolling up moves records; it never edits one.
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
LEAN_POINTS = 100
PARENT_PREFIX = "parent-"
CAL_DIR = "cal"
DAYS_DIR = "days"


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


# --- curves: equity values plus a shared calendar ------------------------------------------------

def _cal_id(dates: list, bench: list) -> str:
    return hashlib.sha1(json.dumps([dates, bench], default=str).encode()).hexdigest()[:12]


def compact_curves(ldir: Path, curves: dict[str, Any] | None, *, lean: bool) -> dict[str, Any]:
    """{window: {dates, equity, benchmark}} -> {window: {cal, equity}}; the calendar (dates and
    benchmark) is written once to ``cal/``. A lean curve keeps the out-of-sample window only,
    at LEAN_POINTS. Curves already compact pass through."""
    out: dict[str, Any] = {}
    for win, cv in (curves or {}).items():
        if lean and win != "outofsample":
            continue
        if not cv or "cal" in cv:
            if cv:
                out[win] = cv
            continue
        dates, eq = list(cv.get("dates") or []), list(cv.get("equity") or [])
        bench = list(cv.get("benchmark") or [None] * len(dates))
        if lean and len(dates) > LEAN_POINTS:
            idx = sorted(set(np.linspace(0, len(dates) - 1, LEAN_POINTS).round().astype(int).tolist()))
            dates, eq, bench = [dates[i] for i in idx], [eq[i] for i in idx], [bench[i] for i in idx]
        cid = _cal_id(dates, bench)
        cp = ldir / CAL_DIR / f"{cid}.json"
        if not cp.exists():
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({"dates": dates, "benchmark": bench}, default=str))
        out[win] = {"cal": cid, "equity": eq}
    return out


def expand_curves(ldir: Path, curves: dict[str, Any] | None, cache: dict[str, Any] | None = None) -> dict[str, Any]:
    """The inverse of compact_curves: {window: {dates, equity, benchmark}}."""
    cache = {} if cache is None else cache
    out: dict[str, Any] = {}
    for win, cv in (curves or {}).items():
        if not cv:
            continue
        if "cal" not in cv:
            out[win] = cv
            continue
        cal = cache.get(cv["cal"])
        if cal is None:
            try:
                cal = json.loads((ldir / CAL_DIR / f"{cv['cal']}.json").read_text())
            except (ValueError, OSError):
                cal = {"dates": [], "benchmark": []}
            cache[cv["cal"]] = cal
        out[win] = {"dates": cal.get("dates") or [], "equity": cv.get("equity") or [],
                    "benchmark": cal.get("benchmark") or []}
    return out


def record_test(ldir: Path, rec: dict[str, Any]) -> Path:
    """Append one test record (never rewritten). Returns its path."""
    ldir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    rec["ts"] = rec.get("ts") or now.isoformat(timespec="seconds")
    # the file name carries the write time to the microsecond, so a sorted listing is the write order
    rec.setdefault("id", f"{now.strftime('%Y%m%dT%H%M%S%f')}-{rec.get('sleeve') or 'x'}-"
                         f"{cell_id(rec.get('sleeve') or '', rec.get('overrides') or {})}")
    if rec.get("curves"):
        rec["curves"] = compact_curves(ldir, rec["curves"], lean=rec.get("by") != "parent" and not rec.get("passed"))
    p = ldir / f"{rec['id']}.json"
    while p.exists():  # never overwrite a record
        rec["id"] += "x"
        p = ldir / f"{rec['id']}.json"
    p.write_text(json.dumps(rec, default=str))
    return p


def _day_files(ldir: Path) -> list[Path]:
    d = ldir / DAYS_DIR
    return sorted(p for p in d.glob("*.jsonl") if not p.name.endswith(".curves.jsonl")) if d.exists() else []


def _record_files(ldir: Path) -> list[Path]:
    return sorted(p for p in ldir.glob("*.json") if not p.name.startswith(PARENT_PREFIX)) if ldir.exists() else []


def read_tests(ldir: Path, *, with_curves: bool = True) -> list[dict[str, Any]]:
    """Every test record, oldest first. Metrics come from the day files and today's record
    files; with ``with_curves`` each record carries its expanded curves."""
    if not ldir.exists():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in _day_files(ldir):
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("id") in seen:
                continue
            seen.add(d.get("id"))
            out.append(d)
    for p in _record_files(ldir):
        try:
            d = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        if d.get("id") in seen:
            continue
        seen.add(d.get("id"))
        if not with_curves:
            d.pop("curves", None)
        out.append(d)
    out.sort(key=lambda r: (r.get("ts") or "", r.get("id") or ""))
    if with_curves:
        got = load_curves(ldir, out)
        for r in out:
            r["curves"] = got.get(r.get("id"), {})
    return out


def load_curves(ldir: Path, records: list[dict[str, Any]], *, expand: bool = True) -> dict[str, dict[str, Any]]:
    """Curves for the given records, read from their record file or their day's curves file;
    expanded to {dates, equity, benchmark}, or as stored (compact) with ``expand=False``."""
    cal: dict[str, Any] = {}
    fn = expand_curves if expand else (lambda _ldir, curves, _cache: curves or {})
    days: dict[str, dict[str, Any]] = {}
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        rid = r.get("id")
        if not rid:
            continue
        if r.get("curves"):
            out[rid] = fn(ldir, r["curves"], cal)
            continue
        fp = ldir / f"{rid}.json"
        if fp.exists():
            try:
                out[rid] = fn(ldir, json.loads(fp.read_text()).get("curves"), cal)
            except (ValueError, OSError):
                pass
            continue
        day = (r.get("ts") or "")[:10]
        if day not in days:
            days[day] = {}
            cp = ldir / DAYS_DIR / f"{day}.curves.jsonl"
            if cp.exists():
                for line in cp.read_text().splitlines():
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    days[day][d.get("id")] = d.get("curves")
        if rid in days[day]:
            out[rid] = fn(ldir, days[day][rid], cal)
    return out


def rollup(ldir: Path, *, today: str | None = None) -> int:
    """Move every record of a finished UTC day from its own file into the day files. Idempotent:
    a record already in its day file is not written twice. Returns how many records moved."""
    today = today or datetime.now(UTC).date().isoformat()
    by_day: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for p in _record_files(ldir):
        try:
            d = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        day = (d.get("ts") or "")[:10]
        if day and day < today:
            by_day.setdefault(day, []).append((p, d))
    moved = 0
    for day, items in sorted(by_day.items()):
        ddir = ldir / DAYS_DIR
        ddir.mkdir(parents=True, exist_ok=True)
        mp, cp = ddir / f"{day}.jsonl", ddir / f"{day}.curves.jsonl"
        have = set()
        if mp.exists():
            for line in mp.read_text().splitlines():
                try:
                    have.add(json.loads(line).get("id"))
                except ValueError:
                    continue
        with mp.open("a", encoding="utf-8") as fm, cp.open("a", encoding="utf-8") as fc:
            for _, d in items:
                if d.get("id") in have:
                    continue
                curves = d.pop("curves", None)
                fm.write(json.dumps(d, default=str) + "\n")
                if curves:
                    lean = d.get("by") != "parent" and not d.get("passed")
                    fc.write(json.dumps({"id": d.get("id"), "curves": compact_curves(ldir, curves, lean=lean)},
                                        default=str) + "\n")
                moved += 1
        for p, _ in items:
            p.unlink()
    return moved


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
        rec["is_prop"], rec["oos_prop"] = ev.insample.get("prop_pass_rate"), ev.outofsample.get("prop_pass_rate")
        rec["oos_score"] = ev.outofsample.get("prop_score")
        rec["oos_breach"] = (float(ev.outofsample.get("prop_fail_daily_rate") or 0.0)
                             + float(ev.outofsample.get("prop_fail_total_rate") or 0.0))
        rec["oos_prop_days"] = ev.outofsample.get("prop_median_days")
        rec["curves"] = ev.curves
    if parent is not None:
        rec["parent_oos_cagr"] = parent.outofsample.get("cagr")
        rec["parent_oos_dd"] = parent.outofsample.get("max_drawdown")
        rec["parent_oos_prop"] = parent.outofsample.get("prop_pass_rate")
        rec["parent_oos_score"] = parent.outofsample.get("prop_score")
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


def load_cals(ldir: Path, ids: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cid in ids:
        try:
            out[cid] = json.loads((ldir / CAL_DIR / f"{cid}.json").read_text())
        except (ValueError, OSError):
            continue
    return out


def export_recent_curves(ldir: Path, out_dir: Path, *, hours: int = 24) -> int:
    """Publish the last ``hours`` of curves next to the dashboard (lab/h-<YYYYMMDDHH>.json plus the
    calendars they use), so the page can draw any recent test on demand without carrying them all."""
    import shutil
    from datetime import timedelta

    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    recs = [r for r in read_tests(ldir, with_curves=False) if (r.get("ts") or "") >= cutoff]
    got = load_curves(ldir, recs, expand=False)
    chunks: dict[str, dict[str, Any]] = {}
    cals: set[str] = set()
    for r in recs:
        cv = got.get(r.get("id"))
        if not cv:
            continue
        key = (r.get("ts") or "")[:13].replace("-", "").replace("T", "")
        chunks.setdefault(key, {})[r["id"]] = cv
        cals |= {w.get("cal") for w in cv.values() if isinstance(w, dict) and w.get("cal")}
    dest = out_dir / "lab"
    if dest.exists():
        shutil.rmtree(dest)
    (dest / CAL_DIR).mkdir(parents=True, exist_ok=True)
    for key, chunk in chunks.items():
        (dest / f"h-{key}.json").write_text(json.dumps(chunk, default=str))
    for cid, cal in load_cals(ldir, cals).items():
        (dest / CAL_DIR / f"{cid}.json").write_text(json.dumps(cal, default=str))
    return sum(len(c) for c in chunks.values())
