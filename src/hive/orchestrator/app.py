"""Wire settings, data, brokers, ledger and the two runs (primary + shadow)."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from hive.agents.llm import LLMClient
from hive.backtest.costs import ALPACA_STOCKS
from hive.config import Settings, StrategyConfig
from hive.data.bars import as_of
from hive.data.seed import load_pit_universe
from hive.data.store import BarStore
from hive.execution.base import Broker
from hive.execution.paper import PaperBroker
from hive.orchestrator.book import PositionBook
from hive.orchestrator.journal import Journal
from hive.orchestrator.report import write_daily_report
from hive.orchestrator.run import DailyRun, RunSummary
from hive.survival.ledger import SurvivalLedger

log = logging.getLogger(__name__)


def latest_close_fn(store: BarStore, classes: dict[str, str], run_date: pd.Timestamp):
    def fn(symbol: str) -> float | None:
        b = store.get(classes.get(symbol, "stock"), symbol)
        if b is None:
            return None
        b = as_of(b, run_date)
        return float(b["close"].iloc[-1]) if len(b) else None
    return fn


def build_primary_broker(settings: Settings, store: BarStore, classes: dict[str, str],
                         run_date: pd.Timestamp, starting_cash: float) -> Broker:
    if settings.hive_mode in ("backtest", "paper") and settings.alpaca_api_key is None:
        return PaperBroker(settings.hive_state_dir / "paper_primary.json",
                           latest_close_fn(store, classes, run_date), starting_cash=starting_cash,
                           cost=ALPACA_STOCKS)
    from hive.execution.alpaca import AlpacaBroker

    if settings.is_live:
        settings.assert_live_allowed()
    return AlpacaBroker(settings.alpaca_api_key.get_secret_value(),
                        settings.alpaca_secret_key.get_secret_value(),
                        paper=not settings.is_live)


def asset_classes_from_store(store: BarStore, cfg: StrategyConfig) -> dict[str, str]:
    classes: dict[str, str] = {}
    for sym in store.symbols("stock"):
        classes[sym] = "stock"
    for sym in store.symbols("etf"):
        classes[sym] = "etf"
    if cfg.risk.crypto_sleeve_pct > 0:
        for sym in store.symbols("crypto"):
            classes[sym] = "crypto"
    return classes


def run_day(settings: Settings, cfg: StrategyConfig, run_date: date, *, with_shadow: bool = True,
            llm_enabled: bool | None = None) -> dict[str, Path]:
    ts = pd.Timestamp(run_date)
    rd = ts.date().isoformat()
    store = BarStore(settings.hive_data_dir / "cache")
    classes = asset_classes_from_store(store, cfg)
    pit = settings.hive_data_dir / "raw" / "sp500_pit_membership.csv"
    universe = load_pit_universe(pit) if pit.exists() else None
    ledger = SurvivalLedger(settings.hive_state_dir / "ledger.jsonl", cfg.survival)
    kill = settings.kill_switch_engaged()

    use_llm = settings.hive_llm_enabled if llm_enabled is None else llm_enabled
    llm = None
    if use_llm and settings.anthropic_api_key is not None:
        calls_journal = Journal(settings.hive_state_dir / "journal", rd)
        llm = LLMClient(api_key=settings.anthropic_api_key.get_secret_value(), budget_usd=0.0,
                        on_call=lambda c: calls_journal.write("llm_call", c.__dict__))

    primary_broker = build_primary_broker(settings, store, classes, ts, cfg.survival.starting_bankroll_usd)
    primary = DailyRun(
        label="primary", run_date=run_date, cfg=cfg, store=store, broker=primary_broker,
        book=PositionBook(settings.hive_state_dir / "book_primary.json"),
        journal=Journal(settings.hive_state_dir / "journal", rd), ledger=ledger, llm=llm,
        universe=universe, asset_classes=classes, kill_switch=kill,
        cheap_model=settings.hive_llm_cheap_model, decision_model=settings.hive_llm_model,
        effort=settings.hive_llm_effort,
    ).execute()

    shadow: RunSummary | None = None
    if with_shadow:
        shadow_broker = PaperBroker(settings.hive_state_dir / "paper_shadow.json",
                                    latest_close_fn(store, classes, ts),
                                    starting_cash=cfg.survival.starting_bankroll_usd, cost=ALPACA_STOCKS)
        shadow = DailyRun(
            label="shadow-rules", run_date=run_date, cfg=cfg, store=store, broker=shadow_broker,
            book=PositionBook(settings.hive_state_dir / "book_shadow.json"),
            journal=Journal(settings.hive_state_dir / "journal", f"{rd}-shadow"), ledger=None, llm=None,
            universe=universe, asset_classes=classes, kill_switch=kill,
        ).execute()

    benchmarks: dict[str, float] = {}
    start_mark = ledger.equity_marks()[0] if ledger.equity_marks() else None
    for ac, sym in (("etf", cfg.universe.regime_stock_symbol), ("crypto", cfg.universe.regime_crypto_symbol)):
        b = store.get(ac, sym)
        if b is None or start_mark is None:
            continue
        b = as_of(b, ts)
        b0 = as_of(b, pd.Timestamp(start_mark[0][:10]))
        if len(b) and len(b0):
            benchmarks[f"{sym} buy&hold"] = cfg.survival.starting_bankroll_usd * float(
                b["adj_close"].iloc[-1] / b0["adj_close"].iloc[-1])
    survival = ledger.status(primary.equity_gross, on=run_date).__dict__
    return write_daily_report(settings.hive_reports_dir / "daily", primary, shadow, benchmarks, survival)
