from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from hive.config import SurvivalRules, Tier

Sleeve = str  # "stocks" | "crypto" | "all" | "meme"


@dataclass
class Child:
    id: str
    name: str
    kind: str                      # "variant" | "scout" | "monitor"
    sleeve: Sleeve
    overrides: dict[str, Any] = field(default_factory=dict)
    status: str = "shadow"         # shadow | live | retired
    born: str = ""
    retired: str | None = None
    equity: float | None = None
    start_equity: float | None = None
    parent_start_equity: float | None = None
    voi: float | None = None       # child return - parent return over the same days
    shadow_days: int = 0
    hypothesis: str = ""
    notes: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    watch: list[str] = field(default_factory=list)   # monitors: topics or symbols to report on daily


class PromotionNeedsHumanError(PermissionError):
    pass


class HiveRegistry:
    def __init__(self, path: Path, rules: SurvivalRules):
        self.path = Path(path)
        self.rules = rules
        self.children: list[Child] = []
        self.iterations: list[dict[str, Any]] = []
        if self.path.exists():
            d = json.loads(self.path.read_text())
            self.children = [Child(**c) for c in d.get("children", [])]
            self.iterations = d.get("iterations", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"children": [asdict(c) for c in self.children],
                                         "iterations": self.iterations[-200:]}, indent=2, default=str))

    # -- queries -----------------------------------------------------------------
    def active(self) -> list[Child]:
        return [c for c in self.children if c.status in ("shadow", "live")]

    def get(self, child_id: str) -> Child:
        for c in self.children:
            if c.id == child_id:
                return c
        raise KeyError(child_id)

    @staticmethod
    def slug(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "child"

    def can_spawn(self, tier: Tier) -> tuple[bool, str]:
        n = len(self.active())
        if n >= min(tier.max_children, self.rules.max_children):
            return False, f"tier {tier.name} allows {tier.max_children} children; {n} active"
        return True, ""

    # -- lifecycle -------------------------------------------------------------------
    def spawn(self, child: Child, tier: Tier, *, force: bool = False) -> Child:
        ok, why = self.can_spawn(tier)
        if not ok and not force:
            raise PermissionError(why)
        base = child.id or self.slug(child.name)
        cid, n = base, 2
        while any(c.id == cid for c in self.children):
            cid, n = f"{base}-{n}", n + 1
        child.id = cid
        child.born = child.born or date.today().isoformat()
        self.children.append(child)
        self.save()
        return child

    def retire(self, child_id: str, why: str, on: date | None = None) -> Child:
        c = self.get(child_id)
        c.status, c.retired = "retired", (on or date.today()).isoformat()
        c.notes.append(f"retired: {why}")
        self.save()
        return c

    def promote(self, child_id: str, *, approved_by_human: bool) -> Child:
        """Live promotion is a human decision, always. The operator may only recommend."""
        if not approved_by_human:
            raise PromotionNeedsHumanError("promotion to live requires --approve from a human")
        c = self.get(child_id)
        if c.shadow_days < self.rules.child_shadow_days:
            raise PermissionError(f"child has {c.shadow_days} shadow days; needs {self.rules.child_shadow_days}")
        # A promoted variant's overrides move into the primary (state/promoted_overrides.json,
        # written by the CLI); the child itself steps aside and frees its seat.
        c.status, c.retired = "promoted", date.today().isoformat()
        c.notes.append("promoted into the primary by human approval")
        self.save()
        return c

    def ready_to_promote(self) -> list[Child]:
        """Shadow variants that have beaten the parent for the required shadow period."""
        return [c for c in self.active() if c.kind == "variant" and c.status == "shadow"
                and c.shadow_days >= self.rules.child_shadow_days and (c.voi or 0.0) > 0.0]

    def retire_expired_monitors(self, ttl_days: int, on: date | None = None) -> list[Child]:
        on = on or date.today()
        out = []
        for c in self.active():
            if c.kind != "monitor" or not c.born:
                continue
            if (on - date.fromisoformat(c.born)).days >= ttl_days:
                out.append(self.retire(c.id, f"monitor expired after {ttl_days} days (re-propose if still useful)", on))
        return out

    # -- marks / value of information ---------------------------------------------------
    def mark(self, child_id: str, equity: float, parent_equity: float, *, on: date | None = None) -> Child:
        c = self.get(child_id)
        if c.start_equity is None:
            c.start_equity, c.parent_start_equity = equity, parent_equity
        c.equity = equity
        c.shadow_days += 1
        if c.start_equity and c.parent_start_equity:
            c.voi = (equity / c.start_equity - 1.0) - (parent_equity / c.parent_start_equity - 1.0)
        self.save()
        return c

    def auto_retire_losers(self, min_days: int, max_negative_voi: float = -0.05, on: date | None = None) -> list[Child]:
        out = []
        for c in self.active():
            if c.status == "shadow" and c.shadow_days >= min_days and c.voi is not None and c.voi <= max_negative_voi:
                out.append(self.retire(c.id, f"VOI {c.voi:+.1%} after {c.shadow_days} days", on))
        return out

    def record_iteration(self, record: dict[str, Any]) -> None:
        self.iterations.append(record)
        self.save()
