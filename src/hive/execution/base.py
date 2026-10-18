from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Side = Literal["buy", "sell"]


@dataclass
class BrokerPosition:
    symbol: str
    asset_class: str
    qty: float
    avg_price: float
    current_price: float
    stop_price: float | None = None

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price


@dataclass
class OrderResult:
    ok: bool
    order_id: str = ""
    filled_qty: float = 0.0
    fill_price: float = 0.0
    fees: float = 0.0
    message: str = ""
    raw: dict = field(default_factory=dict)


class Broker(Protocol):
    name: str

    def equity(self) -> float: ...
    def cash(self) -> float: ...
    def positions(self) -> dict[str, BrokerPosition]: ...
    def submit_market(
        self, symbol: str, side: Side, *, notional: float | None = None, qty: float | None = None,
        asset_class: str = "stock", client_id: str | None = None,
    ) -> OrderResult: ...
    def submit_stop(self, symbol: str, qty: float, stop_price: float, *, asset_class: str = "stock") -> OrderResult: ...
    def cancel_open_orders(self, symbol: str | None = None) -> int: ...
    def close_position(self, symbol: str) -> OrderResult: ...
    def is_market_open(self) -> bool: ...
