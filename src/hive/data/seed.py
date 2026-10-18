"""Seed the parquet cache from offline sources so backtests run without network.

Sources (all public, documented in docs/DATA.md):

* Qlib US daily dataset (Yahoo-derived, 1999-12-31 -> 2020-11-10, ~9k symbols)
* CoinMetrics community CSVs (BTC/ETH daily since inception)
* Point-in-time S&P 500 membership CSV (1996 -> 2025)
* CBOE VIX daily CSV
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from hive.config import StrategyConfig
from hive.data.bars import normalize_bars
from hive.data.sources import coinmetrics
from hive.data.sources.qlib_bin import QlibDataset
from hive.data.store import BarStore
from hive.data.universe import PointInTimeUniverse

log = logging.getLogger(__name__)

INDEX_SYMBOLS = {"^GSPC": "SPX", "^NDX": "NDX", "^DJI": "DJI"}


def qlib_symbol(sym: str) -> str:
    """Membership files use ``BRK-B``/``BRK.B``; Qlib (Yahoo) uses ``BRK-B``."""
    return sym.upper().replace(".", "-")


def seed_from_qlib(
    store: BarStore,
    qlib_root: Path,
    *,
    stock_symbols: Iterable[str],
    etf_symbols: Iterable[str],
    include_indices: bool = True,
) -> dict[str, int]:
    ds = QlibDataset(qlib_root)
    available = set(ds.symbols())
    counts = {"stock": 0, "etf": 0, "index": 0, "missing": 0}
    for sym in sorted({qlib_symbol(s) for s in stock_symbols}):
        if sym not in available:
            counts["missing"] += 1
            continue
        bars = ds.bars(sym, prices="raw")
        if len(bars) < 50:
            continue
        store.put("stock", sym, bars, source="qlib_us_1d", extra_meta={"prices": "raw+adj"})
        counts["stock"] += 1
    for sym in sorted({qlib_symbol(s) for s in etf_symbols}):
        if sym not in available:
            counts["missing"] += 1
            continue
        bars = ds.bars(sym, prices="raw")
        store.put("etf", sym, bars, source="qlib_us_1d", extra_meta={"prices": "raw+adj"})
        counts["etf"] += 1
    if include_indices:
        for raw, name in INDEX_SYMBOLS.items():
            if raw in available:
                store.put("index", name, ds.bars(raw, prices="raw"), source="qlib_us_1d")
                counts["index"] += 1
    return counts


def seed_coinmetrics(store: BarStore, folder: Path, assets: Iterable[str] = ("btc", "eth")) -> list[str]:
    done = []
    for a in assets:
        p = Path(folder) / f"{a.lower()}.csv"
        if not p.exists():
            log.warning("coinmetrics file missing: %s", p)
            continue
        bars = coinmetrics.parse_csv(p)
        store.put("crypto", a.upper(), bars, source="coinmetrics_community",
                  extra_meta={"ohlc": "close-only"})
        done.append(a.upper())
    return done


def seed_vix(store: BarStore, path: Path) -> bool:
    if not Path(path).exists():
        return False
    raw = pd.read_csv(path)
    cols = {c.lower(): c for c in raw.columns}
    date_col = cols.get("date")
    close_col = cols.get("close") or cols.get("vix close")
    df = pd.DataFrame({"date": raw[date_col], "close": raw[close_col]})
    store.put("index", "VIX", normalize_bars(df), source="cboe_vix_daily_csv")
    return True


def load_pit_universe(path: Path) -> PointInTimeUniverse:
    return PointInTimeUniverse.from_membership_csv(path)


def seed_all(
    store: BarStore,
    *,
    raw_dir: Path,
    qlib_root: Path | None,
    cfg: StrategyConfig,
) -> dict[str, object]:
    """One-shot seed using the conventional raw-file layout under ``raw_dir``."""
    summary: dict[str, object] = {}
    pit_path = raw_dir / "sp500_pit_membership.csv"
    stock_syms: set[str] = set()
    if pit_path.exists():
        uni = load_pit_universe(pit_path)
        stock_syms = {qlib_symbol(s) for s in uni.all_tickers()}
        summary["pit_universe_tickers"] = len(stock_syms)
    if qlib_root is not None and Path(qlib_root).exists():
        summary["qlib"] = seed_from_qlib(
            store, Path(qlib_root), stock_symbols=stock_syms, etf_symbols=cfg.universe.etf_symbols,
        )
    summary["coinmetrics"] = seed_coinmetrics(store, raw_dir / "coinmetrics")
    summary["vix"] = seed_vix(store, raw_dir / "vix-daily.csv")
    return summary


def load_bars_for_backtest(
    store: BarStore, cfg: StrategyConfig, *, sleeve: str = "stocks"
) -> tuple[dict[str, pd.DataFrame], dict[str, str], pd.DataFrame]:
    """Return (bars_by_symbol, asset_class_by_symbol, benchmark_bars) for a sleeve."""
    bars: dict[str, pd.DataFrame] = {}
    classes: dict[str, str] = {}
    if sleeve in ("stocks", "all"):
        for sym in store.symbols("stock"):
            b = store.get("stock", sym)
            if b is not None and len(b) >= cfg.universe.min_history_bars:
                bars[sym], classes[sym] = b, "stock"
        for sym in store.symbols("etf"):
            b = store.get("etf", sym)
            if b is not None and len(b) >= cfg.universe.min_history_bars:
                bars[sym], classes[sym] = b, "etf"
        bench = store.get("etf", cfg.universe.regime_stock_symbol)
    if sleeve in ("crypto", "all"):
        for sym in store.symbols("crypto"):
            b = store.get("crypto", sym)
            if b is not None and len(b) >= cfg.universe.min_history_bars:
                bars[sym], classes[sym] = b, "crypto"
        if sleeve == "crypto":
            bench = store.get("crypto", cfg.universe.regime_crypto_symbol)
    if bench is None:
        raise FileNotFoundError("benchmark bars missing from the store; run `hive data seed`")
    return bars, classes, bench
