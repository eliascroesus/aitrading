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
import random
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
SLEEVES = ("stocks", "crypto", "macro")
# the risk.* levers that belong to another sleeve's markets (never searched in this one)
OTHER_MARKETS = {"stocks": ("crypto", "macro"), "crypto": ("stock", "etf", "macro"), "macro": ("stock", "etf", "crypto")}
# Knobs that only matter for one entry family: refining them on a cell of another family
# would retest the same strategy under a different name.
KNOB_FAMILY = {"breakout_lookback": "high_breakout", "pullback_touch_bars": "ma_pullback",
               "momentum_min": "momentum_threshold", "impulse_window": "reset_breakout",
               "impulse_min_z": "reset_breakout", "reset_period": "reset_breakout", "reset_z_max": "reset_breakout",
               "pullback_max_atr": "reset_breakout", "volume_max_ratio": "reset_breakout"}
# Edge-sizing knobs only matter when the cell sizes by edge (config 2.2.0).
EDGE_KNOBS = ("risk.edge_fraction", "risk.edge_min_trades", "risk.edge_lookback_trades",
              "risk.edge_min_risk_pct", "risk.max_risk_per_trade_pct")
REFINE_TOP = 3
REFINE_DIVISORS = (1, 2, 4)   # coarse steps first (a sixth of the range), then finer
EVOLVE_TOP = 8                # the evolve stage mutates the best EVOLVE_TOP cells per sleeve ...
EVOLVE_PER_SLEEVE = 150       # ... into this many fresh candidates per call
EVOLVE_MAX_KNOBS = 6          # a candidate never changes more than this many settings
RESCORE_TOP = 15              # best distinct results per sleeve re-tested once to get their prop score
STAGES = ("rescore", "sweep", "refine", "evolve")

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
        # sizing and concentration: where most of the growth rate is decided (config 2.0.0)
        cells += [(sleeve, {"risk.risk_per_trade_pct": r}) for r in (0.02, 0.04)]
        cells += [(sleeve, {"risk.max_open_risk_pct": h}) for h in (0.12, 0.24)]
        cells += [(sleeve, {"risk.target_annual_vol": v}) for v in (0.3, 0.45)]
        cells += [(sleeve, {"risk.max_positions": n}) for n in (3, 8)]
        cells += [(sleeve, {"risk.risk_per_trade_pct": 0.005})]
        if sleeve == "stocks":
            cells += [(sleeve, {"risk.max_position_pct_stock": c}) for c in (0.35, 0.5)]
            no_caps = {"risk.max_position_pct_stock": 1.0, "risk.max_position_pct_etf": 1.0}
        elif sleeve == "crypto":
            cells += [(sleeve, {"risk.max_position_pct_crypto": c}) for c in (0.5, 0.8)]
            cells += [(sleeve, {"risk.crypto_sleeve_pct": c}) for c in (0.6, 1.0)]
            cells += [(sleeve, {"risk.max_position_pct_crypto_alt": c}) for c in (0.3, 1.0)]
            no_caps = {"risk.max_position_pct_crypto": 1.0, "risk.max_position_pct_crypto_alt": 1.0}
        else:
            cells += [(sleeve, {"risk.max_position_pct_macro": 0.25})]
            no_caps = {"risk.max_position_pct_macro": 1.0}
        # shorts: the same rules on the market upside down, alone or beside the longs (2.3.0);
        # the macro sleeve trades both ways by default, so its cells try one side at a time
        if sleeve == "macro":
            cells += [(sleeve, {f"{pre}.direction": d}) for d in ("long", "short")]
        else:
            cells += [(sleeve, {f"{pre}.direction": d}) for d in ("both", "short")]
            cells += [(sleeve, {f"{pre}.direction": "both", f"{pre}.short_green_mult": 0.5})]
        # no position caps at all, and risk per trade chosen by each book's own record (2.2.0)
        edge = {"risk.sizing_mode": "edge"}
        cells += [(sleeve, dict(no_caps)), (sleeve, dict(edge)),
                  (sleeve, {**edge, "risk.edge_fraction": 0.5}),
                  (sleeve, {**edge, "risk.max_risk_per_trade_pct": 0.1}),
                  (sleeve, {**edge, **no_caps})]
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
                   "oos_dd": it.get("oos_dd"), "is_cagr": it.get("is_cagr"), "verdict": it.get("verdict") or "",
                   "oos_prop": it.get("oos_prop", (it.get("outofsample") or {}).get("prop_pass_rate"))}
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


def rank_key(r: dict[str, Any], objective: str = "growth") -> tuple:
    """Best first under the firm's objective: the simulated prop pass rate, then growth, then the
    shallower drawdown (a cell scored before prop metrics existed ranks as a zero pass rate)."""
    lead = -(float(r.get("oos_prop") or 0.0)) if objective == "prop" else 0.0
    return (lead, -(r.get("oos_cagr") or 0.0), -(r.get("oos_dd") or 0.0))


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
        rows.sort(key=lambda r: rank_key(r, cfg.operator.objective))
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
        keys = _sleeve_keys(bounds, sleeve)[0]
        for div in REFINE_DIVISORS:
            for r in best:
                for k in keys:
                    if _inert(k, r["overrides"], cfg, sleeve):
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


def _inert(k: str, ov: dict[str, Any], cfg: StrategyConfig, sleeve: str) -> bool:
    """A knob the cell's other settings make irrelevant (another entry family's knob, an edge
    knob on a fixed-size cell): testing it would retest the same strategy under a new name."""
    if _inert_short(k, ov, cfg, sleeve):
        return True
    if k.startswith(sleeve + "."):
        mode = ov.get(f"{sleeve}.entry_mode", _get(cfg, f"{sleeve}.entry_mode"))
        return KNOB_FAMILY.get(k.split(".", 1)[1], mode) != mode
    if k in EDGE_KNOBS:
        return str(ov.get("risk.sizing_mode", cfg.risk.sizing_mode)) != "edge"
    return False


def _inert_short(k: str, ov: dict[str, Any], cfg: StrategyConfig, sleeve: str) -> bool:
    """The short-side regime lever does nothing on a long-only cell."""
    return k == f"{sleeve}.short_green_mult" and str(ov.get(f"{sleeve}.direction", _get(cfg, f"{sleeve}.direction"))) == "long"


def _sleeve_keys(bounds: dict[str, Bound], sleeve: str) -> tuple[list[str], list[str]]:
    """(numeric knobs, categorical knobs) that apply to a sleeve: its own and the sizing levers."""
    own = [k for k in bounds if k.startswith(sleeve + ".")
           or (k.startswith("risk.") and not any(o in k for o in OTHER_MARKETS[sleeve]))]
    return (sorted(k for k in own if not isinstance(bounds[k], list)),
            sorted(k for k in own if isinstance(bounds[k], list)))


def evolve(results: list[dict[str, Any]], bounds: dict[str, Bound], cfg: StrategyConfig, *, seed: int,
           top: int = EVOLVE_TOP, per_sleeve: int = EVOLVE_PER_SLEEVE) -> list[Cell]:
    """Stage three, so the search never runs dry: mutations of the best cells found so far. Each
    candidate starts from one of the ``top`` cells (better cells are picked more often), changes
    one to three settings by a random step inside the bounds (sometimes the entry family), and is
    kept only if it stays inside the bounds and uses knobs its entry family reads. The seed is the
    number of tests on record, so every run explores new ground and any run can be replayed."""
    rng = random.Random(seed)
    out: list[Cell] = []
    seen: set[str] = set()
    for sleeve in SLEEVES:
        rows = [r for r in results if r["sleeve"] == sleeve and r.get("oos_cagr") is not None
                and not str(r.get("verdict", "")).startswith("rejected: backtest failed")]
        rows.sort(key=lambda r: rank_key(r, cfg.operator.objective))
        best = [r["overrides"] for r in rows[:top]] or [{}]
        numeric, categorical = _sleeve_keys(bounds, sleeve)
        mode_key = f"{sleeve}.entry_mode"
        other_cats = [k for k in categorical if k != mode_key]
        made, tries = 0, 0
        while made < per_sleeve and tries < per_sleeve * 20:
            tries += 1
            base = best[min(int(rng.expovariate(0.6)), len(best) - 1)]
            ov = dict(base)
            for _ in range(rng.choice((1, 1, 2, 2, 3))):
                if mode_key in categorical and rng.random() < 0.12:
                    ov[mode_key] = rng.choice(bounds[mode_key])          # type: ignore[arg-type]
                    continue
                if other_cats and rng.random() < 0.1:
                    k = rng.choice(other_cats)
                    ov[k] = rng.choice(bounds[k])                         # type: ignore[arg-type]
                    continue
                if not numeric:
                    break
                k = rng.choice(numeric)
                lo, hi = bounds[k]                                        # type: ignore[misc]
                like = _get(cfg, k)
                cur = float(ov.get(k, like))
                width = (hi - lo) * rng.choice((0.04, 0.1, 0.2, 0.35))
                v = min(max(cur + rng.uniform(-width, width), lo), hi)
                ov[k] = coerce_types(cfg, {k: v})[k]
                if isinstance(ov[k], float):
                    ov[k] = round(ov[k], 3)
            ov = {k: v for k, v in ov.items() if not _inert(k, ov, cfg, sleeve)}
            ov = {k: v for k, v in ov.items() if v != coerce_types(cfg, {k: _get(cfg, k)})[k]}   # defaults add nothing
            if not ov or len(ov) > EVOLVE_MAX_KNOBS or not validate_overrides(ov, bounds)[0]:
                continue
            key = cell_key(sleeve, ov)
            if key in seen:
                continue
            seen.add(key)
            out.append((sleeve, ov))
            made += 1
    return out


def queue_staged(registry: HiveRegistry, cfg: StrategyConfig, bounds: dict[str, Bound], ldir=None, *,
                 with_refine: bool = True) -> list[tuple[str, dict[str, Any], str]]:
    """What the lab will test next, in order, with the stage each cell comes from: untested grid
    cells, then refinements around the best cells, then mutations of the best cells (stage three
    never runs out), the sleeves interleaved so the cheap crypto cells never wait behind stocks."""
    results = results_so_far(registry, ldir)
    staged: list[tuple[Cell, str]] = []
    rescore: set[str] = set()
    if cfg.operator.objective == "prop":
        def same(r: dict[str, Any]) -> tuple:     # cells with identical results differ only in inert knobs
            return tuple(round(float(r.get(k) or 0.0), 6) for k in ("oos_cagr", "oos_dd", "is_cagr"))

        for sl in SLEEVES:
            done = {same(r) for r in results if r["sleeve"] == sl and r.get("oos_prop") is not None}
            unscored = sorted((r for r in results if r["sleeve"] == sl and r.get("oos_prop") is None
                               and r.get("oos_cagr") is not None
                               and not str(r.get("verdict", "")).startswith("rejected: backtest failed")),
                              key=lambda r: rank_key(r, "growth"))
            picked = 0
            for r in unscored:
                if picked >= RESCORE_TOP or same(r) in done:
                    continue
                done.add(same(r))
                staged.append(((sl, r["overrides"]), "rescore"))
                rescore.add(cell_key(sl, r["overrides"]))
                picked += 1
    staged += [(c, "sweep") for c in grid()]
    if with_refine:
        staged += [(c, "refine") for c in refine(results, bounds, cfg)]
        staged += [(c, "evolve") for c in evolve(results, bounds, cfg, seed=len(results))]
    seen = {cell_key(r["sleeve"], r["overrides"]) for r in results}
    pending: list[tuple[str, dict[str, Any], str]] = []
    for (sl, ov), stage in staged:
        k = cell_key(sl, ov)
        if k in seen and not (stage == "rescore" and k in rescore):
            continue
        rescore.discard(k)
        seen.add(k)
        pending.append((sl, ov, stage))
    by_sleeve = {s: [c for c in pending if c[0] == s] for s in SLEEVES}
    out: list[tuple[str, dict[str, Any], str]] = []
    while any(by_sleeve.values()):
        for s in SLEEVES:
            if by_sleeve[s]:
                out.append(by_sleeve[s].pop(0))
    return out


def queue(registry: HiveRegistry, cfg: StrategyConfig, bounds: dict[str, Bound], ldir=None, *,
          with_refine: bool = True) -> list[Cell]:
    return [(sl, ov) for sl, ov, _ in queue_staged(registry, cfg, bounds, ldir, with_refine=with_refine)]


def describe(sleeve: str, overrides: dict[str, Any], *, stage: str = "sweep") -> str:
    return f"{stage} {sleeve}: " + ", ".join(f"{k.split('.')[-1]}={v}" for k, v in overrides.items())


def waiting_list(registry: HiveRegistry, ldir=None, objective: str | None = None) -> list[dict[str, Any]]:
    """Cells that passed the verdict while every seat was taken, best out-of-sample return first,
    from the lab's complete record (the registry keeps only its last 200 iterations) and the
    registry; cells that were ever seated (any status) are left out."""
    seated = {cell_key(c.sleeve, c.overrides or {}) for c in registry.children}
    src = (read_tests(ldir, with_curves=False) if ldir is not None else []) + list(registry.iterations)
    latest: dict[str, dict[str, Any]] = {}
    for it in sorted(src, key=lambda r: str(r.get("ts") or r.get("date") or "")):
        if it.get("overrides") and it.get("by") != "seat" and it.get("kind") != "seated":
            latest[cell_key(it.get("sleeve") or "", it["overrides"])] = it      # a cell's latest verdict decides
    rows: dict[str, dict[str, Any]] = {}
    for key, it in latest.items():
        if not str(it.get("verdict", "")).startswith("passed but not spawned"):
            continue
        if key in seated:
            continue
        rows[key] = {"proposal": it.get("proposal") or it.get("name") or "waiting list", "overrides": it["overrides"],
                     "oos_prop": it.get("oos_prop"),
                     "sleeve": it.get("sleeve") or "stocks", "hypothesis": it.get("hypothesis") or "",
                     "date": it.get("date"), "is_cagr": it.get("is_cagr"), "oos_cagr": it.get("oos_cagr"),
                     "oos_dd": it.get("oos_dd"), "parent_oos_cagr": it.get("parent_oos_cagr")}
    objective = objective or "growth"
    return sorted(rows.values(), key=lambda r: rank_key(r, objective))


def seat_waiting(registry: HiveRegistry, tier: Tier, on: date, ldir=None, objective: str | None = None) -> list[dict[str, Any]]:
    """Seat the best waiting cells when seats are free. A cell is never seated twice."""
    out: list[dict[str, Any]] = []
    seated: set[str] = set()
    for it in waiting_list(registry, ldir, objective):
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
    if ldir is not None:
        from hive.operator.lab import rollup

        rollup(ldir)
    records: list[dict[str, Any]] = seat_waiting(registry, tier, on, ldir, cfg.operator.objective)
    todo = queue_staged(registry, cfg, bounds, ldir, with_refine=with_refine)
    inputs = dict(inputs_by_sleeve or {})
    parents: dict[str, Any] = {}
    no_data: set[str] = set()    # a sleeve whose markets are not in the cache yet is skipped, not failed
    started = datetime.now(UTC)
    n_done = 0
    hypothesis = {"rescore": "a top cell re-tested to score it on the prop objective",
                  "sweep": "systematic sweep of the strategy space",
                  "refine": "refinement around the best cells found so far",
                  "evolve": "mutation of one of the best cells found so far"}
    for sleeve, ov, stage in todo:
        if sleeve in no_data:
            continue
        if sleeve not in inputs:
            try:
                if settings is None:
                    raise FileNotFoundError(sleeve)
                inputs[sleeve] = default_inputs(settings, cfg, sleeve)
            except FileNotFoundError:
                no_data.add(sleeve)
                continue
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if (budget_s is not None and elapsed >= budget_s) or (budget_s is None and n_done >= n_cells):
            break
        rec: dict[str, Any] = {"date": on.isoformat(), "proposal": describe(sleeve, ov, stage=stage), "kind": "sweep",
                               "stage": stage, "hypothesis": hypothesis[stage],
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
                    "parent_oos_cagr": parent.outofsample["cagr"],
                    "oos_prop": ev.outofsample.get("prop_pass_rate"), "parent_oos_prop": parent.outofsample.get("prop_pass_rate")})
        passed, why = verdict(parent, ev, cfg.operator.max_drawdown_tolerance, cfg.operator.objective)
        child_id = None
        seated_as = next((c.id for c in registry.children if cell_key(c.sleeve, c.overrides or {}) == cell_key(sleeve, ov)), None)
        if passed and seated_as:
            rec["verdict"] = f"passed; already a sub-agent ({seated_as}): {why}"
        elif passed:
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
        # one compact line per run: the operator reads the last dozen, so it gets the gist, not the flood
        ran = [r for r in records if r.get("kind") == "sweep"]
        won = [r for r in ran if str(r.get("verdict", "")).startswith(("spawned", "passed"))]
        top = sorted((r for r in ran if r.get("oos_cagr") is not None), key=lambda r: -r["oos_cagr"])[:3]
        keep = list({id(r): r for r in (won[:5] + top + [r for r in records if r.get("kind") == "seated"])}.values())
        append_research_log(registry.path.parent / RESEARCH_LOG, {
            "date": on.isoformat(), "note": f"lab: {len(ran)} cells backtested, {len(won)} beat the parent (code, no LLM)",
            "n_tested": len(ran), "n_passed": len(won),
            "parent_oos_cagr": next(iter(parents.values())).outofsample.get("cagr") if parents else None,
            "proposals": [{"name": r["proposal"], "overrides": r["overrides"], "sleeve": r["sleeve"],
                           "oos_cagr": r.get("oos_cagr"), "verdict": (r.get("verdict") or "")[:120]} for r in keep]})
    return records
