"""Position sizing. Pure arithmetic; the LLM never produces these numbers.

Fixed-fractional risk: risk a share of equity (times the regime and tier
multipliers, and scaled down when portfolio vol exceeds target) to the initial
stop. The share is ``risk_per_trade_pct``, or, in ``sizing_mode = "edge"``, what
the book's own closed trades justify (``edge_risk_pct``). Then the per-asset-class
position cap and the venue minimum notional apply. Position = min(risk-based,
cap-based).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hive.config import RiskLimits


@dataclass(frozen=True)
class SizeDecision:
    notional: float
    qty: float
    stop_price: float
    r_per_share: float
    reason: str


def edge_risk_pct(r_multiples: Sequence[float], risk: RiskLimits) -> float:
    """The share of equity to risk on the next trade, chosen by the book's record.

    In ``sizing_mode = "edge"``: ``edge_fraction`` of the Kelly bet W - (1 - W) / payoff
    over the last ``edge_lookback_trades`` closed trades, in R, clamped to
    [``edge_min_risk_pct``, ``max_risk_per_trade_pct``]. A book with a real edge
    risks more, one without risks the floor. Until there are ``edge_min_trades``
    closed trades (and always in "fixed" mode) it is ``risk_per_trade_pct``.
    """
    if risk.sizing_mode != "edge":
        return risk.risk_per_trade_pct
    recent = [float(r) for r in list(r_multiples)[-risk.edge_lookback_trades:]]
    if len(recent) < max(risk.edge_min_trades, 1):
        return risk.risk_per_trade_pct
    wins = [r for r in recent if r > 0]
    losses = [-r for r in recent if r < 0]
    if not wins:
        return risk.edge_min_risk_pct
    if not losses:
        return risk.max_risk_per_trade_pct
    w = len(wins) / len(recent)
    payoff = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    kelly = w - (1 - w) / payoff
    return min(max(risk.edge_fraction * kelly, risk.edge_min_risk_pct), risk.max_risk_per_trade_pct)


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
    cap_pct: float | None = None,
    risk_pct: float | None = None,
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
    risk_dollars = equity * (risk.risk_per_trade_pct if risk_pct is None else risk_pct) * scale
    qty = risk_dollars / r
    # The regime/tier multiplier scales the position cap too, so "half risk"
    # is half risk even when the cap (not the stop) is what binds.
    caps = {"risk": qty * price,
            "position_cap": equity * (risk.max_position_pct(asset_class) if cap_pct is None else cap_pct)
            * min(risk_multiplier, 1.0)}
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
