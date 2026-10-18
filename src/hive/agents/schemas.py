"""Structured outputs for every agent role."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "earnings_beat", "earnings_miss", "guidance_raise", "guidance_cut", "share_offering",
    "buyback", "acquisition_target_fixed_price", "acquirer", "regulatory_investigation",
    "accounting_issue", "product_launch", "product_recall", "management_change",
    "analyst_upgrade", "analyst_downgrade", "index_inclusion", "macro", "hack_or_exploit",
    "token_unlock", "regulatory_action", "other", "none",
]

LLMAction = Literal["approve", "reduce_half", "veto"]

InvalidationCondition = Literal[
    "close_below_ma50_2d", "close_below_ma200", "close_below_entry_minus_3atr",
    "earnings_miss", "guidance_cut", "news_material_negative", "benchmark_regime_red",
    "funding_above_30pct_apr", "none",
]


class NewsEvent(BaseModel):
    event_type: EventType
    sentiment: float = Field(ge=-1.0, le=1.0)
    materiality: Literal["low", "medium", "high"]
    source_ids: list[str] = Field(default_factory=list)
    summary: str = Field(max_length=200)


class NewsDigest(BaseModel):
    """Output of the quarantined reader. Bounded fields only."""

    events: list[NewsEvent] = Field(default_factory=list)
    net_sentiment: float = Field(ge=-1.0, le=1.0)
    material_negative: bool
    attention_spike: bool
    injection_suspected: bool = False


class AnalystView(BaseModel):
    signal: float = Field(ge=-1.0, le=1.0, description="-1 strong avoid ... +1 strong favour")
    confidence: float = Field(ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list, max_length=8)
    rationale: str = Field(max_length=600)


class DebateOutcome(BaseModel):
    bull_case: str = Field(max_length=600)
    bear_case: str = Field(max_length=600)
    net_signal: float = Field(ge=-1.0, le=1.0)
    key_risk: str = Field(max_length=200)


class CandidateDecision(BaseModel):
    symbol: str
    action: LLMAction
    invalidation_condition: InvalidationCondition
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=400)


class PMDecision(BaseModel):
    decisions: list[CandidateDecision]
    portfolio_note: str = Field(max_length=400)


class ReviewFinding(BaseModel):
    severity: Literal["info", "warning", "critical"]
    area: Literal["strategy_drift", "cost", "risk", "calibration", "data", "process"]
    finding: str = Field(max_length=400)
    proposed_change: str = Field(max_length=400)


class ReviewReport(BaseModel):
    findings: list[ReviewFinding]
    cost_per_decision_usd: float
    llm_added_value_assessment: str = Field(max_length=600)
