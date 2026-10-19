"""Constant, code-driven strategy search.

A fixed grid of entry families and knobs (the space in config/bounds.toml) is
backtested a few cells at a time, every day, in-sample and out-of-sample against
the parent, with the same verdict the operator's proposals get. Winners take a
paper seat; every cell is recorded so the operator's research log and the
dashboard show the map of what works. No LLM is involved, so it costs nothing.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from hive.config import REPO_ROOT, StrategyConfig, Tier
from hive.hive.registry import Child, HiveRegistry
from hive.operator.iterate import (
    RESEARCH_LOG,
    append_research_log,
    evaluate,
    load_bounds,
    validate_overrides,
    verdict,
)

MODES = ("template_cross", "high_breakout", "ma_pullback", "momentum_threshold")


def grid() -> list[tuple[str, dict[str, Any]]]:
    cells: list[tuple[str, dict[str, Any]]] = []
    for sleeve in ("stocks", "crypto"):
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


def _key(sleeve: str, overrides: dict[str, Any]) -> str:
    return sleeve + "|" + json.dumps(overrides, sort_keys=True, default=str)


def untested(registry: HiveRegistry, cells: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    seen = {_key(it.get("sleeve", ""), it.get("overrides") or {}) for it in registry.iterations if it.get("overrides")}
    return [c for c in cells if _key(*c) not in seen]


def describe(sleeve: str, overrides: dict[str, Any]) -> str:
    return f"sweep {sleeve}: " + ", ".join(f"{k.split('.')[-1]}={v}" for k, v in overrides.items())


def seat_waiting(registry: HiveRegistry, tier: Tier, on: date) -> list[dict[str, Any]]:
    """Cells that passed the verdict while every seat was taken wait; seat them when one frees.
    A cell that was ever seated (any status) is not seated twice."""
    seated = {_key(c.sleeve, c.overrides or {}) for c in registry.children}
    out: list[dict[str, Any]] = []
    for it in list(registry.iterations):
        if not str(it.get("verdict", "")).startswith("passed but not spawned") or not it.get("overrides"):
            continue
        key = _key(it.get("sleeve", ""), it["overrides"])
        if key in seated:
            continue
        can, reason = registry.can_spawn(tier)
        if not can:
            break
        child = registry.spawn(Child(id="", name=str(it.get("proposal", "waiting list"))[:40], kind="variant",
                                     sleeve=it.get("sleeve", "stocks"), overrides=it["overrides"],
                                     hypothesis=str(it.get("hypothesis", "")),
                                     metrics={"is_cagr": it.get("is_cagr"), "oos_cagr": it.get("oos_cagr"),
                                              "oos_dd": it.get("oos_dd")}), tier)
        seated.add(key)
        rec = {"date": on.isoformat(), "proposal": it.get("proposal"), "kind": "seated", "overrides": it["overrides"],
               "sleeve": it.get("sleeve"), "cost_usd": 0.0, "oos_cagr": it.get("oos_cagr"),
               "parent_oos_cagr": it.get("parent_oos_cagr"), "oos_dd": it.get("oos_dd"),
               "verdict": f"seated from the waiting list as {child.id} (passed on {it.get('date')})"}
        registry.record_iteration(rec)
        out.append(rec)
    return out


def run_sweep(settings, cfg: StrategyConfig, registry: HiveRegistry, tier: Tier, *, n_cells: int = 3,
              on: date | None = None, inputs_by_sleeve: dict[str, dict] | None = None) -> list[dict[str, Any]]:
    from hive.operator.iterate import default_inputs

    on = on or date.today()
    bounds = load_bounds(REPO_ROOT / cfg.operator.bounds_file)
    records: list[dict[str, Any]] = seat_waiting(registry, tier, on)
    cells = untested(registry, grid())[:n_cells]
    inputs = dict(inputs_by_sleeve or {})
    parents: dict[str, Any] = {}
    for sleeve, ov in cells:
        rec: dict[str, Any] = {"date": on.isoformat(), "proposal": describe(sleeve, ov), "kind": "sweep",
                               "hypothesis": "systematic sweep of the pre-registered strategy space",
                               "overrides": ov, "sleeve": sleeve, "cost_usd": 0.0}
        ok, why = validate_overrides(ov, bounds)
        if not ok:
            rec["verdict"] = f"rejected: {why}"
            registry.record_iteration(rec)
            records.append(rec)
            continue
        try:
            if sleeve not in inputs:
                inputs[sleeve] = default_inputs(settings, cfg, sleeve)
            if sleeve not in parents:
                parents[sleeve] = evaluate(cfg, {}, inputs[sleeve])
            ev = evaluate(cfg, ov, inputs[sleeve])
        except Exception as exc:
            rec["verdict"] = f"rejected: backtest failed: {str(exc)[:160]}"
            registry.record_iteration(rec)
            records.append(rec)
            continue
        parent = parents[sleeve]
        rec.update({"is_cagr": ev.insample["cagr"], "oos_cagr": ev.outofsample["cagr"],
                    "is_dd": ev.insample["max_drawdown"], "oos_dd": ev.outofsample["max_drawdown"],
                    "parent_oos_cagr": parent.outofsample["cagr"]})
        passed, why = verdict(parent, ev, cfg.operator.max_drawdown_tolerance)
        if passed:
            can, reason = registry.can_spawn(tier)
            if can:
                child = registry.spawn(Child(id="", name=describe(sleeve, ov)[:40], kind="variant", sleeve=sleeve,
                                             overrides=ov, hypothesis=rec["hypothesis"],
                                             metrics={"insample": ev.insample, "outofsample": ev.outofsample}), tier)
                rec["verdict"] = f"spawned shadow child {child.id}: {why}"
            else:
                rec["verdict"] = f"passed but not spawned: {reason}"
        else:
            rec["verdict"] = f"rejected: {why}"
        registry.record_iteration(rec)
        records.append(rec)
    if records:
        append_research_log(registry.path.parent / RESEARCH_LOG, {
            "date": on.isoformat(), "note": "systematic sweep (code, no LLM)",
            "parent_oos_cagr": next(iter(parents.values())).outofsample.get("cagr") if parents else None,
            "proposals": [{"name": r["proposal"], "overrides": r["overrides"], "sleeve": r["sleeve"],
                           "oos_cagr": r.get("oos_cagr"), "verdict": (r.get("verdict") or "")[:120]} for r in records]})
    return records
