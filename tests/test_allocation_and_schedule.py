"""Capital follows results; crypto bars are closed days only; exit journals carry P&L."""

import json
from datetime import UTC, date, datetime

import pandas as pd

from hive.config import StrategyConfig
from hive.data.refresh import complete_days
from hive.hive.allocation import HIGH, LOW, Scorecard, decide, rebalance, scorecard
from hive.orchestrator.app import apply_allocation
from hive.orchestrator.journal import Journal


def _sc(crypto=0.0, stocks=0.0, n=6):
    return Scorecard(42, {"crypto": crypto, "stock": stocks}, {"crypto": n // 2, "stock": n - n // 2})


def test_allocation_moves_toward_the_desk_that_earns_within_bounds():
    assert decide(0.40, _sc(5.0, 1.0))[0] == 0.45          # crypto ahead: reward
    assert decide(0.40, _sc(-3.0, 2.0))[0] == 0.35         # crypto lost: cut
    assert decide(0.40, _sc(1.0, -4.0))[0] == 0.45         # stocks lost: capital leaves them
    assert decide(0.40, _sc(2.0, 2.0))[0] == 0.40          # level: hold
    assert decide(0.40, _sc(9.0, 0.0, n=2))[0] == 0.40     # too few closed trades: hold
    assert decide(HIGH, _sc(9.0, 0.0))[0] == HIGH          # bounded
    assert decide(LOW, _sc(-9.0, 0.0))[0] == LOW


def test_scorecard_reads_primary_exit_journals_only(tmp_path):
    j = Journal(tmp_path, "2026-10-10")
    j.write("exit", {"symbol": "BTC", "asset_class": "crypto", "pnl_usd": 4.0})
    j.write("exit", {"symbol": "AAPL", "asset_class": "stock", "pnl_usd": -1.5})
    j.write("exit", {"symbol": "X", "reason": "no_protective_stop"})          # no pnl: ignored
    Journal(tmp_path, "2026-10-10-shadow").write("exit", {"symbol": "BTC", "asset_class": "crypto", "pnl_usd": 99})
    Journal(tmp_path, "2026-01-01").write("exit", {"symbol": "ETH", "asset_class": "crypto", "pnl_usd": 50})  # old
    sc = scorecard(tmp_path, date(2026, 10, 12))
    assert sc.pnl_by_class == {"crypto": 4.0, "stock": -1.5} and sc.trades_by_class == {"crypto": 1, "stock": 1}
    alloc = rebalance(tmp_path / "allocation.json", tmp_path, date(2026, 10, 12), 0.40)
    assert alloc["crypto_sleeve_pct"] == 0.40 and "only 2 closed trades" in alloc["history"][-1]["why"]


def test_run_applies_the_allocation_file(tmp_path):
    from hive.config import Settings

    s = Settings(hive_state_dir=tmp_path)
    cfg = StrategyConfig()
    assert apply_allocation(s, cfg).risk.crypto_sleeve_pct == cfg.risk.crypto_sleeve_pct
    (tmp_path / "allocation.json").write_text(json.dumps({"crypto_sleeve_pct": 0.55}))
    assert apply_allocation(s, cfg).risk.crypto_sleeve_pct == 0.55
    assert cfg.risk.crypto_sleeve_pct == 0.40  # the pre-registered config object is untouched


def test_crypto_refresh_drops_the_forming_utc_day():
    today = pd.Timestamp(datetime.now(UTC).date())
    idx = pd.date_range(today - pd.Timedelta(days=3), today, freq="D")
    bars = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]}, index=idx)
    out = complete_days(bars)
    assert len(out) == 3 and out.index[-1] == today - pd.Timedelta(days=1)
    assert len(complete_days(bars.iloc[:0])) == 0
