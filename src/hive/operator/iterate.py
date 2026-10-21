from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from hive.agents import prompts
from hive.agents.llm import BudgetExceededError, LLMClient
from hive.backtest.costs import ALPACA_CRYPTO, ALPACA_STOCKS, MACRO_CFD
from hive.backtest.engine import Backtester
from hive.backtest.experiments import _apply_overrides
from hive.config import REPO_ROOT, StrategyConfig, Tier
from hive.hive.registry import Child, HiveRegistry

log = logging.getLogger(__name__)

OPERATOR_SYSTEM = (
    "You are the operator, the firm's head of research. Your job is to make the firm's strategy "
    "earn more: propose up to {N} variants of its configuration, each changing one to three "
    "parameters within the stated bounds, each with the sleeve it should trade (stocks, crypto, "
    "macro or all) and a one-sentence testable hypothesis grounded in the evidence summary and the firm's "
    "own results so far. A variant may change the entry family (entry_mode) as well as any knob: "
    "the trend template, exits and limits stay, the moment to buy is yours to design. Read "
    "tested_variants, research_log and your_record before proposing: never repeat a tested variant, build on "
    "what beat the parent, and say what you learned in the note. You may also propose a monitor "
    "(kind=monitor, watch=up to five topics or symbols, no overrides): a news-channel sub-agent whose "
    "bounded daily reports reach the portfolio manager; it costs about a cent a day and takes a seat, "
    "so propose one only when a theme bears on open positions or candidates and no monitor covers it. "
    "Code backtests every proposal in-sample and out-of-sample on its own sleeve's markets (stocks, "
    "crypto, or macro: forex, gold, silver and index futures) against that sleeve's parent; a variant "
    "that beats the parent on the firm's objective (the simulated prop-firm pass rate, growth breaking "
    "ties), made money in sample and kept its drawdown within tolerance takes a paper seat while the "
    "tier has one free. Promotion is automatic: a variant that out-earns the parent on paper for the "
    "shadow period is promoted into the firm's configuration by the evidence rule, and reverted if "
    "the baseline sub-agent beats it back; one that breaks a prop loss limit, lags the parent or falls "
    "75% is retired. Books the primary's venue cannot trade (forex, metals, index futures, shorts) "
    "stay on paper and are judged on their own record. So propose what you believe will earn a seat, "
    "not what flatters a backtest. You are judged the same way: your_record compares your proposals' "
    "pass rate with the lab's free search, and your research budget is that ratio times the base "
    "(a quarter to four times), plus a bonus for each proposal later promoted; a failed proposal, or "
    "one outside the bounds, lowers your next budget, and your calls are paid from the bankroll. "
    "Whenever a seat is free, propose at least one variant: if you think nothing deserves a seat, say "
    "why in the note and still propose the single most informative experiment, because an untested "
    "idea earns nothing. The levers you may change are exactly those in bounds (entries, exits, "
    "direction, sizing); budgets, the universe and the death rules are not levers, and you cannot "
    "promote anything yourself or move money to a live venue. " + prompts.OBJECTIVE
)


class Proposal(BaseModel):
    name: str = Field(max_length=40)
    hypothesis: str = Field(max_length=300)
    overrides: dict[str, float | int | bool | str] = Field(default_factory=dict, max_length=3)
    sleeve: str = "stocks"
    kind: str = "variant"            # "variant" (backtested strategy) | "monitor" (news channel)
    watch: list[str] = Field(default_factory=list, max_length=5)


class Proposals(BaseModel):
    proposals: list[Proposal] = Field(max_length=5)
    note: str = Field(default="", max_length=300)


def proposal_schema(bounds: dict[str, tuple[float, float]]) -> type[BaseModel]:
    """The reply schema the model fills: overrides as (param, value) pairs whose ``param``
    is an enum of the pre-registered tunables. A free-form dict does not survive the
    structured-output grammar (the first live run came back with empty overrides)."""
    keys = tuple(sorted(bounds)) or ("stocks.trail_atr",)
    override = create_model("OverrideChoice", param=(Literal[keys], ...), value=(float | str, ...))
    proposal = create_model(
        "ProposalChoice", name=(str, Field(max_length=40)), hypothesis=(str, Field(max_length=300)),
        kind=(Literal["variant", "monitor"], "variant"),
        overrides=(list[override], Field(default_factory=list, max_length=3)),
        watch=(list[str], Field(default_factory=list, max_length=5)),
        sleeve=(Literal["stocks", "crypto", "macro", "all"], "stocks"),
    )
    return create_model("ProposalsChoice", proposals=(list[proposal], Field(max_length=5)),
                        note=(str, Field(default="", max_length=300)))


def to_proposals(parsed: BaseModel | None, limit: int) -> list[Proposal]:
    if parsed is None:
        return []
    return [Proposal(name=p.name, hypothesis=p.hypothesis, sleeve=p.sleeve, kind=getattr(p, "kind", "variant"),
                     watch=[str(w)[:60] for w in getattr(p, "watch", [])],
                     overrides={o.param: o.value for o in p.overrides})
            for p in list(parsed.proposals)[:limit]]


def coerce_types(cfg: StrategyConfig, overrides: dict[str, Any]) -> dict[str, Any]:
    """Integer tunables (bar counts) get integer values; the model replies in floats."""
    out: dict[str, Any] = {}
    for k, v in overrides.items():
        cur = _get(cfg, k)
        if isinstance(cur, bool):
            out[k] = str(v).lower() in ("1", "true", "yes") if isinstance(v, str) else bool(v)
        elif isinstance(cur, int):
            out[k] = int(round(float(v)))
        elif isinstance(cur, str):
            out[k] = str(v)
        else:
            out[k] = float(v)
    return out


Bound = tuple[float, float] | list[str]   # a numeric range, or the allowed choices


def load_bounds(path: Path) -> dict[str, Bound]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    out: dict[str, Bound] = {}
    for k, v in raw.items():
        out[k] = [str(c) for c in v["choices"]] if "choices" in v else (float(v["min"]), float(v["max"]))
    return out


def validate_overrides(overrides: dict[str, Any], bounds: dict[str, Bound]) -> tuple[bool, str]:
    if not overrides:
        return False, "no overrides"
    for k, v in overrides.items():
        if k not in bounds:
            return False, f"{k} is not a pre-registered tunable"
        b = bounds[k]
        if isinstance(b, list):
            if str(v) not in b:
                return False, f"{k}={v} is not one of {b}"
            continue
        lo, hi = b
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
    curves: dict[str, dict[str, list]] = field(default_factory=dict)   # window -> {dates, equity, benchmark}


KEEP = ("cagr", "sharpe", "max_drawdown", "n_trades", "win_rate", "profit_factor", "avg_hold_days", "n_drawdown_halts")


def evaluate(cfg: StrategyConfig, overrides: dict[str, Any], inputs: dict[str, Any], *,
             keep_curves: bool = False) -> EvalResult:
    """Backtest a variant in both windows on the cached data (``inputs`` from load_bars_for_backtest).
    With ``keep_curves`` the downsampled equity curve of each window (and the benchmark's buy & hold
    on the same dates) travels with the result, so the lab can show it."""
    from hive.hive.propfirm import prop_metrics
    from hive.operator.lab import benchmark_on, downsample

    c = _apply_overrides(cfg, overrides)
    out, curves = {}, {}
    for label, (s, e) in (("insample", (cfg.operator.insample_start, cfg.operator.insample_end)),
                          ("outofsample", (cfg.operator.outofsample_start, cfg.operator.outofsample_end))):
        res = Backtester(inputs["bars"], inputs["bench"], c, inputs["cost"], asset_class_by_symbol=inputs["classes"],
                         initial_equity=cfg.survival.starting_bankroll_usd, start=s, end=e,
                         universe=inputs.get("universe"), vix=inputs.get("vix"), calendar=inputs.get("calendar")).run()
        out[label] = {k: float(res.metrics.get(k, 0.0)) for k in KEEP}
        out[label].update(prop_metrics(res.equity, cfg.prop, res.equity_low))
        if keep_curves:
            cv = downsample(res.equity)
            cv["benchmark"] = benchmark_on(cv["dates"], inputs.get("bench"), cfg.survival.starting_bankroll_usd)
            curves[label] = cv
    return EvalResult(overrides, out["insample"], out["outofsample"], curves)


def breach_rate(m: dict[str, Any]) -> float:
    return float(m.get("prop_fail_daily_rate") or 0.0) + float(m.get("prop_fail_total_rate") or 0.0)


def prop_score(m: dict[str, Any]) -> float:
    """The lab's prop objective for one window's metrics: passes earn, breaches cost (a result
    scored before the score existed counts its pass rate)."""
    return float(m["prop_score"]) if m.get("prop_score") is not None else float(m.get("prop_pass_rate") or 0.0)


def verdict(parent: EvalResult, child: EvalResult, tolerance: float, objective: str = "growth") -> tuple[bool, str]:
    """A cell earns a paper seat when it beats the parent out of sample on the firm's objective:
    the simulated prop-firm pass rate (ties broken by growth) or, with "growth", CAGR; the
    in-sample and drawdown checks below apply either way."""
    p, c = parent.outofsample, child.outofsample
    if objective == "prop":
        pp, cp = prop_score(p), prop_score(c)
        if cp < pp or (cp == pp and c["cagr"] <= p["cagr"]):
            return False, (f"OOS prop score {cp:+.2f} (pass {float(c.get('prop_pass_rate') or 0):.0%}, breach "
                           f"{breach_rate(c):.0%}) does not beat parent {pp:+.2f}")
    elif c["cagr"] <= p["cagr"]:
        return False, f"OOS CAGR {c['cagr']:.2%} does not beat parent {p['cagr']:.2%}"
    if c["max_drawdown"] < p["max_drawdown"] * tolerance:
        return False, f"OOS drawdown {c['max_drawdown']:.1%} worse than tolerated {p['max_drawdown'] * tolerance:.1%}"
    if child.insample["cagr"] < 0:
        return False, "negative in-sample return"
    if objective == "prop":
        return True, (f"beats parent on the prop objective out of sample (score {prop_score(c):+.2f} vs {prop_score(p):+.2f}; "
                      f"pass {float(c.get('prop_pass_rate') or 0):.0%} vs {float(p.get('prop_pass_rate') or 0):.0%}, "
                      f"breach {breach_rate(c):.0%} vs {breach_rate(p):.0%}) within the drawdown tolerance")
    return True, "beats parent out of sample within the drawdown tolerance"


RESEARCH_LOG = "research_log.jsonl"


def read_research_log(path: Path, last: int = 12) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows[-last:]


def append_research_log(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def tested_variants(registry: HiveRegistry) -> list[dict[str, Any]]:
    """Every variant ever backtested, condensed, so the operator builds on its own record."""
    out = []
    for it in registry.iterations:
        if it.get("overrides"):
            out.append({"overrides": it["overrides"], "sleeve": it.get("sleeve"), "oos_cagr": it.get("oos_cagr"),
                        "oos_dd": it.get("oos_dd"), "parent_oos_cagr": it.get("parent_oos_cagr"),
                        "verdict": (it.get("verdict") or "")[:60]})
    return out[-60:]


def performance_context(registry: HiveRegistry, status: dict[str, Any] | None, parent_eval: EvalResult | None,
                        bounds: dict[str, Bound], cfg: StrategyConfig,
                        research_log: list[dict[str, Any]] | None = None,
                        your_record: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "your_record": your_record or {},
        "entry_families": {
            "reset_breakout": "the pre-registered rule: strong impulse, quiet reset of short ROC, breakout trigger",
            "template_cross": "buy the first bar the trend template is met (earliest, most trades)",
            "high_breakout": "buy a close above the prior breakout_lookback-bar high inside the template",
            "ma_pullback": "buy the recovery above the mid MA after a touch within pullback_touch_bars",
            "momentum_threshold": "buy when vol-adjusted 12-1 momentum >= momentum_min with the breakout trigger",
        },
        "tested_variants": tested_variants(registry),
        "social_trending": _social_trending(registry.path.parent),
        "research_log": research_log or [],
        "monitors": [{"name": c.name, "watch": c.watch, "born": c.born} for c in registry.active() if c.kind == "monitor"],
        "ready_to_promote": [{"name": c.name, "voi": c.voi, "shadow_days": c.shadow_days, "overrides": c.overrides}
                             for c in registry.ready_to_promote()],
        "firm_status": prompts.firm_status(status, cfg.survival.tiers),
        "survival": status, "parent_backtest": None if parent_eval is None else
        {"insample": parent_eval.insample, "outofsample": parent_eval.outofsample},
        "current_tunables": {k: _get(cfg, k) for k in bounds},
        "bounds": {k: ({"choices": b} if isinstance(b, list) else {"min": b[0], "max": b[1]})
                   for k, b in bounds.items()},
        "children": [{"name": c.name, "status": c.status, "overrides": c.overrides, "voi": c.voi,
                      "shadow_days": c.shadow_days} for c in registry.children][-12:],
        "past_iterations": registry.iterations[-8:],
        "evidence": ("Null tests show entry timing carries no edge inside the trend template; the only lever "
                     "that improved both walk-forward windows was removing the SMA50 exit. Most of a trend "
                     "system's edge is in exits and sizing. Fees and turnover decide outcomes."),
    }


def _social_trending(state_dir: Path) -> list[dict[str, Any]]:
    """Names the social pulse found on more than one source (symbols and counts only), so the
    operator can decide whether a monitor deserves a seat."""
    p = state_dir / "social" / "latest.json"
    try:
        return (json.loads(p.read_text()).get("cross") or [])[:10] if p.exists() else []
    except (ValueError, OSError):
        return []


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
    lab_dir: Path | None = None,
    inputs_for: Any = None,
) -> list[dict[str, Any]]:
    """One operator iteration. Returns the iteration records written to the registry.
    With ``lab_dir`` every backtest (the parent's and each proposal's) is kept as a lab record."""
    from hive.operator.lab import lab_record, parent_eval, record_test
    from hive.survival.earned import explain_operator, operator_multiplier, operator_scorecard

    on = on or date.today()
    bounds = load_bounds(bounds_path or (REPO_ROOT / cfg.operator.bounds_file))
    parent = parent_eval(cfg, "stocks", inputs, lab_dir, on=on.isoformat())
    log_path = registry.path.parent / RESEARCH_LOG
    # the operator is judged too, and told how: its record against the lab's free search sets its budget
    card = operator_scorecard(read_research_log(log_path, last=100_000), registry.children)
    mult = operator_multiplier(card)
    record = {**card, "budget_multiplier": mult, "how_you_are_paid": explain_operator(card, mult)}
    ctx = performance_context(registry, status, parent, bounds, cfg, research_log=read_research_log(log_path),
                              your_record=record)
    # every proposal is backtested on its own sleeve's markets against that sleeve's parent
    sleeve_inputs: dict[str, dict[str, Any]] = {"stocks": inputs}
    parents: dict[str, EvalResult] = {"stocks": parent}

    def for_sleeve(sl: str) -> tuple[dict[str, Any], EvalResult]:
        if sl not in sleeve_inputs:
            sleeve_inputs[sl] = inputs_for(sl) if inputs_for is not None else inputs
        if sl not in parents:
            parents[sl] = parent_eval(cfg, sl, sleeve_inputs[sl], lab_dir, on=on.isoformat())
        return sleeve_inputs[sl], parents[sl]

    proposals: list[Proposal] = []
    cost = 0.0
    if proposals_override is not None:
        proposals = proposals_override
    elif llm is not None and llm.available:
        try:
            parsed, call = llm.parse(
                role="operator", model=model, system=OPERATOR_SYSTEM.replace("{N}", str(cfg.operator.max_variants_per_iteration)),
                user=json.dumps(ctx, default=str)[:40_000], schema=proposal_schema(bounds), effort="medium",
                max_tokens=2500,
            )
            cost = call.cost_usd
            proposals = to_proposals(parsed, cfg.operator.max_variants_per_iteration)
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
    note = getattr(parsed, "note", "") if proposals_override is None and llm is not None else ""
    for p in proposals:
        rec: dict[str, Any] = {"date": on.isoformat(), "proposal": p.name, "hypothesis": p.hypothesis,
                               "overrides": p.overrides, "sleeve": p.sleeve, "cost_usd": cost / max(len(proposals), 1),
                               "kind": p.kind}
        if p.kind == "monitor":
            watch = [w.strip() for w in p.watch if w and w.strip()][:5]
            if not watch:
                rec["verdict"] = "rejected: a monitor needs something to watch"
            else:
                can, reason = registry.can_spawn(tier)
                if can:
                    child = registry.spawn(Child(id="", name=p.name, kind="monitor", sleeve="all", watch=watch,
                                                 hypothesis=p.hypothesis), tier)
                    rec["watch"], rec["verdict"] = watch, f"spawned monitor {child.id} watching {', '.join(watch)}"
                else:
                    rec["watch"], rec["verdict"] = watch, f"monitor not spawned: {reason}"
            registry.record_iteration(rec)
            records.append(rec)
            continue
        ok, why = validate_overrides(p.overrides, bounds)
        if not ok:
            rec["verdict"] = f"rejected: {why}"
            registry.record_iteration(rec)
            records.append(rec)
            continue
        p.overrides = rec["overrides"] = coerce_types(cfg, p.overrides)
        t0 = datetime.now(UTC)
        try:
            p_inputs, parent = for_sleeve(p.sleeve)
            ev = evaluate(cfg, p.overrides, p_inputs, keep_curves=lab_dir is not None)
        except Exception as exc:  # a bad value must not take the whole iteration down
            rec["verdict"] = f"rejected: backtest failed: {str(exc)[:160]}"
            registry.record_iteration(rec)
            records.append(rec)
            if lab_dir is not None:
                record_test(lab_dir, lab_record(by="operator", sleeve=p.sleeve, name=p.name, overrides=p.overrides,
                                                 ev=None, parent=parent, verdict=rec["verdict"], on=on.isoformat(),
                                                 hypothesis=p.hypothesis, cfg=cfg))
            continue
        rec.update({"is_cagr": ev.insample["cagr"], "oos_cagr": ev.outofsample["cagr"],
                    "is_dd": ev.insample["max_drawdown"], "oos_dd": ev.outofsample["max_drawdown"],
                    "parent_oos_cagr": parent.outofsample["cagr"],
                    "oos_prop": ev.outofsample.get("prop_pass_rate"), "parent_oos_prop": parent.outofsample.get("prop_pass_rate")})
        passed, why = verdict(parent, ev, cfg.operator.max_drawdown_tolerance, cfg.operator.objective)
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
        if lab_dir is not None:
            record_test(lab_dir, lab_record(
                by="operator", sleeve=p.sleeve, name=p.name, overrides=p.overrides, ev=ev, parent=parent,
                verdict=rec["verdict"], on=on.isoformat(), hypothesis=p.hypothesis, cfg=cfg,
                child_id=rec["verdict"].split("spawned shadow child ", 1)[1].split(":")[0] if "spawned shadow child" in rec["verdict"] else None,
                elapsed_s=round((datetime.now(UTC) - t0).total_seconds(), 1)))
    # The firm's memory: what was tried, what it found, what the operator thought.
    append_research_log(log_path, {
        "date": on.isoformat(), "by": "operator", "note": note, "budget_multiplier": mult,
        "parent_oos_cagr": parents["stocks"].outofsample.get("cagr"),
        "proposals": [{"name": r.get("proposal"), "overrides": r.get("overrides"), "sleeve": r.get("sleeve"),
                       "oos_cagr": r.get("oos_cagr"), "verdict": (r.get("verdict") or "")[:120]} for r in records],
    })
    return records


def default_inputs(settings, cfg: StrategyConfig, sleeve: str = "stocks") -> dict[str, Any]:
    import pandas as pd

    from hive.data.seed import load_bars_for_backtest, load_pit_universe
    from hive.data.store import BarStore

    store = BarStore(settings.hive_data_dir / "cache")
    bars, classes, bench = load_bars_for_backtest(store, cfg, sleeve=sleeve)
    if sleeve == "macro":
        # forex trades five days a week and the futures keep their own holidays: every market's days
        cal = pd.DatetimeIndex(sorted(set().union(*(b.index for b in bars.values()))))
        return {"bars": bars, "classes": classes, "bench": bench, "cost": MACRO_CFD, "universe": None,
                "vix": None, "calendar": cal}
    pit = settings.hive_data_dir / "raw" / "sp500_pit_membership.csv"
    vix = store.get("index", "VIX")
    return {"bars": bars, "classes": classes, "bench": bench,
            "cost": ALPACA_CRYPTO if sleeve == "crypto" else ALPACA_STOCKS,
            "universe": load_pit_universe(pit) if (sleeve != "crypto" and pit.exists()) else None,
            "vix": vix["close"] if vix is not None else None}
