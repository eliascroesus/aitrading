"""Refresh the bar cache from network sources (best effort; never fatal)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from hive.config import StrategyConfig
from hive.data.sources import hyperliquid_source, yfinance_source
from hive.data.store import BarStore
from hive.data.universe import PointInTimeUniverse

log = logging.getLogger(__name__)


def _chunks(items: list[str], n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def complete_days(bars: pd.DataFrame) -> pd.DataFrame:
    """Drop the current UTC day. Crypto trades around the clock, so the last daily
    candle a source returns is still forming; a signal computed on it would change
    by the close. The run therefore sees only closed days."""
    if not len(bars):
        return bars
    idx = pd.DatetimeIndex(bars.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    today = pd.Timestamp(datetime.now(UTC).date())
    return bars[idx.normalize() < today]


def refresh_all(store: BarStore, cfg: StrategyConfig, *, pit_csv: Path | None, days: int = 400,
                hl_testnet: bool = False) -> dict[str, int]:
    start = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    counts = {"stock": 0, "etf": 0, "crypto": 0, "index": 0, "errors": 0}

    stock_syms: list[str] = []
    if pit_csv is not None and Path(pit_csv).exists():
        uni = PointInTimeUniverse.from_membership_csv(pit_csv)
        stock_syms = sorted(s.replace(".", "-") for s in uni.members(uni.last_date))
    elif cfg.universe.custom_symbols:
        stock_syms = [s.upper() for s in cfg.universe.custom_symbols]

    for ac, syms in (("stock", stock_syms), ("etf", cfg.universe.etf_symbols)):
        for chunk in _chunks(list(syms), 100):
            try:
                got = yfinance_source.fetch_daily(chunk, start=start)
            except Exception as exc:
                counts["errors"] += 1
                log.warning("yfinance chunk failed (%s...): %s", chunk[:3], exc)
                continue
            for sym, bars in got.items():
                store.upsert(ac, sym, bars, source="yfinance")
                counts[ac] += 1

    for coin in cfg.universe.crypto_symbols:
        done = False
        try:
            bars = complete_days(hyperliquid_source.fetch_daily(coin, days=days, testnet=hl_testnet))
            if len(bars):
                store.upsert("crypto", coin, bars, source="hyperliquid_candles")
                done = True
        except Exception as exc:
            log.warning("hyperliquid candles failed for %s: %s", coin, exc)
        if not done:
            try:
                got = yfinance_source.fetch_daily([yfinance_source.yahoo_crypto_symbol(coin)], start=start)
                for _, bars in got.items():
                    bars = complete_days(bars)
                    if len(bars):
                        store.upsert("crypto", coin, bars, source="yfinance")
                        done = True
            except Exception as exc:
                counts["errors"] += 1
                log.warning("yfinance crypto failed for %s: %s", coin, exc)
        counts["crypto"] += int(done)

    try:
        got = yfinance_source.fetch_daily(["^VIX"], start=start)
        for _, bars in got.items():
            store.upsert("index", "VIX", bars, source="yfinance")
            counts["index"] += 1
    except Exception as exc:
        counts["errors"] += 1
        log.warning("VIX refresh failed: %s", exc)
    return counts
