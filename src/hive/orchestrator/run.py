"""DailyRun: one post-close cycle for one portfolio (primary or shadow)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from hive.agents.llm import LLMClient
from hive.agents.pipeline import CandidateContext, CandidatePipeline, features_payload
from hive.config import StrategyConfig
from hive.data.bars import as_of
from hive.data.store import BarStore
from hive.data.universe import PointInTimeUniverse
from hive.execution.base import Broker
from hive.orchestrator.book import OpenPosition, PositionBook
from hive.orchestrator.journal import Journal
from hive.risk.engine import OrderIntent, PortfolioSnapshot, PositionInfo, RiskEngine
from hive.signals.features import compute_features, initial_stop, rank_candidates
from hive.signals.regime import regime_at, regime_frame
from hive.signals.sizing import size_position
from hive.survival.ledger import SurvivalLedger

log = logging.getLogger(__name__)


class SystemDeadError(RuntimeError):
    pass


@dataclass
class RunSummary:
    run_date: str
    label: str
    equity_gross: float
    equity_net: float
    tier: str
    regime: dict[str, Any]
    exits: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    ai_cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)


class DailyRun:
    def __init__(
        self,
        *,
        label: str,
        run_date: date,
        cfg: StrategyConfig,
        store: BarStore,
        broker: Broker,
        book: PositionBook,
        journal: Journal,
        ledger: SurvivalLedger | None,
        llm: LLMClient | None,
        universe: PointInTimeUniverse | None,
        asset_classes: dict[str, str],
        kill_switch: bool = False,
        cheap_model: str = "claude-haiku-5-5",
        decision_model: str = "claude-opus-5-5",
        effort: str = "medium",
    ):
        self.label = label
        self.run_date = pd.Timestamp(run_date)
        self.cfg = cfg
        self.store = store
        self.broker = broker
        self.book = book
        self.journal = journal
        self.ledger = ledger
        self.llm = llm
        self.universe = universe
        self.asset_classes = asset_classes
        self.kill_switch = kill_switch
        self.cheap_model = cheap_model
        self.decision_model = decision_model
        self.effort = effort
        self._bars_cache: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ helpers
    def bars(self, symbol: str) -> pd.DataFrame | None:
        if symbol not in self._bars_cache:
            ac = self.asset_classes.get(symbol, "stock")
            b = self.store.get(ac, symbol)
            self._bars_cache[symbol] = as_of(b, self.run_date) if b is not None else None
        return self._bars_cache[symbol]

    def _features(self, symbol: str) -> pd.DataFrame | None:
        b = self.bars(symbol)
        if b is None or len(b) < self.cfg.universe.min_history_bars:
            return None
        return compute_features(b, self.cfg.rules_for(self.asset_classes.get(symbol, "stock")))

    def _snapshot(self, equity: float, cash: float, entries_today: int = 0) -> PortfolioSnapshot:
        infos = {}
        for s, p in self.book.positions.items():
            b = self.bars(s)
            px = float(b["close"].iloc[-1]) if b is not None and len(b) else p.entry_price
            infos[s] = PositionInfo(s, p.asset_class, p.qty, px, p.stop_price)
        marks = [m for _, m in self.ledger.equity_marks()] if self.ledger else []
        return PortfolioSnapshot(
            equity=equity, cash=cash, positions=infos,
            day_start_equity=marks[-1] if marks else None,
            peak_equity=max(marks) if marks else None, entries_today=entries_today,
        )

    # ------------------------------------------------------------------ main
    def execute(self) -> RunSummary:
        rd = self.run_date.date().isoformat()
        equity = float(self.broker.equity())
        summary = RunSummary(rd, self.label, equity, equity, "n/a", {})
        self.journal.write("run_start", {"label": self.label, "date": rd, "equity": equity})

        if self.kill_switch:
            summary.notes.append("kill switch engaged: no orders")
            self.journal.write("kill_switch", {})
            return summary

        # --- survival ---------------------------------------------------------
        budget = 0.0
        tier_mult, close_only = 1.0, False
        if self.ledger is not None:
            st = self.ledger.status(equity, on=self.run_date.date())
            summary.equity_net, summary.tier = st.equity_net, st.tier
            tier = self.ledger.tier(st.equity_net)
            self.ledger.record_tier(tier)
            tier_mult, close_only, budget = tier.risk_multiplier, tier.close_only, st.budget_remaining
            self.journal.write("survival", {"equity_net": st.equity_net, "tier": st.tier,
                                            "budget_today": st.budget_today, "runway_days": st.runway_days})
            if st.dead:
                self._flatten_all("death")
                self.journal.write("death", {"reason": st.death_reason})
                raise SystemDeadError(st.death_reason)

        # --- regime -------------------------------------------------------------
        bench_sym = self.cfg.universe.regime_stock_symbol
        bench = self.store.get("etf", bench_sym)
        vix_bars = self.store.get("index", "VIX")
        if bench is None:
            raise FileNotFoundError(f"benchmark {bench_sym} missing from cache")
        reg = regime_at(
            regime_frame(as_of(bench, self.run_date), self.cfg.regime,
                         vix_bars["close"] if vix_bars is not None else None),
            self.run_date,
        )
        summary.regime = {k: (bool(v) if isinstance(v, (bool,)) else v) for k, v in reg.to_dict().items()}
        self.journal.write("regime", summary.regime)

        # --- manage open positions ---------------------------------------------
        risk = RiskEngine(self.cfg.risk, kill_switch=lambda: self.kill_switch,
                          tier_risk_multiplier=tier_mult, close_only=close_only)
        self._sync_book_with_broker()
        exits = self._manage_positions()
        summary.exits = exits

        # --- candidates ----------------------------------------------------------
        cash = float(self.broker.cash())
        snap = self._snapshot(equity, cash)
        blocked, why = risk.buys_blocked(snap)
        cands = pd.DataFrame()
        if not blocked:
            cands = self._candidates(reg)
        else:
            summary.notes.append(f"buys blocked: {why}")
        summary.candidates = cands.head(self.cfg.universe.max_candidates_for_llm)[
            ["symbol", "score", "close", "atr", "pct_of_high", "z_reset", "pullback_atr"]
        ].round(4).to_dict("records") if not cands.empty else []
        self.journal.write("candidates", {"n": len(cands), "top": summary.candidates})

        # --- decisions (LLM filter or rules-only) ---------------------------------
        actions: dict[str, tuple[str, str]] = {}  # symbol -> (action, invalidation)
        top = cands.head(self.cfg.universe.max_candidates_for_llm)
        if not top.empty:
            if self.llm is not None and self.llm.available:
                self.llm.budget_usd = self.llm.spent_usd + budget
                pipe = CandidatePipeline(self.llm, cheap_model=self.cheap_model,
                                         decision_model=self.decision_model, effort="low",
                                         pm_effort=self.effort)
                ctxs = [CandidateContext(symbol=r["symbol"], asset_class=self.asset_classes.get(r["symbol"], "stock"),
                                         features=features_payload(r)) for _, r in top.iterrows()]
                evals = pipe.evaluate(ctxs, {"equity": equity, "cash": cash, "n_positions": len(self.book),
                                             "regime": summary.regime})
                for ev in evals:
                    actions[ev.symbol] = (ev.decision.action, ev.decision.invalidation_condition)
                    summary.ai_cost_usd += ev.cost_usd
                    summary.decisions.append({"symbol": ev.symbol, "action": ev.decision.action,
                                              "invalidation": ev.decision.invalidation_condition,
                                              "confidence": ev.decision.confidence,
                                              "rationale": ev.decision.rationale, "cost_usd": ev.cost_usd})
                if summary.ai_cost_usd > 0 and self.ledger is not None:
                    self.ledger.append("ai_cost", summary.ai_cost_usd, {"label": self.label, "date": rd})
            else:
                for _, r in top.iterrows():
                    actions[r["symbol"]] = ("approve", "none")
                    summary.decisions.append({"symbol": r["symbol"], "action": "approve",
                                              "invalidation": "none", "confidence": None,
                                              "rationale": "rules-only", "cost_usd": 0.0})
            self.journal.write("decisions", {"decisions": summary.decisions})

        # --- sizing, risk, orders -------------------------------------------------------
        intents: list[OrderIntent] = []
        mult = float(reg["risk_multiplier"])
        for _, r in top.iterrows():
            sym = r["symbol"]
            action, inval = actions.get(sym, ("veto", "none"))
            if action == "veto":
                continue
            ac = self.asset_classes.get(sym, "stock")
            rules = self.cfg.rules_for(ac)
            price = float(r["close"])
            stop = initial_stop(price, r, rules)
            d = size_position(equity=equity, price=price, stop_price=stop, risk=self.cfg.risk,
                              asset_class=ac, risk_multiplier=mult * (0.5 if action == "reduce_half" else 1.0))
            if d.notional <= 0:
                continue
            intents.append(OrderIntent(sym, ac, "buy", d.notional, d.qty, price, stop,
                                       reason=f"entry:{action}:{inval}", source=self.label))
        verdicts = risk.pre_trade(intents, snap)
        self.journal.write("risk", {"verdicts": [{"symbol": v.intent.symbol, "approved": v.approved,
                                                  "reasons": list(v.reasons),
                                                  "notional": v.adjusted.notional if v.adjusted else 0.0}
                                                 for v in verdicts]})
        for v in verdicts:
            if not v.approved or v.adjusted is None:
                continue
            it = v.adjusted
            res = self.broker.submit_market(it.symbol, "buy", notional=it.notional, asset_class=it.asset_class,
                                            client_id=f"{self.label}-{rd}-{it.symbol}")
            order = {"symbol": it.symbol, "side": "buy", "notional": it.notional, "ok": res.ok,
                     "fill_price": res.fill_price, "filled_qty": res.filled_qty, "message": res.message}
            summary.orders.append(order)
            if res.ok:
                fill_px = res.fill_price or it.price
                qty = res.filled_qty or (it.notional / fill_px)
                stop_px = fill_px - (it.price - it.stop_price)
                stop_res = self.broker.submit_stop(it.symbol, qty, stop_px, asset_class=it.asset_class)
                self.book.add(OpenPosition(
                    symbol=it.symbol, asset_class=it.asset_class, qty=qty, entry_price=fill_px,
                    entry_date=rd, r_per_share=it.price - it.stop_price, stop_price=stop_px,
                    highest_close=fill_px, invalidation_condition=it.reason.split(":")[-1],
                    source=self.label, stop_order_id=stop_res.order_id,
                ))
            self.journal.write("order", order)

        # --- ledger & positions ---------------------------------------------------------------
        equity_end = float(self.broker.equity())
        summary.equity_gross = equity_end
        if self.ledger is not None:
            self.ledger.append("equity_mark", equity_end, {"label": self.label, "date": rd})
            summary.equity_net = self.ledger.status(equity_end, on=self.run_date.date()).equity_net
        else:
            summary.equity_net = equity_end
        summary.positions = [
            {"symbol": p.symbol, "qty": round(p.qty, 6), "entry": p.entry_price, "stop": round(p.stop_price, 4),
             "bars_held": p.bars_held, "source": p.source}
            for p in self.book.positions.values()
        ]
        self.journal.write("run_end", {"equity": equity_end, "equity_net": summary.equity_net,
                                       "n_positions": len(self.book), "ai_cost_usd": summary.ai_cost_usd})
        return summary

    # ------------------------------------------------------------------ pieces
    def _sync_book_with_broker(self) -> None:
        """Reconcile: a position the broker no longer holds (stop filled) leaves the book."""
        held = self.broker.positions()
        for sym in list(self.book.positions):
            if sym not in held:
                self.journal.write("reconcile", {"symbol": sym, "event": "closed_at_broker"})
                self.book.remove(sym)

    def _manage_positions(self) -> list[dict[str, Any]]:
        exits: list[dict[str, Any]] = []
        for sym, pos in list(self.book.positions.items()):
            f = self._features(sym)
            if f is None or self.run_date not in f.index:
                continue
            row = f.loc[self.run_date]
            rules = self.cfg.rules_for(pos.asset_class)
            close = float(row["close"])
            pos.bars_held += 1
            pos.highest_close = max(pos.highest_close, close)
            atr = float(row["atr"]) if pd.notna(row["atr"]) else None
            reason = None
            if close <= pos.stop_price:
                reason = "stop"
            elif row["below_mid_streak"] >= rules.exit_closes_below_mid or bool(row["below_long"]):
                reason = "trend_break"
            elif pos.bars_held == rules.failure_stop_bars and close < pos.entry_price + rules.failure_stop_min_r * pos.r_per_share:
                reason = "failure_stop"
            elif pos.bars_held >= rules.review_bars and not bool(row["template_ok"]):
                reason = "review"
            elif pos.bars_held >= rules.max_hold_bars:
                reason = "max_hold"
            elif pos.invalidation_condition == "close_below_ma50_2d" and row["below_mid_streak"] >= 2:
                reason = "invalidation"
            elif pos.invalidation_condition == "close_below_ma200" and bool(row["below_long"]):
                reason = "invalidation"
            if reason:
                res = self.broker.close_position(sym)
                exits.append({"symbol": sym, "reason": reason, "ok": res.ok, "price": res.fill_price,
                              "bars_held": pos.bars_held, "message": res.message})
                self.journal.write("exit", exits[-1])
                if res.ok:
                    self.book.remove(sym)
                continue
            if atr:
                mult = rules.trail_atr if pos.bars_held < rules.trail_tighten_after_bars else rules.trail_atr_late
                new_stop = max(pos.stop_price, pos.highest_close - atr * mult)
                if new_stop > pos.stop_price + 1e-9:
                    self.broker.cancel_open_orders(sym)
                    sr = self.broker.submit_stop(sym, pos.qty, new_stop, asset_class=pos.asset_class)
                    pos.stop_price, pos.stop_order_id = new_stop, sr.order_id
                    self.journal.write("stop_ratchet", {"symbol": sym, "stop": new_stop})
            self.book.save()
        return exits

    def _candidates(self, reg: pd.Series) -> pd.DataFrame:
        allowed = self.universe.members(self.run_date) if self.universe is not None else None
        rows = []
        for sym, ac in self.asset_classes.items():
            if sym in self.book or ac == "index":
                continue
            if ac == "stock" and not bool(reg["allow_new_stock"]):
                continue
            if ac == "etf" and not bool(reg["allow_new_etf"]):
                continue
            if allowed is not None and ac == "stock" and sym not in allowed:
                continue
            f = self._features(sym)
            if f is None or self.run_date not in f.index:
                continue
            row = f.loc[self.run_date]
            if bool(row["entry_signal"]) and pd.notna(row["atr"]) and row["atr"] > 0:
                if ac != "crypto" and row["raw_close"] < self.cfg.universe.min_price_usd:
                    continue
                rows.append({"symbol": sym, **row.to_dict()})
        return rank_candidates(pd.DataFrame(rows)) if rows else pd.DataFrame()

    def _flatten_all(self, reason: str) -> None:
        self.broker.cancel_open_orders()
        for sym in list(self.book.positions):
            res = self.broker.close_position(sym)
            self.journal.write("exit", {"symbol": sym, "reason": reason, "ok": res.ok})
            if res.ok:
                self.book.remove(sym)
