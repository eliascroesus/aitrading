"""Capital allocation between desks that follows realised results: a prop-firm scaling plan.

Weekly, the crypto sleeve's share of equity moves one step toward whichever desk
earned more over the window and away from the one that lost, inside pre-registered
bounds. Nothing here is an LLM decision: it is the mechanical reward and punishment
on real capital that the study owner asked for, bounded so it cannot blow up.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from hive.orchestrator.journal import Journal

STEP = 0.05
LOW, HIGH = 0.20, 0.60
WINDOW_DAYS = 42
MIN_TRADES = 3


@dataclass
class Scorecard:
    window_days: int
    pnl_by_class: dict[str, float]
    trades_by_class: dict[str, int]

    def desk(self, *classes: str) -> tuple[float, int]:
        return (sum(self.pnl_by_class.get(c, 0.0) for c in classes),
                sum(self.trades_by_class.get(c, 0) for c in classes))


def scorecard(journal_dir: Path, on: date, window_days: int = WINDOW_DAYS) -> Scorecard:
    """Realised P&L per asset class from the primary journals' ``exit`` entries."""
    pnl: dict[str, float] = {}
    n: dict[str, int] = {}
    start = on - timedelta(days=window_days)
    for p in sorted(Path(journal_dir).glob("????-??-??.jsonl")):  # primary only: no label suffix
        try:
            day = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if day < start or day > on:
            continue
        for e in Journal.read(p):
            if e.get("kind") != "exit":
                continue
            pay = e.get("payload", {})
            ac, v = pay.get("asset_class"), pay.get("pnl_usd")
            if ac is None or v is None:
                continue
            pnl[ac] = pnl.get(ac, 0.0) + float(v)
            n[ac] = n.get(ac, 0) + 1
    return Scorecard(window_days, pnl, n)


def decide(current: float, sc: Scorecard, *, step: float = STEP, low: float = LOW, high: float = HIGH,
           min_trades: int = MIN_TRADES) -> tuple[float, str]:
    crypto, n_c = sc.desk("crypto")
    stocks, n_s = sc.desk("stock", "etf")
    if n_c + n_s < min_trades:
        return current, f"hold: only {n_c + n_s} closed trades in {sc.window_days} days"
    if abs(crypto - stocks) < 1e-9:
        return current, "hold: desks level"
    if crypto > stocks:
        new = min(high, round(current + step, 4))
        why = f"crypto desk ${crypto:+.2f} vs stock desk ${stocks:+.2f}: crypto sleeve {current:.0%} -> {new:.0%}"
    else:
        new = max(low, round(current - step, 4))
        why = f"stock desk ${stocks:+.2f} vs crypto desk ${crypto:+.2f}: crypto sleeve {current:.0%} -> {new:.0%}"
    if new == current:
        why += " (at the bound)"
    return new, why


def load_allocation(path: Path, default_crypto_pct: float) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"crypto_sleeve_pct": default_crypto_pct, "updated": None, "history": []}


def rebalance(path: Path, journal_dir: Path, on: date, default_crypto_pct: float) -> dict:
    alloc = load_allocation(path, default_crypto_pct)
    sc = scorecard(journal_dir, on)
    new, why = decide(float(alloc["crypto_sleeve_pct"]), sc)
    alloc["history"] = (alloc.get("history") or [])[-51:] + [
        {"date": on.isoformat(), "from": alloc["crypto_sleeve_pct"], "to": new, "why": why, "scorecard": asdict(sc)}]
    alloc["crypto_sleeve_pct"], alloc["updated"] = new, on.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(alloc, indent=1))
    return alloc
