"""In-process paper broker with persistent JSON state."""

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
                 cost: CostModel = ALPACA_STOCKS):
        self.state_path = Path(state_path)
        self.price_fn = price_fn
        self.cost = cost
        self._cash = starting_cash
        self._positions: dict[str, BrokerPosition] = {}
        self._stops: dict[str, tuple[float, float]] = {}  # symbol -> (qty, stop_price)
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
            "fills": self.fills[-500:],
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.state_path.write_text(json.dumps(d, indent=2, default=str))

    # -- queries ---------------------------------------------------------------------
    def _price(self, symbol: str) -> float:
        px = self.price_fn(symbol)
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

    def cash(self) -> float:
        return self._cash

    def positions(self) -> dict[str, BrokerPosition]:
        self.mark()
        return dict(self._positions)

    def is_market_open(self) -> bool:
        return True

    # -- orders ------------------------------------------------------------------------
    def submit_market(self, symbol: str, side: Side, *, notional: float | None = None,
                      qty: float | None = None, asset_class: str = "stock",
                      client_id: str | None = None) -> OrderResult:
        ref = self._price(symbol)
        price = self.cost.fill_price(ref, side)
        if qty is None:
            if notional is None:
                return OrderResult(False, message="qty or notional required")
            qty = notional / price
        value = qty * price
        fee = self.cost.commission(value)
        if side == "buy":
            if value + fee > self._cash + 1e-9:
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
            if pos is None or pos.qty + 1e-12 < qty:
                return OrderResult(False, message="position too small to sell")
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

    def submit_stop(self, symbol: str, qty: float, stop_price: float, *, asset_class: str = "stock") -> OrderResult:
        if symbol not in self._positions:
            return OrderResult(False, message="no position")
        self._stops[symbol] = (qty, stop_price)
        self._positions[symbol].stop_price = stop_price
        self.save()
        return OrderResult(True, f"stop-{symbol}", message="stop armed")

    def cancel_open_orders(self, symbol: str | None = None) -> int:
        if symbol is None:
            n = len(self._stops)
            self._stops.clear()
        else:
            n = int(symbol in self._stops)
            self._stops.pop(symbol, None)
        self.save()
        return n

    def close_position(self, symbol: str) -> OrderResult:
        pos = self._positions.get(symbol)
        if pos is None:
            return OrderResult(False, message="no position")
        return self.submit_market(symbol, "sell", qty=pos.qty, asset_class=pos.asset_class)

    def check_stops(self, low_fn: PriceFn | None = None) -> list[OrderResult]:
        """Simulate resting stops: fill when the day's low (or last price) trades through."""
        out = []
        for symbol, (qty, stop) in list(self._stops.items()):
            probe = (low_fn or self.price_fn)(symbol)
            if probe is not None and probe <= stop:
                # Fill at the stop, or worse if the market gapped below it.
                fill_ref = min(stop, self._price(symbol))
                saved = self.price_fn
                self.price_fn = lambda s, _p=fill_ref: _p
                try:
                    out.append(self.submit_market(symbol, "sell", qty=qty))
                finally:
                    self.price_fn = saved
        return out
