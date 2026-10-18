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


if __name__ == "__main__":  # pragma: no cover
    app()
