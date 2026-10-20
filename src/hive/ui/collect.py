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


def _equity_csv(p: Path, max_points: int = 400) -> dict[str, list]:
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
            elif k == "trench_round":
                timeline.append({"ts": ts, "who": who, "kind": "scan",
                                 "text": f"scanned {pl.get('scanned')} new launches, {pl.get('passed')} passed, "
                                         f"{pl.get('n_entries', 0)} entries, {pl.get('n_exits', 0)} exits, "
                                         f"paper equity ${pl.get('equity', 0):,.2f}"})
            elif k in ("meme_entry", "meme_exit", "meme_take_half"):
                verb = {"meme_entry": "bought", "meme_exit": "sold", "meme_take_half": "took half of"}[k]
                extra = f" ({pl.get('reason')})" if pl.get("reason") else ""
                timeline.append({"ts": ts, "who": who, "kind": k.replace("meme_", ""),
                                 "text": f"{verb} {pl.get('symbol') or '?'} at ${pl.get('price') or 0:.8g}{extra}"})
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
    hive = {**hive, "iterations": (hive.get("iterations") or [])[-20:]}   # the lab log carries the full record
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
            last_ts = None
            for e in Journal.read(cj[-1]):
                kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
                last_ts = e.get("ts") or last_ts
            d["today"] = {"date": cj[-1].stem[:10], "kinds": kinds}
            d["last_ts"] = last_ts
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
    data["timeline"] = timeline[-160:]
    data["config"] = cfg.model_dump()
    data["mode"] = settings.hive_mode
    data["scouts"] = _scouts(state, hive)
    data["social"] = _read_json(state / "social" / "latest.json") or {}
    data["schedule"] = _schedule()
    data["rules"] = _rules(cfg, settings)
    data["agents"] = _agents(data)
    return data


LAB_CURVES_NEWEST = 60   # the newest tests keep their curves inline ...
LAB_CURVES_BEST = 40     # ... and so do the best winners, the leaderboards and the parents
LAB_LEADERS = 10
LAB_ROWS = 400           # the log shows the newest rows; the whole record is in reports/lab/


def _lab(ldir: Path, state: Path, cfg: StrategyConfig, hive: dict) -> dict[str, Any]:
    from hive.operator.lab import (
        PARENT_PREFIX,
        _cal_id,
        load_cals,
        load_curves,
        read_status,
        read_tests,
    )

    tests = read_tests(ldir, with_curves=False)          # oldest first, metrics only
    today = datetime.now(UTC).date().isoformat()
    child_status = {c.get("id"): c.get("status") for c in hive.get("children", [])}
    cells = [t for t in tests if t.get("by") not in ("parent", "seat")]
    ran = [t for t in cells if t.get("oos_cagr") is not None and not str(t.get("verdict", "")).startswith("rejected: backtest failed")]
    leaders: dict[str, list] = {}
    equivalents: dict[str, int] = {}
    for sl in ("stocks", "crypto"):
        seen_sig: dict[tuple, str] = {}
        rows_: list = []
        for t in sorted((t for t in ran if t.get("sleeve") == sl), key=lambda t: -t["oos_cagr"]):
            sig = tuple(round(float(t.get(k) or 0.0), 6) for k in ("oos_cagr", "oos_dd", "is_cagr", "is_dd"))
            if sig in seen_sig:            # a different setting with identical results: an inert knob
                equivalents[seen_sig[sig]] = equivalents.get(seen_sig[sig], 1) + 1
                continue
            seen_sig[sig] = t["id"]
            rows_.append(t)
            if len(rows_) >= LAB_LEADERS:
                break
        leaders[sl] = rows_
    best = sorted((t for t in ran if t.get("passed")), key=lambda t: -t["oos_cagr"])[:LAB_CURVES_BEST]
    keep = ({t["id"] for t in tests[-LAB_CURVES_NEWEST:]} | {t["id"] for t in best}
            | {t["id"] for rows in leaders.values() for t in rows})
    curves = load_curves(ldir, [t for t in tests if t.get("id") in keep], expand=False)
    cals = load_cals(ldir, {w.get("cal") for cv in curves.values() for w in cv.values() if isinstance(w, dict) and w.get("cal")})
    for cv in curves.values():                    # records from before compact storage: compact them for the page
        for win, w in list(cv.items()):
            if isinstance(w, dict) and "dates" in w:
                cid = _cal_id(w.get("dates") or [], w.get("benchmark") or [None] * len(w.get("dates") or []))
                cals.setdefault(cid, {"dates": w.get("dates") or [], "benchmark": w.get("benchmark") or []})
                cv[win] = {"cal": cid, "equity": w.get("equity") or []}
    metric_keys = ("cagr", "max_drawdown", "sharpe", "n_trades", "win_rate", "profit_factor")
    def r5(v):
        return round(float(v), 5) if isinstance(v, (int, float)) and v is not None else v

    rows = []
    for t in tests:
        row = {k: t.get(k) for k in ("id", "ts", "by", "sleeve", "overrides", "passed", "child_id")}
        if not t.get("overrides"):
            row["name"] = t.get("name")
        row["verdict"] = (t.get("verdict") or "")[:110]
        for k in ("is_cagr", "oos_cagr", "is_dd", "oos_dd", "parent_oos_cagr"):
            row[k] = r5(t.get(k))
        row["oos"] = {k: r5((t.get("outofsample") or {}).get(k)) for k in metric_keys if k in ("sharpe", "n_trades")}
        row["child_status"] = child_status.get(t.get("child_id")) if t.get("child_id") else None
        rows.append(row)
    parents = {}
    for p in sorted(ldir.glob(f"{PARENT_PREFIX}*.json")) if ldir.exists() else []:
        d = _read_json(p)
        if d:
            parents[p.stem[len(PARENT_PREFIX):]] = {"ts": d.get("ts"), "data": d.get("data"), "insample": d.get("insample"),
                                                     "outofsample": d.get("outofsample"), "curves": d.get("curves") or {}}
    # progress: how many cells per hour, and the best out-of-sample growth found so far, per sleeve
    hours: dict[str, dict[str, Any]] = {}
    for t in cells:
        h = (t.get("ts") or "")[:13]
        if not h:
            continue
        b = hours.setdefault(h, {"stocks": 0, "crypto": 0})
        if t.get("sleeve") in b:
            b[t["sleeve"]] += 1
    best_so_far: dict[str, list] = {"stocks": [], "crypto": []}
    run_best: dict[str, float | None] = {"stocks": None, "crypto": None}
    for t in ran:
        sl = t.get("sleeve")
        if sl not in run_best or not t.get("passed"):
            continue
        if run_best[sl] is None or t["oos_cagr"] > run_best[sl]:
            run_best[sl] = t["oos_cagr"]
            best_so_far[sl].append([t.get("ts"), t["oos_cagr"], t.get("id")])
    queue, n_queued, grid_size, waiting, by_stage = [], 0, 0, [], {}
    try:
        from hive.config import REPO_ROOT
        from hive.hive.registry import HiveRegistry
        from hive.operator.iterate import load_bounds
        from hive.operator.sweep import describe, grid, queue_staged, waiting_list

        reg = HiveRegistry(state / "hive.json", cfg.survival)
        q = queue_staged(reg, cfg, load_bounds(REPO_ROOT / cfg.operator.bounds_file), ldir)
        n_queued, grid_size = len(q), len(grid())
        for _, _, st in q:
            by_stage[st] = by_stage.get(st, 0) + 1
        queue = [describe(sl, ov, stage=st) for sl, ov, st in q[:8]]
        waiting = [{"name": w["proposal"], "sleeve": w["sleeve"], "oos_cagr": w["oos_cagr"], "oos_dd": w["oos_dd"],
                    "date": w["date"], "oos_prop": w.get("oos_prop")} for w in waiting_list(reg, ldir, cfg.operator.objective)[:10]]
    except Exception:
        pass
    tl = [{"ts": (t.get("ts") or "")[:16].replace("T", " "), "who": "lab", "kind": t.get("by") or "test",
           "text": f"{t.get('name')}: " + (f"OOS {t['oos_cagr']:.1%}" if t.get("oos_cagr") is not None else "no result")
                   + (f" vs parent {t['parent_oos_cagr']:.1%}" if t.get("parent_oos_cagr") is not None else "")
                   + f" — {(t.get('verdict') or '')[:90]}"} for t in cells[-40:]]
    return {"tests": rows[::-1][:LAB_ROWS], "curves": curves, "cals": cals, "parents": parents,
            "leaders": {sl: [t["id"] for t in rows_] for sl, rows_ in leaders.items()}, "equivalents": equivalents,
            "knobs": _knobs(ran),
            "progress": {"hours": [[h, b["stocks"], b["crypto"]] for h, b in sorted(hours.items())][-96:],
                         "best": best_so_far},
            "queue": queue, "n_queued": n_queued, "by_stage": by_stage, "grid_size": grid_size, "waiting": waiting,
            "status": read_status(state), "n_tests": len(cells), "n_passed": sum(1 for t in cells if t.get("passed")),
            "n_today": sum(1 for t in cells if (t.get("ts") or "")[:10] == today),
            "last_ts": tests[-1].get("ts") if tests else None, "timeline": tl}


def _schedule() -> dict[str, Any]:
    """The firm's fixed calendar, as the next UTC times of each run (the page counts down to them)."""
    from datetime import timedelta

    now = datetime.now(UTC).replace(second=0, microsecond=0)

    def nxt(hour: int, minute: int, weekday: int | None = None) -> list[str]:
        out = []
        for d in range(0, 15):
            c = (now + timedelta(days=d)).replace(hour=hour, minute=minute)
            if c > now and (weekday is None or c.weekday() == weekday):
                out.append(c.isoformat())
            if len(out) >= 3:
                break
        return out

    return {"daily": nxt(0, 37), "daily_safety": nxt(3, 41), "weekly": nxt(14, 23, weekday=6)}


def _rules(cfg: StrategyConfig, settings: Settings) -> dict[str, Any]:
    """Every rule in force, read from the configuration and the code that applies it: what it is,
    its current value, and who can change it (the lab, by evidence; the owner, in config; nobody:
    code)."""
    from hive.agents import prompts
    from hive.config import REPO_ROOT
    from hive.operator.iterate import load_bounds

    bounds = load_bounds(REPO_ROOT / cfg.operator.bounds_file)

    def lever(key: str, value: Any, what: str) -> dict[str, Any]:
        b = bounds.get(key)
        rng = None if b is None else (", ".join(b) if isinstance(b, list) else f"{b[0]:g} to {b[1]:g}")
        return {"key": key, "value": value, "what": what, "who": "lab" if b is not None else "owner", "range": rng}

    r, sv, op = cfg.risk, cfg.survival, cfg.operator
    risk_rows = [
        lever("risk.sizing_mode", r.sizing_mode, "how risk per trade is set: fixed share, or edge (each book's own record)"),
        lever("risk.risk_per_trade_pct", r.risk_per_trade_pct, "equity risked per trade (entry to stop); edge books start here"),
        lever("risk.edge_fraction", r.edge_fraction, "edge sizing: share of the Kelly bet its closed trades imply"),
        lever("risk.edge_min_trades", r.edge_min_trades, "edge sizing: closed trades before the record decides"),
        lever("risk.max_risk_per_trade_pct", r.max_risk_per_trade_pct, "edge sizing: most a book may risk on one trade"),
        lever("risk.max_positions", r.max_positions, "open positions at once"),
        lever("risk.max_new_entries_per_day", r.max_new_entries_per_day, "new entries per day"),
        lever("risk.max_position_pct_stock", r.max_position_pct_stock, "largest single stock, share of equity (1.0 = no cap)"),
        lever("risk.max_position_pct_etf", r.max_position_pct_etf, "largest single ETF, share of equity (1.0 = no cap)"),
        lever("risk.max_position_pct_crypto", r.max_position_pct_crypto, "largest single coin, share of equity (1.0 = no cap)"),
        lever("risk.max_position_pct_crypto_alt", r.max_position_pct_crypto_alt, "largest DOGE/SHIB/PEPE position (1.0 = no cap)"),
        lever("risk.crypto_sleeve_pct", r.crypto_sleeve_pct, "share of equity crypto may use"),
        lever("risk.crypto_max_positions", r.crypto_max_positions, "coins held at once"),
        lever("risk.max_open_risk_pct", r.max_open_risk_pct, "total equity at risk across open stops"),
        lever("risk.target_annual_vol", r.target_annual_vol, "portfolio volatility target for sizing"),
        lever("risk.max_daily_loss_pct", r.max_daily_loss_pct, "no new buys after losing this much in a day"),
        lever("risk.max_drawdown_halt_pct", r.max_drawdown_halt_pct, "drawdown from peak that liquidates and pauses"),
        lever("risk.halt_cooldown_bars", r.halt_cooldown_bars, "days paused after a drawdown halt"),
        lever("risk.max_gross_exposure", r.max_gross_exposure, "total exposure, share of equity (1.0 = no margin)"),
        lever("risk.max_leverage", r.max_leverage, "leverage on stocks"),
        lever("risk.crypto_max_leverage", r.crypto_max_leverage, "leverage on crypto (only a perps venue could use it)"),
        lever("risk.min_order_notional_usd", r.min_order_notional_usd, "smallest order"),
    ]
    tiers = [{"name": t.name, "min_equity_usd": t.min_equity_usd, "seats": t.max_children, "risk_multiplier": t.risk_multiplier,
              "close_only": t.close_only, "ai_budget_pct_per_day": t.daily_ai_budget_pct} for t in sv.tiers]
    survival_rows = [
        {"key": "survival.death_floor_usd", "value": sv.death_floor_usd, "who": "owner",
         "what": "net equity at which the firm dies, permanently"},
        {"key": "survival.death_drawdown_pct", "value": sv.death_drawdown_pct, "who": "owner",
         "what": "drawdown from peak at which the firm dies, permanently"},
        {"key": "survival.daily_ai_budget", "value": f"{sv.daily_ai_budget_min_usd:g} to {sv.daily_ai_budget_max_usd:g} USD",
         "who": "owner", "what": "the day's AI budget, a share of equity set by the tier; unspent budget accrues to the weekly research pot"},
        {"key": "survival.tiers", "value": " · ".join(f"{t['name']} ≥${t['min_equity_usd']:g}: {t['seats']} seats, risk ×{t['risk_multiplier']:g}"
                                                       + (", close-only" if t["close_only"] else "") for t in tiers),
         "who": "owner", "what": "what equity unlocks: sub-agent seats, risk multiplier, research budget"},
    ]
    search_rows = [
        {"key": "verdict", "value": f"OOS CAGR > parent · IS CAGR ≥ 0 · OOS drawdown no worse than parent × {op.max_drawdown_tolerance:g}",
         "who": "owner", "what": "what a backtested cell must show to take a paper seat"},
        {"key": "survival.child_shadow_days", "value": sv.child_shadow_days, "who": "owner",
         "what": "paper days a sub-agent must out-grow the parent before promotion"},
        {"key": "operator.promote_min_voi", "value": op.promote_min_voi, "who": "owner",
         "what": "lead over the parent needed at promotion (0 = any lead)"},
        {"key": "survival.child_retire_days / child_retire_voi", "value": f"{sv.child_retire_days} days / {sv.child_retire_voi:g}",
         "who": "owner", "what": "a variant behind the parent by at least this after this many days is retired"},
        {"key": "survival.child_max_drawdown_pct", "value": sv.child_max_drawdown_pct, "who": "owner",
         "what": "a sub-agent (or scout) whose own paper book falls this far dies the same day"},
        {"key": "operator.demote_after_days / demote_min_lag", "value": f"{op.demote_after_days} days / {op.demote_min_lag:g}",
         "who": "owner", "what": "the baseline reverts a promotion if it out-grows it by this much over this long"},
        {"key": "operator.monitor_ttl_days", "value": op.monitor_ttl_days, "who": "owner", "what": "a news monitor expires after this many days"},
        {"key": "bounds", "value": f"{len(bounds)} levers in config/bounds.toml", "who": "owner",
         "what": "the only settings the lab and the operator may change; anything else is rejected in code"},
    ]
    code_rows = [
        "every buy carries a protective stop below its price, or it is not placed",
        "orders are capped at available cash: no margin, no borrowing, no shorting",
        "the language models never place orders, choose sizes or prices; they may only approve, halve or veto a buy the rules found",
        "a model can never block an exit",
        "untrusted text (news, social posts, token names) is read by a quarantined reader that can only return numbers and labels",
        "a bar is traded at most once (a repeat run the same day places no new buys and does not age a sub-agent)",
        "stock signals never use a session that has not closed; crypto never uses the forming UTC day",
        "a price series with an implausible print is cut at that print before any signal reads it",
        "the run refuses to size on a broker balance more than twice the recorded bankroll (a paper account left at $100,000)",
        "a file named state/KILL stops all order placement at once",
        "AI spend past the day's budget is refused, not borrowed",
        "every ledger and journal entry is hash-chained; a broken chain shows on the dashboard",
        "crypto stops are stop-limit orders (Alpaca rejects plain crypto stops)",
    ]
    venue_rows = [
        {"key": "HIVE_MODE", "value": settings.hive_mode, "who": "owner",
         "what": "paper or live; live also needs HIVE_LIVE_CONFIRM set to the confirmation phrase"},
        {"key": "Alpaca", "value": "paper account" if not settings.is_live else "LIVE", "who": "owner",
         "what": "stocks, ETFs, BTC/ETH/SOL and DOGE/SHIB/PEPE"},
        {"key": "Hyperliquid", "value": "adapter built, not connected to the trading loop", "who": "owner",
         "what": "needs keys, HL_OPERATOR_NOT_RESTRICTED_PERSON=true and the routing change (refused by this session's permission gate); its terms exclude US persons"},
        {"key": "DEX memecoins (Jupiter)", "value": "refuses to construct", "who": "owner",
         "what": "live swaps need a dedicated wallet, an RPC, pre-trade rug checks and simulated sells; the meme and trench scouts are paper only"},
    ]
    return {"objective": prompts.OBJECTIVE, "risk": risk_rows, "survival": survival_rows, "search": search_rows,
            "code": code_rows, "venues": venue_rows}


def _ts_of(s: Any) -> str | None:
    """'2026-10-19 21:49' / '2026-10-19T21:49:00+00:00' / '2026-10-19' -> ISO UTC."""
    if not s:
        return None
    t = str(s).strip().replace(" ", "T")
    if len(t) == 10:
        t += "T00:00"
    if not (t.endswith("Z") or "+" in t[10:]):
        t += "+00:00"
    try:
        return datetime.fromisoformat(t).isoformat()
    except ValueError:
        return None


def _agents(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every employee of the firm, with an honest state read from the record: working (did something
    in the last twelve minutes, or the lab's round is running), recent (in the last 26 hours), idle,
    or retired. Nothing here is animated unless the record says it moved."""
    now = datetime.now(UTC)

    def state_of(ts: str | None, *, running: bool = False) -> str:
        if running:
            return "working"
        if not ts:
            return "idle"
        age = (now - datetime.fromisoformat(ts)).total_seconds()
        return "working" if age <= 12 * 60 else "recent" if age <= 26 * 3600 else "idle"

    tl = data.get("timeline") or []
    last_run = next((e for e in reversed(tl) if e.get("who") == "primary" and e.get("kind") == "run"), None)
    run_ts = _ts_of(last_run["ts"]) if last_run else None
    sched = data.get("schedule") or {}
    lab = data.get("lab") or {}
    st = lab.get("status") or {}
    running = st.get("state") == "running" and st.get("started") and \
        (now - datetime.fromisoformat(st["started"])).total_seconds() <= ((st.get("minutes") or 10) + 5) * 60
    last_test = (lab.get("tests") or [{}])[0] if lab.get("tests") else {}
    hb = data.get("heartbeat") or {}
    desks = data.get("desks") or {}
    hive = data.get("hive") or {}
    cd = data.get("children_detail") or {}
    rl = [r for r in (data.get("research_log") or []) if not str(r.get("note", "")).startswith(("lab:", "systematic sweep"))]
    review = data.get("review") or {}
    alloc = (data.get("allocation") or {}).get("history") or []
    notes = desks.get("notes") or []
    out: list[dict[str, Any]] = []

    def add(aid, name, role, group, ts, doing, nxt=None, cadence="", running_=False, status=None):
        out.append({"id": aid, "name": name, "role": role, "group": group, "last_ts": ts,
                    "state": status or state_of(ts, running=running_), "doing": doing, "next": nxt, "cadence": cadence})

    add("operator", "Operator · CEO", "governs by evidence: seats winners, promotes out-growers, retires laggards",
        "core", _ts_of(lab.get("last_ts")) or run_ts,
        f"{lab.get('n_tests', 0)} backtests on record, {len([c for c in hive.get('children', []) if c.get('status') in ('shadow', 'live')])} sub-agents active",
        cadence="every lab round and every daily cycle", running_=bool(running))
    add("lab", "Strategy lab", "backtests the strategy space: grid, refinements, mutations; no LLM", "research",
        _ts_of(st.get("finished") or lab.get("last_ts")),
        (f"round {st.get('round')} of {st.get('rounds')}: " if st.get("round") else "")
        + (f"last: {last_test.get('name')} → {(last_test.get('verdict') or '')[:60]}" if last_test else "no test yet"),
        nxt="next round" if running or (st.get("round") or 0) < (st.get("rounds") or 0) else "next session (chained)",
        cadence="around the clock, six rounds a session", running_=bool(running))
    ch = {c.get("id"): c for c in hive.get("children", [])}
    tr = next((c for c in hive.get("children", []) if c.get("sleeve") == "trench"), None)
    if tr:
        latest = (cd.get(tr["id"]) or {}).get("latest") or {}
        add(tr["id"], "Trench scout", "paper-trades brand-new Solana launches", "scout", _ts_of(latest.get("ts")),
            f"scanned {latest.get('scanned', 0)} launches, {latest.get('passed', 0)} passed, {latest.get('n_open', 0)} open, paper ${latest.get('equity') or 0:,.2f}"
            if latest else "first scan at the next lab round",
            cadence="every lab round", status="retired" if tr.get("status") not in ("shadow", "live") else None)
    meme = next((c for c in hive.get("children", []) if c.get("sleeve") == "meme"), None)
    if meme:
        sc = desks.get("scout") or {}
        add(meme["id"], "Meme scout", "paper-trades established memecoins after a retrace", "scout",
            _ts_of((cd.get(meme["id"]) or {}).get("last_ts")),
            f"scanned {sc.get('scanned', '—')}, {sc.get('n_candidates', 0)} passed, {sc.get('n_open', 0)} open" if sc else "no scan yet",
            nxt=(sched.get("daily") or [None])[0], cadence="daily",
            status="retired" if meme.get("status") not in ("shadow", "live") else None)
    soc = data.get("social") or {}
    add("social", "Social pulse", "what StockTwits, Reddit, DexScreener (and X, with a paid token) talk about", "desk",
        _ts_of(soc.get("ts")), (f"{len(soc.get('stocktwits') or [])} trending on StockTwits, {soc.get('reddit_posts_read', 0)} Reddit posts read, "
                                f"{len(soc.get('cross') or [])} names on two or more sources") if soc else "first pulse at the next lab round",
        cadence="every lab round")
    pd_ = next((c for c in hive.get("children", []) if c.get("sleeve") == "perps"), None)
    if pd_:
        lp = (cd.get(pd_["id"]) or {}).get("latest") or {}
        add(pd_["id"], "Hyperliquid desk", "the firm's crypto rules on Hyperliquid perps, public prices, paper", "scout",
            _ts_of(lp.get("ts")), (f"{lp.get('n_tradable', 0)} of {lp.get('n_listed', 0)} perps tradable, {len(lp.get('positions') or [])} open, "
                                   f"paper ${lp.get('equity') or 0:,.2f}, funding paid ${lp.get('funding_paid_total') or 0:,.4f}") if lp else "first cycle tonight",
            nxt=(sched.get("daily") or [None])[0], cadence="daily trading cycle",
            status="retired" if pd_.get("status") not in ("shadow", "live") else None)
    add("heartbeat", "Heartbeat", "reads the broker's live positions and prices", "infra", _ts_of(hb.get("ts")),
        f"equity ${hb.get('equity') or 0:,.2f}, {len(hb.get('positions') or [])} positions, market {'open' if hb.get('market_open') else 'closed'}"
        if hb else "no heartbeat yet", cadence="every lab round")
    desk_doing = f"{len(notes)} candidates reviewed" if notes else "no candidate reached the desks"
    for aid, name, role in (("news", "News desk", "quarantined reader: headlines, StockTwits, Reddit"),
                            ("numbers", "Numbers desk", "fundamental and technical analysts"),
                            ("sentiment", "Sentiment desk", "crowding and positioning"),
                            ("debate", "Bull vs bear", "argues both sides of each candidate"),
                            ("pm", "Portfolio manager", "approves, halves or vetoes what the rules found")):
        add(aid, name, role, "desk", run_ts, desks.get("portfolio_note") if aid == "pm" and desks.get("portfolio_note") else desk_doing,
            nxt=(sched.get("daily") or [None])[0], cadence="daily trading cycle")
    watch = desks.get("watch") or []
    add("watch", "News watch", "re-reads the news on every open position", "desk", run_ts,
        f"{len(watch)} checks on record" if watch else "nothing held to watch", nxt=(sched.get("daily") or [None])[0],
        cadence="daily trading cycle")
    add("research", "Operator research (LLM)", "proposes variants and news monitors from the record", "research",
        _ts_of(rl[-1].get("date")) if rl else None, (rl[-1].get("note") or "")[:120] if rl else "no proposal yet",
        nxt=(sched.get("weekly") or [None])[0], cadence="weekly")
    add("auditor", "Auditor", "reads the week's journals, reports findings", "research", _ts_of(review.get("date")),
        f"{len(review.get('findings') or [])} findings" if review else "no review yet",
        nxt=(sched.get("weekly") or [None])[0], cadence="weekly")
    add("allocator", "Allocator", "moves capital between stocks and crypto from realised results", "research",
        _ts_of(alloc[-1].get("date")) if alloc else None, (alloc[-1].get("why") or "")[:100] if alloc else "no rebalance yet",
        nxt=(sched.get("weekly") or [None])[0], cadence="weekly")
    for c in hive.get("children", []):
        if c.get("sleeve") in ("trench", "meme", "perps"):
            continue
        d = cd.get(c.get("id")) or {}
        active = c.get("status") in ("shadow", "live")
        role = {"variant": "paper-trades a variant beside the parent", "monitor": "watches topics, files daily reports",
                "baseline": "runs the original rules, can revert a promotion"}.get(c.get("kind"), c.get("kind"))
        voi = c.get("voi")
        add(c.get("id"), c.get("name"), role, "sub-agent", _ts_of(d.get("last_ts")),
            (f"equity ${c.get('equity') or 0:,.2f} · VOI {voi:+.1%}" if voi is not None else f"equity ${c.get('equity') or 0:,.2f}")
            + f" · {c.get('shadow_days') or 0} d" if c.get("kind") != "monitor" else f"watching {', '.join(c.get('watch') or [])}",
            nxt=(sched.get("daily") or [None])[0] if active else None, cadence="daily trading cycle",
            status=None if active else c.get("status"))
    _ = ch
    return out


def _knobs(ran: list[dict[str, Any]], *, per_sleeve: int = 8, min_tests: int = 6) -> dict[str, list[dict[str, Any]]]:
    """What the lab is learning: for each setting it has varied, every tested value against the
    out-of-sample result it produced, ranked by how much the result moves with the setting
    (spread between the best and worst average across up to five value buckets)."""
    import statistics

    out: dict[str, list[dict[str, Any]]] = {}
    for sl in ("stocks", "crypto"):
        pts: dict[str, list] = {}
        for t in ran:
            if t.get("sleeve") != sl:
                continue
            for k, v in (t.get("overrides") or {}).items():
                pts.setdefault(k, []).append([round(v, 4) if isinstance(v, float) else v, round(t["oos_cagr"], 4),
                                              bool(t.get("passed"))])
        rows = []
        for k, p in pts.items():
            if len(p) < min_tests:
                continue
            categorical = any(isinstance(x[0], str) for x in p)
            if categorical:
                groups: dict[str, list[float]] = {}
                for v, y, _ in p:
                    groups.setdefault(str(v), []).append(y)
                means = [statistics.fmean(g) for g in groups.values()]
            else:
                q = sorted(p, key=lambda x: float(x[0]))
                n = len(q)
                buckets = [q[i * n // 5:(i + 1) * n // 5] for i in range(5)]
                means = [statistics.fmean(y for _, y, _ in b) for b in buckets if b]
            if len(p) > 160:      # sample across the values tested, not just the latest ones
                if categorical:
                    per: dict[str, list] = {}
                    for x in p:
                        per.setdefault(str(x[0]), []).append(x)
                    p = [x for g in per.values() for x in g[-max(8, 160 // len(per)):]]
                else:
                    q = sorted(p, key=lambda x: float(x[0]))
                    p = [q[round(i * (len(q) - 1) / 159)] for i in range(160)]
            rows.append({"knob": k, "categorical": categorical, "n": len(pts[k]), "spread": max(means) - min(means),
                         "points": p})
        out[sl] = sorted(rows, key=lambda r: -r["spread"])[:per_sleeve]
    return out


def _scouts(state: Path, hive: dict[str, Any]) -> dict[str, Any]:
    """Each scout's paper equity over time, its recent trades, and (trench) the last scan's funnel."""
    jdir = state / "journal"
    out: dict[str, Any] = {}
    for c in hive.get("children", []):
        if c.get("kind") != "scout":
            continue
        cid = str(c.get("id"))
        files = sorted(jdir.glob(f"*-child-{cid}.jsonl"))[-14:] if jdir.exists() else []
        equity, trades = [], []
        for f in files:
            for e in Journal.read(f):
                k, pl, ts = e["kind"], e["payload"], e.get("ts", "")
                if k in ("trench_round", "meme_day", "perps_day") and pl.get("equity") is not None:
                    equity.append([ts[:16].replace("T", " "), round(float(pl["equity"]), 4)])
                elif k in ("meme_entry", "meme_exit", "meme_take_half"):
                    trades.append({"ts": ts, "kind": k.replace("meme_", ""), "symbol": pl.get("symbol"), "price": pl.get("price"),
                                   "usd": pl.get("notional") if k == "meme_entry" else pl.get("proceeds"),
                                   "pnl": pl.get("pnl_approx"), "reason": pl.get("reason"), "age_hours": pl.get("age_hours")})
                elif k == "order" and c.get("sleeve") == "perps":
                    trades.append({"ts": ts, "kind": pl.get("side") or "order", "symbol": pl.get("symbol"),
                                   "usd": pl.get("notional"), "reason": pl.get("message")})
                elif k == "exit" and c.get("sleeve") == "perps":
                    trades.append({"ts": ts, "kind": "exit", "symbol": pl.get("symbol"), "reason": pl.get("reason")})
        latest = _read_json(state / "children" / cid / "latest.json") or {}
        rej = latest.get("rejects") or {}
        funnel = None
        if c.get("sleeve") == "trench" and latest:
            passed_filters = int(latest.get("passed") or 0) + int(rej.get("rugcheck") or 0) + int(rej.get("not rug-checked this scan") or 0)
            funnel = [["launches quoted", int(latest.get("scanned") or 0)], ["passed the launch filters", passed_filters],
                      ["rug-checked", int(latest.get("rugchecked") or 0)], ["passed RugCheck", int(latest.get("passed") or 0)],
                      ["bought this scan", len(latest.get("entries") or [])]]
        out[cid] = {"sleeve": c.get("sleeve"), "name": c.get("name"), "status": c.get("status"),
                    "equity": equity[-400:], "trades": trades[-40:][::-1], "funnel": funnel}
    return out
