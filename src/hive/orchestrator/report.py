"""Daily markdown + JSON report for the documentary."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from hive.orchestrator.run import RunSummary


def write_daily_report(out_dir: Path, primary: RunSummary, shadow: RunSummary | None,
                       benchmarks: dict[str, float], survival: dict | None) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rd = primary.run_date
    payload = {"primary": asdict(primary), "shadow": asdict(shadow) if shadow else None,
               "benchmarks": benchmarks, "survival": survival}
    json_path = out_dir / f"{rd}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    lines = [f"# HIVE daily report — {rd}", ""]
    lines.append(f"**Primary ({primary.label})**: equity ${primary.equity_gross:.2f} gross, "
                 f"${primary.equity_net:.2f} net of AI costs · tier `{primary.tier}` · "
                 f"AI spend today ${primary.ai_cost_usd:.4f}")
    if shadow:
        lines.append(f"**Shadow (rules-only)**: equity ${shadow.equity_gross:.2f}")
    if benchmarks:
        lines.append("**Benchmarks**: " + ", ".join(f"{k} ${v:.2f}" for k, v in benchmarks.items()))
    if survival:
        lines.append(f"**Survival**: budget today ${survival.get('budget_today', 0):.4f}, "
                     f"runway {survival.get('runway_days')} days, dead={survival.get('dead')}")
    lines += ["", f"**Regime**: `{primary.regime.get('state')}` crash_guard={primary.regime.get('crash_guard')}", ""]
    if primary.notes:
        lines += ["**Notes**: " + "; ".join(primary.notes), ""]
    lines.append("## Positions")
    if primary.positions:
        lines.append("| symbol | qty | entry | stop | bars | source |")
        lines.append("|---|---|---|---|---|---|")
        for p in primary.positions:
            lines.append(f"| {p['symbol']} | {p['qty']} | {p['entry']:.4f} | {p['stop']:.4f} | {p['bars_held']} | {p['source']} |")
    else:
        lines.append("_none_")
    lines += ["", "## Exits"]
    lines += [f"- {e['symbol']}: {e['reason']} (ok={e['ok']})" for e in primary.exits] or ["_none_"]
    lines += ["", "## Candidates and decisions"]
    if primary.decisions:
        lines.append("| symbol | action | invalidation | confidence | rationale | cost |")
        lines.append("|---|---|---|---|---|---|")
        for d in primary.decisions:
            lines.append(f"| {d['symbol']} | {d['action']} | {d['invalidation']} | {d['confidence']} | "
                         f"{str(d['rationale'])[:120]} | ${d['cost_usd']:.4f} |")
    else:
        lines.append("_no candidates_")
    lines += ["", "## Orders"]
    lines += [f"- {o['side']} {o['symbol']} ${o['notional']:.2f} ok={o['ok']} {o['message']}" for o in primary.orders] or ["_none_"]
    md_path = out_dir / f"{rd}.md"
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": json_path, "markdown": md_path}
