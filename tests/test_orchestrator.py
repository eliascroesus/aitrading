from datetime import date

import numpy as np
import pandas as pd

from hive.config import REPO_ROOT, Settings, StrategyConfig
from hive.data.store import BarStore
from hive.orchestrator.app import run_day
from hive.orchestrator.book import PositionBook
from hive.orchestrator.journal import Journal
from hive.survival.ledger import SurvivalLedger
from tests.test_features import bars_from_path, textbook_setup


def test_strategy_toml_matches_code_defaults():
    loaded = StrategyConfig.load(REPO_ROOT / "config" / "strategy.toml")
    assert loaded.model_dump() == StrategyConfig().model_dump()


def _seed_store(root):
    store = BarStore(root / "cache")
    close, volume = textbook_setup(n_after=40)
    bars = bars_from_path(close, volume, start="2018-01-01")
    store.put("stock", "AAA", bars, source="test")
    store.put("stock", "BBB", bars * 1.5, source="test")  # same shape, different level
    # Benchmark in a clean uptrend so the regime is GREEN.
    bench = bars_from_path(100 * np.cumprod(np.full(len(close), 1.001)), start="2018-01-01")
    store.put("etf", "SPY", bench, source="test")
    return store, bars


def test_daily_run_replay_opens_positions_and_books_costs(tmp_path):
    store, bars = _seed_store(tmp_path)
    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", hive_llm_enabled=False, anthropic_api_key=None,
                 alpaca_api_key=None, hive_network_data=False, _env_file=None)
    cfg = StrategyConfig()
    # the entry signal fires near the end of the textbook path; pick that day
    from hive.signals.features import compute_features

    f = compute_features(bars, cfg.stocks)
    signal_day = f.index[f["entry_signal"]][-1].date()
    paths = run_day(s, cfg, signal_day, with_shadow=True)
    assert paths["markdown"].exists() and paths["json"].exists()
    book = PositionBook(tmp_path / "state" / "book_primary.json")
    assert len(book) >= 1, "rules-only primary should have opened a position on the signal day"
    pos = next(iter(book.positions.values()))
    assert pos.stop_price < pos.entry_price and pos.r_per_share > 0
    ledger = SurvivalLedger(tmp_path / "state" / "ledger.jsonl", cfg.survival)
    assert ledger.last_equity_mark() is not None and ledger.verify()
    shadow = PositionBook(tmp_path / "state" / "book_shadow.json")
    assert len(shadow) == len(book)
    # journal is chained and has the expected kinds
    entries = Journal.read(tmp_path / "state" / "journal" / f"{signal_day.isoformat()}.jsonl")
    kinds = {e["kind"] for e in entries}
    assert {"run_start", "regime", "candidates", "decisions", "risk", "order", "run_end"} <= kinds
    # next day: position is managed (bars_held increments), no duplicate entry
    next_day = (pd.Timestamp(signal_day) + pd.offsets.BDay(1)).date()
    run_day(s, cfg, next_day, with_shadow=False)
    book2 = PositionBook(tmp_path / "state" / "book_primary.json")
    assert all(p.bars_held >= 1 for p in book2.positions.values())


def test_kill_switch_blocks_orders(tmp_path):
    store, bars = _seed_store(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "KILL").write_text("stop")
    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", hive_kill_switch_file=tmp_path / "state" / "KILL",
                 hive_llm_enabled=False, anthropic_api_key=None, alpaca_api_key=None,
                 hive_network_data=False, _env_file=None)
    cfg = StrategyConfig()
    run_day(s, cfg, date(2019, 6, 3), with_shadow=False)
    assert len(PositionBook(tmp_path / "state" / "book_primary.json")) == 0
