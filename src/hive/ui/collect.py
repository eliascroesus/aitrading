"""Collect everything the dashboard shows into one JSON-serialisable dict."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.config import Settings, StrategyConfig
from hive.orchestrator.book import PositionBook
from hive.orchestrator.journal import Journal
from hive.survival.ledger import SurvivalLedger


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _equity_csv(p: Path, max_points: int = 1500) -> dict[str, list]:
    """``equity.csv`` from a backtest -> {dates, equity, benchmark}; downsampled."""
    if not p.exists():
        return {"dates": [], "equity": [], "benchmark": []}
    rows = list(csv.DictReader(p.open()))
    step = max(1, len(rows) // max_points)
    rows = rows[::step]
    out = {"dates": [r[""] if "" in r else r.get("date", "") for r in rows],
           "equity": [float(r["equity"]) for r in rows],
           "benchmark": [float(r["benchmark_buy_hold"]) if r.get("benchmark_buy_hold") not in (None, "") else None for r in rows]}
    return out


def collect(settings: Settings, cfg: StrategyConfig) -> dict[str, Any]:
    state, reports = settings.hive_state_dir, settings.hive_reports_dir
    data: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                            "strategy": {"name": cfg.name, "version": cfg.version}}

    # --- ledger / survival ------------------------------------------------------
    ledger_path = state / "ledger.jsonl"
    try:
        ledger = SurvivalLedger(ledger_path, cfg.survival)
        chain_ok = ledger.verify()
    except ValueError:
        ledger, chain_ok = None, False
    marks = ledger.equity_marks() if ledger else []
    costs_by_day: dict[str, float] = {}
    if ledger:
        for e in ledger.entries:
            if e.kind in ("ai_cost", "data_cost", "infra_cost"):
                costs_by_day[e.day] = costs_by_day.get(e.day, 0.0) + e.amount_usd
    cum, cum_costs = 0.0, {}
    for d, _ in marks:
        cum += costs_by_day.get(d, 0.0)
        cum_costs[d] = cum
    last_eq = marks[-1][1] if marks else None
    status = ledger.status(last_eq, on=datetime.fromisoformat(marks[-1][0]).date()).__dict__ if (ledger and last_eq is not None) else None
    data["survival"] = {
        "status": status, "chain_ok": chain_ok, "n_marks": len(marks),
        "start_date": marks[0][0] if marks else None, "last_date": marks[-1][0] if marks else None,
        "starting_bankroll": cfg.survival.starting_bankroll_usd,
        "death_floor": cfg.survival.death_floor_usd,
        "tiers": [t.model_dump() for t in sorted(cfg.survival.tiers, key=lambda t: t.min_equity_usd)],
        "tier_changes": [e.meta for e in ledger.entries if e.kind == "tier_change"] if ledger else [],
        "halts": [e.meta for e in ledger.entries if e.kind == "halt"] if ledger else [],
        "death": (ledger.death_recorded().meta if ledger and ledger.death_recorded() else None),
        "ai_cost_total": ledger.ai_cost_total() if ledger else 0.0,
    }
    shadow_marks = []
    sp = state / "ledger_shadow.jsonl"
    if sp.exists():
        try:
            shadow_marks = SurvivalLedger(sp, cfg.survival).equity_marks()
        except ValueError:
            shadow_marks = []
    data["equity"] = {
        "dates": [d for d, _ in marks],
        "primary_gross": [v for _, v in marks],
        "primary_net": [v - cum_costs.get(d, 0.0) for d, v in marks],
        "shadow": dict(shadow_marks),
        "cum_ai_cost": [cum_costs.get(d, 0.0) for d, _ in marks],
    }

    # --- daily reports: benchmarks, decisions, regime ----------------------------------
    daily_dir = reports / "daily"
    daily = sorted(daily_dir.glob("*.json")) if daily_dir.exists() else []
    bench: dict[str, dict[str, float]] = {}
    latest = None
    regime_by_day: dict[str, str] = {}
    for p in daily:
        d = _read_json(p)
        if not d:
            continue
        day = p.stem
        for k, v in (d.get("benchmarks") or {}).items():
            bench.setdefault(k, {})[day] = v
        regime_by_day[day] = (d.get("primary") or {}).get("regime", {}).get("state", "")
        latest = d
    data["benchmarks"] = bench
    data["regime_by_day"] = regime_by_day
    data["latest"] = latest

    # --- books ----------------------------------------------------------------------
    def book(label: str) -> list[dict]:
        b = PositionBook(state / f"book_{label}.json")
        return [{"symbol": p.symbol, "asset_class": p.asset_class, "qty": p.qty, "entry": p.entry_price,
                 "stop": p.stop_price, "r": p.r_per_share, "bars_held": p.bars_held, "entry_date": p.entry_date,
                 "source": p.source, "stop_armed": bool(p.stop_order_id),
                 "fill_pending": bool(p.meta.get("fill_pending")), "invalidation": p.invalidation_condition}
                for p in b.positions.values()]
    data["positions"] = {"primary": book("primary"), "shadow": book("shadow")}

    # --- journals: recent decisions, orders, exits, llm calls, anomalies --------------------
    jdir = state / "journal"
    files = sorted(p for p in jdir.glob("*.jsonl") if "-shadow" not in p.name) if jdir.exists() else []
    recent, llm_calls, anomalies, broken = [], [], 0, []
    desks: dict = {"date": None, "notes": [], "watch": [], "scout": None}
    for p in files[-30:]:
        ok = Journal.verify_file(p)
        if not ok:
            broken.append(p.name)
        for e in Journal.read(p):
            k, pl = e["kind"], e["payload"]
            if k == "desk_notes":
                desks["notes"], desks["date"] = pl.get("notes", []), p.stem[:10]
                desks["portfolio_note"] = pl.get("portfolio_note", "")
            elif k == "news_watch":
                desks["watch"].append({"date": p.stem[:10], **pl})
            elif k == "meme_day":
                desks["scout"] = {"date": p.stem[:10], **pl}
            if k in ("decisions",):
                for d in pl.get("decisions", []):
                    recent.append({"date": p.stem, "kind": "decision", **d})
            elif k in ("order", "exit"):
                recent.append({"date": p.stem, "kind": k, **pl})
            elif k == "llm_call":
                llm_calls.append({"date": p.stem, **pl})
            elif k == "data_anomaly":
                anomalies += 1
    data["journal"] = {"recent": recent[-200:], "llm_calls": llm_calls[-200:], "anomalies_30d": anomalies,
                       "broken_chains": broken, "n_files": len(files)}
    desks["watch"] = desks["watch"][-20:]
    data["desks"] = desks
    data["heartbeat"] = _read_json(state / "heartbeat.json") or {}

    # --- firm activity timeline: every desk and sub-agent, newest last ----------------------------
    timeline: list[dict] = []

    def who_of(stem: str) -> str:
        if "-child-" in stem:
            return stem.split("-child-", 1)[1]
        return "shadow" if stem.endswith("-shadow") else "primary"

    all_files = sorted(jdir.glob("*.jsonl")) if jdir.exists() else []
    for p in all_files[-40:]:
        who = who_of(p.stem)
        for e in Journal.read(p):
            k, pl, ts = e["kind"], e["payload"], e.get("ts", "")[:16].replace("T", " ")
            if k == "decisions":
                for d in pl.get("decisions", []):
                    timeline.append({"ts": ts, "who": who, "kind": "decision",
                                     "text": f"{d.get('action')} {d.get('symbol')}"
                                             + (f" ({d.get('confidence'):.2f})" if d.get("confidence") is not None else "")})
            elif k in ("order", "exit", "fill", "fill_confirmed", "untradable", "reconcile", "death", "kill_switch"):
                sym = pl.get("symbol", "")
                detail = pl.get("reason") or pl.get("event") or pl.get("message") or ""
                timeline.append({"ts": ts, "who": who, "kind": k, "text": f"{sym} {detail}".strip()})
            elif k == "news_watch" and (pl.get("material_negative") or pl.get("attention_spike")):
                timeline.append({"ts": ts, "who": "news watch", "kind": "flag",
                                 "text": f"{pl.get('symbol')}: " + ("material negative" if pl.get("material_negative") else "attention spike")})
            elif k == "monitor_report":
                timeline.append({"ts": ts, "who": who, "kind": "report",
                                 "text": f"{pl.get('query')}: sent {pl.get('net_sentiment', 0):+.2f}"
                                         + (" material negative" if pl.get("material_negative") else "")
                                         + (" attention spike" if pl.get("attention_spike") else "")})
            elif k == "meme_day":
                timeline.append({"ts": ts, "who": who, "kind": "scan",
                                 "text": f"scanned {pl.get('scanned')} tokens, {pl.get('n_candidates')} passed, "
                                         f"{len(pl.get('entries') or [])} entries, {len(pl.get('exits') or [])} exits"})
            elif k == "run_start":
                timeline.append({"ts": ts, "who": who, "kind": "run", "text": f"cycle for {pl.get('date')} at ${pl.get('equity', 0):,.2f}"})
    for line in (state / "ledger.jsonl").read_text().splitlines() if (state / "ledger.jsonl").exists() else []:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("kind") in ("tier_change", "allocation_change", "promotion", "halt", "death", "capital_in"):
            m = e.get("meta", {})
            timeline.append({"ts": e.get("ts", "")[:16].replace("T", " "), "who": "ledger", "kind": e["kind"],
                             "text": m.get("why") or m.get("reason") or (f"{m.get('from')} -> {m.get('to')}" if "to" in m else json.dumps(m)[:120])})
    timeline.sort(key=lambda x: x["ts"])
    timeline = timeline[-90:]

    # --- what the operator thinks, what the auditor found, what was promoted ------------------------
    rl = state / "research_log.jsonl"
    rows = [json.loads(x) for x in rl.read_text().splitlines() if x.strip()] if rl.exists() else []
    data["research_log"] = rows[-8:]
    reviews = sorted((reports / "reviews").glob("review-*.json")) if (reports / "reviews").exists() else []
    rv = _read_json(reviews[-1]) if reviews else None
    data["review"] = ({"date": reviews[-1].stem.replace("review-", ""),
                       "findings": (rv.get("report") or {}).get("findings", []),
                       "assessment": (rv.get("report") or {}).get("llm_added_value_assessment", "")}
                      if rv and isinstance(rv, dict) else {})
    data["promoted"] = _read_json(state / "promoted_overrides.json") or {}

    # --- hive registry (children / iterations) -----------------------------------------------
    hive = _read_json(state / "hive.json") or {"children": [], "iterations": []}
    try:
        from hive.hive.registry import HiveRegistry

        hive["ready_ids"] = [c.id for c in HiveRegistry(state / "hive.json", cfg.survival).ready_to_promote()] \
            if (state / "hive.json").exists() else []
    except Exception:
        hive["ready_ids"] = []
    data["hive"] = hive
    detail: dict[str, dict] = {}
    for c in hive.get("children", []):
        cid = c.get("id")
        cdir = state / "children" / str(cid)
        d: dict = {"marks": [], "positions": [], "today": None, "latest": None, "meme_book": None}
        if (cdir / "ledger.jsonl").exists():
            try:
                d["marks"] = [[dt, round(v, 2)] for dt, v in SurvivalLedger(cdir / "ledger.jsonl", cfg.survival).equity_marks()][-120:]
            except Exception:
                pass
        if (cdir / "book.json").exists():
            d["positions"] = list(((_read_json(cdir / "book.json") or {}).get("positions") or {}).keys())
        d["latest"] = _read_json(cdir / "latest.json") if (cdir / "latest.json").exists() else None
        d["meme_book"] = _read_json(cdir / "meme_book.json") if (cdir / "meme_book.json").exists() else None
        cj = sorted(jdir.glob(f"*-child-{cid}.jsonl")) if jdir.exists() else []
        if cj:
            kinds: dict[str, int] = {}
            for e in Journal.read(cj[-1]):
                kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
            d["today"] = {"date": cj[-1].stem[:10], "kinds": kinds}
        detail[str(cid)] = d
    data["children_detail"] = detail
    data["allocation"] = _read_json(state / "allocation.json") or {}

    # --- backtests & experiments --------------------------------------------------------------
    bts = []
    for d in sorted(reports.glob("backtest-*")):
        m = _read_json(d / "metrics.json")
        if not m:
            continue
        keys = ["cagr", "sharpe", "max_drawdown", "n_trades", "win_rate", "profit_factor", "avg_hold_days",
                "avg_exposure", "n_drawdown_halts"]
        bts.append({"name": d.name, **{k: m.get(k) for k in keys},
                    "benchmark": {k: (m.get("benchmark") or {}).get(k) for k in ("cagr", "sharpe", "max_drawdown")},
                    "config_version": (m.get("config") or {}).get("version"),
                    "curve": _equity_csv(d / "equity.csv")})
    data["backtests"] = bts
    exp = {}
    edir = reports / "experiments" / "clean"
    if edir.exists():
        for p in sorted(edir.glob("*.csv")):
            try:
                rows = list(csv.DictReader(p.open()))
            except Exception:
                continue
            exp[p.stem] = rows[:64]
    data["experiments"] = exp

    # --- the lab: every backtest the firm has run, with curves for the ones worth drawing ------
    data["lab"] = _lab(reports / "lab", state, cfg, hive)
    for t in data["lab"]["timeline"]:
        timeline.append(t)
    timeline.sort(key=lambda x: x["ts"])
    data["timeline"] = timeline[-100:]
    data["config"] = cfg.model_dump()
    return data


LAB_CURVES_KEPT = 40   # the newest tests keep their curves inline; winners and parents always do


def _lab(ldir: Path, state: Path, cfg: StrategyConfig, hive: dict) -> dict[str, Any]:
    from hive.operator.lab import PARENT_PREFIX, read_tests

    tests = read_tests(ldir)                      # oldest first, with curves
    today = datetime.now(UTC).date().isoformat()
    child_status = {c.get("id"): c.get("status") for c in hive.get("children", [])}
    best = sorted((t for t in tests if t.get("passed") and t.get("oos_cagr") is not None),
                  key=lambda t: -t["oos_cagr"])[:LAB_CURVES_KEPT]
    keep = {t["id"] for t in tests[-LAB_CURVES_KEPT:]} | {t["id"] for t in best}
    rows, curves = [], {}
    for t in tests:
        row = {k: t.get(k) for k in ("id", "ts", "date", "by", "sleeve", "name", "overrides", "verdict", "passed",
                                     "child_id", "hypothesis", "is_cagr", "oos_cagr", "is_dd", "oos_dd",
                                     "parent_oos_cagr", "parent_oos_dd", "elapsed_s")}
        row["is"] = t.get("insample") or {}
        row["oos"] = t.get("outofsample") or {}
        row["child_status"] = child_status.get(t.get("child_id")) if t.get("child_id") else None
        rows.append(row)
        if t["id"] in keep and t.get("curves"):
            curves[t["id"]] = t["curves"]
    parents = {}
    for p in sorted(ldir.glob(f"{PARENT_PREFIX}*.json")) if ldir.exists() else []:
        d = _read_json(p)
        if d:
            parents[p.stem[len(PARENT_PREFIX):]] = {"ts": d.get("ts"), "data": d.get("data"), "insample": d.get("insample"),
                                                     "outofsample": d.get("outofsample"), "curves": d.get("curves") or {}}
    queue, n_queued, grid_size, waiting = [], 0, 0, []
    try:
        from hive.config import REPO_ROOT
        from hive.hive.registry import HiveRegistry
        from hive.operator.iterate import load_bounds
        from hive.operator.sweep import describe, grid, waiting_list
        from hive.operator.sweep import queue as lab_queue

        reg = HiveRegistry(state / "hive.json", cfg.survival)
        q = lab_queue(reg, cfg, load_bounds(REPO_ROOT / cfg.operator.bounds_file), ldir)
        n_queued, grid_size = len(q), len(grid())
        queue = [describe(sl, ov, stage="sweep" if (sl, ov) in grid() else "refine") for sl, ov in q[:8]]
        waiting = [{"name": w["proposal"], "sleeve": w["sleeve"], "oos_cagr": w["oos_cagr"], "oos_dd": w["oos_dd"],
                    "date": w["date"]} for w in waiting_list(reg, ldir)[:10]]
    except Exception:
        pass
    cells = [t for t in tests if t.get("by") not in ("parent", "seat")]
    tl = [{"ts": (t.get("ts") or "")[:16].replace("T", " "), "who": "lab", "kind": t.get("by") or "test",
           "text": f"{t.get('name')}: " + (f"OOS {t['oos_cagr']:.1%}" if t.get("oos_cagr") is not None else "no result")
                   + (f" vs parent {t['parent_oos_cagr']:.1%}" if t.get("parent_oos_cagr") is not None else "")
                   + f" — {(t.get('verdict') or '')[:90]}"} for t in cells[-12:]]
    return {"tests": rows[::-1][:600], "curves": curves, "parents": parents, "queue": queue, "n_queued": n_queued,
            "waiting": waiting,
            "grid_size": grid_size, "n_tests": len(cells), "n_passed": sum(1 for t in cells if t.get("passed")),
            "n_today": sum(1 for t in cells if (t.get("ts") or "")[:10] == today),
            "last_ts": tests[-1].get("ts") if tests else None, "timeline": tl}
