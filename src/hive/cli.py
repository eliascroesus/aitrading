"""Command-line entry point: ``hive <command>``."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint

from hive.config import REPO_ROOT, StrategyConfig, get_settings
from hive.log import setup_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="HIVE trading system")
data_app = typer.Typer(no_args_is_help=True, help="Data cache management")
app.add_typer(data_app, name="data")
ui_app = typer.Typer(no_args_is_help=True, help="Dashboard")
app.add_typer(ui_app, name="ui")


@ui_app.command("build")
def ui_build(out: Path | None = typer.Option(None, help="Output dir (default reports/dashboard)")) -> None:
    """Generate the self-contained dashboard (index.html + data.json) from state and reports."""
    from hive.ui.dashboard import build

    s = get_settings()
    paths = build(s, StrategyConfig.load(), out)
    rprint({k: str(v) for k, v in paths.items()})


@ui_app.command("serve")
def ui_serve(port: int = typer.Option(8787)) -> None:
    """Serve the dashboard locally, rebuilding on every page load."""
    from hive.ui.dashboard import serve

    serve(get_settings(), StrategyConfig.load(), port)


@data_app.command("seed")
def data_seed(
    qlib_root: Path | None = typer.Option(None, help="Unzipped Qlib US daily dataset root"),
    raw_dir: Path | None = typer.Option(None, help="Raw CSV folder (default data/raw)"),
) -> None:
    """Seed the parquet cache from offline sources."""
    from hive.data.seed import seed_all
    from hive.data.store import BarStore

    setup_logging()
    s = get_settings()
    cfg = StrategyConfig.load()
    store = BarStore(s.hive_data_dir / "cache")
    summary = seed_all(store, raw_dir=raw_dir or (s.hive_data_dir / "raw"), qlib_root=qlib_root, cfg=cfg)
    rprint(summary)


@data_app.command("refresh")
def data_refresh(days: int = typer.Option(400, help="Lookback for the network refresh")) -> None:
    """Refresh the cache from network sources (yfinance, Hyperliquid). Best effort."""
    from hive.data.refresh import refresh_all
    from hive.data.store import BarStore

    setup_logging()
    s = get_settings()
    cfg = StrategyConfig.load()
    store = BarStore(s.hive_data_dir / "cache")
    counts = refresh_all(store, cfg, pit_csv=s.hive_data_dir / "raw" / "sp500_pit_membership.csv",
                         days=days, hl_testnet=s.hl_testnet)
    rprint(counts)


@data_app.command("macro")
def data_macro(days: int = typer.Option(30, help="Lookback for markets already in the cache")) -> None:
    """Forex, metals and index futures from Yahoo: the whole history for a market not yet cached
    (2000 onwards, for the lab), else the last few days. Best effort."""
    from hive.data.refresh import refresh_macro
    from hive.data.store import BarStore

    setup_logging()
    s = get_settings()
    counts = refresh_macro(BarStore(s.hive_data_dir / "cache"), StrategyConfig.load(), days=days)
    rprint(counts)


@data_app.command("status")
def data_status() -> None:
    from hive.data.store import BarStore

    s = get_settings()
    store = BarStore(s.hive_data_dir / "cache")
    for ac in ("stock", "etf", "crypto", "index"):
        syms = store.symbols(ac)
        span = ""
        if syms:
            m = store.meta(ac, syms[0]) or {}
            span = f"  e.g. {syms[0]} {m.get('first')} -> {m.get('last')}"
        rprint(f"{ac:7s} {len(syms):5d} symbols{span}")


@app.command()
def backtest(
    sleeve: str = typer.Option("stocks", help="stocks | crypto | all"),
    start: str = typer.Option("2001-01-01"),
    end: str | None = typer.Option(None),
    equity: float = typer.Option(100.0),
    no_regime: bool = typer.Option(False, help="Disable the benchmark regime filter"),
    no_pit: bool = typer.Option(False, help="Ignore point-in-time index membership"),
    out: Path | None = typer.Option(None, help="Output directory (default reports/backtest-<sleeve>)"),
    config: Path | None = typer.Option(None, help="Strategy TOML (default config/strategy.toml)"),
) -> None:
    """Run the deterministic strategy on cached data and write a report."""
    from hive.backtest.costs import ALPACA_CRYPTO, ALPACA_STOCKS
    from hive.backtest.engine import Backtester
    from hive.backtest.report import write_report
    from hive.data.seed import load_bars_for_backtest, load_pit_universe
    from hive.data.store import BarStore

    setup_logging()
    s = get_settings()
    cfg = StrategyConfig.load(config)
    store = BarStore(s.hive_data_dir / "cache")
    bars, classes, bench = load_bars_for_backtest(store, cfg, sleeve=sleeve)
    universe = None
    pit_path = s.hive_data_dir / "raw" / "sp500_pit_membership.csv"
    if not no_pit and sleeve != "crypto" and pit_path.exists():
        universe = load_pit_universe(pit_path)
    vix = None
    vix_bars = store.get("index", "VIX")
    if vix_bars is not None:
        vix = vix_bars["close"]
    cost = ALPACA_CRYPTO if sleeve == "crypto" else ALPACA_STOCKS
    rprint(f"[bold]backtest[/bold] sleeve={sleeve} symbols={len(bars)} {start}..{end or 'end'} equity={equity}")
    bt = Backtester(
        bars, bench, cfg, cost, asset_class_by_symbol=classes, initial_equity=equity,
        start=start, end=end, universe=universe, use_regime_filter=not no_regime, vix=vix,
    )
    res = bt.run()
    out = out or (s.hive_reports_dir / f"backtest-{sleeve}")
    paths = write_report(res, out, benchmark_bars=bench, title=f"{cfg.name} / {sleeve}")
    rprint(json.dumps(res.metrics, indent=2, default=str))
    rprint({k: str(v) for k, v in paths.items()})


@app.command()
def run(
    as_of: str | None = typer.Option(None, help="Run date (default: last cached bar). Used for replays."),
    no_shadow: bool = typer.Option(False, help="Skip the rules-only shadow portfolio"),
    no_llm: bool = typer.Option(False, help="Force rules-only for the primary portfolio"),
    live: bool = typer.Option(False, help="Require HIVE_MODE=live and the confirmation phrase"),
) -> None:
    """One daily cycle: regime -> signals -> (LLM filter) -> risk -> orders -> ledger -> report."""
    from hive.data.store import BarStore
    from hive.orchestrator.app import run_day
    from hive.orchestrator.run import SystemDeadError

    s = get_settings()
    setup_logging(jsonl_path=s.hive_state_dir / "logs" / "hive.jsonl")
    cfg = StrategyConfig.load()
    if live:
        if not s.is_live:
            raise typer.BadParameter("--live requires HIVE_MODE=live in the environment")
        s.assert_live_allowed()
    elif s.is_live:
        rprint("[yellow]HIVE_MODE=live but --live not passed: running in paper mode[/yellow]")
        s = s.model_copy(update={"hive_mode": "paper"})
    if as_of is None:
        store = BarStore(s.hive_data_dir / "cache")
        bench = store.get("etf", cfg.universe.regime_stock_symbol)
        if bench is None:
            raise typer.BadParameter("no cached data; run `hive data seed` first")
        last = bench.index[-1].date()
        btc = store.get("crypto", cfg.universe.regime_crypto_symbol)
        if btc is not None and len(btc):
            # Crypto closes a bar every UTC day, so weekend runs trade the crypto sleeve;
            # classes without a bar on the run date are skipped automatically.
            last = max(last, btc.index[-1].date())
        as_of = last.isoformat()
        age = (__import__("datetime").date.today() - last).days
        if age > 5:
            rprint(f"[yellow]cached data ends {as_of} ({age} days ago): this is a replay; "
                   f"run `hive data refresh` for a current cycle[/yellow]")
    try:
        paths = run_day(s, cfg, __import__("datetime").date.fromisoformat(as_of),
                        with_shadow=not no_shadow, llm_enabled=False if no_llm else None)
    except SystemDeadError as exc:
        rprint(f"[bold red]THE SYSTEM IS DEAD: {exc}[/bold red]")
        raise typer.Exit(code=2) from exc
    rprint({k: str(v) for k, v in paths.items()})


@app.command()
def status() -> None:
    """Survival status from the ledger and position books."""
    from hive.orchestrator.book import PositionBook
    from hive.survival.ledger import SurvivalLedger

    s = get_settings()
    cfg = StrategyConfig.load()
    ledger = SurvivalLedger(s.hive_state_dir / "ledger.jsonl", cfg.survival)
    last = ledger.last_equity_mark()
    if last is None:
        rprint("no runs yet")
        return
    st = ledger.status(last)
    rprint(st.__dict__)
    for label in ("primary", "shadow"):
        book = PositionBook(s.hive_state_dir / f"book_{label}.json")
        rprint(f"{label}: {len(book)} open positions: {sorted(book.positions)}")
    from hive.orchestrator.journal import Journal

    bad = [p.name for p in sorted((s.hive_state_dir / "journal").glob("*.jsonl")) if not Journal.verify_file(p)]
    rprint(f"kill switch: {'ENGAGED' if s.kill_switch_engaged() else 'off'}; ledger chain ok: {ledger.verify()}; "
           f"journal chains ok: {not bad}{' broken: ' + ', '.join(bad) if bad else ''}")


@app.command()
def review(days: int = typer.Option(7)) -> None:
    """Weekly auditor: proposes changes from the journals (never applies them)."""
    from hive.agents.llm import LLMClient
    from hive.agents.review import collect_review_inputs, run_review

    s = get_settings()
    setup_logging()
    inputs = collect_review_inputs(s.hive_state_dir, days=days)
    rprint({k: v for k, v in inputs.items() if k not in ("orders", "exits", "runs")})
    if s.anthropic_api_key is None:
        rprint("[yellow]no ANTHROPIC_API_KEY: inputs printed, no LLM review[/yellow]")
        return
    from hive.survival.ledger import SurvivalLedger

    cfg = StrategyConfig.load()
    ledger = SurvivalLedger(s.hive_state_dir / "ledger.jsonl", cfg.survival)
    last = ledger.last_equity_mark() or cfg.survival.starting_bankroll_usd
    today = __import__("datetime").date.today()
    st = ledger.status(last, on=today)
    # The weekly review is paid from the bankroll like every other call, out of the
    # research pot that unspent daily budget accrues into (ledger.research_budget).
    budget = ledger.research_budget(st.equity_net, ledger.tier(st.equity_net), today,
                                    bonus=_research_bonus(cfg, s.hive_state_dir))
    llm = LLMClient(api_key=s.anthropic_api_key.get_secret_value(), budget_usd=max(budget, 0.0))
    out = s.hive_reports_dir / "reviews" / f"review-{today.isoformat()}.json"
    rep = run_review(llm, s.hive_llm_model, inputs, out)
    if llm.spent_usd > 0:
        ledger.append("ai_cost", llm.spent_usd, {"label": "review", "date": f"{today.isoformat()}-research"})
    rprint(rep.model_dump() if rep else "review failed (fail closed)")
    rprint(f"wrote {out}")


hive_app = typer.Typer(no_args_is_help=True, help="Children and operator iterations")
app.add_typer(hive_app, name="hive")


@hive_app.command("list")
def hive_list() -> None:
    from hive.hive.registry import HiveRegistry

    s = get_settings()
    cfg = StrategyConfig.load()
    reg = HiveRegistry(s.hive_state_dir / "hive.json", cfg.survival)
    for c in reg.children:
        rprint(f"{c.id:28s} {c.sleeve:7s} {c.status:8s} born {c.born} days {c.shadow_days:3d} "
               f"equity {c.equity if c.equity is None else round(c.equity, 2)} voi {c.voi if c.voi is None else round(c.voi, 4)} {c.overrides}")
    rprint(f"{len(reg.active())} active; {len(reg.iterations)} iterations recorded")


@hive_app.command("spawn")
def hive_spawn(name: str, sleeve: str = typer.Option("stocks", help="stocks | crypto | all | meme"),
               overrides: str = typer.Option("", help='JSON, e.g. {"stocks.trail_atr": 6}'),
               force: bool = typer.Option(False, help="Ignore the tier's child limit (human decision)")) -> None:
    """Spawn a paper-traded child by hand (the operator does this automatically when allowed)."""
    from hive.hive.registry import Child, HiveRegistry
    from hive.operator.iterate import load_bounds, validate_overrides
    from hive.survival.ledger import SurvivalLedger

    s = get_settings()
    cfg = StrategyConfig.load()
    reg = HiveRegistry(s.hive_state_dir / "hive.json", cfg.survival)
    ledger = SurvivalLedger(s.hive_state_dir / "ledger.jsonl", cfg.survival)
    tier = ledger.tier(ledger.last_equity_mark() or cfg.survival.starting_bankroll_usd)
    ov = json.loads(overrides) if overrides else {}
    if ov:
        ok, why = validate_overrides(ov, load_bounds(s.hive_config_dir / "bounds.toml"))
        if not ok:
            raise typer.BadParameter(why)
    child = reg.spawn(Child(id="", name=name, kind="scout" if sleeve == "meme" else "variant", sleeve=sleeve,
                            overrides=ov), tier, force=force)
    rprint(f"spawned {child.id} ({sleeve}) in shadow mode")


@hive_app.command("retire")
def hive_retire(child_id: str, why: str = typer.Option("retired by human")) -> None:
    from hive.hive.registry import HiveRegistry

    s = get_settings()
    reg = HiveRegistry(s.hive_state_dir / "hive.json", StrategyConfig.load().survival)
    rprint(reg.retire(child_id, why))


@hive_app.command("promote")
def hive_promote(child_id: str, approve: bool = typer.Option(False, help="Required: a human approves")) -> None:
    """Promote a shadow child to live. Always a human decision."""
    from hive.hive.registry import HiveRegistry

    s = get_settings()
    cfg = StrategyConfig.load()
    reg = HiveRegistry(s.hive_state_dir / "hive.json", cfg.survival)
    from datetime import date as _date

    from hive.hive.governance import record_promotion

    c = reg.promote(child_id, approved_by_human=approve)
    cur = record_promotion(s, cfg, c, by="human", on=_date.today())
    rprint(f"{c.id} promoted: the primary now runs {cur['overrides']} (state/promoted_overrides.json)")


@app.command()
def operate(dry_run: bool = typer.Option(False, help="Propose and evaluate without spawning")) -> None:
    """One operator iteration: propose variants (LLM), backtest them, spawn shadow children if earned."""
    from hive.agents.llm import LLMClient
    from hive.hive.registry import HiveRegistry
    from hive.operator.iterate import default_inputs
    from hive.operator.iterate import operate as _operate
    from hive.survival.ledger import SurvivalLedger

    s = get_settings()
    setup_logging()
    cfg = StrategyConfig.load()
    if not cfg.operator.enabled:
        rprint("operator disabled in config")
        return
    ledger = SurvivalLedger(s.hive_state_dir / "ledger.jsonl", cfg.survival)
    last = ledger.last_equity_mark() or cfg.survival.starting_bankroll_usd
    today = __import__("datetime").date.today()
    st = ledger.status(last, on=today)
    tier = ledger.tier(st.equity_net)
    llm = None
    if s.anthropic_api_key is not None:
        llm = LLMClient(api_key=s.anthropic_api_key.get_secret_value(),
                        budget_usd=ledger.research_budget(st.equity_net, tier, today,
                                                          bonus=_research_bonus(cfg, s.hive_state_dir)))
    reg = HiveRegistry(s.hive_state_dir / "hive.json", cfg.survival)
    if dry_run:
        reg.path = s.hive_state_dir / "hive.dryrun.json"
    rprint(f"operator: tier {tier.name}, research pot ${(llm.budget_usd if llm else 0.0):.4f}, "
           f"llm={'on' if llm and llm.available else 'off'}")
    from hive.operator.lab import lab_dir

    recs = _operate(cfg=cfg, registry=reg, tier=tier, status=st.__dict__, llm=llm, model=s.hive_llm_model,
                    inputs=default_inputs(s, cfg), lab_dir=None if dry_run else lab_dir(s))
    if llm is not None and llm.spent_usd > 0:
        ledger.append("ai_cost", llm.spent_usd, {"label": "operator", "date": f"{today.isoformat()}-research"})
    for r in recs:
        rprint(r)


@app.command()
def watch() -> None:
    """Heartbeat: read the broker's live positions and prices into state/heartbeat.json (no orders, no LLM)."""
    from datetime import UTC, datetime

    import pandas as pd

    from hive.data.store import BarStore
    from hive.orchestrator.app import asset_classes_from_store, build_primary_broker

    s = get_settings()
    setup_logging()
    cfg = StrategyConfig.load()
    store = BarStore(s.hive_data_dir / "cache")
    classes = asset_classes_from_store(store, cfg)
    now = datetime.now(UTC)
    broker = build_primary_broker(s, store, classes, pd.Timestamp(now.date()), cfg.survival.starting_bankroll_usd)
    positions = []
    for p in broker.positions().values():
        pnl = (p.current_price / p.avg_price - 1.0) if p.avg_price else None
        positions.append({"symbol": p.symbol, "asset_class": p.asset_class, "qty": p.qty, "avg_price": p.avg_price,
                          "current_price": p.current_price, "market_value": p.market_value, "unrealised_pct": pnl,
                          "stop_price": p.stop_price})
    hb = {"ts": now.isoformat(timespec="seconds"), "broker": getattr(broker, "name", "broker"),
          "equity": broker.equity(), "cash": broker.cash(), "market_open": bool(broker.is_market_open()),
          "positions": positions}
    s.hive_state_dir.mkdir(parents=True, exist_ok=True)
    (s.hive_state_dir / "heartbeat.json").write_text(json.dumps(hb, indent=1, default=str))
    rprint(f"heartbeat {hb['ts']}: {hb['broker']} equity ${hb['equity']:.2f}, {len(positions)} positions, "
           f"market {'open' if hb['market_open'] else 'closed'}")


@app.command()
def sweep(cells: int = typer.Option(3, help="Untested cells to backtest this run (ignored with --minutes)"),
          minutes: float | None = typer.Option(None, help="Backtest cells until this many minutes have passed"),
          refine: bool = typer.Option(True, help="After the grid, refine around the best cells so far"),
          round_: int = typer.Option(1, "--round", help="This round's number within the hourly run"),
          rounds: int = typer.Option(1, help="Rounds in the hourly run (for the dashboard's status line)")) -> None:
    """The lab: backtest the next cells of the strategy space against the parent (no LLM).
    Every cell is kept in reports/lab/ with its curves and shown on the dashboard."""
    from datetime import UTC, datetime
    from datetime import date as _date

    from hive.hive.registry import HiveRegistry
    from hive.operator.iterate import load_bounds
    from hive.operator.lab import lab_dir, summary, write_status
    from hive.operator.sweep import queue, run_sweep
    from hive.survival.ledger import SurvivalLedger

    s = get_settings()
    setup_logging()
    cfg = StrategyConfig.load()
    ledger = SurvivalLedger(s.hive_state_dir / "ledger.jsonl", cfg.survival)
    last = ledger.last_equity_mark() or cfg.survival.starting_bankroll_usd
    tier = ledger.tier(ledger.status(last).equity_net)
    reg = HiveRegistry(s.hive_state_dir / "hive.json", cfg.survival)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    write_status(s.hive_state_dir, {"state": "running", "started": started, "round": round_, "rounds": rounds,
                                    "minutes": minutes})
    recs = run_sweep(s, cfg, reg, tier, n_cells=cells, on=_date.today(), with_refine=refine,
                     budget_s=None if minutes is None else minutes * 60.0)
    for r in recs:
        rprint(f"{r['proposal']}: {r['verdict']}")
    sm = summary(lab_dir(s))
    left = len(queue(reg, cfg, load_bounds(REPO_ROOT / cfg.operator.bounds_file), lab_dir(s), with_refine=refine))
    write_status(s.hive_state_dir, {"state": "done", "started": started, "round": round_, "rounds": rounds,
                                    "minutes": minutes, "finished": datetime.now(UTC).isoformat(timespec="seconds"),
                                    "cells": len(recs), "queued": left})
    rprint(f"lab: {len(recs)} cells this run, {sm['n_tests']} tests on record ({sm['n_passed']} beat the parent), "
           f"{left} queued")


def _research_bonus(cfg: StrategyConfig, state_dir: Path) -> float:
    """1 + 0.25 per sub-agent that has beaten the parent for the shadow period, capped at 2x."""
    from hive.hive.registry import HiveRegistry

    try:
        n = len(HiveRegistry(state_dir / "hive.json", cfg.survival).ready_to_promote())
    except Exception:
        n = 0
    return min(1.0 + 0.25 * n, 2.0)


@app.command()
def trench() -> None:
    """Trench scout: one paper scan of brand-new Solana launches (keyless; no wallet; no orders)."""
    from datetime import UTC, datetime

    from hive.hive.registry import Child, HiveRegistry
    from hive.meme.scout import MemeBook
    from hive.meme.trench import run_trench_round
    from hive.orchestrator.journal import Journal
    from hive.survival.ledger import SurvivalLedger

    s = get_settings()
    setup_logging()
    cfg = StrategyConfig.load()
    if not s.hive_network_data:
        rprint("trench scout skipped: network data disabled")
        return
    reg = HiveRegistry(s.hive_state_dir / "hive.json", cfg.survival)
    child = next((c for c in reg.children if c.kind == "scout" and c.sleeve == "trench"), None)
    if child is None:
        ledger = SurvivalLedger(s.hive_state_dir / "ledger.jsonl", cfg.survival)
        tier = ledger.tier(ledger.status(ledger.last_equity_mark() or cfg.survival.starting_bankroll_usd).equity_net)
        child = reg.spawn(Child(id="trench-scout", name="trench scout", kind="scout", sleeve="trench",
                                hypothesis="brand-new Solana launches with live buyer flow; paper only, own $100 book"),
                          tier, force=True)
    if child.status not in ("shadow", "live"):
        rprint(f"trench scout is {child.status}: {(child.notes or ['-'])[-1]}")
        return
    cdir = s.hive_state_dir / "children" / child.id
    book = MemeBook.load(cdir / "trench_book.json", cfg.trench.starting_cash_usd)
    now = datetime.now(UTC)
    journal = Journal(s.hive_state_dir / "journal", f"{now.date().isoformat()}-child-{child.id}")
    out = run_trench_round(book=book, rules=cfg.trench, now=now, journal=journal.write)
    book.save(cdir / "trench_book.json")
    journal.write("trench_round", {k: out[k] for k in ("ts", "scanned", "discovered", "passed", "rugchecked", "rejects",
                                                       "equity", "cash", "n_open")}
                  | {"n_entries": len(out["entries"]), "n_exits": len(out["exits"])})
    (cdir / "latest.json").write_text(json.dumps(out, default=str))
    child.equity = out["equity"]
    if child.start_equity is None:
        child.start_equity = cfg.trench.starting_cash_usd
    reg.save()
    reg.auto_retire_losers(min_days=cfg.survival.child_retire_days, max_negative_voi=cfg.survival.child_retire_voi,
                           max_drawdown=cfg.survival.child_max_drawdown_pct, on=now.date())
    rprint(f"trench scout: scanned {out['scanned']} launches, {out['passed']} passed, {len(out['entries'])} entries, "
           f"{len(out['exits'])} exits, {out['n_open']} open, paper equity ${out['equity']:.2f}")


@app.command()
def pulse() -> None:
    """The social pulse: what StockTwits, Reddit, DexScreener (and X, with a paid token) are talking about."""
    from hive.data.sources.social_pulse import pulse as _pulse

    s = get_settings()
    setup_logging()
    if not (s.hive_network_data and s.hive_social_data):
        rprint("social pulse skipped: network or social data disabled")
        return
    from hive.data.sources.social_pulse import SUBREDDITS

    sdir = s.hive_state_dir / "social"
    sdir.mkdir(parents=True, exist_ok=True)
    prev = json.loads((sdir / "latest.json").read_text()) if (sdir / "latest.json").exists() else {}
    known = prev.get("reddit_by_sub") or {}
    # one community a round, the one read longest ago: anonymous Reddit allows a cloud runner one
    # read and refuses the next, so the rotation covers all six about once a session
    order = sorted(SUBREDDITS, key=lambda sub: (known.get(sub) or {}).get("ts") or "")
    out = _pulse(x_token=s.x_bearer_token.get_secret_value() if s.x_bearer_token else None,
                 subreddits=tuple(order[:1]), known_reddit=known)
    (sdir / "latest.json").write_text(json.dumps(out, default=str))
    compact = {"ts": out["ts"], "stocktwits": [r["symbol"] for r in out["stocktwits"][:10]],
               "reddit": out["reddit"][:10], "cross": [r["symbol"] for r in out["cross"][:10]], "x": out["x"]}
    with (sdir / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(compact, default=str) + "\n")
    rprint(f"social pulse: {len(out['stocktwits'])} trending on StockTwits, {out['reddit_posts_read']} Reddit posts read, "
           f"{len(out['cross'])} names on more than one source; X {out['x_status']}")


@app.command()
def prop(notify_file: Path | None = typer.Option(None, help="Write an issue body here when a book is newly ready"),
         mark_notified: bool = typer.Option(False, help="Record the books ready now as already announced")) -> None:
    """Prop-firm readiness: is any book ready for a real evaluation? Writes state/prop_readiness.json."""
    from hive.hive.propfirm import notice, readiness_report
    from hive.hive.registry import HiveRegistry
    from hive.operator.lab import lab_dir, read_tests

    s = get_settings()
    cfg = StrategyConfig.load()
    path = s.hive_state_dir / "prop_readiness.json"
    prev = json.loads(path.read_text()) if path.exists() else {}
    reg = HiveRegistry(s.hive_state_dir / "hive.json", cfg.survival)
    report = readiness_report(s, cfg, reg, read_tests(lab_dir(s), with_curves=False))
    notified = set(prev.get("notified") or [])
    if mark_notified:
        notified |= set(report["ready"])
    report["notified"] = sorted(notified)
    path.write_text(json.dumps(report, indent=1, default=str))
    new = [k for k in report["ready"] if k not in notified]
    if notify_file is not None:
        notify_file.write_text(notice(report, new) if new else "")
    for b in report["books"][:6]:
        rprint(f"{'READY' if b['ready'] else 'not yet'}  {b['name']}: {b['n_ok']}/{len(b['checks'])} checks; "
               + "; ".join(f"{'ok' if c['ok'] else 'no'} {c['check']} ({c['value']})" for c in b["checks"]))
    rprint(f"{len(report['ready'])} book(s) ready, {len(new)} not yet announced")


@app.command("capital-in")
def capital_in(amount: float = typer.Option(..., help="USD added to the bankroll (e.g. a venue deposit)"),
               note: str = typer.Option("", help="Where it went (e.g. 'Alpaca crypto deposit')")) -> None:
    """Record money added to the bankroll. The study's basis grows by exactly this amount, publicly."""
    from datetime import date as _date

    from hive.survival.ledger import SurvivalLedger

    s = get_settings()
    cfg = StrategyConfig.load()
    if amount <= 0:
        raise typer.BadParameter("amount must be positive")
    ledger = SurvivalLedger(s.hive_state_dir / "ledger.jsonl", cfg.survival)
    ledger.append("capital_in", float(amount), {"why": note or "capital added", "date": _date.today().isoformat()})
    rprint(f"capital_in ${amount:,.2f} recorded ({note or 'no note'}); the bankroll basis is now "
           f"${cfg.survival.starting_bankroll_usd + ledger.capital_in_total():,.2f}")


@app.command()
def allocate() -> None:
    """Weekly capital allocation between desks from realised results (bounded; no LLM)."""
    from datetime import date as _date

    from hive.hive.allocation import rebalance
    from hive.survival.ledger import SurvivalLedger

    s = get_settings()
    setup_logging()
    cfg = StrategyConfig.load()
    before = None
    path = s.hive_state_dir / "allocation.json"
    if path.exists():
        before = json.loads(path.read_text()).get("crypto_sleeve_pct")
    alloc = rebalance(path, s.hive_state_dir / "journal", _date.today(), cfg.risk.crypto_sleeve_pct)
    last = alloc["history"][-1]
    if before is not None and abs(float(before) - float(alloc["crypto_sleeve_pct"])) > 1e-9:
        SurvivalLedger(s.hive_state_dir / "ledger.jsonl", cfg.survival).append(
            "allocation_change", 0.0, {"from": before, "to": alloc["crypto_sleeve_pct"], "why": last["why"]})
    rprint(f"allocation: crypto sleeve {float(alloc['crypto_sleeve_pct']):.0%} — {last['why']}")


def _parse_variations(spec: str) -> dict[str, list]:
    """``"a.b=1,2,3;c.d=0.5,1"`` -> ``{"a.b": [1,2,3], "c.d": [0.5, 1]}`` (JSON-typed values)."""
    out: dict[str, list] = {}
    for part in filter(None, spec.split(";")):
        key, _, vals = part.partition("=")
        parsed = []
        for v in vals.split(","):
            v = v.strip()
            try:
                parsed.append(json.loads(v))
            except json.JSONDecodeError:
                parsed.append(v)
        out[key.strip()] = parsed
    return out


@app.command()
def experiment(
    kind: str = typer.Option("oat", help="oat | grid | null"),
    params: str = typer.Option("", help='e.g. "stocks.trail_atr=3,4,5;stocks.reset_period=15,21,30"'),
    seeds: int = typer.Option(5, help="Null-test seeds"),
    sleeve: str = typer.Option("stocks"),
    start: str = typer.Option("2001-01-01"),
    end: str | None = typer.Option(None),
    equity: float = typer.Option(100.0),
    n_jobs: int = typer.Option(3),
    out: Path | None = typer.Option(None, help="CSV path"),
    config: Path | None = typer.Option(None),
    entry_mode: str = typer.Option("signal", help="signal | template_cross | random_template"),
) -> None:
    """Robustness grid / one-at-a-time sensitivity / null test on cached data."""
    from hive.backtest.costs import ALPACA_CRYPTO, ALPACA_STOCKS
    from hive.backtest.experiments import (
        ExperimentInputs,
        benchmark_row,
        run_grid,
        run_null_test,
        run_oat,
        summarize_null,
    )
    from hive.data.seed import load_bars_for_backtest, load_pit_universe
    from hive.data.store import BarStore

    setup_logging()
    s = get_settings()
    cfg = StrategyConfig.load(config)
    store = BarStore(s.hive_data_dir / "cache")
    bars, classes, bench = load_bars_for_backtest(store, cfg, sleeve=sleeve)
    pit_path = s.hive_data_dir / "raw" / "sp500_pit_membership.csv"
    universe = load_pit_universe(pit_path) if (sleeve != "crypto" and pit_path.exists()) else None
    vix_bars = store.get("index", "VIX")
    inputs = ExperimentInputs(
        bars=bars, classes=classes, bench=bench,
        cost=ALPACA_CRYPTO if sleeve == "crypto" else ALPACA_STOCKS, universe=universe,
        vix=vix_bars["close"] if vix_bars is not None else None, equity=equity, start=start, end=end,
    )
    out = out or (s.hive_reports_dir / "experiments" / f"{kind}-{sleeve}-{start}-{end or 'end'}.csv")
    rprint(f"[bold]experiment[/bold] {kind} sleeve={sleeve} {start}..{end or 'end'} symbols={len(bars)}")
    rprint({"benchmark": {k: round(v, 4) for k, v in benchmark_row(inputs).items()}})
    extra = {"entry_mode": entry_mode} if entry_mode != "signal" else {}
    if kind == "oat":
        df = run_oat(inputs, cfg, _parse_variations(params), out_csv=out, n_jobs=n_jobs, extra=extra)
        cols = ["param", "value", "cagr", "sharpe", "max_drawdown", "n_trades", "win_rate",
                "profit_factor", "avg_hold_days", "avg_exposure", "n_drawdown_halts"]
    elif kind == "grid":
        df = run_grid(inputs, cfg, _parse_variations(params), out_csv=out, n_jobs=n_jobs, extra=extra)
        cols = list(df.columns)
    else:
        from hive.backtest.experiments import _apply_overrides

        # For the null test, --params gives single-value overrides of the config under test.
        single = {k: v[0] for k, v in _parse_variations(params).items()} if params else {}
        cfg = _apply_overrides(cfg, single)
        df = run_null_test(inputs, cfg, seeds=list(range(seeds)), out_csv=out, n_jobs=n_jobs)
        base = run_oat(inputs, cfg, {}, out_csv=out.with_suffix(".base.csv"), n_jobs=1)
        rprint(summarize_null(df, float(base["cagr"].iloc[0])))
        cols = ["seed", "cagr", "sharpe", "max_drawdown", "n_trades", "win_rate", "avg_hold_days"]
    with __import__("pandas").option_context("display.width", 200, "display.max_columns", 30):
        rprint(df[[c for c in cols if c in df.columns]].round(4).to_string())
    rprint(f"wrote {out}")


if __name__ == "__main__":  # pragma: no cover
    app()
