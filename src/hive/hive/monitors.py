"""News-channel sub-agents.

A monitor watches up to five topics or symbols the operator chose (a sector, a
theme, a coin, a held name) and files one bounded report per topic per day
through the quarantined reader: sentiment, material-negative flag, attention
spike, event types. The portfolio manager reads yesterday's reports as "desk
reports". Nothing a monitor reads reaches the decision model as text.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from hive.agents.llm import BudgetExceededError, LLMClient
from hive.agents.pipeline import CandidateContext, CandidatePipeline
from hive.config import Settings
from hive.orchestrator.journal import Journal

log = logging.getLogger(__name__)
MAX_WATCH = 5
REPORT_FIELDS = ("query", "n_items", "net_sentiment", "material_negative", "attention_spike", "event_types")


def fetch_items(query: str, classes: dict[str, str], news_fn, settings: Settings) -> list[tuple[str, str]]:
    """Headlines and social posts for a symbol (via the firm's news feed) or a free topic."""
    q = query.strip()
    sym = q.upper().lstrip("$")
    if sym in classes and news_fn is not None:
        try:
            return list(news_fn(sym))[:30]
        except Exception as exc:
            log.info("monitor feed failed for %s: %s", sym, exc)
            return []
    from hive.data.sources.news import google_news_headlines

    items = google_news_headlines(q)
    if settings.hive_social_data:
        from hive.data.sources.social import reddit_posts

        items = items + reddit_posts(q)
    return items[:30]


def run_monitor_child(settings: Settings, child, cdir: Path, journal: Journal, llm: LLMClient | None,
                      run_date: date, classes: dict[str, str], news_fn) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if llm is None or not llm.available:
        journal.write("monitor_skip", {"reason": "no LLM budget or key today"})
        return reports
    pipe = CandidatePipeline(llm, cheap_model=settings.hive_llm_cheap_model, decision_model=settings.hive_llm_model,
                             effort="low", pm_effort=settings.hive_llm_effort)
    for q in list(child.watch)[:MAX_WATCH]:
        items = fetch_items(q, classes, news_fn, settings)
        if not items:
            reports.append({"query": q, "n_items": 0})
            continue
        ctx = CandidateContext(symbol=q.upper().lstrip("$"), asset_class="topic", features={}, headlines=items)
        try:
            digest, cost = pipe.read_news(ctx)
        except BudgetExceededError as exc:
            journal.write("monitor_skip", {"query": q, "reason": str(exc)[:120]})
            break
        if digest is None:
            continue
        rep = {"query": q, "n_items": len(items), "net_sentiment": digest.net_sentiment,
               "material_negative": digest.material_negative, "attention_spike": digest.attention_spike,
               "event_types": sorted({str(e.event_type) for e in digest.events})[:6], "cost_usd": cost}
        journal.write("monitor_report", rep)
        reports.append(rep)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "latest.json").write_text(json.dumps({"date": run_date.isoformat(), "child": child.id,
                                                   "reports": reports}, default=str))
    return reports


def latest_desk_reports(settings: Settings, registry) -> list[dict[str, Any]]:
    """Yesterday's bounded monitor reports, for the portfolio manager's context."""
    out: list[dict[str, Any]] = []
    for c in registry.active():
        if c.kind != "monitor":
            continue
        p = settings.hive_state_dir / "children" / c.id / "latest.json"
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        out.append({"monitor": c.name, "date": d.get("date"),
                    "reports": [{k: r.get(k) for k in REPORT_FIELDS if k in r} for r in d.get("reports", [])]})
    return out
