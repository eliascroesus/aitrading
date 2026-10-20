"""Paper-trading memecoin scout with the research sleeve's hard caps."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.config import MemeRules


@dataclass
class MemePosition:
    mint: str
    symbol: str
    qty: float
    entry_price: float
    peak_price: float
    entered_at: str
    cost_usd: float
    took_half: bool = False
    misses: int = 0          # scans in a row without a quote (the trench scout exits at zero after a few)


@dataclass
class MemeBook:
    cash: float
    positions: dict[str, MemePosition] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, starting_cash: float) -> MemeBook:
        if path.exists():
            d = json.loads(path.read_text())
            return cls(cash=d["cash"], positions={k: MemePosition(**v) for k, v in d["positions"].items()},
                       history=d.get("history", []))
        return cls(cash=starting_cash)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cash": self.cash, "positions": {k: asdict(v) for k, v in self.positions.items()},
                                    "history": self.history[-300:]}, indent=2, default=str))

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(m, p.entry_price) for m, p in self.positions.items())


def _net_buy(price: float, rules: MemeRules) -> float:
    return price * (1 + rules.slippage_pct + rules.fee_pct)


def _net_sell(price: float, rules: MemeRules) -> float:
    return price * (1 - rules.slippage_pct - rules.fee_pct)


def run_scout_day(
    *,
    book: MemeBook,
    rules: MemeRules,
    candidates: list[dict[str, Any]],
    prices: dict[str, float],
    sleeve_equity: float,
    now: datetime | None = None,
    journal: Callable[[str, dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """One day: manage exits on current prices, then enter filtered candidates within caps."""
    now = now or datetime.now(UTC)
    log = journal or (lambda k, p: None)
    exits: list[dict[str, Any]] = []
    for mint, pos in list(book.positions.items()):
        px = prices.get(mint)
        if px is None:
            continue
        pos.peak_price = max(pos.peak_price, px)
        age_h = (now - datetime.fromisoformat(pos.entered_at)).total_seconds() / 3600
        reason = None
        if px <= pos.entry_price * (1 - rules.hard_stop_pct):
            reason = "hard_stop"
        elif px <= pos.peak_price * (1 - rules.trail_pct):
            reason = "trail"
        elif age_h >= rules.time_stop_hours:
            reason = "time_stop"
        elif not pos.took_half and px >= pos.entry_price * (1 + rules.take_half_at_pct):
            half = pos.qty / 2
            proceeds = half * _net_sell(px, rules)
            book.cash += proceeds
            pos.qty -= half
            pos.took_half = True
            ev = {"mint": mint, "symbol": pos.symbol, "event": "take_half", "price": px, "proceeds": proceeds}
            book.history.append(ev)
            log("meme_take_half", ev)
            continue
        if reason:
            proceeds = pos.qty * _net_sell(px, rules)
            book.cash += proceeds
            pnl = proceeds + (pos.cost_usd / 2 if pos.took_half else 0.0) - pos.cost_usd  # approx incl. half-take
            ev = {"mint": mint, "symbol": pos.symbol, "event": "exit", "reason": reason, "price": px,
                  "proceeds": proceeds, "pnl_approx": pnl, "age_hours": age_h}
            book.history.append(ev)
            exits.append(ev)
            log("meme_exit", ev)
            del book.positions[mint]
    entries: list[dict[str, Any]] = []
    held_value = sum(p.qty * prices.get(m, p.entry_price) for m, p in book.positions.items())
    cap_total = sleeve_equity * rules.total_pct
    for c in candidates:
        if not c.get("passes") or c["mint"] in book.positions:
            continue
        if len(book.positions) >= rules.max_open or held_value >= cap_total:
            break
        px = c.get("price_usd")
        if not px:
            continue
        notional = min(sleeve_equity * rules.per_token_pct, cap_total - held_value, book.cash)
        if notional < 1.0:
            break
        qty = notional / _net_buy(px, rules)
        book.cash -= notional
        held_value += notional
        book.positions[c["mint"]] = MemePosition(mint=c["mint"], symbol=c.get("symbol") or "?", qty=qty,
                                                 entry_price=px, peak_price=px, entered_at=now.isoformat(),
                                                 cost_usd=notional)
        ev = {"mint": c["mint"], "symbol": c.get("symbol"), "event": "entry", "price": px, "notional": notional,
              "liquidity_usd": c.get("liquidity_usd"), "age_hours": c.get("age_hours")}
        book.history.append(ev)
        entries.append(ev)
        log("meme_entry", ev)
    return {"exits": exits, "entries": entries, "equity": book.equity(prices), "n_open": len(book.positions)}
