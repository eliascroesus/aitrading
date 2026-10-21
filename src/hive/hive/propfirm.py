"""Prop-firm evaluations: the firm's objective, and how it knows when it is ready.

A prop firm funds a trader who passes an evaluation: reach a profit target without ever
breaching a daily loss limit or a total loss limit, often within a time limit and over a
minimum number of trading days. The rules here are a typical two-sided evaluation and are
configurable (``[prop]`` in config/strategy.toml); a specific firm's rules should be copied
in before relying on them.

Two uses:

* ``prop_metrics`` scores an equity curve by simulating an evaluation starting every few
  days and walking forward until it passes, breaches or times out. The pass rate is what
  the lab climbs; the breach rates say why cells fail.
* ``forward_status`` reads a live paper record (equity marks) as one evaluation that began
  on its first day: did it pass, did it breach, how far along it is. It drives punishment
  (a variant that breaches is retired, as a firm would close the account) and readiness.

Firms check their loss limits against equity during the day, floating losses included
(config 2.6.0). A backtest also gives each day's worst equity (every position at its adverse
extreme of the day at once, a little harsher than the real path), and the limits are checked
against it: a day's loss is measured from the previous close in units of the starting balance,
as firms measure it, and the total loss from the start. A target counts on the close. A live
paper record has only its closes, so ``forward_status`` checks those.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from hive.config import PropRules

PROP_KEYS = ("prop_pass_rate", "prop_fail_daily_rate", "prop_fail_total_rate", "prop_timeout_rate",
             "prop_median_days", "prop_n", "prop_score", "prop_score_se")
# What a score record carries beyond the rules (config 2.7.0: its standard error); a record
# without it is rescored like one made under other rules.
SCORE_FORMAT = "se"


@dataclass
class Evaluation:
    outcome: str            # passed | failed_daily | failed_total | timeout | open
    days: int               # trading days elapsed
    ret: float              # return from the start at the end
    worst_day: float        # worst single-day return
    max_loss: float         # deepest fall below the reference (start, or the peak when trailing)


def rules_key(rules: PropRules) -> str:
    """The rules a prop score was made under, so a score from other rules is not compared."""
    return (f"{rules.profit_target_pct:g}/{rules.phase2_target_pct:g}/{rules.max_daily_loss_pct:g}/"
            f"{rules.max_total_loss_pct:g}/{'trail' if rules.trailing_total_loss else 'static'}/"
            f"{rules.min_trading_days}d/{rules.eval_horizon_days}h/{rules.eval_step_days}s/{rules.breach_penalty:g}"
            + ("/intraday" if rules.intraday_breach else "") + f"/{SCORE_FORMAT}")


def _walk(eq: np.ndarray, start: int, rules: PropRules, *, horizon: int | None,
          target: float | None = None, low: np.ndarray | None = None) -> Evaluation:
    target = rules.profit_target_pct if target is None else target
    e0 = eq[start]
    peak = e0
    worst_day, max_loss = 0.0, 0.0
    end = len(eq) if horizon is None else min(len(eq), start + horizon + 1)
    for i in range(start + 1, end):
        if low is None:
            worst = eq[i]
            day_ret = eq[i] / eq[i - 1] - 1.0
        else:                                    # the day's worst equity, against the starting balance
            worst = min(low[i], eq[i])
            day_ret = (worst - eq[i - 1]) / e0
        worst_day = min(worst_day, day_ret)
        ref = peak if rules.trailing_total_loss else e0
        loss = worst / ref - 1.0
        max_loss = min(max_loss, loss)
        days = i - start
        if day_ret <= -rules.max_daily_loss_pct:
            return Evaluation("failed_daily", days, eq[i] / e0 - 1.0, worst_day, max_loss)
        if loss <= -rules.max_total_loss_pct:
            return Evaluation("failed_total", days, eq[i] / e0 - 1.0, worst_day, max_loss)
        peak = max(peak, eq[i])
        if eq[i] / e0 - 1.0 >= target and days >= rules.min_trading_days:
            return Evaluation("passed", days, eq[i] / e0 - 1.0, worst_day, max_loss)
    days = end - 1 - start
    outcome = "timeout" if horizon is not None and days >= horizon else "open"
    return Evaluation(outcome, days, eq[end - 1] / e0 - 1.0, worst_day, max_loss)


def _evaluate(eq: np.ndarray, start: int, rules: PropRules, *, horizon: int | None,
              low: np.ndarray | None = None) -> Evaluation:
    """One whole challenge from ``start``: phase 1, then (when ``phase2_target_pct`` > 0) phase 2
    from a fresh balance on the day phase 1 passed, with the same loss limits and minimum days,
    inside the same horizon. It passes only if both phases do."""
    p1 = _walk(eq, start, rules, horizon=horizon, low=low)
    if p1.outcome != "passed" or rules.phase2_target_pct <= 0:
        return p1
    rest = None if horizon is None else horizon - p1.days
    if rest is not None and rest <= 0:
        return Evaluation("timeout", p1.days, p1.ret, p1.worst_day, p1.max_loss)
    p2 = _walk(eq, start + p1.days, rules, horizon=rest, target=rules.phase2_target_pct, low=low)
    days = p1.days + p2.days
    return Evaluation(p2.outcome, days, float(eq[start + days] / eq[start] - 1.0), min(p1.worst_day, p2.worst_day),
                      min(p1.max_loss, p2.max_loss))


def prop_metrics(equity: pd.Series, rules: PropRules, low: pd.Series | None = None) -> dict[str, float]:
    """Rolling simulated evaluations over an equity curve (one start every ``eval_step_days``,
    each walked for at most ``eval_horizon_days``). Starts without a full horizon left are
    skipped so every evaluation had the same chance to finish. ``low`` is each day's worst
    equity; with ``intraday_breach`` the loss limits are checked against it."""
    s = pd.Series(equity).dropna().astype(float)
    eq = np.asarray(s.values)
    lw = None
    if low is not None and rules.intraday_breach:
        lw = np.asarray(pd.Series(low).reindex(s.index).astype(float).fillna(s).values)
    out = dict.fromkeys(PROP_KEYS, 0.0)
    h = rules.eval_horizon_days
    starts = range(0, max(0, len(eq) - h), max(1, rules.eval_step_days))
    evals = [_evaluate(eq, i, rules, horizon=h, low=lw) for i in starts if eq[i] > 0]
    if not evals:
        return out
    n = len(evals)
    passed = [e for e in evals if e.outcome == "passed"]
    out.update({"prop_pass_rate": len(passed) / n,
                "prop_fail_daily_rate": sum(e.outcome == "failed_daily" for e in evals) / n,
                "prop_fail_total_rate": sum(e.outcome == "failed_total" for e in evals) / n,
                "prop_timeout_rate": sum(e.outcome == "timeout" for e in evals) / n,
                "prop_median_days": float(np.median([e.days for e in passed])) if passed else 0.0,
                "prop_n": float(n)})
    # the reward and the punishment in one number: a pass earns 1, a breached evaluation (the fee
    # lost) costs breach_penalty, running out of time costs nothing
    out["prop_score"] = out["prop_pass_rate"] - rules.breach_penalty * (out["prop_fail_daily_rate"]
                                                                        + out["prop_fail_total_rate"])
    # how far chance alone could move it: overlapping evaluations share their days, so only about
    # one in horizon / step of them is independent
    pts = np.array([1.0 if e.outcome == "passed" else -rules.breach_penalty if e.outcome.startswith("failed")
                    else 0.0 for e in evals])
    n_ind = min(float(n), max(1.0, n * max(1, rules.eval_step_days) / max(1, h)))
    out["prop_score_se"] = float(pts.std(ddof=1) / np.sqrt(n_ind)) if n > 1 else 0.0
    return out


def forward_status(marks: list[tuple[str, float]], rules: PropRules) -> dict[str, Any]:
    """A live paper record read as one evaluation that began on its first mark. ``passed``:
    the target was reached before any breach. ``breached``: a daily or total loss limit was
    broken at any point, before or after passing (a funded account keeps the same limits)."""
    if len(marks) < 2:
        return {"days": max(len(marks) - 1, 0), "outcome": "open", "ret": 0.0, "worst_day": 0.0, "max_loss": 0.0,
                "breached": False, "breach_day": None, "passed": False, "passed_on": None,
                "start": marks[0][0] if marks else None, "last": marks[-1][0] if marks else None}
    eq = np.asarray([m[1] for m in marks], dtype=float)
    ev = _evaluate(eq, 0, rules, horizon=None)                                    # the evaluation
    full = _walk(eq, 0, rules.model_copy(update={"profit_target_pct": float("inf")}), horizon=None)   # every day
    breached = full.outcome.startswith("failed")
    passed = ev.outcome == "passed"
    return {"outcome": full.outcome if breached else ev.outcome, "days": len(marks) - 1,
            "ret": float(eq[-1] / eq[0] - 1.0), "worst_day": full.worst_day, "max_loss": full.max_loss,
            "breached": breached, "breach_day": full.days if breached else None,
            "passed": passed, "passed_on": marks[ev.days][0] if passed else None,
            "start": marks[0][0], "last": marks[-1][0]}


def time_weighted(marks: list[tuple[str, float]], flows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Equity marks with deposits taken out: each day's return is (equity - money added that
    day) / previous equity, chained from the first mark, so a deposit is not a gain."""
    if not marks or not flows:
        return list(marks)
    out = [marks[0]]
    level = marks[0][1]
    for (d0, e0), (d1, e1) in zip(marks, marks[1:], strict=False):
        added = sum(a for d, a in flows if d0 < d <= d1)
        level *= (e1 - added) / e0 if e0 > 0 else 1.0
        out.append((d1, level))
    return out


def readiness(*, forward: dict[str, Any], backtest_pass_rate: float | None, rules: PropRules) -> dict[str, Any]:
    """The checklist the firm must clear before it says it is ready for a real evaluation."""
    checks = [
        {"check": f"at least {rules.ready_min_forward_days} trading days of live paper record",
         "ok": forward.get("days", 0) >= rules.ready_min_forward_days,
         "value": f"{forward.get('days', 0)} days"},
        {"check": "never breached the daily or total loss limit on paper",
         "ok": not forward.get("breached"), "value": forward.get("outcome")},
        {"check": f"reached the {rules.profit_target_pct:.0%} target on paper",
         "ok": bool(forward.get("passed")), "value": f"{forward.get('ret', 0.0):+.1%}"},
        {"check": f"out-of-sample simulated pass rate of at least {rules.ready_min_backtest_pass_rate:.0%}",
         "ok": backtest_pass_rate is not None and backtest_pass_rate >= rules.ready_min_backtest_pass_rate,
         "value": "—" if backtest_pass_rate is None else f"{backtest_pass_rate:.0%}"},
    ]
    return {"ready": all(c["ok"] for c in checks), "checks": checks}


def readiness_report(settings: Any, cfg: Any, registry: Any, lab_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Every book that could sit a prop evaluation (the primary, and each active variant), run
    through the readiness checklist. A book's record starts when its strategy last changed: the
    primary's at its last promotion or reversion, a variant's when it was seated. The simulated
    pass rate is the lab's out-of-sample one: the parent's for the primary (the weaker sleeve,
    since one account carries both), the cell's own for a variant."""
    from datetime import UTC, datetime

    from hive.hive.governance import read_promoted
    from hive.operator.lab import cell_key
    from hive.survival.ledger import SurvivalLedger

    rules = cfg.prop
    state = settings.hive_state_dir
    latest: dict[str, float] = {}
    parent: dict[str, float] = {}
    for r in sorted(lab_rows, key=lambda r: str(r.get("ts") or r.get("date") or "")):
        if r.get("oos_prop") is None:
            continue
        if r.get("by") == "parent":
            parent[str(r.get("sleeve"))] = float(r["oos_prop"])
        elif r.get("sleeve") and r.get("overrides"):
            latest[cell_key(r["sleeve"], r["overrides"])] = float(r["oos_prop"])

    def marks_of(path: Any, since: str | None) -> list[tuple[str, float]]:
        if not path.exists():
            return []
        try:
            led = SurvivalLedger(path, cfg.survival)
        except ValueError:
            return []
        flows = [(e.day, float(e.amount_usd)) for e in led.entries if e.kind == "capital_in"]
        marks = time_weighted(led.equity_marks(), flows)
        return [m for m in marks if since is None or m[0] >= since]

    books: list[dict[str, Any]] = []
    hist = read_promoted(settings).get("history") or []
    since = str(hist[-1].get("date")) if hist else None
    sleeves = {sl: v for sl, v in parent.items() if sl in ("stocks", "crypto")}
    books.append({"id": "primary", "name": "the firm (primary book)", "kind": "primary", "sleeve": "stocks+crypto",
                  "since": since, "marks": marks_of(state / "ledger.jsonl", since),
                  "backtest_pass_rate": min(sleeves.values()) if sleeves else None, "backtest_by_sleeve": sleeves})
    for c in registry.active():
        if c.kind != "variant":
            continue
        books.append({"id": c.id, "name": c.name, "kind": "variant", "sleeve": c.sleeve, "since": c.born or None,
                      "marks": marks_of(state / "children" / c.id / "ledger.jsonl", None),
                      "backtest_pass_rate": latest.get(cell_key(c.sleeve, c.overrides), (c.metrics or {}).get("oos_prop"))})
    out = []
    for b in books:
        fwd = forward_status(b.pop("marks"), rules)
        chk = readiness(forward=fwd, backtest_pass_rate=b["backtest_pass_rate"], rules=rules)
        out.append({**b, "forward": fwd, **chk, "n_ok": sum(c["ok"] for c in chk["checks"])})
    out.sort(key=lambda b: (not b["ready"], b["kind"] != "primary", -b["n_ok"], -(b["forward"].get("ret") or 0.0)))
    return {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "rules": rules.model_dump(),
            "ready": [f"{b['id']}@{b['since'] or 'start'}" for b in out if b["ready"]], "books": out}


def notice(report: dict[str, Any], new: list[str]) -> str:
    """The issue body: which book is ready, its checklist, and what the owner does next."""
    r = report["rules"]
    lines = ["HIVE's readiness check passed for a book. This is a paper result: nothing has been bought, "
             "connected or traded with a prop firm.", ""]
    for b in report["books"]:
        if f"{b['id']}@{b['since'] or 'start'}" not in new:
            continue
        f = b["forward"]
        lines += [f"### {b['name']} ({b['kind']}, {b['sleeve']})", "",
                  f"Paper record since {f.get('start')}: {f.get('days')} trading days, {f.get('ret', 0):+.1%}, "
                  f"worst day {f.get('worst_day', 0):+.1%}, deepest loss {f.get('max_loss', 0):+.1%}; "
                  f"target reached on {f.get('passed_on')}.", ""]
        lines += [f"- [x] {c['check']} ({c['value']})" for c in b["checks"]] + [""]
    lines += ["### The rules it was judged by", "",
              f"Profit target {r['profit_target_pct']:.0%}, daily loss limit {r['max_daily_loss_pct']:.0%}, total loss limit "
              f"{r['max_total_loss_pct']:.0%} ({'trailing' if r['trailing_total_loss'] else 'static'}), at least "
              f"{r['min_trading_days']} trading days; simulated evaluations over {r['eval_horizon_days']} days.", "",
              "### What to do next", "",
              "1. Pick a firm that funds the instruments HIVE trades (US stocks and ETFs, or crypto) and allows automated trading.",
              "2. Copy that firm's exact rules into `[prop]` in `config/strategy.toml` and let the lab rescore for a day: "
              "if the book still clears the checklist under the real rules, it is ready for that firm.",
              "3. Buy the evaluation yourself and decide how orders reach it; HIVE has no connection to any prop firm.",
              "", "Daily closes only: an intraday breach the closes do not show is invisible to the simulator, so the "
              "real evaluation is somewhat harder than the paper one."]
    return "\n".join(lines) + "\n"
