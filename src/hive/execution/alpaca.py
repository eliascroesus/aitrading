"""Alpaca adapter (US stocks/ETFs; spot crypto). Paper endpoint unless live."""

from __future__ import annotations

import logging

from hive.execution.base import BrokerPosition, OrderResult, Side

log = logging.getLogger(__name__)


def round_price(px: float, asset_class: str) -> float:
    """Stocks trade in cents; crypto pairs quote to many decimals (SHIB, PEPE), so keep
    six significant figures there instead of rounding a sub-cent price to zero."""
    if asset_class == "crypto":
        return float(f"{px:.6g}")
    return round(px, 2)


def stop_request(sym: str, qty: float, stop_price: float, asset_class: str, tif):
    """Alpaca accepts plain stop orders for stocks only; crypto needs a stop-limit. The
    limit sits 1% under the stop so it behaves like a stop while being a valid crypto order."""
    from alpaca.trading.enums import OrderSide
    from alpaca.trading.requests import StopLimitOrderRequest, StopOrderRequest

    stop = round_price(stop_price, asset_class)
    if asset_class == "crypto":
        return StopLimitOrderRequest(symbol=sym, qty=qty, side=OrderSide.SELL, time_in_force=tif,
                                     stop_price=stop, limit_price=round_price(stop * 0.99, asset_class))
    return StopOrderRequest(symbol=sym, qty=qty, side=OrderSide.SELL, time_in_force=tif, stop_price=stop)


def book_symbol(order) -> str:
    """An Alpaca order's symbol in the book's spelling (``BTC/USD`` and ``BTCUSD`` are ``BTC``)."""
    sym = str(order.symbol).upper().replace("/USD", "")
    if (sym.endswith("USD") and len(sym) > 3 and "/" not in str(order.symbol)
            and str(getattr(order, "asset_class", "")).lower().endswith("crypto")):
        sym = sym[:-3]
    return sym


def alpaca_symbol(symbol: str, asset_class: str) -> str:
    """Alpaca spot crypto symbols look like ``BTC/USD``."""
    if asset_class == "crypto" and "/" not in symbol:
        return f"{symbol.upper()}/USD"
    return symbol.upper()


class AlpacaBroker:
    name = "alpaca"

    def __init__(self, api_key: str, secret_key: str, *, paper: bool = True):
        from alpaca.trading.client import TradingClient

        self.paper = paper
        self.client = TradingClient(api_key, secret_key, paper=paper)
        self.name = "alpaca-paper" if paper else "alpaca-live"
        self._tradable: dict[tuple[str, str], bool] = {}

    # -- queries ---------------------------------------------------------------
    def supports(self, symbol: str, asset_class: str = "stock") -> bool:
        """Whether Alpaca lists the asset as tradable; unknown or unlisted means no order."""
        key = (symbol.upper(), asset_class)
        if key not in self._tradable:
            try:
                asset = self.client.get_asset(alpaca_symbol(symbol, asset_class))
                self._tradable[key] = bool(getattr(asset, "tradable", False))
            except Exception:
                self._tradable[key] = False
        return self._tradable[key]

    def equity(self) -> float:
        return float(self.client.get_account().equity)

    def cash(self) -> float:
        return float(self.client.get_account().cash)

    def positions(self) -> dict[str, BrokerPosition]:
        out: dict[str, BrokerPosition] = {}
        for p in self.client.get_all_positions():
            ac = "crypto" if str(getattr(p, "asset_class", "")).lower().endswith("crypto") else "stock"
            sym = p.symbol
            if ac == "crypto":  # Alpaca reports crypto positions as BTCUSD or BTC/USD
                sym = sym.replace("/USD", "")
                sym = sym[:-3] if sym.endswith("USD") else sym
            out[sym] = BrokerPosition(
                symbol=sym, asset_class=ac, qty=float(p.qty), avg_price=float(p.avg_entry_price),
                current_price=float(p.current_price or p.avg_entry_price),
            )
        return out

    def is_market_open(self) -> bool:
        return bool(self.client.get_clock().is_open)

    # -- orders ------------------------------------------------------------------
    def submit_market(self, symbol: str, side: Side, *, notional: float | None = None,
                      qty: float | None = None, asset_class: str = "stock",
                      client_id: str | None = None) -> OrderResult:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        if asset_class == "crypto":
            tif = TimeInForce.GTC
        else:
            tif = TimeInForce.DAY  # fractional/notional equity orders must be DAY
        req = MarketOrderRequest(
            symbol=alpaca_symbol(symbol, asset_class),
            notional=round(notional, 2) if notional is not None and qty is None else None,
            qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=tif, client_order_id=client_id,
        )
        try:
            o = self.client.submit_order(req)
        except Exception as exc:  # APIError and transport errors alike -> no trade
            log.error("alpaca submit_market failed for %s: %s", symbol, exc)
            return OrderResult(False, message=str(exc))
        return OrderResult(
            True, str(o.id), float(o.filled_qty or 0), float(o.filled_avg_price or 0), 0.0,
            str(o.status), raw={"status": str(o.status)},
        )

    def submit_stop(self, symbol: str, qty: float, stop_price: float, *, asset_class: str = "stock") -> OrderResult:
        from alpaca.trading.enums import TimeInForce

        sym = alpaca_symbol(symbol, asset_class)
        last_exc: Exception | None = None
        # GTC stops are preferred; fractional stock quantities may only be accepted as DAY.
        for tif in (TimeInForce.GTC, TimeInForce.DAY):
            try:
                o = self.client.submit_order(stop_request(sym, qty, stop_price, asset_class, tif))
                return OrderResult(True, str(o.id), message=f"stop {tif.value}")
            except Exception as exc:
                last_exc = exc
        log.error("alpaca submit_stop failed for %s: %s", symbol, last_exc)
        return OrderResult(False, message=str(last_exc))

    def pending_orders(self) -> list[dict]:
        """Open (queued, unfilled) orders, with symbols in the book's spelling."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        out = []
        try:
            for o in self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
                out.append({"symbol": book_symbol(o), "id": str(o.id), "side": str(getattr(o, "side", "")).lower()})
        except Exception as exc:
            log.warning("alpaca pending_orders failed: %s", exc)
        return out

    def open_stops(self) -> dict[str, str] | None:
        """Resting sell stops by symbol, in the book's spelling; None when Alpaca could not be
        asked (then nothing is assumed). A fractional stock stop is a day order and lapses at
        the close, so a stop the book armed may be gone."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        except Exception as exc:
            log.warning("alpaca open_stops failed: %s", exc)
            return None
        out: dict[str, str] = {}
        for o in orders:
            kind = str(getattr(o, "order_type", None) or getattr(o, "type", "")).lower()
            if "sell" in str(getattr(o, "side", "")).lower() and "stop" in kind:
                out[book_symbol(o)] = str(o.id)
        return out

    def cancel_open_orders(self, symbol: str | None = None) -> int:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        if symbol is None:
            return len(self.client.cancel_orders())
        n = 0
        for o in self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])):
            self.client.cancel_order_by_id(o.id)
            n += 1
        return n

    def close_position(self, symbol: str) -> OrderResult:
        try:
            o = self.client.close_position(symbol)
            return OrderResult(True, str(o.id), message=str(o.status))
        except Exception as exc:
            return OrderResult(False, message=str(exc))
