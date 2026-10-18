"""Logging: human-readable console + append-only JSON lines for the audit trail."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path


class JsonLinesHandler(logging.Handler):
    def __init__(self, path: Path):
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.format(record)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")


def setup_logging(level: str = "INFO", jsonl_path: Path | None = None) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    try:
        from rich.logging import RichHandler

        console: logging.Handler = RichHandler(rich_tracebacks=False, show_path=False, markup=False)
    except Exception:  # pragma: no cover - rich is a hard dependency, but stay safe
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)
    if jsonl_path is not None:
        root.addHandler(JsonLinesHandler(jsonl_path))
    # Third-party chatter is noise for an audit log.
    for noisy in ("httpx", "httpcore", "urllib3", "yfinance", "peewee", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("hive")
