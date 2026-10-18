from types import SimpleNamespace

import pytest

from hive.agents.llm import BudgetExceededError, LLMClient, estimate_cost_usd
from hive.agents.pipeline import CandidateContext, CandidatePipeline
from hive.agents.prompts import anonymise
from hive.agents.schemas import AnalystView, DebateOutcome, NewsDigest, PMDecision
from hive.backtest.costs import CostModel
from hive.execution.hyperliquid import round_px, round_sz
from hive.execution.paper import PaperBroker


# ---------------------------------------------------------------- paper broker
def test_paper_broker_roundtrip_and_stops(tmp_path):
    prices = {"AAA": 10.0}
    pb = PaperBroker(tmp_path / "paper.json", lambda s: prices.get(s), starting_cash=100.0,
                     cost=CostModel(commission_pct=0.0, slippage_bps=0.0))
    r = pb.submit_market("AAA", "buy", notional=20.0)
    assert r.ok and r.filled_qty == pytest.approx(2.0)
    assert pb.cash() == pytest.approx(80.0) and pb.equity() == pytest.approx(100.0)
    assert pb.submit_stop("AAA", 2.0, 9.0).ok
    prices["AAA"] = 11.0
    assert pb.equity() == pytest.approx(102.0)
    # reload from disk
    pb2 = PaperBroker(tmp_path / "paper.json", lambda s: prices.get(s),
                      cost=CostModel(commission_pct=0.0, slippage_bps=0.0))
    assert pb2.positions()["AAA"].qty == pytest.approx(2.0)
    # stop triggers when low trades through, fills at the stop
    fills = pb2.check_stops(low_fn=lambda s: 8.5)
    assert len(fills) == 1 and fills[0].fill_price == pytest.approx(9.0)
    assert not pb2.positions() and pb2.cash() == pytest.approx(80.0 + 18.0)
    assert not pb2.submit_market("AAA", "sell", qty=1.0).ok


def test_paper_broker_rejects_overspend(tmp_path):
    pb = PaperBroker(tmp_path / "p.json", lambda s: 10.0, starting_cash=10.0)
    assert not pb.submit_market("AAA", "buy", notional=50.0).ok


# ---------------------------------------------------------------- hyperliquid helpers
def test_hyperliquid_rounding():
    assert round_sz(0.123456, 3) == pytest.approx(0.123)
    assert round_px(12345.678, 3) == pytest.approx(12346.0)
    assert round_px(1.234567, 0) == pytest.approx(1.2346)
    assert round_px(0.0012345678, 0) == pytest.approx(0.001235, rel=1e-6)


# ---------------------------------------------------------------- llm wrapper
class FakeMessages:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(
            parsed_output=out, stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
        )


def fake_client(outputs):
    return SimpleNamespace(messages=FakeMessages(outputs))


def test_cost_estimate_matches_price_table():
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)
    assert estimate_cost_usd("claude-haiku-5-5", usage) == pytest.approx(0.10)
    assert estimate_cost_usd("claude-opus-5-5", usage) == pytest.approx(4.0)


def test_llm_fail_closed_and_budget():
    view = AnalystView(signal=0.5, confidence=0.6, flags=[], rationale="ok")
    calls = []
    llm = LLMClient(api_key=None, budget_usd=0.05, on_call=calls.append,
                    client=fake_client([view, RuntimeError("boom"), view]))
    parsed, call = llm.parse(role="t", model="claude-haiku-5-5", system="s", user="u", schema=AnalystView)
    assert parsed == view and call.ok and call.cost_usd > 0
    parsed2, call2 = llm.parse(role="t", model="claude-haiku-5-5", system="s", user="u", schema=AnalystView)
    assert parsed2 is None and not call2.ok and llm.failures == 1
    llm.spent_usd = 0.049
    with pytest.raises(BudgetExceededError):
        llm.parse(role="t", model="claude-haiku-5-5", system="s", user="u", schema=AnalystView)
    assert len(calls) == 2
    # system prompt is sent with a cache breakpoint
    assert calls[0] and llm._client.messages.calls[0]["system"][0]["cache_control"]["type"] == "ephemeral"


def test_llm_disabled_returns_none():
    llm = LLMClient(api_key=None, budget_usd=1.0)
    assert not llm.available
    parsed, call = llm.parse(role="t", model="m", system="s", user="u", schema=AnalystView)
    assert parsed is None and call.stop_reason == "no_client"


def test_anonymise_replaces_names_and_cashtags():
    s = "Apple ($AAPL) beats; AAPL up 5% as Apple Inc. guides higher"
    out = anonymise(s, ["AAPL", "Apple Inc.", "Apple"])
    assert "AAPL" not in out and "Apple" not in out and "THE_COMPANY" in out


# ---------------------------------------------------------------- pipeline
def _ctx(sym="AAA", headlines=None):
    return CandidateContext(symbol=sym, asset_class="stock", features={"close": 10.0, "score": 0.8},
                            fundamentals={"earnings_in_days": 20}, headlines=headlines or [],
                            company_names=["Alpha Corp"])


def _view():
    return AnalystView(signal=0.4, confidence=0.5, flags=[], rationale="fine")


def test_pipeline_approves_with_full_chain():
    news = NewsDigest(events=[], net_sentiment=0.1, material_negative=False, attention_spike=False)
    pm = PMDecision(decisions=[{"symbol": "AAA", "action": "reduce_half",
                                "invalidation_condition": "close_below_ma50_2d", "confidence": 0.6,
                                "rationale": "ok"}], portfolio_note="n")
    outputs = [news, _view(), _view(), _view(),
               DebateOutcome(bull_case="b", bear_case="r", net_signal=0.2, key_risk="k"), pm]
    llm = LLMClient(api_key=None, budget_usd=1.0, client=fake_client(outputs))
    pipe = CandidatePipeline(llm, cheap_model="claude-haiku-5-5", decision_model="claude-opus-5-5")
    evals = pipe.evaluate([_ctx(headlines=[("s1", "Alpha Corp wins contract")])], {"equity": 100})
    assert evals[0].decision.action == "reduce_half"
    assert evals[0].decision.invalidation_condition == "close_below_ma50_2d"
    assert evals[0].cost_usd > 0
    # the reader saw anonymised text
    sent = llm._client.messages.calls[0]["messages"][0]["content"]
    assert "Alpha Corp" not in sent and "THE_COMPANY" in sent


def test_pipeline_vetoes_on_material_news_and_failures():
    bad_news = NewsDigest(events=[], net_sentiment=-0.8, material_negative=True, attention_spike=False)
    llm = LLMClient(api_key=None, budget_usd=1.0, client=fake_client([bad_news]))
    pipe = CandidatePipeline(llm, cheap_model="h", decision_model="o")
    ev = pipe.evaluate([_ctx(headlines=[("s", "x")])], {})[0]
    assert ev.decision.action == "veto"
    # analyst failure -> veto, PM never called
    news = NewsDigest(events=[], net_sentiment=0.0, material_negative=False, attention_spike=False)
    llm2 = LLMClient(api_key=None, budget_usd=1.0, client=fake_client([news, RuntimeError("x"), _view(), _view(),
                                                                       DebateOutcome(bull_case="b", bear_case="r", net_signal=0, key_risk="k")]))
    ev2 = CandidatePipeline(llm2, cheap_model="h", decision_model="o").evaluate([_ctx(headlines=[("s", "x")])], {})[0]
    assert ev2.decision.action == "veto"
    # PM omits the symbol -> veto
    llm3 = LLMClient(api_key=None, budget_usd=1.0, client=fake_client([
        news, _view(), _view(), _view(), DebateOutcome(bull_case="b", bear_case="r", net_signal=0, key_risk="k"),
        PMDecision(decisions=[], portfolio_note="")]))
    ev3 = CandidatePipeline(llm3, cheap_model="h", decision_model="o").evaluate(
        [_ctx(headlines=[("s", "x")])], {})[0]
    assert ev3.decision.action == "veto"
    # no headlines -> the reader is skipped without an LLM call
    llm4 = LLMClient(api_key=None, budget_usd=1.0, client=fake_client([
        _view(), _view(), _view(), DebateOutcome(bull_case="b", bear_case="r", net_signal=0, key_risk="k"),
        PMDecision(decisions=[{"symbol": "AAA", "action": "approve", "invalidation_condition": "none",
                               "confidence": 0.5, "rationale": "r"}], portfolio_note="")]))
    ev4 = CandidatePipeline(llm4, cheap_model="h", decision_model="o").evaluate([_ctx()], {})[0]
    assert ev4.decision.action == "approve" and len(llm4._client.messages.calls) == 5


def test_pipeline_rules_only_when_llm_disabled():
    llm = LLMClient(api_key=None, budget_usd=1.0)
    evals = CandidatePipeline(llm, cheap_model="h", decision_model="o").evaluate([_ctx(), _ctx("BBB")], {})
    assert all(e.decision.action == "veto" and e.note == "rules-only" for e in evals)
