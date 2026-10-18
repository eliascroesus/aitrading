"""Command-line entry point: ``hive <command>``."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint

from hive.config import StrategyConfig, get_settings
from hive.log import setup_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="HIVE trading system")
data_app = typer.Typer(no_args_is_help=True, help="Data cache management")
app.add_typer(data_app, name="data")


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
        as_of = bench.index[-1].date().isoformat()
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
    rprint(f"kill switch: {'ENGAGED' if s.kill_switch_engaged() else 'off'}; ledger chain ok: {ledger.verify()}")


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
    llm = LLMClient(api_key=s.anthropic_api_key.get_secret_value(), budget_usd=1.0)
    out = s.hive_reports_dir / "reviews" / f"review-{__import__('datetime').date.today().isoformat()}.json"
    rep = run_review(llm, s.hive_llm_model, inputs, out)
    rprint(rep.model_dump() if rep else "review failed (fail closed)")
    rprint(f"wrote {out}")


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
        df = run_null_test(inputs, cfg, seeds=list(range(seeds)), out_csv=out, n_jobs=n_jobs)
        base = run_oat(inputs, cfg, {}, out_csv=out.with_suffix(".base.csv"), n_jobs=1)
        rprint(summarize_null(df, float(base["cagr"].iloc[0])))
        cols = ["seed", "cagr", "sharpe", "max_drawdown", "n_trades", "win_rate", "avg_hold_days"]
    with __import__("pandas").option_context("display.width", 200, "display.max_columns", 30):
        rprint(df[[c for c in cols if c in df.columns]].round(4).to_string())
    rprint(f"wrote {out}")


if __name__ == "__main__":  # pragma: no cover
    app()
