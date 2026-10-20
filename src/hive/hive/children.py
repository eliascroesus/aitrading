"""Run every active child as its own paper portfolio inside the daily cycle."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from hive.backtest.costs import ALPACA_CRYPTO, ALPACA_STOCKS
from hive.backtest.experiments import _apply_overrides
from hive.config import Settings, StrategyConfig
from hive.data.store import BarStore
from hive.data.universe import PointInTimeUniverse
from hive.execution.paper import PaperBroker
from hive.hive.registry import Child, HiveRegistry
from hive.orchestrator.book import PositionBook
from hive.orchestrator.journal import Journal
from hive.orchestrator.run import DailyRun, SystemDeadError
from hive.survival.ledger import SurvivalLedger

log = logging.getLogger(__name__)


def _classes_for_sleeve(classes: dict[str, str], sleeve: str) -> dict[str, str]:
    if sleeve == "stocks":
        return {s: c for s, c in classes.items() if c in ("stock", "etf")}
    if sleeve == "crypto":
        return {s: c for s, c in classes.items() if c == "crypto"}
    return dict(classes)


def run_children(
    settings: Settings, cfg: StrategyConfig, run_date: date, *, store: BarStore, classes: dict[str, str],
    universe: PointInTimeUniverse | None, kill: bool, parent_equity: float, price_fn_factory,
    llm=None, news_fn=None,
) -> list[dict[str, Any]]:
    from hive.hive.monitors import run_monitor_child

    registry = HiveRegistry(settings.hive_state_dir / "hive.json", cfg.survival)
    results = []
    rd = run_date.isoformat()
    is_replay = run_date < datetime.now(UTC).date() - timedelta(days=1)
    # the Hyperliquid paper desk joins the first time the cycle runs live on the network
    if (settings.hive_network_data and settings.hive_mode != "backtest" and not is_replay
            and not any(c.sleeve == "perps" for c in registry.children)):
        from hive.hive.perps_desk import DESK_ID

        led = SurvivalLedger(settings.hive_state_dir / "ledger.jsonl", cfg.survival)
        registry.spawn(Child(id=DESK_ID, name="Hyperliquid desk", kind="scout", sleeve="perps",
                             hypothesis="the firm's crypto rules on Hyperliquid's most traded perps; public prices, "
                                        "1x, taker fee and funding charged; paper only, own $100 book"),
                       led.tier(led.status(parent_equity, on=run_date).equity_net), force=True)
    for child in registry.active():
        cdir = settings.hive_state_dir / "children" / child.id
        journal = Journal(settings.hive_state_dir / "journal", f"{rd}-child-{child.id}")
        try:
            if child.kind == "monitor":
                reports = run_monitor_child(settings, child, cdir, journal, llm, run_date, classes, news_fn)
                results.append({"id": child.id, "name": child.name, "kind": "monitor", "sleeve": child.sleeve,
                                "reports": len(reports), "equity": None})
                continue
            if child.sleeve == "meme":
                eq = _run_meme_child(settings, cfg, child, cdir, journal, parent_equity, run_date)
            elif child.sleeve == "perps":
                from hive.hive.perps_desk import run_perps_desk

                if is_replay:
                    journal.write("perps_skip", {"reason": "replay: no historical public prices"})
                    continue
                eq = run_perps_desk(settings, cfg, child, cdir, journal, run_date, store, kill)
            elif child.sleeve == "trench":
                latest = cdir / "latest.json"
                eq = float(json.loads(latest.read_text()).get("equity")) if latest.exists() else cfg.trench.starting_cash_usd
            else:
                # the baseline runs the pre-registered rules, never the promoted ones
                ccfg = StrategyConfig.load() if child.kind == "baseline" else _apply_overrides(cfg, child.overrides)
                ccls = _classes_for_sleeve(classes, child.sleeve)
                broker = PaperBroker(cdir / "paper.json", price_fn_factory(ccls, pd.Timestamp(run_date)),
                                     starting_cash=cfg.survival.starting_bankroll_usd,
                                     cost=ALPACA_CRYPTO if child.sleeve == "crypto" else ALPACA_STOCKS)
                summary = DailyRun(
                    label=f"child-{child.id}", run_date=run_date, cfg=ccfg, store=store, broker=broker,
                    book=PositionBook(cdir / "book.json"), journal=journal,
                    ledger=SurvivalLedger(cdir / "ledger.jsonl", cfg.survival), llm=None, universe=universe,
                    asset_classes=ccls, kill_switch=kill,
                ).execute()
                eq = summary.equity_gross
            registry.mark(child.id, eq, parent_equity, on=run_date)
            results.append({"id": child.id, "name": child.name, "sleeve": child.sleeve, "equity": eq,
                            "voi": registry.get(child.id).voi})
        except SystemDeadError as exc:
            registry.retire(child.id, f"dead: {exc}", on=run_date)
            results.append({"id": child.id, "name": child.name, "sleeve": child.sleeve, "equity": None, "dead": True})
        except Exception as exc:  # a child must never take the primary down
            log.exception("child %s failed", child.id)
            journal.write("child_error", {"error": str(exc)[:300]})
    registry.auto_retire_losers(min_days=cfg.survival.child_retire_days, max_negative_voi=cfg.survival.child_retire_voi,
                                max_drawdown=cfg.survival.child_max_drawdown_pct, on=run_date)
    for rec in retire_prop_failures(settings, cfg, registry, run_date):
        results.append(rec)
    registry.retire_expired_monitors(cfg.operator.monitor_ttl_days, on=run_date)
    # governance by evidence: promote what earned it, revert what the baseline beats
    from hive.hive.governance import demote_if_baseline_wins, promote_earned

    ledger = SurvivalLedger(settings.hive_state_dir / "ledger.jsonl", cfg.survival)
    tier = ledger.tier(ledger.status(parent_equity, on=run_date).equity_net)
    try:
        for rec in promote_earned(settings, cfg, registry, tier, run_date):
            results.append({"id": "operator", "name": "operator", "kind": "promotion", "verdict": rec["verdict"]})
        rec = demote_if_baseline_wins(settings, cfg, registry, run_date)
        if rec:
            results.append({"id": "operator", "name": "operator", "kind": "demotion", "verdict": rec["verdict"]})
    except Exception as exc:  # governance must never take the cycle down
        log.exception("governance failed: %s", exc)
    return results


def _run_meme_child(settings: Settings, cfg: StrategyConfig, child, cdir: Path, journal: Journal,
                    parent_equity: float, run_date: date) -> float:
    from hive.meme.scanner import current_prices, scan
    from hive.meme.scout import MemeBook, run_scout_day

    rules = cfg.meme.model_copy(update={"enabled": True})
    sleeve_equity = parent_equity * rules.total_pct / rules.total_pct  # scout books its own cash
    book = MemeBook.load(cdir / "meme_book.json", sleeve_equity * rules.total_pct)
    is_replay = run_date < datetime.now(UTC).date() - timedelta(days=1)
    if is_replay or not settings.hive_network_data:
        journal.write("meme_skip", {"reason": "replay or network data disabled: no live DEX prices"})
        return book.equity({})
    cands = scan(rules)
    prices = current_prices(rules.chain, list(book.positions))
    out = run_scout_day(book=book, rules=rules, candidates=cands, prices=prices, sleeve_equity=sleeve_equity,
                        journal=journal.write)
    book.save(cdir / "meme_book.json")
    journal.write("meme_day", {"n_candidates": sum(1 for c in cands if c.get("passes")), "scanned": len(cands), **out})
    return out["equity"]


def retire_prop_failures(settings: Settings, cfg: StrategyConfig, registry: HiveRegistry, on: date) -> list[dict[str, Any]]:
    """The prop firm's punishment: a variant whose own paper record breached the daily or total
    loss limit failed its evaluation, and is retired the same day, as a firm would close the
    account. Scouts and the baseline trade other things and are judged on their own books."""
    from hive.hive.propfirm import forward_status

    out: list[dict[str, Any]] = []
    if not cfg.prop.retire_on_breach:
        return out
    for c in list(registry.active()):
        if c.kind != "variant":
            continue
        lp = settings.hive_state_dir / "children" / c.id / "ledger.jsonl"
        if not lp.exists():
            continue
        try:
            marks = SurvivalLedger(lp, cfg.survival).equity_marks()
        except ValueError:
            continue
        st = forward_status(marks, cfg.prop)
        if st["breached"]:
            why = (f"failed its prop evaluation: {st['outcome'].replace('failed_', '')} limit breached after "
                   f"{st['breach_day']} days (worst day {st['worst_day']:+.1%}, deepest loss {st['max_loss']:+.1%})")
            registry.retire(c.id, why, on)
            registry.record_iteration({"date": on.isoformat(), "proposal": c.name, "kind": "retired",
                                       "overrides": c.overrides, "sleeve": c.sleeve, "cost_usd": 0.0, "verdict": why})
            out.append({"id": c.id, "name": c.name, "kind": "retired", "verdict": why})
    return out
