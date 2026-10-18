"""Position sizing. Pure arithmetic; the LLM never produces these numbers.

Fixed-fractional risk: risk ``risk_per_trade_pct`` of equity (times the regime
and tier multipliers, and scaled down when portfolio vol exceeds target) to the
initial stop. Then the per-asset-class position cap and the venue minimum
notional apply. Position = min(risk-based, cap-based).
"""

from __future__ import annotations

from dataclasses import dataclass

from hive.config import RiskLimits


@dataclass(frozen=True)
class SizeDecision:
    notional: float
    qty: float
    stop_price: float
    r_per_share: float
    reason: str


def size_position(
    *,
    equity: float,
    price: float,
    stop_price: float,
    risk: RiskLimits,
    asset_class: str = "stock",
    risk_multiplier: float = 1.0,
    portfolio_vol_ann: float | None = None,
    fractional: bool = True,
    lot_size: float = 1.0,
) -> SizeDecision:
    if equity <= 0 or price <= 0:
        return SizeDecision(0.0, 0.0, 0.0, 0.0, "no equity or bad price")
    r = price - stop_price
    if r <= 0:
        return SizeDecision(0.0, 0.0, stop_price, 0.0, "stop not below price")
    if risk_multiplier <= 0:
        return SizeDecision(0.0, 0.0, stop_price, r, "risk multiplier is zero")

    scale = risk_multiplier
    if portfolio_vol_ann and portfolio_vol_ann > risk.target_annual_vol:
        scale *= risk.target_annual_vol / portfolio_vol_ann
    risk_dollars = equity * risk.risk_per_trade_pct * scale
    qty = risk_dollars / r
    # The regime/tier multiplier scales the position cap too, so "half risk"
    # is half risk even when the cap (not the stop) is what binds.
    caps = {"risk": qty * price,
            "position_cap": equity * risk.max_position_pct(asset_class) * min(risk_multiplier, 1.0)}
    binding = min(caps, key=caps.get)
    notional = caps[binding]
    qty = notional / price
    if not fractional:
        qty = (qty // lot_size) * lot_size
        notional = qty * price
        if qty <= 0:
            return SizeDecision(0.0, 0.0, stop_price, r, "whole-share rounding to zero")
    if notional < risk.min_order_notional_usd:
        return SizeDecision(0.0, 0.0, stop_price, r, f"below min notional ({binding})")
    return SizeDecision(round(notional, 2), qty, stop_price, r, binding)
