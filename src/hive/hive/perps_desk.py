"""The Hyperliquid paper desk: the firm's crypto rules on Hyperliquid's most traded perps.

Read-only: it fetches public prices (no account, no keys) into its own bar store, runs the
same daily cycle the primary runs (signals, regime, risk engine, stops) on a paper book with
Hyperliquid's taker fee, and charges the hourly funding a long would have paid. It cannot
place an order anywhere. It is a scout: it takes no seat and is judged on its own book.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from hive.backtest.costs import CostModel
from hive.backtest.experiments import _apply_overrides
from hive.config import Settings, StrategyConfig
from hive.data.store import BarStore

log = logging.getLogger(__name__)

DESK_ID = "hyperliquid-desk"


def desk_store(settings: Settings) -> BarStore:
    return BarStore(settings.hive_data_dir / "cache_hl")


def run_perps_desk(settings: Settings, cfg: StrategyConfig, child, cdir: Path, journal, run_date: date,
                   main_store: BarStore, kill: bool, *, data: Any = None) -> float:
    from hive.data.sources import hyperliquid_public
    from hive.execution.paper import PaperBroker
    from hive.orchestrator.app import latest_close_fn
    from hive.orchestrator.book import PositionBook
    from hive.orchestrator.run import DailyRun
    from hive.survival.ledger import SurvivalLedger

    data = data or hyperliquid_public
    rules = cfg.perps
    store = desk_store(settings)
    uni = data.universe(rules.top_n)
    loaded: list[str] = []
    for row in uni:
        try:
            bars = data.candles(row["coin"])
        except Exception as exc:  # one coin's failure never stops the desk
            journal.write("perps_skip", {"coin": row["coin"], "error": str(exc)[:160]})
            continue
        if len(bars) < 30:
            continue
        store.put("crypto", row["coin"], bars, source="hyperliquid-public")
        if len(bars) >= rules.min_history_days:
            loaded.append(row["coin"])
    spy = main_store.get("etf", cfg.universe.regime_stock_symbol)
    if spy is not None:   # the daily cycle reads the stock regime too; the desk trades crypto only
        store.put("etf", cfg.universe.regime_stock_symbol, spy, source="copy of the main store")
    classes = {c: "crypto" for c in loaded}
    cost = CostModel(commission_pct=rules.taker_fee_pct, slippage_bps=rules.slippage_bps, name="hyperliquid_taker_paper")
    broker = PaperBroker(cdir / "paper.json", latest_close_fn(store, classes, pd.Timestamp(run_date)),
                         starting_cash=rules.starting_cash_usd, cost=cost)
    summary = None
    if classes and cfg.universe.regime_crypto_symbol in classes and spy is not None:
        dcfg = _apply_overrides(cfg, {"risk.crypto_sleeve_pct": 1.0, "risk.crypto_max_positions": rules.max_positions,
                                      "risk.max_positions": rules.max_positions})
        summary = DailyRun(label=f"child-{child.id}", run_date=run_date, cfg=dcfg, store=store, broker=broker,
                           book=PositionBook(cdir / "book.json"), journal=journal,
                           ledger=SurvivalLedger(cdir / "ledger.jsonl", cfg.survival), llm=None, universe=None,
                           asset_classes=classes, kill_switch=kill).execute()
    else:
        journal.write("perps_skip", {"reason": "no regime coin or no stock benchmark in the desk's store",
                                     "loaded": len(loaded)})
    # funding: what a long would have paid since the last charge (positive rate: longs pay)
    fpath = cdir / "funding.json"
    fstate = json.loads(fpath.read_text()) if fpath.exists() else {"last_ms": None, "paid_total": 0.0}
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    since = int(fstate.get("last_ms") or now_ms - 86_400_000)
    paid_today = charge_funding(broker, data, since, journal.write)
    fstate = {"last_ms": now_ms, "paid_total": float(fstate.get("paid_total") or 0.0) + paid_today}
    cdir.mkdir(parents=True, exist_ok=True)
    fpath.write_text(json.dumps(fstate))
    eq = float(broker.equity())
    latest = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "run_date": run_date.isoformat(),
              "universe": uni[:12], "n_listed": len(uni), "n_tradable": len(loaded), "equity": eq, "cash": float(broker.cash()),
              "positions": [{"symbol": s, "qty": p.qty, "avg": p.avg_price, "last": p.current_price, "value": p.market_value}
                            for s, p in broker.positions().items()],
              "funding_paid_today": paid_today, "funding_paid_total": fstate["paid_total"],
              "regime": (summary.regime if summary else {}), "notes": (summary.notes if summary else []),
              "decisions": len(summary.decisions) if summary else 0, "orders": len(summary.orders) if summary else 0}
    (cdir / "latest.json").write_text(json.dumps(latest, default=str))
    journal.write("perps_day", {k: latest[k] for k in ("n_listed", "n_tradable", "equity", "funding_paid_today", "orders")})
    return eq


def charge_funding(broker, data, since_ms: int, log_event) -> float:
    """Deduct from the paper book the funding each open long would have paid since ``since_ms``
    (a negative rate is income). Returns the net amount paid."""
    paid = 0.0
    for sym, pos in broker.positions().items():
        try:
            rate = float(data.funding_since(sym, since_ms))
        except Exception as exc:
            log_event("funding_skip", {"symbol": sym, "error": str(exc)[:160]})
            continue
        usd = pos.qty * pos.current_price * rate
        if usd:
            broker.adjust_cash(-usd, f"funding {sym} {rate:+.6f}")
            paid += usd
        log_event("funding", {"symbol": sym, "rate_sum": rate, "usd": usd})
    return paid
