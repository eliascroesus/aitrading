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

Daily closes only: an intraday breach the closes do not show is invisible here, so the
real evaluation is somewhat harder than the simulated one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from hive.config import PropRules

PROP_KEYS = ("prop_pass_rate", "prop_fail_daily_rate", "prop_fail_total_rate", "prop_timeout_rate",
             "prop_median_days", "prop_n")


@dataclass
class Evaluation:
    outcome: str            # passed | failed_daily | failed_total | timeout | open
    days: int               # trading days elapsed
    ret: float              # return from the start at the end
    worst_day: float        # worst single-day return
    max_loss: float         # deepest fall below the reference (start, or the peak when trailing)


def _walk(eq: np.ndarray, start: int, rules: PropRules, *, horizon: int | None) -> Evaluation:
    e0 = eq[start]
    peak = e0
    worst_day, max_loss = 0.0, 0.0
    end = len(eq) if horizon is None else min(len(eq), start + horizon + 1)
    for i in range(start + 1, end):
        day_ret = eq[i] / eq[i - 1] - 1.0
        worst_day = min(worst_day, day_ret)
        ref = peak if rules.trailing_total_loss else e0
        loss = eq[i] / ref - 1.0
        max_loss = min(max_loss, loss)
        days = i - start
        if day_ret <= -rules.max_daily_loss_pct:
            return Evaluation("failed_daily", days, eq[i] / e0 - 1.0, worst_day, max_loss)
        if loss <= -rules.max_total_loss_pct:
            return Evaluation("failed_total", days, eq[i] / e0 - 1.0, worst_day, max_loss)
        peak = max(peak, eq[i])
        if eq[i] / e0 - 1.0 >= rules.profit_target_pct and days >= rules.min_trading_days:
            return Evaluation("passed", days, eq[i] / e0 - 1.0, worst_day, max_loss)
    days = end - 1 - start
    outcome = "timeout" if horizon is not None and days >= horizon else "open"
    return Evaluation(outcome, days, eq[end - 1] / e0 - 1.0, worst_day, max_loss)


def prop_metrics(equity: pd.Series, rules: PropRules) -> dict[str, float]:
    """Rolling simulated evaluations over an equity curve (one start every ``eval_step_days``,
    each walked for at most ``eval_horizon_days``). Starts without a full horizon left are
    skipped so every evaluation had the same chance to finish."""
    eq = np.asarray(pd.Series(equity).dropna().astype(float).values)
    out = dict.fromkeys(PROP_KEYS, 0.0)
    h = rules.eval_horizon_days
    starts = range(0, max(0, len(eq) - h), max(1, rules.eval_step_days))
    evals = [_walk(eq, s, rules, horizon=h) for s in starts if eq[s] > 0]
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
    return out


def forward_status(marks: list[tuple[str, float]], rules: PropRules) -> dict[str, Any]:
    """A live paper record read as one evaluation that began on its first mark."""
    if len(marks) < 2:
        return {"days": len(marks), "outcome": "open", "ret": 0.0, "worst_day": 0.0, "max_loss": 0.0,
                "breached": False, "passed": False, "start": marks[0][0] if marks else None}
    eq = np.asarray([m[1] for m in marks], dtype=float)
    ev = _walk(eq, 0, rules, horizon=None)
    d = asdict(ev)
    d.update({"breached": ev.outcome.startswith("failed"), "passed": ev.outcome == "passed",
              "start": marks[0][0], "last": marks[-1][0], "days": len(marks) - 1})
    return d


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
