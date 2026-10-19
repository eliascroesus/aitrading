"""Governance by evidence: promotion and demotion without a person in the loop.

* A variant that has beaten the parent for the shadow period by at least
  ``operator.promote_min_voi`` is promoted: its overrides are merged into
  ``state/promoted_overrides.json`` (applied by every run) and it frees its seat.
* A ``baseline`` sub-agent always runs the pre-registered rules on paper. If it
  beats the promoted configuration by ``operator.demote_min_lag`` over
  ``operator.demote_after_days``, the promotion is reverted and the comparison
  restarts. Every step is a ledger entry and an iteration record.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from hive.config import Settings, StrategyConfig, Tier
from hive.hive.registry import Child, HiveRegistry
from hive.survival.ledger import SurvivalLedger

BASELINE_ID = "baseline"


def promoted_path(settings: Settings) -> Path:
    return settings.hive_state_dir / "promoted_overrides.json"


def read_promoted(settings: Settings) -> dict[str, Any]:
    p = promoted_path(settings)
    if not p.exists():
        return {"overrides": {}, "history": []}
    try:
        d = json.loads(p.read_text())
    except (ValueError, OSError):
        return {"overrides": {}, "history": []}
    d.setdefault("overrides", {})
    d.setdefault("history", [])
    return d


def record_promotion(settings: Settings, cfg: StrategyConfig, child: Child, *, by: str, on: date) -> dict[str, Any]:
    cur = read_promoted(settings)
    cur["overrides"] = {**cur["overrides"], **(child.overrides or {})}
    cur["history"] = cur["history"][-50:] + [{"date": on.isoformat(), "event": "promoted", "by": by, "child": child.id,
                                              "name": child.name, "overrides": child.overrides, "voi": child.voi,
                                              "shadow_days": child.shadow_days}]
    promoted_path(settings).parent.mkdir(parents=True, exist_ok=True)
    promoted_path(settings).write_text(json.dumps(cur, indent=1, default=str))
    SurvivalLedger(settings.hive_state_dir / "ledger.jsonl", cfg.survival).append(
        "promotion", 0.0, {"child": child.id, "overrides": child.overrides, "voi": child.voi, "by": by,
                           "date": on.isoformat()})
    return cur


def ensure_baseline(registry: HiveRegistry, tier: Tier, on: date) -> Child:
    """The pre-registered rules keep running on paper as the yardstick for every promotion."""
    for c in registry.children:
        if c.id == BASELINE_ID and c.status == "shadow":
            return c
    return registry.spawn(Child(id=BASELINE_ID, name="baseline (pre-registered rules)", kind="baseline",
                                sleeve="all", overrides={}, hypothesis="the yardstick every promotion is judged against",
                                born=on.isoformat()), tier, force=True)


def promote_earned(settings: Settings, cfg: StrategyConfig, registry: HiveRegistry, tier: Tier,
                   on: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not cfg.operator.auto_promote:
        return out
    for c in registry.ready_to_promote():
        if (c.voi or 0.0) < cfg.operator.promote_min_voi:
            continue
        registry.promote(c.id, by_operator=True)
        record_promotion(settings, cfg, c, by="operator", on=on)
        rec = {"date": on.isoformat(), "proposal": c.name, "kind": "promotion", "overrides": c.overrides,
               "sleeve": c.sleeve, "cost_usd": 0.0,
               "verdict": f"promoted into the primary by the operator: VOI {c.voi:+.1%} over {c.shadow_days} shadow days"}
        registry.record_iteration(rec)
        out.append(rec)
    if out:
        base = ensure_baseline(registry, tier, on)
        base.start_equity = base.parent_start_equity = None   # the comparison restarts at every promotion
        base.shadow_days, base.voi = 0, None
        registry.save()
        from hive.operator.sweep import seat_waiting

        out += seat_waiting(registry, tier, on)   # a freed seat goes to the best cell still waiting
    return out


def demote_if_baseline_wins(settings: Settings, cfg: StrategyConfig, registry: HiveRegistry,
                            on: date) -> dict[str, Any] | None:
    cur = read_promoted(settings)
    if not cur["overrides"]:
        return None
    base = next((c for c in registry.children if c.id == BASELINE_ID and c.status == "shadow"), None)
    if base is None or base.shadow_days < cfg.operator.demote_after_days or (base.voi or 0.0) < cfg.operator.demote_min_lag:
        return None
    reverted = cur["overrides"]
    cur["history"] = cur["history"][-50:] + [{"date": on.isoformat(), "event": "reverted", "by": "operator",
                                              "overrides": reverted, "baseline_voi": base.voi,
                                              "days": base.shadow_days}]
    cur["overrides"] = {}
    promoted_path(settings).write_text(json.dumps(cur, indent=1, default=str))
    SurvivalLedger(settings.hive_state_dir / "ledger.jsonl", cfg.survival).append(
        "demotion", 0.0, {"overrides": reverted, "baseline_voi": base.voi, "days": base.shadow_days,
                          "date": on.isoformat()})
    rec = {"date": on.isoformat(), "proposal": "revert promotion", "kind": "demotion", "overrides": reverted,
           "sleeve": "all", "cost_usd": 0.0,
           "verdict": f"reverted: the pre-registered baseline beat the promoted configuration by "
                      f"{base.voi:+.1%} over {base.shadow_days} days"}
    registry.record_iteration(rec)
    base.start_equity = base.parent_start_equity = None
    base.shadow_days, base.voi = 0, None
    registry.save()
    return rec
