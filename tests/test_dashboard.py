import json
from datetime import date

from hive.config import Settings, StrategyConfig
from hive.orchestrator.app import run_day
from hive.ui.dashboard import build
from tests.test_orchestrator import _seed_store


def test_dashboard_builds_from_real_state(tmp_path):
    store, bars = _seed_store(tmp_path)
    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", hive_llm_enabled=False, anthropic_api_key=None,
                 alpaca_api_key=None, hive_network_data=False, _env_file=None)
    cfg = StrategyConfig()
    from hive.signals.features import compute_features

    f = compute_features(bars, cfg.stocks)
    d0 = f.index[f["entry_signal"]][-1]
    d1 = bars.index[bars.index.get_loc(d0) + 1]
    run_day(s, cfg, d0.date(), with_shadow=True)
    run_day(s, cfg, d1.date(), with_shadow=True)
    paths = build(s, cfg)
    html = paths["html"].read_text()
    data = json.loads(paths["data"].read_text())
    assert "<title>HIVE dashboard</title>" in html and "prefers-color-scheme" in html
    assert data["survival"]["chain_ok"] and data["survival"]["n_marks"] == 2
    assert len(data["equity"]["dates"]) == 2 and data["equity"]["shadow"]
    assert data["positions"]["primary"], "positions should show up on the dashboard"
    assert data["latest"]["primary"]["run_date"] == d1.date().isoformat()
    assert "</script>" not in json.dumps(data).replace("</", "<\\/")  # embedding is safe


def test_dashboard_builds_with_empty_state(tmp_path):
    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", _env_file=None)
    paths = build(s, StrategyConfig())
    data = json.loads(paths["data"].read_text())
    assert data["survival"]["status"] is None and data["equity"]["dates"] == []
    assert paths["html"].exists() and date.today().isoformat()[:4] in data["generated_at"]
