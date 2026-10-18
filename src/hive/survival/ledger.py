"""Append-only, hash-chained ledger of equity marks and costs; tier logic."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from hive.config import SurvivalRules, Tier

Kind = Literal["equity_mark", "ai_cost", "data_cost", "infra_cost", "capital_in", "note", "tier_change"]
GENESIS = "0" * 64


@dataclass(frozen=True)
class LedgerEntry:
    ts: str
    kind: Kind
    amount_usd: float
    meta: dict[str, Any]
    prev_hash: str
    hash: str = ""

    def compute_hash(self) -> str:
        payload = json.dumps(
            {"ts": self.ts, "kind": self.kind, "amount_usd": self.amount_usd, "meta": self.meta,
             "prev_hash": self.prev_hash},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class SurvivalStatus:
    equity_gross: float
    equity_net: float
    ai_cost_total: float
    ai_cost_today: float
    budget_today: float
    budget_remaining: float
    runway_days: float | None
    tier: str
    dead: bool
    death_reason: str


class SurvivalLedger:
    def __init__(self, path: Path, rules: SurvivalRules):
        self.path = Path(path)
        self.rules = rules
        self.entries: list[LedgerEntry] = []
        self._tier_name: str | None = None
        if self.path.exists():
            self._load()

    # -- persistence -----------------------------------------------------------
    def _load(self) -> None:
        prev = GENESIS
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            e = LedgerEntry(**d)
            if e.prev_hash != prev or e.compute_hash() != e.hash:
                raise ValueError(f"ledger chain broken at {e.ts} ({e.kind})")
            self.entries.append(e)
            prev = e.hash
            if e.kind == "tier_change":
                self._tier_name = e.meta.get("to")

    def append(self, kind: Kind, amount_usd: float, meta: dict[str, Any] | None = None,
               ts: datetime | None = None) -> LedgerEntry:
        prev = self.entries[-1].hash if self.entries else GENESIS
        e = LedgerEntry(
            ts=(ts or datetime.now(UTC)).isoformat(timespec="seconds"),
            kind=kind, amount_usd=float(amount_usd), meta=meta or {}, prev_hash=prev,
        )
        e = LedgerEntry(**{**asdict(e), "hash": e.compute_hash()})
        self.entries.append(e)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(e), default=str) + "\n")
        return e

    def verify(self) -> bool:
        prev = GENESIS
        for e in self.entries:
            if e.prev_hash != prev or e.compute_hash() != e.hash:
                return False
            prev = e.hash
        return True

    # -- aggregates ----------------------------------------------------------------
    def _costs(self, kinds: tuple[str, ...], on: date | None = None) -> float:
        total = 0.0
        for e in self.entries:
            if e.kind in kinds and (on is None or e.ts[:10] == on.isoformat()):
                total += e.amount_usd
        return total

    def ai_cost_total(self) -> float:
        return self._costs(("ai_cost", "data_cost"))

    def ai_cost_on(self, on: date) -> float:
        return self._costs(("ai_cost", "data_cost"), on)

    def all_in_cost_total(self) -> float:
        return self._costs(("ai_cost", "data_cost", "infra_cost"))

    def capital_in_total(self) -> float:
        return self._costs(("capital_in",))

    def last_equity_mark(self) -> float | None:
        for e in reversed(self.entries):
            if e.kind == "equity_mark":
                return e.amount_usd
        return None

    def equity_marks(self) -> list[tuple[str, float]]:
        return [(e.ts, e.amount_usd) for e in self.entries if e.kind == "equity_mark"]

    def net_positive_days(self) -> int:
        """Consecutive days (ending now) in which net equity closed above the mark 30 days earlier."""
        marks = self.equity_marks()
        if len(marks) < 2:
            return 0
        streak = 0
        for i in range(len(marks) - 1, 0, -1):
            if marks[i][1] > marks[i - 1][1]:
                streak += 1
            else:
                break
        return streak

    # -- budget / death / tiers ------------------------------------------------------
    def tier(self, equity_net: float) -> Tier:
        tiers = sorted(self.rules.tiers, key=lambda t: t.min_equity_usd)
        current = next((t for t in tiers if t.name == self._tier_name), None)
        eligible = [t for t in tiers if equity_net >= t.min_equity_usd]
        candidate = eligible[-1] if eligible else tiers[0]
        if current is None:
            return candidate
        if candidate.min_equity_usd > current.min_equity_usd:
            # Upgrades need the net-positive streak the target tier demands.
            if self.net_positive_days() >= candidate.min_net_positive_days:
                return candidate
            return current
        if candidate.min_equity_usd < current.min_equity_usd:
            # Downgrade only once equity is below the current tier's floor minus hysteresis.
            floor = current.min_equity_usd * (1 - self.rules.tier_hysteresis_pct)
            return candidate if equity_net < floor else current
        return current

    def record_tier(self, tier: Tier) -> None:
        if tier.name != self._tier_name:
            self.append("tier_change", 0.0, {"from": self._tier_name, "to": tier.name})
            self._tier_name = tier.name

    def budget_today(self, equity_net: float, tier: Tier) -> float:
        raw = equity_net * tier.daily_ai_budget_pct
        return float(min(max(raw, self.rules.daily_ai_budget_min_usd), self.rules.daily_ai_budget_max_usd))

    def is_dead(self, equity_net: float) -> tuple[bool, str]:
        if equity_net <= self.rules.death_floor_usd:
            return True, f"equity {equity_net:.2f} <= death floor {self.rules.death_floor_usd:.2f}"
        marks = [m for _, m in self.equity_marks()]
        if marks:
            peak = max(max(marks), self.rules.starting_bankroll_usd)
            if equity_net <= peak * (1 - self.rules.death_drawdown_pct):
                return True, f"drawdown from peak {peak:.2f} exceeds {self.rules.death_drawdown_pct:.0%}"
        return False, ""

    def runway_days(self, equity_net: float, lookback_days: int = 7) -> float | None:
        """(equity - death floor) / trailing average daily all-in burn."""
        today = datetime.now(UTC).date()
        costs = [e for e in self.entries if e.kind in ("ai_cost", "data_cost", "infra_cost")]
        recent = [e.amount_usd for e in costs if (today - date.fromisoformat(e.ts[:10])).days < lookback_days]
        burn = sum(recent) / lookback_days
        if burn <= 0:
            return None
        return max(equity_net - self.rules.death_floor_usd, 0.0) / burn

    def status(self, equity_gross: float, on: date | None = None) -> SurvivalStatus:
        """``equity_gross`` is broker equity; costs are deducted to get net equity."""
        on = on or datetime.now(UTC).date()
        net = equity_gross - self.all_in_cost_total() + 0.0
        tier = self.tier(net)
        budget = self.budget_today(net, tier)
        spent = self.ai_cost_on(on)
        dead, why = self.is_dead(net)
        return SurvivalStatus(
            equity_gross=equity_gross, equity_net=net, ai_cost_total=self.ai_cost_total(),
            ai_cost_today=spent, budget_today=budget, budget_remaining=max(budget - spent, 0.0),
            runway_days=self.runway_days(net), tier=tier.name, dead=dead, death_reason=why,
        )
