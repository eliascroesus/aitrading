import numpy as np
import pandas as pd
import pytest

from hive.signals import indicators as ind


@pytest.fixture
def close() -> pd.Series:
    idx = pd.bdate_range("2024-01-01", periods=10)
    return pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 19], index=idx, dtype=float)


def test_sma_matches_hand_computation(close):
    out = ind.sma(close, 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(11.0)
    assert out.iloc[-1] == pytest.approx(18.0)


def test_sma_has_no_lookahead(close):
    # Perturbing a future value must not change earlier outputs.
    base = ind.sma(close, 3)
    bumped = close.copy()
    bumped.iloc[-1] = 1000.0
    out = ind.sma(bumped, 3)
    pd.testing.assert_series_equal(base.iloc[:-1], out.iloc[:-1])


def test_roc_and_slope(close):
    roc = ind.roc_pct(close, 5)
    # 15/10 - 1 = 50%
    assert roc.iloc[5] == pytest.approx(50.0)
    slope = ind.slope_pct(close, 1)
    assert slope.iloc[1] == pytest.approx(10.0)


def test_atr_simple_case():
    idx = pd.bdate_range("2024-01-01", periods=5)
    high = pd.Series([12, 13, 14, 15, 16], index=idx, dtype=float)
    low = pd.Series([10, 11, 12, 13, 14], index=idx, dtype=float)
    close = pd.Series([11, 12, 13, 14, 15], index=idx, dtype=float)
    # True range is 2 every day (high-low dominates), so ATR must be exactly 2.
    out = ind.atr(high, low, close, period=3)
    assert out.iloc[-1] == pytest.approx(2.0)


def test_pct_from_high_and_drawdown():
    idx = pd.bdate_range("2024-01-01", periods=4)
    s = pd.Series([100, 120, 90, 96], index=idx, dtype=float)
    assert ind.pct_from_high(s, 252).iloc[2] == pytest.approx(-25.0)
    dd = ind.drawdown(s)
    assert dd.iloc[2] == pytest.approx(-0.25)
    assert dd.iloc[3] == pytest.approx(-0.20)


def test_rsi_bounds_and_extremes():
    idx = pd.bdate_range("2024-01-01", periods=30)
    up = pd.Series(np.linspace(100, 130, 30), index=idx)
    r = ind.rsi(up, 14)
    assert r.dropna().between(0, 100).all()
    assert r.iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.linspace(130, 100, 30), index=idx)
    assert ind.rsi(down, 14).iloc[-1] == pytest.approx(0.0)


def test_realized_vol_is_positive_and_scaled():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2020-01-01", periods=300)
    px = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))), index=idx)
    vol = ind.realized_vol_annualized(px, 20)
    assert vol.dropna().gt(0).all()
    # Daily sigma 1% -> ~16% annualised; allow wide tolerance on a 20-day window.
    assert 0.05 < vol.dropna().median() < 0.30
