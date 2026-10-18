"""Per-ticker CSV folders in the Yahoo/Kaggle layout (``<TICKER>.csv``).

Each file: ``Date,Open,High,Low,Close,Adj Close,Volume``. Used to seed the
cache from public historical dumps so backtests can run fully offline.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from hive.data.bars import normalize_bars
from hive.data.store import BarStore

log = logging.getLogger(__name__)


def read_ticker_csv(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    return normalize_bars(raw)


def available_symbols(folder: Path) -> list[str]:
    return sorted(p.stem.upper() for p in Path(folder).glob("*.csv"))


def seed_store(
    store: BarStore,
    folder: Path,
    asset_class: str,
    *,
    symbols: Iterable[str] | None = None,
    source: str = "csv_folder",
) -> list[str]:
    """Load every (or the requested) ``<TICKER>.csv`` into the store. Returns loaded symbols."""
    folder = Path(folder)
    wanted = {s.upper() for s in symbols} if symbols is not None else None
    loaded: list[str] = []
    for p in sorted(folder.glob("*.csv")):
        sym = p.stem.upper()
        if wanted is not None and sym not in wanted:
            continue
        try:
            bars = read_ticker_csv(p)
        except Exception as exc:  # malformed file in a public dump; skip, don't abort
            log.warning("skipping %s: %s", p.name, exc)
            continue
        if bars.empty:
            continue
        store.put(asset_class, sym, bars, source=f"{source}:{p.name}")
        loaded.append(sym)
    return loaded
