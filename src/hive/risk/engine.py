"""Pre-trade risk checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from hive.config import RiskLimits

Side = Literal["buy", "sell"]
LLMAction = Literal["approve", "reduce_half", "veto"]


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    asset_class: str
    side: Side
    notional: float
    qty: float
    price: float
    stop_price: float | None = None
    reason: str = ""
    source: str = "rules"


@dataclass(frozen=True)
class PositionInfo:
    symbol: str
    asset_class: str
    qty: float
    price: float
    stop_price: float | None = None


@dataclass(frozen=True)
class PortfolioSnapshot:
    equity: float
    cash: float
    positions: dict[str, PositionInfo] = field(default_factory=dict)
    day_start_equity: float | None = None
    peak_equity: float | None = None
    entries_today: int = 0

    @property
    def gross_exposure(self) -> float:
        if self.equity <= 0:
            return 0.0
        return sum(abs(p.qty * p.price) for p in self.positions.values()) / self.equity

    def exposure_of(self, asset_class: str) -> float:
        if self.equity <= 0:
            return 0.0
        return sum(
            abs(p.qty * p.price) for p in self.positions.values() if p.asset_class == asset_class
        ) / self.equity

    @property
    def open_risk(self) -> float:
        if self.equity <= 0:
            return 0.0
        return sum(
            max(p.qty * (p.price - p.stop_price), 0.0)
            for p in self.positions.values()
            if p.stop_price is not None
        ) / self.equity


@dataclass(frozen=True)
class RiskVerdict:
    intent: OrderIntent
    approved: bool
    adjusted: OrderIntent | None
    reasons: tuple[str, ...]


class RiskEngine:
    def __init__(
        self,
        limits: RiskLimits,
        *,
        kill_switch: Callable[[], bool] | None = None,
        tier_risk_multiplier: float = 1.0,
        close_only: bool = False,
        drawdown_halt: bool = True,
    ):
        self.limits = limits
        self.kill_switch = kill_switch or (lambda: False)
        self.tier_risk_multiplier = tier_risk_multiplier
        self.close_only = close_only
        self.drawdown_halt = drawdown_halt     # real money only by default (risk.halt_paper)

    # -- LLM adjustments can only reduce ----------------------------------------
    @staticmethod
    def apply_llm_action(intent: OrderIntent, action: LLMAction) -> OrderIntent | None:
        if intent.side == "sell":
            return intent  # the LLM never blocks a risk-reducing exit
        if action == "veto":
            return None
        if action == "reduce_half":
            return replace(intent, notional=intent.notional / 2.0, qty=intent.qty / 2.0,
                           source=f"{intent.source}+llm_half")
        return intent

    # -- portfolio-level gates ------------------------------------------------------
    def halted(self, snap: PortfolioSnapshot) -> tuple[bool, str]:
        if self.kill_switch():
            return True, "kill switch engaged"
        if (self.drawdown_halt and snap.peak_equity
                and snap.equity <= snap.peak_equity * (1 - self.limits.max_drawdown_halt_pct)):
            return True, f"drawdown halt ({self.limits.max_drawdown_halt_pct:.0%})"
        return False, ""

    def buys_blocked(self, snap: PortfolioSnapshot) -> tuple[bool, str]:
        halted, why = self.halted(snap)
        if halted:
            return True, why
        if self.close_only:
            return True, "tier is close-only"
        if snap.day_start_equity and snap.equity <= snap.day_start_equity * (1 - self.limits.max_daily_loss_pct):
            return True, f"daily loss limit ({self.limits.max_daily_loss_pct:.0%})"
        return False, ""

    # -- per-order checks ---------------------------------------------------------------
    def pre_trade(self, intents: list[OrderIntent], snap: PortfolioSnapshot) -> list[RiskVerdict]:
        lim = self.limits
        verdicts: list[RiskVerdict] = []
        blocked, why = self.buys_blocked(snap)
        n_positions = len(snap.positions)
        gross = snap.gross_exposure
        crypto_exp = snap.exposure_of("crypto")
        heat = snap.open_risk
        cash = snap.cash
        entries = snap.entries_today

        for it in intents:
            reasons: list[str] = []
            if it.side == "sell":
                if it.symbol not in snap.positions:
                    verdicts.append(RiskVerdict(it, False, None, ("no such position",)))
                    continue
                verdicts.append(RiskVerdict(it, True, it, ("exit",)))
                continue

            if blocked:
                verdicts.append(RiskVerdict(it, False, None, (why,)))
                continue
            if it.symbol in snap.positions:
                verdicts.append(RiskVerdict(it, False, None, ("already held",)))
                continue
            if it.price <= 0 or it.notional <= 0 or it.qty <= 0:
                verdicts.append(RiskVerdict(it, False, None, ("bad price/size",)))
                continue
            if it.stop_price is None or it.stop_price >= it.price:
                verdicts.append(RiskVerdict(it, False, None, ("buy without a protective stop",)))
                continue
            if n_positions >= lim.max_positions:
                verdicts.append(RiskVerdict(it, False, None, ("max positions",)))
                continue
            if entries >= lim.max_new_entries_per_day:
                verdicts.append(RiskVerdict(it, False, None, ("max new entries today",)))
                continue
            if it.asset_class == "crypto":
                n_crypto = sum(1 for p in snap.positions.values() if p.asset_class == "crypto")
                if n_crypto >= lim.crypto_max_positions:
                    verdicts.append(RiskVerdict(it, False, None, ("crypto max positions",)))
                    continue

            notional = it.notional * self.tier_risk_multiplier
            if self.tier_risk_multiplier != 1.0:
                reasons.append(f"tier multiplier x{self.tier_risk_multiplier:g}")

            # Clip to caps rather than reject outright.
            cap_pos = snap.equity * lim.max_position_pct(it.asset_class)
            if notional > cap_pos:
                notional, _ = cap_pos, reasons.append("clipped: position cap")
            room_gross = max(lim.max_gross_exposure - gross, 0.0) * snap.equity
            if notional > room_gross:
                notional, _ = room_gross, reasons.append("clipped: gross exposure")
            if it.asset_class == "crypto":
                room_crypto = max(lim.crypto_sleeve_pct - crypto_exp, 0.0) * snap.equity
                if notional > room_crypto:
                    notional, _ = room_crypto, reasons.append("clipped: crypto sleeve")
            r_per_share = it.price - it.stop_price
            room_heat = max(lim.max_open_risk_pct - heat, 0.0) * snap.equity
            if (notional / it.price) * r_per_share > room_heat:
                notional, _ = room_heat / r_per_share * it.price, reasons.append("clipped: open-risk heat")
            if lim.max_leverage <= 1.0 and notional > cash:
                notional, _ = cash, reasons.append("clipped: cash available")
            if notional < lim.min_order_notional_usd:
                verdicts.append(RiskVerdict(it, False, None, (*reasons, "below min notional")))
                continue

            adj = replace(it, notional=round(notional, 2), qty=notional / it.price)
            verdicts.append(RiskVerdict(it, True, adj, tuple(reasons) or ("ok",)))
            n_positions += 1
            entries += 1
            gross += notional / snap.equity
            heat += (notional / it.price) * r_per_share / snap.equity
            cash -= notional
            if it.asset_class == "crypto":
                crypto_exp += notional / snap.equity
        return verdicts
