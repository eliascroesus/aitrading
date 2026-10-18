from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from hive.agents import prompts
from hive.agents.llm import BudgetExceededError, LLMClient
from hive.backtest.costs import ALPACA_CRYPTO, ALPACA_STOCKS
from hive.backtest.engine import Backtester
from hive.backtest.experiments import _apply_overrides
from hive.config import REPO_ROOT, StrategyConfig, Tier
from hive.hive.registry import Child, HiveRegistry

log = logging.getLogger(__name__)

OPERATOR_SYSTEM = (
    "You are the operator, the firm's head of research. Your job is to make the firm's strategy "
    "earn more: propose up to {N} variants of its configuration, each changing one to three "
    "parameters within the stated bounds, each with the sleeve it should trade (stocks, crypto or "
    "all) and a one-sentence testable hypothesis grounded in the evidence summary and the firm's "
    "own results so far. Code backtests every proposal in-sample and out-of-sample; only variants "
    "that beat the parent out of sample without a worse drawdown become paper-traded sub-agents, "
    "and only while the firm's tier has a free seat. Sub-agents that underperform the parent are "
    "retired; a human promotes the winners into the live configuration. Whenever a seat is free, "
    "propose at least one variant: if you think nothing deserves a seat, say why in the note and "
    "still propose the single most informative experiment, because an untested idea earns nothing. "
    "You cannot change risk limits, budgets or the universe, and you cannot promote anything to "
    "live. " + prompts.OBJECTIVE
)


class Proposal(BaseModel):
    name: str = Field(max_length=40)
    hypothesis: str = Field(max_length=300)
    overrides: dict[str, float | int | bool] = Field(max_length=3)
    sleeve: str = "stocks"


class Proposals(BaseModel):
    proposals: list[Proposal] = Field(max_length=5)
    note: str = Field(default="", max_length=300)


def load_bounds(path: Path) -> dict[str, tuple[float, float]]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return {k: (float(v["min"]), float(v["max"])) for k, v in raw.items()}


def validate_overrides(overrides: dict[str, Any], bounds: dict[str, tuple[float, float]]) -> tuple[bool, str]:
    if not overrides:
        return False, "no overrides"
    for k, v in overrides.items():
        if k not in bounds:
            return False, f"{k} is not a pre-registered tunable"
        lo, hi = bounds[k]
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return False, f"{k} must be numeric"
        if not (lo <= fv <= hi):
            return False, f"{k}={v} outside [{lo}, {hi}]"
    return True, ""


@dataclass
class EvalResult:
    overrides: dict[str, Any]
    insample: dict[str, float]
    outofsample: dict[str, float]


KEEP = ("cagr", "sharpe", "max_drawdown", "n_trades", "win_rate", "profit_factor", "avg_hold_days", "n_drawdown_halts")


def evaluate(cfg: StrategyConfig, overrides: dict[str, Any], inputs: dict[str, Any]) -> EvalResult:
    """Backtest a variant in both windows on the cached data (``inputs`` from load_bars_for_backtest)."""
    c = _apply_overrides(cfg, overrides)
    out = {}
    for label, (s, e) in (("insample", (cfg.operator.insample_start, cfg.operator.insample_end)),
                          ("outofsample", (cfg.operator.outofsample_start, cfg.operator.outofsample_end))):
        res = Backtester(inputs["bars"], inputs["bench"], c, inputs["cost"], asset_class_by_symbol=inputs["classes"],
                         initial_equity=cfg.survival.starting_bankroll_usd, start=s, end=e,
                         universe=inputs.get("universe"), vix=inputs.get("vix")).run()
        out[label] = {k: float(res.metrics.get(k, 0.0)) for k in KEEP}
    return EvalResult(overrides, out["insample"], out["outofsample"])


def verdict(parent: EvalResult, child: EvalResult, tolerance: float) -> tuple[bool, str]:
    p, c = parent.outofsample, child.outofsample
    if c["cagr"] <= p["cagr"]:
        return False, f"OOS CAGR {c['cagr']:.2%} does not beat parent {p['cagr']:.2%}"
    if c["max_drawdown"] < p["max_drawdown"] * tolerance:
        return False, f"OOS drawdown {c['max_drawdown']:.1%} worse than tolerated {p['max_drawdown'] * tolerance:.1%}"
    if child.insample["cagr"] < 0:
        return False, "negative in-sample return"
    return True, "beats parent out of sample within the drawdown tolerance"


def performance_context(registry: HiveRegistry, status: dict[str, Any] | None, parent_eval: EvalResult | None,
                        bounds: dict[str, tuple[float, float]], cfg: StrategyConfig) -> dict[str, Any]:
    return {
        "firm_status": prompts.firm_status(status, cfg.survival.tiers),
        "survival": status, "parent_backtest": None if parent_eval is None else
        {"insample": parent_eval.insample, "outofsample": parent_eval.outofsample},
        "current_tunables": {k: _get(cfg, k) for k in bounds},
        "bounds": {k: {"min": lo, "max": hi} for k, (lo, hi) in bounds.items()},
        "children": [{"name": c.name, "status": c.status, "overrides": c.overrides, "voi": c.voi,
                      "shadow_days": c.shadow_days} for c in registry.children][-12:],
        "past_iterations": registry.iterations[-8:],
        "evidence": ("Null tests show entry timing carries no edge inside the trend template; the only lever "
                     "that improved both walk-forward windows was removing the SMA50 exit. Most of a trend "
                     "system's edge is in exits and sizing. Fees and turnover decide outcomes."),
    }


def _get(cfg: StrategyConfig, dotted: str) -> Any:
    node: Any = cfg
    for p in dotted.split("."):
        node = getattr(node, p)
    return node


def operate(
    *,
    cfg: StrategyConfig,
    registry: HiveRegistry,
    tier: Tier,
    status: dict[str, Any] | None,
    llm: LLMClient | None,
    model: str,
    inputs: dict[str, Any],
    bounds_path: Path | None = None,
    on: date | None = None,
    proposals_override: list[Proposal] | None = None,
) -> list[dict[str, Any]]:
    """One operator iteration. Returns the iteration records written to the registry."""
    on = on or date.today()
    bounds = load_bounds(bounds_path or (REPO_ROOT / cfg.operator.bounds_file))
    parent = evaluate(cfg, {}, inputs)
    ctx = performance_context(registry, status, parent, bounds, cfg)

    proposals: list[Proposal] = []
    cost = 0.0
    if proposals_override is not None:
        proposals = proposals_override
    elif llm is not None and llm.available:
        try:
            parsed, call = llm.parse(
                role="operator", model=model, system=OPERATOR_SYSTEM.replace("{N}", str(cfg.operator.max_variants_per_iteration)),
                user=json.dumps(ctx, default=str)[:40_000], schema=Proposals, effort="medium", max_tokens=2500,
            )
            cost = call.cost_usd
            proposals = parsed.proposals[: cfg.operator.max_variants_per_iteration] if parsed else []
            if not proposals:
                why = (f"model reply rejected: {call.error or call.stop_reason}" if parsed is None
                       else f"model proposed nothing: {parsed.note or 'no note'}")
                registry.record_iteration({"date": on.isoformat(), "proposal": "", "hypothesis": "",
                                           "overrides": {}, "sleeve": "", "cost_usd": cost,
                                           "verdict": f"no proposals ({why})"})
                return registry.iterations[-1:]
        except BudgetExceededError as exc:
            registry.record_iteration({"date": on.isoformat(), "proposal": "", "verdict": f"skipped: {exc}"})
            return registry.iterations[-1:]
    records = []
    for p in proposals:
        ok, why = validate_overrides(p.overrides, bounds)
        rec: dict[str, Any] = {"date": on.isoformat(), "proposal": p.name, "hypothesis": p.hypothesis,
                               "overrides": p.overrides, "sleeve": p.sleeve, "cost_usd": cost / max(len(proposals), 1)}
        if not ok:
            rec["verdict"] = f"rejected: {why}"
            registry.record_iteration(rec)
            records.append(rec)
            continue
        ev = evaluate(cfg, p.overrides, inputs)
        rec.update({"is_cagr": ev.insample["cagr"], "oos_cagr": ev.outofsample["cagr"],
                    "is_dd": ev.insample["max_drawdown"], "oos_dd": ev.outofsample["max_drawdown"],
                    "parent_oos_cagr": parent.outofsample["cagr"]})
        passed, why = verdict(parent, ev, cfg.operator.max_drawdown_tolerance)
        if passed:
            can, reason = registry.can_spawn(tier)
            if can:
                child = registry.spawn(Child(id="", name=p.name, kind="variant", sleeve=p.sleeve,
                                             overrides=p.overrides, hypothesis=p.hypothesis,
                                             metrics={"insample": ev.insample, "outofsample": ev.outofsample}),
                                       tier, )
                rec["verdict"] = f"spawned shadow child {child.id}: {why}"
            else:
                rec["verdict"] = f"passed but not spawned: {reason}"
        else:
            rec["verdict"] = f"rejected: {why}"
        registry.record_iteration(rec)
        records.append(rec)
    return records


def default_inputs(settings, cfg: StrategyConfig, sleeve: str = "stocks") -> dict[str, Any]:
    from hive.data.seed import load_bars_for_backtest, load_pit_universe
    from hive.data.store import BarStore

    store = BarStore(settings.hive_data_dir / "cache")
    bars, classes, bench = load_bars_for_backtest(store, cfg, sleeve=sleeve)
    pit = settings.hive_data_dir / "raw" / "sp500_pit_membership.csv"
    vix = store.get("index", "VIX")
    return {"bars": bars, "classes": classes, "bench": bench,
            "cost": ALPACA_CRYPTO if sleeve == "crypto" else ALPACA_STOCKS,
            "universe": load_pit_universe(pit) if (sleeve != "crypto" and pit.exists()) else None,
            "vix": vix["close"] if vix is not None else None}
