"""Entry families: each is a valid, lookahead-free signal; the operator may choose them."""

import numpy as np
import pandas as pd
import pytest

from hive.config import REPO_ROOT, StrategyConfig, TrendRules
from hive.operator.iterate import (
    coerce_types,
    load_bounds,
    proposal_schema,
    to_proposals,
    validate_overrides,
)
from hive.signals.features import compute_features

MODES = ["reset_breakout", "template_cross", "high_breakout", "ma_pullback", "momentum_threshold"]


def _bars(n=900, seed=3):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0006, 0.012, n)
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.bdate_range("2015-01-01", periods=n)
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close,
                         "adj_close": close, "volume": rng.integers(100_000, 1_000_000, n)}, index=idx)


@pytest.mark.parametrize("mode", MODES)
def test_each_family_is_boolean_and_lookahead_free(mode):
    bars = _bars()
    rules = TrendRules(entry_mode=mode)
    f_full = compute_features(bars, rules)
    f_cut = compute_features(bars.iloc[:-50], rules)
    assert f_full["entry_signal"].dtype == bool
    pd.testing.assert_series_equal(f_full["entry_signal"].iloc[:-50], f_cut["entry_signal"], check_names=False)


def test_families_differ_from_each_other():
    bars = _bars(seed=7)
    sigs = {m: int(compute_features(bars, TrendRules(entry_mode=m))["entry_signal"].sum()) for m in MODES}
    assert len(set(sigs.values())) > 1, sigs


def test_operator_may_choose_a_family_within_choices():
    bounds = load_bounds(REPO_ROOT / "config" / "bounds.toml")
    assert bounds["stocks.entry_mode"] == MODES
    ok, _ = validate_overrides({"stocks.entry_mode": "high_breakout", "stocks.breakout_lookback": 40}, bounds)
    assert ok
    ok, why = validate_overrides({"stocks.entry_mode": "fly_to_the_moon"}, bounds)
    assert not ok and "not one of" in why
    schema = proposal_schema(bounds)
    parsed = schema.model_validate({"proposals": [{"name": "breakout desk", "hypothesis": "h",
                                                   "overrides": [{"param": "crypto.entry_mode", "value": "high_breakout"},
                                                                 {"param": "crypto.breakout_lookback", "value": 30.0}],
                                                   "sleeve": "crypto"}]})
    p = to_proposals(parsed, 3)[0]
    ov = coerce_types(StrategyConfig(), p.overrides)
    assert ov == {"crypto.entry_mode": "high_breakout", "crypto.breakout_lookback": 30}
