"""Persisted per-position metadata the broker does not keep (R, stops, bars held)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class OpenPosition:
    symbol: str
    asset_class: str
    qty: float
    entry_price: float
    entry_date: str
    r_per_share: float
    stop_price: float
    highest_close: float
    bars_held: int = 0
    invalidation_condition: str = "none"
    source: str = "rules"
    stop_order_id: str = ""
    below_mid_streak: int = 0
    meta: dict = field(default_factory=dict)


class PositionBook:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.positions: dict[str, OpenPosition] = {}
        if self.path.exists():
            d = json.loads(self.path.read_text())
            self.positions = {s: OpenPosition(**p) for s, p in d.get("positions", {}).items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"positions": {s: asdict(p) for s, p in self.positions.items()}},
                                        indent=2, default=str))

    def add(self, pos: OpenPosition) -> None:
        self.positions[pos.symbol] = pos
        self.save()

    def remove(self, symbol: str) -> OpenPosition | None:
        pos = self.positions.pop(symbol, None)
        self.save()
        return pos

    def __contains__(self, symbol: str) -> bool:
        return symbol in self.positions

    def __len__(self) -> int:
        return len(self.positions)
