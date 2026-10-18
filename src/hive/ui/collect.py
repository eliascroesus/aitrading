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
    for p in files[-30:]:
        ok = Journal.verify_file(p)
        if not ok:
            broken.append(p.name)
        for e in Journal.read(p):
            k, pl = e["kind"], e["payload"]
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

    # --- hive registry (children / iterations) -----------------------------------------------
    hive = _read_json(state / "hive.json") or {"children": [], "iterations": []}
    data["hive"] = hive

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
    data["config"] = cfg.model_dump()
    return data
