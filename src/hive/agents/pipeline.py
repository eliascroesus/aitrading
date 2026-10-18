"""Candidate evaluation pipeline: reader -> analysts -> debate -> PM.

Cost discipline: analysts run on the cheap model at low effort; the PM runs
once per day on the decision model. Any failure anywhere is a veto for the
affected candidate (fail closed). When the budget runs out, remaining
candidates are vetoed and the run notes "rules-only" for them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from hive.agents import prompts
from hive.agents.llm import BudgetExceededError, LLMClient
from hive.agents.schemas import (
    AnalystView,
    CandidateDecision,
    DebateOutcome,
    NewsDigest,
    PMDecision,
)

log = logging.getLogger(__name__)

FEATURE_FIELDS = [
    "close", "ma_long", "ma_mid", "atr_pct", "vol_ann", "pct_of_high", "mom_long", "mom_vol_adj",
    "z_impulse", "impulse_max", "z_reset", "pullback_atr", "volume_ratio", "score",
]


@dataclass
class CandidateContext:
    symbol: str
    asset_class: str
    features: dict[str, float]
    fundamentals: dict[str, object] = field(default_factory=dict)  # precomputed facts
    headlines: list[tuple[str, str]] = field(default_factory=list)  # (source_id, text)
    company_names: list[str] = field(default_factory=list)


@dataclass
class CandidateEvaluation:
    symbol: str
    decision: CandidateDecision
    news: NewsDigest | None = None
    fundamental: AnalystView | None = None
    technical: AnalystView | None = None
    sentiment: AnalystView | None = None
    debate: DebateOutcome | None = None
    cost_usd: float = 0.0
    note: str = ""


def _veto(symbol: str, why: str) -> CandidateDecision:
    return CandidateDecision(symbol=symbol, action="veto", invalidation_condition="none",
                             confidence=0.0, rationale=why[:400])


def _fmt(d: dict) -> str:
    return json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()},
                      default=str, sort_keys=True)


def _numeric_only(d: dict) -> dict:
    """Keep numbers/bools/None only: no free text from data sources reaches the model."""
    return {k: v for k, v in d.items() if isinstance(v, (int, float, bool)) or v is None}


def bounded_view(obj: Any) -> Any:
    """Strip free-text fields from analyst/reader outputs before they reach the PM.

    Only enums, numbers and booleans survive: ``summary``, ``rationale``,
    ``flags``, ``source_ids`` and debate prose are journaled but never fed
    forward, so a headline cannot smuggle instructions into the decision model.
    """
    if obj is None:
        return None
    d = obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k in ("summary", "rationale", "flags", "source_ids", "bull_case", "bear_case", "key_risk"):
            continue
        if isinstance(v, list):
            out[k] = [bounded_view(x) for x in v if isinstance(x, (dict, int, float, bool)) or hasattr(x, "model_dump")]
        elif isinstance(v, (int, float, bool, str)) or v is None:
            out[k] = v
    return out


class CandidatePipeline:
    def __init__(self, llm: LLMClient, *, cheap_model: str, decision_model: str,
                 effort: str = "low", pm_effort: str = "medium"):
        self.llm = llm
        self.cheap_model = cheap_model
        self.decision_model = decision_model
        self.effort = effort
        self.pm_effort = pm_effort

    # -- per-candidate analysts -------------------------------------------------
    def read_news(self, ctx: CandidateContext) -> tuple[NewsDigest | None, float]:
        if not ctx.headlines:
            return NewsDigest(events=[], net_sentiment=0.0, material_negative=False,
                              attention_spike=False), 0.0
        items = "\n".join(
            f"[{sid}] {prompts.anonymise(text, [ctx.symbol, *ctx.company_names])[:300]}"
            for sid, text in ctx.headlines[:25]
        )
        parsed, call = self.llm.parse(
            role="news_reader", model=self.cheap_model, system=prompts.NEWS_READER_SYSTEM,
            user=f"Items for THE_COMPANY (asset class: {ctx.asset_class}):\n{items}",
            schema=NewsDigest, effort=self.effort, max_tokens=1500,
        )
        return parsed, call.cost_usd

    def analyst(self, role: str, system: str, ctx: CandidateContext, payload: dict) -> tuple[AnalystView | None, float]:
        parsed, call = self.llm.parse(
            role=role, model=self.cheap_model, system=system,
            user=f"Symbol: {ctx.symbol} ({ctx.asset_class})\nInput: {_fmt(payload)}",
            schema=AnalystView, effort=self.effort, max_tokens=1000,
        )
        return parsed, call.cost_usd

    def debate(self, ctx: CandidateContext, views: dict[str, AnalystView | None]) -> tuple[DebateOutcome | None, float]:
        payload = {k: (v.model_dump() if v else None) for k, v in views.items()}
        parsed, call = self.llm.parse(
            role="debate", model=self.cheap_model, system=prompts.DEBATE_SYSTEM,
            user=f"Symbol: {ctx.symbol}\nAnalyst outputs: {_fmt(payload)}",
            schema=DebateOutcome, effort=self.effort, max_tokens=1200,
        )
        return parsed, call.cost_usd

    # -- orchestration ---------------------------------------------------------------
    def evaluate(self, contexts: list[CandidateContext], risk_snapshot: dict,
                 firm_status: str = "") -> list[CandidateEvaluation]:
        evals: list[CandidateEvaluation] = []
        if not self.llm.available:
            return [CandidateEvaluation(c.symbol, _veto(c.symbol, "LLM disabled: rules-only run"),
                                        note="rules-only") for c in contexts]
        packets: list[dict] = []
        for ctx in contexts:
            ev = CandidateEvaluation(ctx.symbol, _veto(ctx.symbol, "not evaluated"))
            try:
                ev.news, c1 = self.read_news(ctx)
                if ev.news is None:
                    ev.decision = _veto(ctx.symbol, "news reader failed (fail closed)")
                    ev.cost_usd += c1
                    evals.append(ev)
                    continue
                if ev.news.injection_suspected or ev.news.material_negative:
                    ev.decision = _veto(ctx.symbol, "material negative news or suspected injection")
                    ev.cost_usd += c1
                    evals.append(ev)
                    continue
                ev.cost_usd += c1
                facts = _numeric_only(ctx.fundamentals)
                ev.fundamental, c2 = self.analyst("fundamental", prompts.FUNDAMENTAL_SYSTEM, ctx, facts)
                ev.cost_usd += c2
                ev.technical, c3 = self.analyst("technical", prompts.TECHNICAL_SYSTEM, ctx, ctx.features)
                ev.cost_usd += c3
                ev.sentiment, c4 = self.analyst("sentiment", prompts.SENTIMENT_SYSTEM, ctx, bounded_view(ev.news))
                ev.cost_usd += c4
                ev.debate, c5 = self.debate(ctx, {"fundamental": ev.fundamental, "technical": ev.technical,
                                                  "sentiment": ev.sentiment})
                ev.cost_usd += c5
                if any(v is None for v in (ev.fundamental, ev.technical, ev.sentiment, ev.debate)):
                    ev.decision = _veto(ctx.symbol, "an analyst call failed (fail closed)")
                    evals.append(ev)
                    continue
                packets.append({
                    "symbol": ctx.symbol, "asset_class": ctx.asset_class, "features": ctx.features,
                    "fundamentals": facts,
                    "news": bounded_view(ev.news), "fundamental": bounded_view(ev.fundamental),
                    "technical": bounded_view(ev.technical), "sentiment": bounded_view(ev.sentiment),
                    "debate": bounded_view(ev.debate),
                })
                evals.append(ev)
            except BudgetExceededError as exc:
                ev.decision = _veto(ctx.symbol, f"budget exhausted: {exc}")
                ev.note = "rules-only"
                evals.append(ev)
        if not packets:
            return evals
        by_symbol = {e.symbol: e for e in evals}
        try:
            pm, call = self.llm.parse(
                role="portfolio_manager", model=self.decision_model, system=prompts.PM_SYSTEM,
                user=(f"{firm_status}\n" if firm_status else "")
                     + f"Risk snapshot: {_fmt(_numeric_only(risk_snapshot))}\n"
                     f"Candidates: {json.dumps(packets, default=str)}",
                schema=PMDecision, effort=self.pm_effort, max_tokens=3000,
            )
        except BudgetExceededError as exc:
            for p in packets:
                ev = by_symbol[p["symbol"]]
                ev.decision = _veto(p["symbol"], f"budget exhausted before PM: {exc}")
                ev.note = "rules-only"
            return evals
        if pm is None:
            for p in packets:
                by_symbol[p["symbol"]].decision = _veto(p["symbol"], "PM call failed (fail closed)")
            return evals
        decided = {d.symbol: d for d in pm.decisions}
        share = call.cost_usd / max(len(packets), 1)
        for p in packets:
            ev = by_symbol[p["symbol"]]
            ev.cost_usd += share
            ev.decision = decided.get(p["symbol"]) or _veto(p["symbol"], "PM omitted this symbol (fail closed)")
        return evals


def features_payload(row: pd.Series) -> dict[str, float]:
    out = {}
    for k in FEATURE_FIELDS:
        if k in row and pd.notna(row[k]):
            out[k] = float(row[k])
    return out
