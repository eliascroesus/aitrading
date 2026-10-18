"""Hyperliquid perps adapter using an agent (API) wallet. Testnet by default.

Facts baked in (see docs/research/03): $10 minimum notional, sizes rounded to
``szDecimals`` from ``meta``, prices to 5 significant figures, market orders
are IOC limits with a slippage bound, stops are trigger orders with
``reduce_only``. The agent wallet cannot withdraw. Hyperliquid's terms restrict
US persons; the operator must assert eligibility in settings.
"""

from __future__ import annotations

import logging
import math
import time

from hive.execution.base import BrokerPosition, OrderResult, Side

log = logging.getLogger(__name__)
MIN_NOTIONAL_USD = 10.0


def round_sz(sz: float, sz_decimals: int) -> float:
    q = 10 ** sz_decimals
    return math.floor(sz * q) / q


def round_px(px: float, sz_decimals: int, *, is_spot: bool = False) -> float:
    """5 significant figures and at most (6 - szDecimals) decimals for perps."""
    if px <= 0:
        return px
    sig = float(f"{px:.5g}")
    max_dec = (8 if is_spot else 6) - sz_decimals
    return round(sig, max(max_dec, 0))


class HyperliquidBroker:
    name = "hyperliquid"

    def __init__(self, agent_private_key: str, account_address: str, *, testnet: bool = True,
                 operator_not_restricted_person: bool = False, max_leverage: int = 1):
        if not operator_not_restricted_person:
            raise RuntimeError(
                "Hyperliquid is disabled: set HL_OPERATOR_NOT_RESTRICTED_PERSON=true only if the "
                "operator is not a US/restricted person under Hyperliquid's terms."
            )
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        base = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.testnet = testnet
        self.name = "hyperliquid-testnet" if testnet else "hyperliquid-live"
        self.account_address = account_address
        wallet = Account.from_key(agent_private_key)
        self.info = Info(base, skip_ws=True)
        self.exchange = Exchange(wallet, base, account_address=account_address)
        self.max_leverage = max_leverage
        meta = self.info.meta()
        self._sz_decimals = {a["name"]: int(a["szDecimals"]) for a in meta["universe"]}
        self._max_lev = {a["name"]: int(a.get("maxLeverage", 1)) for a in meta["universe"]}

    # -- queries ---------------------------------------------------------------
    def _state(self) -> dict:
        return self.info.user_state(self.account_address)

    def equity(self) -> float:
        return float(self._state()["marginSummary"]["accountValue"])

    def cash(self) -> float:
        return float(self._state().get("withdrawable", 0.0))

    def positions(self) -> dict[str, BrokerPosition]:
        out: dict[str, BrokerPosition] = {}
        mids = self.info.all_mids()
        for ap in self._state().get("assetPositions", []):
            p = ap["position"]
            szi = float(p["szi"])
            if szi == 0:
                continue
            coin = p["coin"]
            out[coin] = BrokerPosition(
                symbol=coin, asset_class="crypto", qty=szi, avg_price=float(p.get("entryPx") or 0.0),
                current_price=float(mids.get(coin, p.get("entryPx") or 0.0)),
            )
        return out

    def is_market_open(self) -> bool:
        return True

    # -- orders ------------------------------------------------------------------
    def _mid(self, coin: str) -> float:
        return float(self.info.all_mids()[coin])

    def submit_market(self, symbol: str, side: Side, *, notional: float | None = None,
                      qty: float | None = None, asset_class: str = "crypto",
                      client_id: str | None = None) -> OrderResult:
        coin = symbol.upper()
        if coin not in self._sz_decimals:
            return OrderResult(False, message=f"unknown coin {coin}")
        mid = self._mid(coin)
        if qty is None:
            if notional is None:
                return OrderResult(False, message="qty or notional required")
            qty = notional / mid
        sz = round_sz(qty, self._sz_decimals[coin])
        if sz * mid < MIN_NOTIONAL_USD and side == "buy":
            return OrderResult(False, message=f"below Hyperliquid minimum notional ${MIN_NOTIONAL_USD}")
        if side == "buy":
            lev = max(1, min(self.max_leverage, self._max_lev.get(coin, 1)))
            try:
                self.exchange.update_leverage(lev, coin, is_cross=False)
            except Exception as exc:
                log.warning("update_leverage failed for %s: %s", coin, exc)
        try:
            res = self.exchange.market_open(coin, side == "buy", sz, None, 0.01)
        except Exception as exc:
            log.error("hyperliquid market order failed for %s: %s", coin, exc)
            return OrderResult(False, message=str(exc))
        return self._parse_order_response(res, sz)

    def submit_stop(self, symbol: str, qty: float, stop_price: float, *, asset_class: str = "crypto") -> OrderResult:
        coin = symbol.upper()
        sd = self._sz_decimals[coin]
        trigger = round_px(stop_price, sd)
        order_type = {"trigger": {"triggerPx": trigger, "isMarket": True, "tpsl": "sl"}}
        try:
            res = self.exchange.order(coin, False, round_sz(qty, sd), trigger, order_type, reduce_only=True)
        except Exception as exc:
            return OrderResult(False, message=str(exc))
        return self._parse_order_response(res, qty, msg="stop armed")

    def cancel_open_orders(self, symbol: str | None = None) -> int:
        n = 0
        for o in self.info.open_orders(self.account_address):
            if symbol is None or o["coin"] == symbol.upper():
                try:
                    self.exchange.cancel(o["coin"], o["oid"])
                    n += 1
                except Exception as exc:
                    log.warning("cancel failed: %s", exc)
        return n

    def close_position(self, symbol: str) -> OrderResult:
        try:
            res = self.exchange.market_close(symbol.upper(), None, None, 0.01)
        except Exception as exc:
            return OrderResult(False, message=str(exc))
        return self._parse_order_response(res, 0.0, msg="closed")

    def arm_dead_mans_switch(self, seconds: int = 24 * 3600) -> None:
        """Cancel all open orders if no heartbeat arrives within ``seconds``."""
        self.exchange.schedule_cancel(int(time.time() * 1000) + seconds * 1000)

    @staticmethod
    def _parse_order_response(res: dict, sz: float, msg: str = "") -> OrderResult:
        try:
            statuses = res["response"]["data"]["statuses"]
            st = statuses[0]
            if "error" in st:
                return OrderResult(False, message=st["error"], raw=res)
            if "filled" in st:
                f = st["filled"]
                return OrderResult(True, str(f.get("oid", "")), float(f.get("totalSz", sz)),
                                   float(f.get("avgPx", 0.0)), 0.0, msg or "filled", raw=res)
            if "resting" in st:
                return OrderResult(True, str(st["resting"].get("oid", "")), 0.0, 0.0, 0.0,
                                   msg or "resting", raw=res)
        except (KeyError, IndexError, TypeError):
            pass
        return OrderResult(res.get("status") == "ok", message=msg or str(res), raw=res)
