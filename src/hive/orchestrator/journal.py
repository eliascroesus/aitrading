"""Append-only decision journal: one JSONL file per run date, hash-chained."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, directory: Path, run_date: str):
        self.path = Path(directory) / f"{run_date}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prev = "0" * 64
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    self._prev = json.loads(line).get("hash", self._prev)

    def write(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "kind": kind,
            "payload": payload,
            "prev_hash": self._prev,
        }
        entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        self._prev = entry["hash"]
        return entry

    @staticmethod
    def read(path: Path) -> list[dict[str, Any]]:
        if not Path(path).exists():
            return []
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]

    @staticmethod
    def verify_file(path: Path) -> bool:
        prev = "0" * 64
        for e in Journal.read(path):
            h = e.get("hash")
            body = {k: v for k, v in e.items() if k != "hash"}
            if e.get("prev_hash") != prev:
                return False
            if hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest() != h:
                return False
            prev = h
        return True
