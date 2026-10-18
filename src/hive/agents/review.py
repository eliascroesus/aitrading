"""Weekly reviewer/auditor: reads the journals and ledger, proposes (never applies) changes."""

from __future__ import annotations

import json
from pathlib import Path

from hive.agents import prompts
from hive.agents.llm import LLMClient
from hive.agents.schemas import ReviewReport
from hive.orchestrator.journal import Journal


def collect_review_inputs(state_dir: Path, days: int = 7) -> dict:
    jdir = state_dir / "journal"
    files = sorted(p for p in jdir.glob("*.jsonl") if "-shadow" not in p.name)[-days:]
    runs, decisions, orders, exits, llm_calls = [], [], [], [], []
    for p in files:
        for e in Journal.read(p):
            k, pl = e["kind"], e["payload"]
            if k == "run_end":
                runs.append({"date": p.stem, **pl})
            elif k == "decisions":
                decisions += pl.get("decisions", [])
            elif k == "order":
                orders.append(pl)
            elif k == "exit":
                exits.append(pl)
            elif k == "llm_call":
                llm_calls.append(pl)
    ai_cost = sum(c.get("cost_usd", 0.0) for c in llm_calls)
    return {
        "runs": runs, "n_decisions": len(decisions),
        "actions": {a: sum(1 for d in decisions if d.get("action") == a) for a in ("approve", "reduce_half", "veto")},
        "orders": orders[-50:], "exits": exits[-50:],
        "llm_calls": len(llm_calls), "llm_failures": sum(1 for c in llm_calls if not c.get("ok", True)),
        "ai_cost_usd": ai_cost,
        "cost_per_decision_usd": ai_cost / max(len(decisions), 1),
        "shadow_vs_primary": None,
    }


def run_review(llm: LLMClient, model: str, inputs: dict, out_path: Path) -> ReviewReport | None:
    parsed, call = llm.parse(
        role="reviewer", model=model, system=prompts.REVIEW_SYSTEM,
        user="Weekly journal summary:\n" + json.dumps(inputs, default=str)[:60_000],
        schema=ReviewReport, effort="medium", max_tokens=4000,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"inputs": inputs, "report": parsed.model_dump() if parsed else None,
                                    "call": call.__dict__}, indent=2, default=str))
    return parsed
