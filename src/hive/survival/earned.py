"""Earned AI budgets: the firm's models are paid by what they add, not by a fixed share.

Both multipliers sit between ``FLOOR`` and ``CAP`` times the tier's base budget, and both are
recomputed from the record every time they are used, so a model that stops adding value
loses its budget as fast as it earned it.

* The daily desks (news, numbers, sentiment, bull vs bear, portfolio manager) only filter
  what the rules find. Their budget follows ``desk_value_add``: the AI-filtered primary
  against the rules-only shadow over the last ``DESK_WINDOW_DAYS`` days, after the AI's own
  costs. Each 1% the primary leads raises the budget by 25%, each 1% it trails cuts it by
  25%; until the two books share ``DESK_MIN_DAYS`` days it is 1.
* The operator (weekly research) is paid by its hit rate: the share of its proposals that
  passed the lab's verdict against the share the free mutation search passed (each smoothed
  by one pass and one miss), plus ``PROMOTION_BONUS`` per proposal that was later promoted.
  A proposal outside the bounds counts as a miss.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FLOOR, CAP = 0.25, 4.0
DESK_WINDOW_DAYS = 30
DESK_MIN_DAYS = 5
DESK_SLOPE = 25.0            # +1% of value added -> +25% budget
PROMOTION_BONUS = 0.5
EARNED_FILE = "earned_budgets.json"


def _clamp(x: float) -> float:
    return float(min(max(x, FLOOR), CAP))


def desk_value_add(primary: Any, shadow: Any, days: int = DESK_WINDOW_DAYS) -> tuple[float | None, int]:
    """(primary return net of its daily AI costs - shadow return, days compared) over the last
    ``days`` days both books were marked. Deposits are taken out of both records."""
    from hive.hive.propfirm import time_weighted

    def marks(led: Any) -> dict[str, float]:
        flows = [(e.day, float(e.amount_usd)) for e in led.entries if e.kind == "capital_in"]
        return dict(time_weighted(led.equity_marks(), flows))

    pm, sm = marks(primary), marks(shadow)
    common = sorted(set(pm) & set(sm))[-(days + 1):]
    if len(common) < DESK_MIN_DAYS + 1:
        return None, max(len(common) - 1, 0)
    d0, d1 = common[0], common[-1]
    ai = sum(float(e.amount_usd) for e in primary.entries
             if e.kind == "ai_cost" and not str(e.day).endswith("-research") and d0 < str(e.day)[:10] <= d1)
    if pm[d0] <= 0 or sm[d0] <= 0:
        return None, len(common) - 1
    return (pm[d1] - ai) / pm[d0] - sm[d1] / sm[d0], len(common) - 1


def desk_multiplier(value_add: float | None) -> float:
    return 1.0 if value_add is None else _clamp(1.0 + DESK_SLOPE * value_add)


def operator_scorecard(research_log: list[dict[str, Any]], children: list[Any]) -> dict[str, Any]:
    """The operator's record against the lab's free search, from the research log (complete
    history) and the registry (what became of the seats its proposals earned)."""
    from hive.operator.lab import cell_key

    op_n = op_pass = lab_n = lab_pass = 0
    mine: set[str] = set()
    recent: list[dict[str, Any]] = []
    def passed(p: dict[str, Any]) -> bool:
        v = str(p.get("verdict") or "")
        return v.startswith("spawned shadow child") or v.startswith("passed")

    for row in research_log:
        if "n_tested" in row:                      # a lab sweep's summary line
            lab_n += int(row.get("n_tested") or 0)
            lab_pass += int(row.get("n_passed") or 0)
            continue
        note = str(row.get("note") or "")
        if row.get("by") != "operator" and (note.startswith("systematic sweep") or note.startswith("lab:")):
            # an early lab line, before the summary counts: every cell it tested is listed
            cells = [p for p in row.get("proposals") or [] if p.get("overrides")]
            lab_n += len(cells)
            lab_pass += sum(passed(p) for p in cells)
            continue
        for p in row.get("proposals") or []:
            if not p.get("overrides"):             # a monitor, not a strategy
                continue
            v = str(p.get("verdict") or "")
            won = passed(p)
            op_n += 1
            op_pass += int(won)
            if won:
                mine.add(cell_key(str(p.get("sleeve") or "stocks"), p["overrides"]))
            recent.append({"date": row.get("date"), "name": p.get("name"), "sleeve": p.get("sleeve"),
                           "oos_cagr": p.get("oos_cagr"), "won": won, "verdict": v[:100]})
    fate = {"promoted": 0, "retired": 0, "on_paper": 0}
    for c in children:
        if c.kind != "variant" or cell_key(c.sleeve, c.overrides or {}) not in mine:
            continue
        fate["promoted" if c.status == "promoted" else "retired" if c.status == "retired" else "on_paper"] += 1
    op_rate, lab_rate = (op_pass + 1) / (op_n + 2), (lab_pass + 1) / (lab_n + 2)
    return {"proposals": op_n, "passed": op_pass, "pass_rate": op_rate, "lab_tests": lab_n, "lab_passed": lab_pass,
            "lab_pass_rate": lab_rate, "seats": fate, "recent": recent[-12:]}


def operator_multiplier(card: dict[str, Any]) -> float:
    return _clamp(card["pass_rate"] / card["lab_pass_rate"] + PROMOTION_BONUS * card["seats"]["promoted"])


def explain_operator(card: dict[str, Any], mult: float) -> str:
    """What the operator is told about its own pay, in one paragraph."""
    s = card["seats"]
    return (f"Your record: {card['passed']} of {card['proposals']} proposals passed the lab's verdict "
            f"({card['pass_rate']:.0%} smoothed); the free mutation search passed {card['lab_passed']} of "
            f"{card['lab_tests']} ({card['lab_pass_rate']:.0%}). Of the seats you earned, {s['promoted']} were "
            f"promoted, {s['retired']} retired, {s['on_paper']} are still on paper. Your research budget this "
            f"week is {mult:.2f}x the base: your pass rate divided by the search's, plus {PROMOTION_BONUS:g} for "
            f"each promotion, between {FLOOR:g}x and {CAP:g}x. A proposal that fails, or falls outside the bounds, "
            f"lowers the next budget; one that passes and is promoted raises it. Your calls are paid from the bankroll.")


def write_earned(state_dir: Path, key: str, payload: dict[str, Any]) -> None:
    p = Path(state_dir) / EARNED_FILE
    cur = read_earned(state_dir)
    cur[key] = payload
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, indent=1, default=str))


def read_earned(state_dir: Path) -> dict[str, Any]:
    p = Path(state_dir) / EARNED_FILE
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (ValueError, OSError):
        return {}
