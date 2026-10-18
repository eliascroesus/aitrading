"""Transaction cost model per venue."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Commission as a fraction of notional per side, plus slippage in bps per side."""

    commission_pct: float = 0.0
    slippage_bps: float = 5.0
    min_commission_usd: float = 0.0
    name: str = "generic"

    def fill_price(self, reference: float, side: str) -> float:
        """Reference price moved against us by the slippage assumption."""
        adj = reference * self.slippage_bps / 10_000.0
        return reference + adj if side == "buy" else max(reference - adj, 0.0)

    def commission(self, notional: float) -> float:
        return max(abs(notional) * self.commission_pct, self.min_commission_usd if notional else 0.0)


# Alpaca US equities: zero commission; regulatory fees on sells are pennies and
# are folded into slippage. Small/fractional orders see wider effective spreads.
ALPACA_STOCKS = CostModel(commission_pct=0.0, slippage_bps=5.0, name="alpaca_stocks")
# Alpaca crypto tier 1: 0.25% taker (0.15% maker). Assume taker to be conservative.
ALPACA_CRYPTO = CostModel(commission_pct=0.0025, slippage_bps=10.0, name="alpaca_crypto")
# Hyperliquid perps base tier: 0.045% taker / 0.015% maker. Assume taker.
HYPERLIQUID_PERPS = CostModel(commission_pct=0.00045, slippage_bps=5.0, name="hyperliquid_perps")

VENUE_COSTS = {
    "alpaca_stocks": ALPACA_STOCKS,
    "alpaca_crypto": ALPACA_CRYPTO,
    "hyperliquid_perps": HYPERLIQUID_PERPS,
}
