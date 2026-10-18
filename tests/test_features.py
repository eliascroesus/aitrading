import numpy as np
import pandas as pd
import pytest

from hive.config import RegimeRules, RiskLimits, TrendRules
from hive.data.bars import BAR_COLUMNS
from hive.signals.features import compute_features, initial_stop, latest_candidates
from hive.signals.regime import RegimeState, regime_at, regime_frame
from hive.signals.sizing import size_position


def bars_from_path(close: np.ndarray, volume: np.ndarray | None = None, start="2018-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(close))
    df = pd.DataFrame(index=idx)
    df["close"] = close
    df["open"] = np.r_[close[0], close[:-1]]
    df["high"] = np.maximum(df["open"], df["close"]) * 1.004
    df["low"] = np.minimum(df["open"], df["close"]) * 0.996
    df["adj_close"] = close
    df["volume"] = volume if volume is not None else 1e6
    return df[BAR_COLUMNS]


def textbook_setup(n_up=320, n_pull=10, n_bounce=5, n_after=0) -> tuple[np.ndarray, np.ndarray]:
    """A strong, steady leg up; a quiet 4% pullback; a breakout bounce."""
    up_steps = np.tile([1.006, 1.000], n_up // 2)  # mean +0.3%/day with nonzero sigma
    up = 100 * np.cumprod(up_steps)
    pull = up[-1] * np.cumprod(np.full(n_pull, 0.996))
    bounce = pull[-1] * np.cumprod(np.full(n_bounce, 1.008))
    after = bounce[-1] * np.cumprod(np.tile([1.006, 1.000], n_after // 2)) if n_after else np.array([])
    close = np.concatenate([up, pull, bounce, after])
    volume = np.concatenate([np.full(n_up, 2e6), np.full(n_pull + n_bounce, 1e6), np.full(len(after), 2e6)])
    return close, volume


@pytest.fixture
def rules() -> TrendRules:
    return TrendRules()


def test_entry_fires_on_breakout_after_quiet_pullback(rules):
    close, volume = textbook_setup()
    f = compute_features(bars_from_path(close, volume), rules)
    tail = f.iloc[-5:]
    assert tail["template_ok"].all(), "above a rising 200DMA, 50>200, near highs, 12-1 mom > 0"
    assert f["setup"].iloc[-15:].any(), "the quiet pullback should register as a setup"
    assert tail["entry_signal"].any(), "the bounce should trigger within the setup window"
    # Never fires while the impulse leg is still running (no pullback yet).
    assert not f["entry_signal"].iloc[200:320].any()


def test_no_entry_in_downtrend(rules):
    down = 100 * np.cumprod(np.tile([0.996, 1.000], 200))
    f = compute_features(bars_from_path(down), rules)
    assert not f["entry_signal"].any()
    assert f["below_long"].iloc[-1]
    assert f["below_mid_streak"].iloc[-1] >= 2


def test_volume_dryup_required(rules):
    close, _ = textbook_setup()
    f = compute_features(bars_from_path(close, np.full(len(close), 1e6)), rules)
    assert not f["setup"].iloc[-15:].any(), "flat volume means no dry-up, so no setup"
    rules2 = rules.model_copy(update={"require_volume": False})
    f2 = compute_features(bars_from_path(close, np.full(len(close), 1e6)), rules2)
    assert f2["setup"].iloc[-15:].any()


def test_features_have_no_lookahead(rules):
    rng = np.random.default_rng(1)
    px = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 450)))
    bars = bars_from_path(px, rng.uniform(5e5, 2e6, 450))
    full = compute_features(bars, rules)
    truncated = compute_features(bars.iloc[:-30], rules)
    pd.testing.assert_frame_equal(full.loc[truncated.index], truncated, check_freq=False)


def test_latest_candidates_ranks_and_filters(rules):
    close, volume = textbook_setup()
    good = compute_features(bars_from_path(close, volume), rules)
    bad = compute_features(bars_from_path(100 * np.cumprod(np.full(len(close), 0.998))), rules)
    when = good.index[good["entry_signal"]][-1]
    cands = latest_candidates({"GOOD": good, "BAD": bad}, when)
    assert cands["symbol"].tolist() == ["GOOD"]
    assert 0 <= cands["score"].iloc[0] <= 1


def test_initial_stop_is_wider_of_two(rules):
    row = pd.Series({"atr": 2.0, "swing_low": 95.0})
    # swing-low stop: 95 - 1 = 94 ; ATR stop: 100 - 6 = 94 -> max = 94
    assert initial_stop(100.0, row, rules) == pytest.approx(94.0)
    row2 = pd.Series({"atr": 2.0, "swing_low": 98.0})
    assert initial_stop(100.0, row2, rules) == pytest.approx(97.0)


def test_regime_states():
    rr = RegimeRules()
    up = 100 * np.cumprod(np.full(400, 1.002))
    frame = regime_frame(bars_from_path(up), rr)
    last = regime_at(frame, frame.index[-1])
    assert last["state"] == RegimeState.GREEN.value and last["allow_new_stock"]
    assert frame["state"].iloc[0] == RegimeState.RED.value  # warm-up
    down = 100 * np.cumprod(np.full(400, 0.998))
    f2 = regime_frame(bars_from_path(down), rr)
    assert regime_at(f2, f2.index[-1])["state"] == RegimeState.RED.value
    assert not regime_at(f2, f2.index[-1])["allow_new_stock"]
    # VIX spike turns GREEN into YELLOW
    vix = pd.Series(40.0, index=frame.index)
    f3 = regime_frame(bars_from_path(up), rr, vix=vix)
    assert regime_at(f3, f3.index[-1])["state"] == RegimeState.YELLOW.value
    assert regime_at(f3, f3.index[-1])["risk_multiplier"] == 0.5


def test_sizing_risk_and_caps():
    risk = RiskLimits(risk_per_trade_pct=0.01, max_position_pct_stock=0.20, min_order_notional_usd=1.0)
    # $100 equity, $50 price, stop $47 -> R=3 -> risk $1 -> qty 1/3 -> $16.67 (< 20% cap)
    d = size_position(equity=100, price=50, stop_price=47, risk=risk)
    assert d.notional == pytest.approx(16.67, abs=0.01) and d.reason == "risk"
    # Tight stop -> cap binds
    d2 = size_position(equity=100, price=50, stop_price=49.9, risk=risk)
    assert d2.notional == pytest.approx(20.0) and d2.reason == "position_cap"
    # Regime half risk
    d3 = size_position(equity=100, price=50, stop_price=47, risk=risk, risk_multiplier=0.5)
    assert d3.notional == pytest.approx(8.33, abs=0.01)
    # Portfolio vol above target scales down
    d4 = size_position(equity=100, price=50, stop_price=47, risk=risk, portfolio_vol_ann=0.40)
    assert d4.notional == pytest.approx(8.33, abs=0.01)
    # Whole shares: 0.33 shares -> 0
    d5 = size_position(equity=100, price=50, stop_price=47, risk=risk, fractional=False)
    assert d5.qty == 0.0
    # Bad stop
    assert size_position(equity=100, price=50, stop_price=51, risk=risk).notional == 0.0
