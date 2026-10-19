"""Constant, code-driven strategy search.

Stage one is a fixed grid of entry families and knobs (the space in
config/bounds.toml). Stage two refines around the best cells found so far:
one numeric knob at a time, in steps that halve as the coarse steps run out,
plus pairwise combinations of winners with disjoint knobs. Cells are backtested
a few at a time, every hour, in-sample and out-of-sample against the parent,
with the same verdict the operator's proposals get. Winners take a paper seat;
every cell is recorded in the registry, the research log and the lab
(reports/lab/, with its equity curves) so the dashboard shows the map of what
works. No LLM is involved, so it costs nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from hive.config import REPO_ROOT, StrategyConfig, Tier
from hive.hive.registry import Child, HiveRegistry
from hive.operator.iterate import (
    RESEARCH_LOG,
    Bound,
    _get,
    append_research_log,
    coerce_types,
    evaluate,
    load_bounds,
    validate_overrides,
    verdict,
)
from hive.operator.lab import cell_key, lab_dir, lab_record, parent_eval, read_tests, record_test

log = logging.getLogger(__name__)

MODES = ("template_cross", "high_breakout", "ma_pullback", "momentum_threshold")
SLEEVES = ("stocks", "crypto")
# Knobs that only matter for one entry family: refining them on a cell of another family
# would retest the same strategy under a different name.
KNOB_FAMILY = {"breakout_lookback": "high_breakout", "pullback_touch_bars": "ma_pullback",
               "momentum_min": "momentum_threshold", "impulse_window": "reset_breakout",
               "impulse_min_z": "reset_breakout", "reset_period": "reset_breakout", "reset_z_max": "reset_breakout",
               "pullback_max_atr": "reset_breakout", "volume_max_ratio": "reset_breakout"}
REFINE_TOP = 3
REFINE_DIVISORS = (1, 2, 4)   # coarse steps first (a sixth of the range), then finer

Cell = tuple[str, dict[str, Any]]


def grid() -> list[Cell]:
    cells: list[Cell] = []
    for sleeve in SLEEVES:
        pre = sleeve
        for mode in MODES:
            base = {f"{pre}.entry_mode": mode}
            variants = [dict(base)]
            if mode == "high_breakout":
                variants += [{**base, f"{pre}.breakout_lookback": n} for n in (30, 90)]
            if mode == "ma_pullback":
                variants += [{**base, f"{pre}.pullback_touch_bars": k} for k in (3, 8)]
            if mode == "momentum_threshold":
                variants += [{**base, f"{pre}.momentum_min": m} for m in (0.3, 1.0)]
            cells += [(sleeve, v) for v in variants]
        cells += [(sleeve, {f"{pre}.exit_closes_below_mid": n}) for n in (1, 3)]
        cells += [(sleeve, {f"{pre}.trail_atr": t}) for t in (3.0, 7.0)]
    return cells


_key = cell_key


def results_so_far(registry: HiveRegistry, ldir=None) -> list[dict[str, Any]]:
    """Every tested cell with its out-of-sample result: the lab's records (complete) plus the
    registry's iterations (the last 200), one row per cell, the latest result winning."""
    rows: dict[str, dict[str, Any]] = {}
    src = (read_tests(ldir, with_curves=False) if ldir is not None else []) + list(registry.iterations)
    for it in src:
        ov = it.get("overrides")
        if not ov or it.get("by") == "seat" or it.get("kind") == "seated":
            continue
        k = cell_key(it.get("sleeve") or "", ov)
        rows[k] = {"sleeve": it.get("sleeve") or "", "overrides": ov, "oos_cagr": it.get("oos_cagr"),
                   "oos_dd": it.get("oos_dd"), "is_cagr": it.get("is_cagr"), "verdict": it.get("verdict") or ""}
    return list(rows.values())


def tested(registry: HiveRegistry, ldir=None) -> set[str]:
    return {cell_key(r["sleeve"], r["overrides"]) for r in results_so_far(registry, ldir)}


def untested(registry: HiveRegistry, cells: list[Cell], ldir=None) -> list[Cell]:
    seen = tested(registry, ldir)
    out, mine = [], set()
    for c in cells:
        k = cell_key(*c)
        if k in seen or k in mine:
            continue
        mine.add(k)
        out.append(c)
    return out


def _step(cur: float, lo: float, hi: float, div: int, sign: int, like: Any) -> Any:
    step = (hi - lo) / 6.0 / div
    v = min(max(cur + sign * step, lo), hi)
    if isinstance(like, bool):
        return like
    if isinstance(like, int):
        return int(round(v))
    return round(float(v), 3)


def refine(results: list[dict[str, Any]], bounds: dict[str, Bound], cfg: StrategyConfig, *,
           top: int = REFINE_TOP) -> list[Cell]:
    """Stage two: neighbours of the best cells so far. For each sleeve the ``top`` cells by
    out-of-sample CAGR (that ran) are perturbed one numeric knob at a time, both ways, inside the
    bounds; pairs of winners with disjoint knobs are merged. Deterministic, so a run continues
    where the last one stopped; it moves as better cells arrive."""
    out: list[Cell] = []
    seen: set[str] = set()

    def add(sleeve: str, ov: dict[str, Any]) -> None:
        k = cell_key(sleeve, ov)
        if k not in seen:
            seen.add(k)
            out.append((sleeve, ov))

    for sleeve in SLEEVES:
        rows = [r for r in results if r["sleeve"] == sleeve and r.get("oos_cagr") is not None
                and not str(r.get("verdict", "")).startswith("rejected: backtest failed")]
        rows.sort(key=lambda r: (-(r["oos_cagr"] or 0.0), -(r.get("oos_dd") or 0.0)))
        best = rows[:top]
        if not best:
            continue
        # pairwise merges of winners with disjoint knobs (same entry family or none)
        for i, a in enumerate(best):
            for b in best[i + 1:]:
                ka, kb = set(a["overrides"]), set(b["overrides"])
                if ka & kb:
                    continue
                add(sleeve, {**a["overrides"], **b["overrides"]})
        keys = sorted(k for k, b in bounds.items() if k.startswith(sleeve + ".") and not isinstance(b, list))
        for div in REFINE_DIVISORS:
            for r in best:
                mode = r["overrides"].get(f"{sleeve}.entry_mode", _get(cfg, f"{sleeve}.entry_mode"))
                for k in keys:
                    knob = k.split(".", 1)[1]
                    if KNOB_FAMILY.get(knob, mode) != mode:
                        continue
                    lo, hi = bounds[k]            # type: ignore[misc]
                    like = _get(cfg, k)
                    cur = float(r["overrides"].get(k, like))
                    for sign in (-1, 1):
                        v = _step(cur, lo, hi, div, sign, like)
                        if v == coerce_types(cfg, {k: cur})[k]:
                            continue
                        add(sleeve, {**r["overrides"], k: v})
    return out


def queue(registry: HiveRegistry, cfg: StrategyConfig, bounds: dict[str, Bound], ldir=None, *,
          with_refine: bool = True) -> list[Cell]:
    """What the lab will test next, in order: untested grid cells, then refinements, the sleeves
    interleaved so the cheap crypto cells never wait behind the stock ones."""
    cells = grid()
    if with_refine:
        cells = cells + refine(results_so_far(registry, ldir), bounds, cfg)
    pending = untested(registry, cells, ldir)
    by_sleeve = {s: [c for c in pending if c[0] == s] for s in SLEEVES}
    out: list[Cell] = []
    while any(by_sleeve.values()):
        for s in SLEEVES:
            if by_sleeve[s]:
                out.append(by_sleeve[s].pop(0))
    return out


def describe(sleeve: str, overrides: dict[str, Any], *, stage: str = "sweep") -> str:
    return f"{stage} {sleeve}: " + ", ".join(f"{k.split('.')[-1]}={v}" for k, v in overrides.items())


def waiting_list(registry: HiveRegistry, ldir=None) -> list[dict[str, Any]]:
    """Cells that passed the verdict while every seat was taken, best out-of-sample return first,
    from the lab's complete record (the registry keeps only its last 200 iterations) and the
    registry; cells that were ever seated (any status) are left out."""
    seated = {cell_key(c.sleeve, c.overrides or {}) for c in registry.children}
    src = (read_tests(ldir, with_curves=False) if ldir is not None else []) + list(registry.iterations)
    rows: dict[str, dict[str, Any]] = {}
    for it in src:
        if not str(it.get("verdict", "")).startswith("passed but not spawned") or not it.get("overrides"):
            continue
        key = cell_key(it.get("sleeve") or "", it["overrides"])
        if key in seated:
            continue
        rows[key] = {"proposal": it.get("proposal") or it.get("name") or "waiting list", "overrides": it["overrides"],
                     "sleeve": it.get("sleeve") or "stocks", "hypothesis": it.get("hypothesis") or "",
                     "date": it.get("date"), "is_cagr": it.get("is_cagr"), "oos_cagr": it.get("oos_cagr"),
                     "oos_dd": it.get("oos_dd"), "parent_oos_cagr": it.get("parent_oos_cagr")}
    return sorted(rows.values(), key=lambda r: (-(r["oos_cagr"] or 0.0), -(r["oos_dd"] or 0.0)))


def seat_waiting(registry: HiveRegistry, tier: Tier, on: date, ldir=None) -> list[dict[str, Any]]:
    """Seat the best waiting cells when seats are free. A cell is never seated twice."""
    out: list[dict[str, Any]] = []
    seated: set[str] = set()
    for it in waiting_list(registry, ldir):
        key = cell_key(it["sleeve"], it["overrides"])
        if key in seated:
            continue
        can, reason = registry.can_spawn(tier)
        if not can:
            break
        child = registry.spawn(Child(id="", name=str(it["proposal"])[:40], kind="variant", sleeve=it["sleeve"],
                                     overrides=it["overrides"], hypothesis=str(it["hypothesis"]),
                                     metrics={"is_cagr": it["is_cagr"], "oos_cagr": it["oos_cagr"],
                                              "oos_dd": it["oos_dd"]}), tier)
        seated.add(key)
        rec = {"date": on.isoformat(), "proposal": it["proposal"], "kind": "seated", "overrides": it["overrides"],
               "sleeve": it["sleeve"], "cost_usd": 0.0, "oos_cagr": it["oos_cagr"],
               "parent_oos_cagr": it["parent_oos_cagr"], "oos_dd": it["oos_dd"],
               "verdict": f"seated from the waiting list as {child.id} (passed on {it['date']})"}
        registry.record_iteration(rec)
        if ldir is not None:
            record_test(ldir, lab_record(by="seat", sleeve=it["sleeve"], name=it["proposal"], overrides=it["overrides"],
                                         ev=None, parent=None, verdict=rec["verdict"], on=on.isoformat(),
                                         hypothesis=it["hypothesis"], child_id=child.id))
        out.append(rec)
    return out


def run_sweep(settings, cfg: StrategyConfig, registry: HiveRegistry, tier: Tier, *, n_cells: int = 3,
              on: date | None = None, inputs_by_sleeve: dict[str, dict] | None = None,
              budget_s: float | None = None, with_refine: bool = True,
              ldir=None) -> list[dict[str, Any]]:
    """Backtest the next cells of the queue: ``n_cells`` of them, or as many as fit in ``budget_s``
    seconds when that is given (the cell in flight always finishes). Every cell becomes a registry
    iteration, a research-log line and a lab record with its curves."""
    from hive.operator.iterate import default_inputs

    on = on or date.today()
    ldir = ldir if ldir is not None else (lab_dir(settings) if settings is not None else None)
    bounds = load_bounds(REPO_ROOT / cfg.operator.bounds_file)
    records: list[dict[str, Any]] = seat_waiting(registry, tier, on, ldir)
    todo = queue(registry, cfg, bounds, ldir, with_refine=with_refine)
    inputs = dict(inputs_by_sleeve or {})
    parents: dict[str, Any] = {}
    started = datetime.now(UTC)
    n_done = 0
    for sleeve, ov in todo:
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if (budget_s is not None and elapsed >= budget_s) or (budget_s is None and n_done >= n_cells):
            break
        stage = "sweep" if (sleeve, ov) in grid() else "refine"
        rec: dict[str, Any] = {"date": on.isoformat(), "proposal": describe(sleeve, ov, stage=stage), "kind": "sweep",
                               "stage": stage,
                               "hypothesis": ("systematic sweep of the pre-registered strategy space" if stage == "sweep"
                                              else "refinement around the best cells found so far"),
                               "overrides": ov, "sleeve": sleeve, "cost_usd": 0.0}
        ok, why = validate_overrides(ov, bounds)
        if not ok:
            rec["verdict"] = f"rejected: {why}"
            registry.record_iteration(rec)
            records.append(rec)
            continue
        t0 = datetime.now(UTC)
        try:
            if sleeve not in inputs:
                inputs[sleeve] = default_inputs(settings, cfg, sleeve)
            if sleeve not in parents:
                parents[sleeve] = parent_eval(cfg, sleeve, inputs[sleeve], ldir, on=on.isoformat())
            ev = evaluate(cfg, ov, inputs[sleeve], keep_curves=ldir is not None)
        except Exception as exc:
            rec["verdict"] = f"rejected: backtest failed: {str(exc)[:160]}"
            registry.record_iteration(rec)
            records.append(rec)
            if ldir is not None:
                record_test(ldir, lab_record(by=stage, sleeve=sleeve, name=rec["proposal"], overrides=ov, ev=None,
                                              parent=parents.get(sleeve), verdict=rec["verdict"], on=on.isoformat(),
                                              hypothesis=rec["hypothesis"], cfg=cfg))
            n_done += 1
            continue
        parent = parents[sleeve]
        rec.update({"is_cagr": ev.insample["cagr"], "oos_cagr": ev.outofsample["cagr"],
                    "is_dd": ev.insample["max_drawdown"], "oos_dd": ev.outofsample["max_drawdown"],
                    "parent_oos_cagr": parent.outofsample["cagr"]})
        passed, why = verdict(parent, ev, cfg.operator.max_drawdown_tolerance)
        child_id = None
        if passed:
            can, reason = registry.can_spawn(tier)
            if can:
                child = registry.spawn(Child(id="", name=rec["proposal"][:40], kind="variant", sleeve=sleeve,
                                             overrides=ov, hypothesis=rec["hypothesis"],
                                             metrics={"insample": ev.insample, "outofsample": ev.outofsample}), tier)
                child_id = child.id
                rec["verdict"] = f"spawned shadow child {child.id}: {why}"
            else:
                rec["verdict"] = f"passed but not spawned: {reason}"
        else:
            rec["verdict"] = f"rejected: {why}"
        registry.record_iteration(rec)
        records.append(rec)
        n_done += 1
        if ldir is not None:
            from hive.operator.lab import data_fingerprint

            record_test(ldir, lab_record(by=stage, sleeve=sleeve, name=rec["proposal"], overrides=ov, ev=ev,
                                          parent=parent, verdict=rec["verdict"], on=on.isoformat(),
                                          hypothesis=rec["hypothesis"], child_id=child_id, cfg=cfg,
                                          elapsed_s=round((datetime.now(UTC) - t0).total_seconds(), 1),
                                          data=data_fingerprint(inputs[sleeve])))
        log.info("lab: %s -> %s", rec["proposal"], rec["verdict"])
    if records:
        append_research_log(registry.path.parent / RESEARCH_LOG, {
            "date": on.isoformat(), "note": "systematic sweep (code, no LLM)",
            "parent_oos_cagr": next(iter(parents.values())).outofsample.get("cagr") if parents else None,
            "proposals": [{"name": r["proposal"], "overrides": r["overrides"], "sleeve": r["sleeve"],
                           "oos_cagr": r.get("oos_cagr"), "verdict": (r.get("verdict") or "")[:120]} for r in records]})
    return records
