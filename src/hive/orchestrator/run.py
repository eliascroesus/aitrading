"""DailyRun: one post-close cycle for one portfolio (primary or shadow).

Units: every price the broker sees (entries, stops, marks) is in **dollars**
(the unadjusted close). Features are computed on the total-return series, so
each row carries ``k = raw_close / close`` to convert ATRs and swing lows.
"""

from __future__ import annotations

import glob
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from hive.agents import prompts
from hive.agents.llm import BudgetExceededError, LLMClient
from hive.agents.pipeline import CandidateContext, CandidatePipeline, bounded_view, features_payload
from hive.config import StrategyConfig
from hive.data.bars import as_of
from hive.data.quality import max_abs_return_for, truncate_at_first_anomaly
from hive.data.store import BarStore
from hive.data.universe import PointInTimeUniverse
from hive.execution.base import Broker
from hive.orchestrator.book import OpenPosition, PositionBook
from hive.orchestrator.journal import Journal
from hive.risk.engine import OrderIntent, PortfolioSnapshot, PositionInfo, RiskEngine
from hive.signals.features import compute_features, initial_stop, rank_candidates
from hive.signals.regime import class_multipliers, regime_at, regime_frame
from hive.signals.sizing import edge_risk_pct, size_position
from hive.survival.ledger import SurvivalLedger

log = logging.getLogger(__name__)

# Process-wide feature cache shared by the primary, the shadow and every child
# within one daily cycle: same symbol, same as-of date, same rule set -> same
# features. ``clear_feature_cache()`` is called at the start of each cycle.
_FEATURE_CACHE: dict[tuple, pd.DataFrame | None] = {}


def clear_feature_cache() -> None:
    _FEATURE_CACHE.clear()


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


PENDING_GRACE_DAYS = 4   # a Friday-night order fills Monday; holidays add a day


def keep_pending(pos: OpenPosition, pending_symbols: set[str], pending_ids: set[str], run_date: date,
                 grace_days: int = PENDING_GRACE_DAYS) -> bool:
    """A queued entry stays in the book while its order is still open at the broker, or
    for ``grace_days`` calendar days after it was placed when the broker cannot say."""
    if pos.symbol in pending_symbols or str(pos.meta.get("order_id", "")) in pending_ids:
        return True
    try:
        placed = date.fromisoformat(str(pos.entry_date))
    except ValueError:
        return False
    return (run_date - placed).days <= grace_days


def closed_r_multiples(journal_dir: Path, limit: int, *, book: str = "") -> list[float]:
    """The book's last ``limit`` closed trades in R, oldest first, from its journal: managed
    exits (``r_multiple``) and positions a resting stop closed (``r_multiple_est``). Every book
    writes to the same directory, one file a day: ``<date>.jsonl`` for the primary and
    ``<date><book>.jsonl`` for the others (``-shadow``, ``-child-<id>``); only this book's count."""
    out: list[float] = []
    for f in sorted(Path(journal_dir).glob(f"????-??-??{glob.escape(book)}.jsonl"), reverse=True):
        day: list[float] = []
        for e in Journal.read(f):
            pl = e.get("payload") or {}
            r = pl.get("r_multiple") if e.get("kind") == "exit" else (
                pl.get("r_multiple_est") if e.get("kind") == "reconcile" else None)
            if r is not None:
                day.append(float(r))
        out = day + out
        if len(out) >= limit:
            break
    return out[-limit:] if limit > 0 else []


class BankrollMismatchError(RuntimeError):
    """The broker's equity is not the study's bankroll (an un-reset paper account, usually)."""


def check_bankroll(equity: float, basis: float, marks: list[float], *, broker: str = "broker") -> None:
    """Refuse to size on money the study does not have.

    A paper account left at its $100,000 default would otherwise trade a thousand
    times the bankroll. Genuine growth shows up in earlier equity marks, so only an
    unexplained jump above twice the capital basis trips this.
    """
    if basis > 0 and equity > 2 * basis and (not marks or equity > 2 * max(marks)):
        raise BankrollMismatchError(
            f"{broker} reports ${equity:,.2f} but the study bankroll is ${basis:,.2f}: reset the paper account "
            f"to ${basis:,.0f} (then regenerate its keys) or record a capital_in ledger entry. See docs/SETUP.md.")


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
        news_fn: Callable[[str], list[tuple[str, str]]] | None = None,
        fundamentals_fn: Callable[[str], dict[str, Any]] | None = None,
        company_names: dict[str, list[str]] | None = None,
        desk_reports: list[dict[str, Any]] | None = None,
        live: bool = False,
    ):
        self.live = live
        self.desk_reports = desk_reports or []
        self.news_fn = news_fn
        self.fundamentals_fn = fundamentals_fn
        self.company_names = company_names or {}
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
        self._bars_cache: dict[tuple[str, bool], pd.DataFrame | None] = {}
        self._features_cache: dict[tuple[str, bool], pd.DataFrame | None] = {}

    # ------------------------------------------------------------------ data
    def bars(self, symbol: str, *, for_candidate: bool = True) -> pd.DataFrame | None:
        """Bars as of the run date. Candidates get the quality-truncated series;
        open positions get the raw series so a genuine crash still triggers exits."""
        key = (symbol, for_candidate)
        if key not in self._bars_cache:
            ac = self.asset_classes.get(symbol, "stock")
            b = self.store.get(ac, symbol)
            if b is not None:
                b = as_of(b, self.run_date)
                if for_candidate:
                    b, anomaly = truncate_at_first_anomaly(b, max_abs_return=max_abs_return_for(ac))
                    if anomaly is not None:
                        self.journal.write("data_anomaly", {"symbol": symbol, "date": str(anomaly.date.date()),
                                                            "kind": anomaly.kind, "value": anomaly.value})
            self._bars_cache[key] = b
        return self._bars_cache[key]

    def _features(self, symbol: str, *, for_candidate: bool = True) -> pd.DataFrame | None:
        key = (symbol, for_candidate)
        if key not in self._features_cache:
            ac = self.asset_classes.get(symbol, "stock")
            rules = self.cfg.rules_for(ac)
            gkey = (ac, symbol, self.run_date, for_candidate, rules.model_dump_json())
            if gkey not in _FEATURE_CACHE:
                b = self.bars(symbol, for_candidate=for_candidate)
                _FEATURE_CACHE[gkey] = (None if b is None or len(b) < self.cfg.universe.min_history_bars
                                        else compute_features(b, rules))
            self._features_cache[key] = _FEATURE_CACHE[gkey]
        return self._features_cache[key]

    def raw_price(self, symbol: str, column: str = "close") -> float | None:
        b = self.bars(symbol, for_candidate=False)
        if b is None or len(b) == 0 or b.index[-1] != self.run_date:
            return None
        return float(b[column].iloc[-1])

    @staticmethod
    def _k(row: pd.Series) -> float:
        """Dollar-per-adjusted-unit conversion for a feature row."""
        c, rc = float(row["close"]), float(row["raw_close"])
        return rc / c if c > 0 and rc > 0 else 1.0

    def _snapshot(self, equity: float, cash: float, status: Any | None) -> PortfolioSnapshot:
        infos = {}
        for s, p in self.book.positions.items():
            px = self.raw_price(s) or p.entry_price
            infos[s] = PositionInfo(s, p.asset_class, p.qty, px, p.stop_price)
        rd = self.run_date.date().isoformat()
        entries_today = sum(1 for p in self.book.positions.values() if p.entry_date == rd)
        marks = [m for _, m in self.ledger.equity_marks()] if self.ledger else []
        return PortfolioSnapshot(
            equity=equity, cash=cash, positions=infos,
            day_start_equity=marks[-1] if marks else None,
            peak_equity=(status.peak_equity if status is not None else None),
            entries_today=entries_today,
        )

    # ------------------------------------------------------------------ main
    def execute(self) -> RunSummary:
        rd = self.run_date.date().isoformat()
        # Paper brokers mirror the backtester: queued orders fill at today's open,
        # resting stops are evaluated against today's low.
        if hasattr(self.broker, "process_pending"):
            for r in self.broker.process_pending(lambda s: self.raw_price(s, "open")):
                self.journal.write("fill", {"symbol": r.order_id, "qty": r.filled_qty, "price": r.fill_price,
                                            "message": r.message})
        if hasattr(self.broker, "check_stops"):
            for r in self.broker.check_stops(lambda s: self.raw_price(s, "low"), lambda s: self.raw_price(s, "open")):
                self.journal.write("stop_fill", {"qty": r.filled_qty, "price": r.fill_price})

        equity = float(self.broker.equity())
        if self.ledger is not None:
            check_bankroll(equity, self.cfg.survival.starting_bankroll_usd + self.ledger.capital_in_total(),
                           [m for _, m in self.ledger.equity_marks()], broker=getattr(self.broker, "name", "broker"))
        summary = RunSummary(rd, self.label, equity, equity, "n/a", {})
        self.journal.write("run_start", {"label": self.label, "date": rd, "equity": equity})

        if self.kill_switch:
            summary.notes.append("kill switch engaged: no orders")
            self.journal.write("kill_switch", {})
            if self.ledger is not None:
                self.ledger.append("equity_mark", equity, {"label": self.label, "date": rd})
            return summary

        # --- survival ---------------------------------------------------------
        budget = 0.0
        tier_mult, close_only = 1.0, False
        status = None
        if self.ledger is not None:
            status = self.ledger.status(equity, on=self.run_date.date())
            summary.equity_net, summary.tier = status.equity_net, status.tier
            tier = self.ledger.tier(status.equity_net)
            self.ledger.record_tier(tier)
            tier_mult, close_only, budget = tier.risk_multiplier, tier.close_only, status.budget_remaining
            self.journal.write("survival", {"equity_net": status.equity_net, "tier": status.tier,
                                            "budget_today": status.budget_today, "runway_days": status.runway_days,
                                            "in_halt_cooldown": status.in_halt_cooldown})
            if status.dead:
                self._flatten_all("death")
                if self.ledger.death_recorded() is None:
                    self.ledger.record_death(self.run_date.date(), status.death_reason, equity)
                self.journal.write("death", {"reason": status.death_reason})
                raise SystemDeadError(status.death_reason)
            if status.in_halt_cooldown and (self.live or self.cfg.risk.halt_paper):
                close_only = True
                summary.notes.append("drawdown halt cooldown: close-only")

        # The day's research budget is granted once, here, and shared by the news desk
        # (open positions) and the candidate pipeline; nothing re-grants it later.
        self._news_pipe = None
        if self.llm is not None and self.llm.available:
            self.llm.budget_usd = self.llm.spent_usd + budget
            if budget > 0 and self.news_fn is not None:
                self._news_pipe = CandidatePipeline(self.llm, cheap_model=self.cheap_model,
                                                    decision_model=self.decision_model, effort="low",
                                                    pm_effort=self.effort)

        # --- regime -------------------------------------------------------------
        bench = self.store.get("etf", self.cfg.universe.regime_stock_symbol)
        vix_bars = self.store.get("index", "VIX")
        if bench is None:
            raise FileNotFoundError(f"benchmark {self.cfg.universe.regime_stock_symbol} missing from cache")
        reg = regime_at(regime_frame(as_of(bench, self.run_date), self.cfg.regime,
                                     vix_bars["close"] if vix_bars is not None else None), self.run_date)
        mult = class_multipliers(reg)
        btc = self.store.get("crypto", self.cfg.universe.regime_crypto_symbol)
        if btc is not None and len(as_of(btc, self.run_date)) > self.cfg.regime.ma_long:
            reg_c = regime_at(regime_frame(as_of(btc, self.run_date), self.cfg.regime), self.run_date)
            mult["crypto"] = class_multipliers(reg_c)["stock"]
        summary.regime = {k: (bool(v) if isinstance(v, bool) else v) for k, v in reg.to_dict().items()}
        summary.regime["class_multipliers"] = mult
        self.journal.write("regime", summary.regime)

        # --- manage open positions ---------------------------------------------
        self._sync_book_with_broker()
        halt_on = self.live or self.cfg.risk.halt_paper     # the drawdown halt protects real money
        risk = RiskEngine(self.cfg.risk, kill_switch=lambda: self.kill_switch,
                          tier_risk_multiplier=tier_mult, close_only=close_only, drawdown_halt=halt_on)
        snap0 = self._snapshot(equity, float(self.broker.cash()), status)
        halted, why_halt = risk.halted(snap0)
        if halted and self.ledger is not None and not (status and status.in_halt_cooldown):
            self._flatten_all("dd_halt")
            self.ledger.record_halt(self.run_date.date(), self.cfg.risk.halt_cooldown_bars, equity)
            summary.notes.append(f"drawdown halt: liquidated, cooldown {self.cfg.risk.halt_cooldown_bars}d")
            close_only = True
            risk = RiskEngine(self.cfg.risk, kill_switch=lambda: self.kill_switch,
                              tier_risk_multiplier=tier_mult, close_only=True, drawdown_halt=halt_on)
        summary.exits = self._manage_positions(reg)
        self._ensure_stops()

        # --- candidates ----------------------------------------------------------
        cash = float(self.broker.cash())
        snap = self._snapshot(equity, cash, status)
        blocked, why = risk.buys_blocked(snap)
        if not blocked and any(e.get("kind") == "candidates" for e in Journal.read(self.journal.path)):
            blocked, why = True, "this bar was already processed by an earlier run today"
        cands = pd.DataFrame()
        if not blocked:
            cands = self._candidates(mult)
        else:
            summary.notes.append(f"buys blocked: {why}")
        summary.candidates = cands.head(self.cfg.universe.max_candidates_for_llm)[
            ["symbol", "score", "raw_close", "atr_pct", "pct_of_high", "z_reset", "pullback_atr"]
        ].round(4).to_dict("records") if not cands.empty else []
        self.journal.write("candidates", {"n": len(cands), "top": summary.candidates})

        # --- decisions (LLM filter or rules-only) ---------------------------------
        actions: dict[str, tuple[str, str]] = {}  # symbol -> (action, invalidation)
        top = cands.head(self.cfg.universe.max_candidates_for_llm)
        fundamentals: dict[str, dict[str, Any]] = {}
        if not top.empty and self.fundamentals_fn is not None:
            keep = []
            for _, r in top.iterrows():
                sym = r["symbol"]
                rules = self.cfg.rules_for(self.asset_classes.get(sym, "stock"))
                try:
                    facts = self.fundamentals_fn(sym) or {}
                except Exception as exc:  # a data hiccup must never crash the run
                    self.journal.write("fundamentals_error", {"symbol": sym, "error": str(exc)[:200]})
                    facts = {}
                fundamentals[sym] = facts
                eid = facts.get("earnings_in_days")
                if isinstance(eid, int) and 0 <= eid <= rules.earnings_blackout_bars:
                    self.journal.write("blackout", {"symbol": sym, "earnings_in_days": eid})
                    continue
                keep.append(sym)
            top = top[top["symbol"].isin(keep)]
        spent_before = self.llm.spent_usd if self.llm is not None else 0.0
        if not top.empty:
            if self.llm is not None and self.llm.available:
                pipe = CandidatePipeline(self.llm, cheap_model=self.cheap_model,
                                         decision_model=self.decision_model, effort="low",
                                         pm_effort=self.effort)
                ctxs = []
                for _, r in top.iterrows():
                    sym = r["symbol"]
                    headlines: list[tuple[str, str]] = []
                    if self.news_fn is not None:
                        try:
                            headlines = self.news_fn(sym)
                        except Exception as exc:
                            self.journal.write("news_error", {"symbol": sym, "error": str(exc)[:200]})
                    ctxs.append(CandidateContext(
                        symbol=sym, asset_class=self.asset_classes.get(sym, "stock"),
                        features=features_payload(r), fundamentals=fundamentals.get(sym, {}),
                        headlines=headlines, company_names=self.company_names.get(sym, []),
                    ))
                evals = pipe.evaluate(ctxs, {"equity": equity, "cash": cash, "n_positions": len(self.book),
                                             "regime_risk_multiplier": float(reg["risk_multiplier"])},
                                      firm_status=prompts.firm_status(
                                          status.__dict__ if status is not None else None, self.cfg.survival.tiers)
                                      + (("\nDesk reports from the firm's monitors (bounded): "
                                          + json.dumps(self.desk_reports, default=str)[:3000]) if self.desk_reports else ""))
                self.journal.write("desk_notes", {"notes": [
                    {"symbol": ev.symbol, "news": bounded_view(ev.news), "fundamental": bounded_view(ev.fundamental),
                     "technical": bounded_view(ev.technical), "sentiment": bounded_view(ev.sentiment),
                     "debate": bounded_view(ev.debate), "action": ev.decision.action,
                     "confidence": ev.decision.confidence, "cost_usd": ev.cost_usd} for ev in evals],
                    "portfolio_note": getattr(pipe, "last_portfolio_note", "")})
                for ev in evals:
                    actions[ev.symbol] = (ev.decision.action, ev.decision.invalidation_condition)
                    summary.decisions.append({"symbol": ev.symbol, "action": ev.decision.action,
                                              "invalidation": ev.decision.invalidation_condition,
                                              "confidence": ev.decision.confidence,
                                              "rationale": ev.decision.rationale, "cost_usd": ev.cost_usd,
                                              "note": ev.note})
            else:
                for _, r in top.iterrows():
                    actions[r["symbol"]] = ("approve", "none")
                    summary.decisions.append({"symbol": r["symbol"], "action": "approve",
                                              "invalidation": "none", "confidence": None,
                                              "rationale": "rules-only", "cost_usd": 0.0, "note": ""})
            self.journal.write("decisions", {"decisions": summary.decisions})
        # Book what the LLM actually spent (not what succeeded), so nothing leaks.
        if self.llm is not None:
            summary.ai_cost_usd = max(self.llm.spent_usd - spent_before, 0.0)
            if summary.ai_cost_usd > 0 and self.ledger is not None:
                self.ledger.append("ai_cost", summary.ai_cost_usd, {"label": self.label, "date": rd})

        # --- sizing, risk, orders -------------------------------------------------------
        intents: list[OrderIntent] = []
        port_vol = self.ledger.portfolio_vol_ann() if self.ledger is not None else None
        risk_pct = edge_risk_pct(closed_r_multiples(self.journal.path.parent, self.cfg.risk.edge_lookback_trades,
                                                    book=self.journal.path.stem[len(rd):]), self.cfg.risk)
        if self.cfg.risk.sizing_mode == "edge":
            self.journal.write("sizing", {"mode": "edge", "risk_pct": risk_pct})
        for _, r in top.iterrows():
            sym = r["symbol"]
            action, inval = actions.get(sym, ("veto", "none"))
            if action == "veto":
                continue
            ac = self.asset_classes.get(sym, "stock")
            if hasattr(self.broker, "supports") and not self.broker.supports(sym, ac):
                self.journal.write("untradable", {"symbol": sym, "asset_class": ac,
                                                  "broker": getattr(self.broker, "name", "")})
                continue
            rules = self.cfg.rules_for(ac)
            k = self._k(r)
            price = float(r["raw_close"])
            stop = initial_stop(price, pd.Series({"atr": float(r["atr"]) * k, "swing_low": float(r["swing_low"]) * k}),
                                rules)
            cap = self.cfg.risk.max_position_pct_crypto_alt if sym in self.cfg.universe.crypto_alt_symbols else None
            d = size_position(equity=equity, price=price, stop_price=stop, risk=self.cfg.risk,
                              asset_class=ac, risk_multiplier=mult.get(ac, 0.0), portfolio_vol_ann=port_vol,
                              cap_pct=cap, risk_pct=risk_pct)
            if d.notional <= 0:
                continue
            it = OrderIntent(sym, ac, "buy", d.notional, d.qty, price, stop,
                             reason=f"entry:{action}:{inval}", source=self.label)
            it = RiskEngine.apply_llm_action(it, action)  # reduce_half halves after sizing
            if it is not None:
                intents.append(it)
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
                confirmed = res.filled_qty > 0 and res.fill_price > 0
                fill_px = res.fill_price if confirmed else it.price
                qty = res.filled_qty if confirmed else it.notional / fill_px
                r_ps = it.price - it.stop_price
                pos = OpenPosition(
                    symbol=it.symbol, asset_class=it.asset_class, qty=qty, entry_price=fill_px,
                    entry_date=rd, r_per_share=r_ps, stop_price=fill_px - r_ps, highest_close=fill_px,
                    invalidation_condition=it.reason.split(":")[-1], source=self.label,
                    meta={"fill_pending": not confirmed, "order_id": res.order_id},
                )
                self.book.add(pos)
                if confirmed:
                    self._arm_stop(pos)
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
             "bars_held": p.bars_held, "source": p.source, "stop_armed": bool(p.stop_order_id),
             "fill_pending": bool(p.meta.get("fill_pending"))}
            for p in self.book.positions.values()
        ]
        self.journal.write("run_end", {"equity": equity_end, "equity_net": summary.equity_net,
                                       "n_positions": len(self.book), "ai_cost_usd": summary.ai_cost_usd})
        return summary

    # ------------------------------------------------------------------ pieces
    def _sync_book_with_broker(self) -> None:
        """Two-way reconciliation between the book and what the broker actually holds."""
        held = self.broker.positions()
        open_orders = self.broker.pending_orders() if hasattr(self.broker, "pending_orders") else []
        pending_symbols = {str(o.get("symbol")) for o in open_orders}
        pending_ids = {str(o.get("id", "")) for o in open_orders}
        for sym, pos in list(self.book.positions.items()):
            bp = held.get(sym)
            if bp is not None:
                if pos.meta.get("fill_pending"):
                    pos.qty, pos.entry_price = bp.qty, bp.avg_price
                    pos.stop_price = bp.avg_price - pos.r_per_share
                    pos.highest_close = bp.avg_price
                    pos.meta["fill_pending"] = False
                    self.journal.write("fill_confirmed", {"symbol": sym, "qty": bp.qty, "price": bp.avg_price})
                elif abs(bp.qty - pos.qty) > 1e-6:
                    pos.qty = bp.qty
                continue
            if pos.meta.get("fill_pending") and keep_pending(pos, pending_symbols, pending_ids, self.run_date.date()):
                continue  # still queued at the broker (orders placed after the close fill at the next open)
            # Gone at the broker: a resting stop filled (or the order never did). The R is an
            # estimate at the stop price, for the book's own sizing record.
            self.journal.write("reconcile", {"symbol": sym, "event": "closed_or_unfilled_at_broker",
                                             "r_multiple_est": None if pos.meta.get("fill_pending") or not pos.r_per_share
                                             else (pos.stop_price - pos.entry_price) / pos.r_per_share})
            self.book.remove(sym)
        for sym, bp in held.items():
            if sym in self.book or bp.qty <= 0:
                continue
            # Position at the broker we do not know about: adopt it with a protective stop.
            f = self._features(sym, for_candidate=False)
            rules = self.cfg.rules_for(bp.asset_class)
            atr = float(f["atr"].iloc[-1] * self._k(f.iloc[-1])) if f is not None and pd.notna(f["atr"].iloc[-1]) else bp.avg_price * 0.03
            r_ps = rules.stop_initial_atr * atr
            self.book.add(OpenPosition(symbol=sym, asset_class=bp.asset_class, qty=bp.qty, entry_price=bp.avg_price,
                                       entry_date=self.run_date.date().isoformat(), r_per_share=r_ps,
                                       stop_price=bp.avg_price - r_ps, highest_close=bp.current_price,
                                       source="reconciled"))
            self.journal.write("reconcile", {"symbol": sym, "event": "adopted_from_broker", "qty": bp.qty})
        self.book.save()

    def _arm_stop(self, pos: OpenPosition) -> bool:
        res = self.broker.submit_stop(pos.symbol, pos.qty, pos.stop_price, asset_class=pos.asset_class)
        pos.stop_order_id = res.order_id if res.ok else ""
        if not res.ok:
            pos.meta["stop_failures"] = int(pos.meta.get("stop_failures", 0)) + 1
            self.journal.write("stop_failed", {"symbol": pos.symbol, "message": res.message,
                                               "failures": pos.meta["stop_failures"]})
        self.book.save()
        return res.ok

    def _ensure_stops(self) -> None:
        """Every confirmed position must have a resting stop; two failures close it."""
        for sym, pos in list(self.book.positions.items()):
            if pos.meta.get("fill_pending") or pos.stop_order_id:
                continue
            if not self._arm_stop(pos) and int(pos.meta.get("stop_failures", 0)) >= 2:
                res = self.broker.close_position(sym)
                self.journal.write("exit", {"symbol": sym, "reason": "no_protective_stop", "ok": res.ok})
                if res.ok:
                    self.book.remove(sym)

    def _manage_positions(self, reg: pd.Series) -> list[dict[str, Any]]:
        exits: list[dict[str, Any]] = []
        kept: list[tuple[str, OpenPosition, float]] = []     # what is still held, at today's close
        for sym, pos in list(self.book.positions.items()):
            if pos.meta.get("fill_pending"):
                continue
            f = self._features(sym, for_candidate=False)
            if f is None or self.run_date not in f.index:
                continue
            row = f.loc[self.run_date]
            rules = self.cfg.rules_for(pos.asset_class)
            k = self._k(row)
            close = float(row["raw_close"])
            pos.bars_held += 1
            pos.highest_close = max(pos.highest_close, close)
            atr = float(row["atr"]) * k if pd.notna(row["atr"]) else None
            failure_due = pos.bars_held >= rules.failure_stop_bars and not pos.meta.get("failure_checked")
            if failure_due:
                pos.meta["failure_checked"] = True
            reason = None
            if close <= pos.stop_price:
                reason = "stop"
            elif rules.take_profit_r > 0 and pos.r_per_share > 0 and close >= pos.entry_price + rules.take_profit_r * pos.r_per_share:
                reason = "target"          # the reward-to-risk target, sold at the next open as in the backtest
            elif row["below_mid_streak"] >= rules.exit_closes_below_mid or bool(row["below_long"]):
                reason = "trend_break"
            elif failure_due and close < pos.entry_price + rules.failure_stop_min_r * pos.r_per_share:
                reason = "failure_stop"
            elif pos.bars_held >= rules.review_bars and not bool(row["template_ok"]):
                reason = "review"
            elif pos.bars_held >= rules.max_hold_bars:
                reason = "max_hold"
            elif pos.invalidation_condition == "close_below_ma50_2d" and row["below_mid_streak"] >= 2:
                reason = "invalidation"
            elif pos.invalidation_condition == "close_below_ma200" and bool(row["below_long"]):
                reason = "invalidation"
            elif (pos.invalidation_condition == "close_below_entry_minus_3atr" and atr
                  and close < pos.entry_price - 3 * atr):
                reason = "invalidation"
            elif pos.invalidation_condition == "benchmark_regime_red" and str(reg["state"]) == "red":
                reason = "invalidation"
            if reason is None and self._news_negative(sym, pos):
                reason = "news_material_negative"
            if reason:
                self.broker.cancel_open_orders(sym)
                res = self.broker.close_position(sym)
                pnl = ((float(res.fill_price) - pos.entry_price) * pos.qty
                       if res.ok and res.fill_price else None)
                exits.append({"symbol": sym, "reason": reason, "ok": res.ok, "price": res.fill_price,
                              "bars_held": pos.bars_held, "message": res.message,
                              "asset_class": pos.asset_class, "pnl_usd": pnl,
                              "r_multiple": ((float(res.fill_price) - pos.entry_price) / pos.r_per_share
                                             if res.ok and res.fill_price and pos.r_per_share else None)})
                self.journal.write("exit", exits[-1])
                if res.ok:
                    self.book.remove(sym)
                continue
            if atr:
                mult = rules.trail_atr if pos.bars_held < rules.trail_tighten_after_bars else rules.trail_atr_late
                new_stop = max(pos.stop_price, pos.highest_close - atr * mult)
                if new_stop > pos.stop_price + 1e-9:
                    self.broker.cancel_open_orders(sym)
                    pos.stop_price = new_stop
                    self._arm_stop(pos)
                    self.journal.write("stop_ratchet", {"symbol": sym, "stop": new_stop})
            kept.append((sym, pos, close))
            self.book.save()
        self._guard_heat(kept)
        return exits

    def _guard_heat(self, kept: list[tuple[str, OpenPosition, float]]) -> None:
        """The heat guard, as in the backtest: when the book would lose more than
        ``risk.heat_guard_pct`` of equity with every stop filled, every stop is raised so each
        position's loss to its stop shrinks in the same proportion."""
        cap = self.cfg.risk.heat_guard_pct
        if cap <= 0 or not kept:
            return
        equity = float(self.broker.equity())
        heat = sum(max(pos.qty * (close - pos.stop_price), 0.0) for _, pos, close in kept)
        if equity <= 0 or heat <= cap * equity:
            return
        f = cap * equity / heat
        for sym, pos, close in kept:
            new_stop = close - (close - pos.stop_price) * f
            if new_stop > pos.stop_price + 1e-9:
                self.broker.cancel_open_orders(sym)
                pos.stop_price = new_stop
                self._arm_stop(pos)
        self.journal.write("heat_guard", {"heat_pct": heat / equity, "cap_pct": cap,
                                          "stops": {sym: pos.stop_price for sym, pos, _ in kept}})
        self.book.save()

    def _news_negative(self, sym: str, pos: OpenPosition) -> bool:
        """The news desk: the quarantined reader checks today's headlines for every open
        position and the position is closed on a material negative event. Only bounded
        fields leave the reader; the cost comes out of the day's research budget."""
        pipe = getattr(self, "_news_pipe", None)
        if pipe is None or self.news_fn is None:
            return False
        try:
            headlines = self.news_fn(sym)
        except Exception as exc:
            self.journal.write("news_error", {"symbol": sym, "error": str(exc)[:200]})
            return False
        if not headlines:
            return False
        ctx = CandidateContext(symbol=sym, asset_class=pos.asset_class, features={}, headlines=headlines,
                               company_names=self.company_names.get(sym, []))
        try:
            digest, cost = pipe.read_news(ctx)
        except BudgetExceededError as exc:
            self.journal.write("news_skip", {"symbol": sym, "reason": str(exc)[:120]})
            return False
        if digest is None:
            return False
        self.journal.write("news_watch", {"symbol": sym, "material_negative": digest.material_negative,
                                          "net_sentiment": digest.net_sentiment,
                                          "attention_spike": digest.attention_spike, "cost_usd": cost})
        return bool(digest.material_negative)

    def _candidates(self, mult: dict[str, float]) -> pd.DataFrame:
        allowed = self.universe.members(self.run_date) if self.universe is not None else None
        rows = []
        for sym, ac in self.asset_classes.items():
            if sym in self.book or ac == "index" or mult.get(ac, 0.0) <= 0:
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
