"""Parquet bar store with provenance sidecars."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from hive.data.bars import BAR_COLUMNS, normalize_bars

ASSET_CLASSES = ("stock", "etf", "crypto", "index", "macro")


def _safe_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "_").replace(":", "_")


class BarStore:
    """``data/cache/<asset_class>/<SYMBOL>.parquet`` + ``.json`` sidecar."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # -- paths ---------------------------------------------------------------
    def path(self, asset_class: str, symbol: str) -> Path:
        if asset_class not in ASSET_CLASSES:
            raise ValueError(f"unknown asset class {asset_class!r}")
        return self.root / asset_class / f"{_safe_symbol(symbol)}.parquet"

    def meta_path(self, asset_class: str, symbol: str) -> Path:
        return self.path(asset_class, symbol).with_suffix(".json")

    # -- queries -------------------------------------------------------------
    def has(self, asset_class: str, symbol: str) -> bool:
        return self.path(asset_class, symbol).exists()

    def symbols(self, asset_class: str) -> list[str]:
        d = self.root / asset_class
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.parquet"))

    def get(self, asset_class: str, symbol: str) -> pd.DataFrame | None:
        p = self.path(asset_class, symbol)
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        df.index = pd.DatetimeIndex(df.index, name="date")
        return df[BAR_COLUMNS]

    def meta(self, asset_class: str, symbol: str) -> dict[str, Any] | None:
        p = self.meta_path(asset_class, symbol)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    # -- writes --------------------------------------------------------------
    def put(
        self,
        asset_class: str,
        symbol: str,
        bars: pd.DataFrame,
        *,
        source: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> Path:
        bars = normalize_bars(bars) if list(bars.columns) != BAR_COLUMNS else bars
        p = self.path(asset_class, symbol)
        p.parent.mkdir(parents=True, exist_ok=True)
        bars.to_parquet(p)
        meta = {
            "symbol": _safe_symbol(symbol),
            "asset_class": asset_class,
            "source": source,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "rows": int(len(bars)),
            "first": bars.index.min().date().isoformat() if len(bars) else None,
            "last": bars.index.max().date().isoformat() if len(bars) else None,
            **(extra_meta or {}),
        }
        self.meta_path(asset_class, symbol).write_text(json.dumps(meta, indent=2))
        return p

    def upsert(
        self,
        asset_class: str,
        symbol: str,
        new_bars: pd.DataFrame,
        *,
        source: str,
    ) -> Path:
        """Merge new rows into the cached frame (new rows win on overlap).

        Different sources use different adjustment bases (a Qlib total-return
        index normalised at the first bar vs. yfinance's backward adjustment
        anchored at the last bar). Splicing them raw puts a step of 100x in
        ``adj_close`` at the seam, so the old rows' ``adj_close`` is rescaled
        to the new basis on the first overlapping bar (or at the seam when
        there is no overlap), keeping ``adj_close/close`` continuous.
        """
        new_bars = normalize_bars(new_bars) if list(new_bars.columns) != BAR_COLUMNS else new_bars
        old = self.get(asset_class, symbol)
        if old is None or new_bars.empty:
            merged = new_bars if old is None else old
        else:
            old = old.copy()
            overlap = old.index.intersection(new_bars.index)
            if len(overlap):
                anchor = overlap[0]
                k = float(new_bars.at[anchor, "adj_close"] / old.at[anchor, "adj_close"])
            else:
                # No overlap: carry the new factor (adj/close) back onto the old rows.
                f_new = float(new_bars["adj_close"].iloc[0] / new_bars["close"].iloc[0])
                f_old = float(old["adj_close"].iloc[-1] / old["close"].iloc[-1])
                k = f_new / f_old
            if np.isfinite(k) and k > 0:
                old["adj_close"] = old["adj_close"] * k
            merged = pd.concat([old, new_bars])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        return self.put(asset_class, symbol, merged, source=source)
