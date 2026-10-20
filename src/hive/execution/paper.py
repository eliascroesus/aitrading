"""In-process paper broker with persistent JSON state.

Fill convention mirrors the backtester: a market order placed after the close
is **queued** and fills at the next session's open (``process_pending``), and
resting stops are evaluated against the session's low (``check_stops``).
Both hooks are called by the orchestrator at the start of each run.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hive.backtest.costs import ALPACA_STOCKS, CostModel
from hive.execution.base import BrokerPosition, OrderResult, Side

PriceFn = Callable[[str], float | None]


class PaperBroker:
    name = "paper"

    def __init__(self, state_path: Path, price_fn: PriceFn, *, starting_cash: float = 100.0,
                 cost: CostModel = ALPACA_STOCKS, fill_mode: str = "next_open"):
        self.state_path = Path(state_path)
        self.price_fn = price_fn
        self.cost = cost
        self.fill_mode = fill_mode  # "next_open" (queue) or "immediate" (tests/simulations)
        self._cash = starting_cash
        self._positions: dict[str, BrokerPosition] = {}
        self._stops: dict[str, tuple[float, float]] = {}  # symbol -> (qty, stop_price)
        self._pending: list[dict] = []
        self.fills: list[dict] = []
        if self.state_path.exists():
            self._load()

    # -- persistence -------------------------------------------------------------
    def _load(self) -> None:
        d = json.loads(self.state_path.read_text())
        self._cash = d["cash"]
        self._positions = {
            s: BrokerPosition(**{**p, "symbol": s}) for s, p in d.get("positions", {}).items()
        }
        self._stops = {s: tuple(v) for s, v in d.get("stops", {}).items()}
        self._pending = d.get("pending", [])
        self.fills = d.get("fills", [])

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        d = {
            "cash": self._cash,
            "positions": {
                s: {"asset_class": p.asset_class, "qty": p.qty, "avg_price": p.avg_price,
                    "current_price": p.current_price, "stop_price": p.stop_price}
                for s, p in self._positions.items()
            },
            "stops": {s: list(v) for s, v in self._stops.items()},
            "pending": self._pending,
            "fills": self.fills[-500:],
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.state_path.write_text(json.dumps(d, indent=2, default=str))

    # -- queries ---------------------------------------------------------------------
    def _price(self, symbol: str, fn: PriceFn | None = None) -> float:
        px = (fn or self.price_fn)(symbol)
        if px is None or px <= 0:
            raise ValueError(f"no price for {symbol}")
        return float(px)

    def mark(self) -> None:
        for p in self._positions.values():
            try:
                p.current_price = self._price(p.symbol)
            except ValueError:
                pass

    def equity(self) -> float:
        self.mark()
        return self._cash + sum(p.market_value for p in self._positions.values())

    def adjust_cash(self, delta: float, reason: str) -> None:
        """Book a cash flow that is not a fill (perp funding on the paper desk)."""
        self._cash += float(delta)
        self.fills.append({"event": "cash_adjustment", "delta": float(delta), "reason": reason,
                           "ts": datetime.now(UTC).isoformat(timespec="seconds")})
        self.save()

    def cash(self) -> float:
        return self._cash

    def positions(self) -> dict[str, BrokerPosition]:
        self.mark()
        return dict(self._positions)

    def pending_orders(self) -> list[dict]:
        return list(self._pending)

    def is_market_open(self) -> bool:
        return True

    # -- orders ------------------------------------------------------------------------
    def _fill(self, symbol: str, side: Side, qty: float | None, notional: float | None,
              asset_class: str, client_id: str | None, ref: float) -> OrderResult:
        price = self.cost.fill_price(ref, side)
        if qty is None:
            if notional is None:
                return OrderResult(False, message="qty or notional required")
            qty = notional / price
        value = qty * price
        fee = self.cost.commission(value)
        if side == "buy":
            if value + fee > self._cash + 1e-9:
                # Scale down to what cash allows rather than reject outright.
                qty = max((self._cash - fee) / price, 0.0)
                value = qty * price
                if qty <= 0:
                    return OrderResult(False, message="insufficient cash")
            self._cash -= value + fee
            pos = self._positions.get(symbol)
            if pos is None:
                self._positions[symbol] = BrokerPosition(symbol, asset_class, qty, price, ref)
            else:
                total = pos.qty + qty
                pos.avg_price = (pos.avg_price * pos.qty + price * qty) / total
                pos.qty = total
        else:
            pos = self._positions.get(symbol)
            if pos is None:
                return OrderResult(False, message="position too small to sell")
            qty = min(qty, pos.qty)
            value = qty * price
            fee = self.cost.commission(value)
            self._cash += value - fee
            pos.qty -= qty
            if pos.qty <= 1e-9:
                del self._positions[symbol]
                self._stops.pop(symbol, None)
        oid = client_id or uuid.uuid4().hex[:12]
        self.fills.append({"ts": datetime.now(UTC).isoformat(timespec="seconds"), "symbol": symbol,
                           "side": side, "qty": qty, "price": price, "fee": fee, "id": oid})
        self.save()
        return OrderResult(True, oid, qty, price, fee, "filled")

    def submit_market(self, symbol: str, side: Side, *, notional: float | None = None,
                      qty: float | None = None, asset_class: str = "stock",
                      client_id: str | None = None) -> OrderResult:
        if self.fill_mode == "immediate":
            return self._fill(symbol, side, qty, notional, asset_class, client_id, self._price(symbol))
        oid = client_id or uuid.uuid4().hex[:12]
        self._pending.append({"id": oid, "symbol": symbol, "side": side, "qty": qty,
                              "notional": notional, "asset_class": asset_class})
        self.save()
        return OrderResult(True, oid, 0.0, 0.0, 0.0, "queued for next open")

    def process_pending(self, open_fn: PriceFn) -> list[OrderResult]:
        """Fill queued market orders at the given open prices (call at the start of a run)."""
        out = []
        for o in list(self._pending):
            try:
                ref = self._price(o["symbol"], open_fn)
            except ValueError:
                continue  # no bar yet for this venue today; stays queued
            out.append(self._fill(o["symbol"], o["side"], o["qty"], o["notional"], o["asset_class"],
                                  o["id"], ref))
            self._pending.remove(o)
        self.save()
        return out

    def submit_stop(self, symbol: str, qty: float, stop_price: float, *, asset_class: str = "stock") -> OrderResult:
        if symbol not in self._positions:
            return OrderResult(False, message="no position")
        self._stops[symbol] = (min(qty, self._positions[symbol].qty), stop_price)
        self._positions[symbol].stop_price = stop_price
        self.save()
        return OrderResult(True, f"stop-{symbol}", message="stop armed")

    def open_stop_ids(self) -> dict[str, str]:
        return {s: f"stop-{s}" for s in self._stops}

    def cancel_open_orders(self, symbol: str | None = None) -> int:
        if symbol is None:
            n = len(self._stops) + len(self._pending)
            self._stops.clear()
            self._pending.clear()
        else:
            n = int(symbol in self._stops)
            self._stops.pop(symbol, None)
            before = len(self._pending)
            self._pending = [o for o in self._pending if o["symbol"] != symbol]
            n += before - len(self._pending)
        self.save()
        return n

    def close_position(self, symbol: str) -> OrderResult:
        pos = self._positions.get(symbol)
        if pos is None:
            return OrderResult(False, message="no position")
        self._stops.pop(symbol, None)
        return self.submit_market(symbol, "sell", qty=pos.qty, asset_class=pos.asset_class)

    def check_stops(self, low_fn: PriceFn | None = None, open_fn: PriceFn | None = None) -> list[OrderResult]:
        """Resting stops: fill at the stop, or at the open when the session gapped through it."""
        out = []
        for symbol, (qty, stop) in list(self._stops.items()):
            try:
                probe = self._price(symbol, low_fn)
            except ValueError:
                continue
            if probe <= stop:
                try:
                    opn = self._price(symbol, open_fn) if open_fn else stop
                except ValueError:
                    opn = stop
                fill_ref = min(stop, opn)
                out.append(self._fill(symbol, "sell", qty, None, self._positions[symbol].asset_class
                                      if symbol in self._positions else "stock", None, fill_ref))
                self._stops.pop(symbol, None)
        self.save()
        return out
