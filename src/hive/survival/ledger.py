"""Append-only, hash-chained ledger of equity marks and costs; tier, halt and death logic."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from hive.config import SurvivalRules, Tier

Kind = Literal["equity_mark", "ai_cost", "data_cost", "infra_cost", "capital_in", "note",
               "tier_change", "halt", "death"]
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

    @property
    def day(self) -> str:
        """The run date this entry belongs to (``meta.date`` when set, else the wall-clock day)."""
        return str(self.meta.get("date") or self.ts[:10])


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
    in_halt_cooldown: bool = False
    peak_equity: float | None = None


class SurvivalLedger:
    def __init__(self, path: Path, rules: SurvivalRules):
        self.path = Path(path)
        self.rules = rules
        self.entries: list[LedgerEntry] = []
        self._tier_name: str | None = None
        # The daily desks' earned multiplier (hive.survival.earned): what the AI layer added over
        # the rules-only shadow sets its budget, between a quarter and four times the base.
        self.ai_multiplier = 1.0
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
        key = on.isoformat() if on else None
        return sum(e.amount_usd for e in self.entries if e.kind in kinds and (key is None or e.day == key))

    def ai_cost_total(self) -> float:
        return self._costs(("ai_cost", "data_cost"))

    def ai_cost_on(self, on: date) -> float:
        return self._costs(("ai_cost", "data_cost"), on)

    def all_in_cost_total(self) -> float:
        return self._costs(("ai_cost", "data_cost", "infra_cost"))

    def capital_in_total(self) -> float:
        return self._costs(("capital_in",))

    def equity_marks(self) -> list[tuple[str, float]]:
        """One mark per run date (the last written wins), ordered by date."""
        by_day: dict[str, float] = {}
        for e in self.entries:
            if e.kind == "equity_mark":
                by_day[e.day] = e.amount_usd
        return sorted(by_day.items())

    def last_equity_mark(self) -> float | None:
        marks = self.equity_marks()
        return marks[-1][1] if marks else None

    def net_positive_days(self) -> int:
        """Trailing run of dates whose mark exceeds the mark ~30 calendar days earlier."""
        marks = self.equity_marks()
        if len(marks) < 2:
            return 0
        dates = [date.fromisoformat(d) for d, _ in marks]
        vals = [v for _, v in marks]
        streak = 0
        for i in range(len(marks) - 1, -1, -1):
            target = dates[i] - timedelta(days=30)
            j = max((k for k in range(i) if dates[k] <= target), default=None)
            if j is None:
                break
            if vals[i] > vals[j]:
                streak += 1
            else:
                break
        return streak

    # -- halts (drawdown cooldown) ------------------------------------------------------
    def last_halt(self) -> LedgerEntry | None:
        for e in reversed(self.entries):
            if e.kind == "halt":
                return e
        return None

    def record_halt(self, on: date, cooldown_days: int, equity: float) -> None:
        self.append("halt", 0.0, {"date": on.isoformat(), "equity": equity,
                                  "cooldown_until": (on + timedelta(days=cooldown_days)).isoformat()})

    def halt_state(self, on: date) -> tuple[bool, float | None]:
        """(in_cooldown, peak_equity since the last halt released)."""
        h = self.last_halt()
        marks = self.equity_marks()
        if h is None:
            peak = max((v for _, v in marks), default=None)
            return False, peak
        until = date.fromisoformat(h.meta["cooldown_until"])
        if on <= until:
            return True, None
        since = [v for d, v in marks if date.fromisoformat(d) > until]
        return False, (max(since) if since else None)

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
        raw = equity_net * tier.daily_ai_budget_pct * self.ai_multiplier
        floor = self.rules.daily_ai_budget_min_usd if tier.risk_multiplier > 0 else 0.0
        return float(min(max(raw, floor), self.rules.daily_ai_budget_max_usd))

    def research_budget(self, equity_net: float, tier: Tier, on: date, days: int = 7, bonus: float = 1.0) -> float:
        """What the weekly auditor and operator may spend.

        Unspent daily budget over the last ``days`` days accrues into a research pot
        (capped at ``days`` × today's budget), minus research already booked in the
        window. Research calls book their cost under ``<date>-research`` so they never
        draw down the day's decision budget, and the day's decisions never starve
        the weekly research.
        """
        per_day = self.budget_today(equity_net, tier)
        window = [(on - timedelta(days=i)).isoformat() for i in range(days)]
        pot = 0.0
        for d in window:
            spent = sum(e.amount_usd for e in self.entries if e.kind == "ai_cost" and e.day == d)
            pot += max(per_day - spent, 0.0)
        research = sum(e.amount_usd for e in self.entries
                       if e.kind == "ai_cost" and str(e.day).endswith("-research") and str(e.day)[:10] in window)
        # ``bonus`` is earned (hive.survival.earned): above 1 rewards research that beat the free
        # search, below 1 punishes research that did worse; a quarter to four times the pot.
        return max(min(pot, days * per_day) * min(max(bonus, 0.25), 4.0) - research, 0.0)

    def death_recorded(self) -> LedgerEntry | None:
        for e in reversed(self.entries):
            if e.kind == "death":
                return e
        return None

    def record_death(self, on: date, reason: str, equity: float) -> None:
        self.append("death", 0.0, {"date": on.isoformat(), "reason": reason, "equity": equity})

    def is_dead(self, equity_net: float) -> tuple[bool, str]:
        d = self.death_recorded()
        if d is not None:
            return True, f"declared dead on {d.meta.get('date')}: {d.meta.get('reason')}"
        if equity_net <= self.rules.death_floor_usd:
            return True, f"equity {equity_net:.2f} <= death floor {self.rules.death_floor_usd:.2f}"
        marks = [m for _, m in self.equity_marks()]
        if marks:
            peak = max(max(marks), self.rules.starting_bankroll_usd)
            if equity_net <= peak * (1 - self.rules.death_drawdown_pct):
                return True, f"drawdown from peak {peak:.2f} exceeds {self.rules.death_drawdown_pct:.0%}"
        return False, ""

    def runway_days(self, equity_net: float, on: date | None = None, lookback_days: int = 7) -> float | None:
        """(equity - death floor) / trailing average daily all-in burn."""
        today = on or datetime.now(UTC).date()
        costs = [e for e in self.entries if e.kind in ("ai_cost", "data_cost", "infra_cost")]
        # research entries carry a "<date>-research" key: the first ten characters are the day
        recent = [e.amount_usd for e in costs if 0 <= (today - date.fromisoformat(str(e.day)[:10])).days < lookback_days]
        burn = sum(recent) / lookback_days
        if burn <= 0:
            return None
        return max(equity_net - self.rules.death_floor_usd, 0.0) / burn

    def portfolio_vol_ann(self, lookback: int = 21, periods_per_year: int = 252) -> float | None:
        marks = [v for _, v in self.equity_marks()][-(lookback + 1):]
        if len(marks) < lookback + 1 or min(marks) <= 0:
            return None
        import math

        rets = [math.log(marks[i] / marks[i - 1]) for i in range(1, len(marks))]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(periods_per_year)

    def status(self, equity_gross: float, on: date | None = None) -> SurvivalStatus:
        """``equity_gross`` is broker equity; costs are deducted to get net equity."""
        on = on or datetime.now(UTC).date()
        net = equity_gross - self.all_in_cost_total() + 0.0
        tier = self.tier(net)
        budget = self.budget_today(net, tier)
        spent = self.ai_cost_on(on)
        dead, why = self.is_dead(net)
        in_cooldown, peak = self.halt_state(on)
        return SurvivalStatus(
            equity_gross=equity_gross, equity_net=net, ai_cost_total=self.ai_cost_total(),
            ai_cost_today=spent, budget_today=budget, budget_remaining=max(budget - spent, 0.0),
            runway_days=self.runway_days(net, on), tier=tier.name, dead=dead, death_reason=why,
            in_halt_cooldown=in_cooldown, peak_equity=peak,
        )
