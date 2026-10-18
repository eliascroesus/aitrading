# OSS LLM Trading Frameworks, Engines and Benchmarks: What to Borrow for a $100 Multi-Agent Bot

*Research snapshot as of 2026-10-18. Scope: open-source multi-agent LLM traders, classical engines, Hyperliquid tooling, live LLM-trading benchmarks, and practitioner pitfalls.*

**How this was gathered, and its limits.** GitHub READMEs, configs and issue trackers were fetched directly, so star counts, licenses and feature lists below come from the live pages as of today. arxiv.org, huggingface.co, nof1.ai, Reddit, HN and most news sites could not be reached from this sandbox (DNS/proxy blocked), and the shared web-search budget ran out partway through. So paper results and Alpha Arena numbers come from search-engine summaries of abstracts and secondary coverage. Wherever sources disagree, or I could not check a primary source, I mark the claim **[unverified]**. Some details (for example the Alpha Arena output schema, Hyperliquid fee tiers and Alpaca's minimum order size) come from my prior knowledge and carry the same tag.

---

## TL;DR

1. **Every serious live test in 2025-2026 ended mostly in losses.** Alpha Arena Season 1 had 2 of 6 models profitable. In Season 1.5, models finished in profit in only 6 of 32 result sets. DeepFund, StockBench, AI-Trader and LiveTradeBench found that most agents fail to beat buy-and-hold, and that general LLM ability does not predict trading skill.
2. **Backtests of LLM agents run before the model's training cutoff are inflated by memorization.** "Profit Mirage" measured 50-72% return decay once the test window moved past the knowledge cutoff. FINSABER (KDD 2026) found that the reported edge disappears over 20 years and 100+ symbols.
3. **The winning design pattern across the newest projects is the same:** deterministic code computes signals, sizes positions and enforces risk, and the LLM only forms views, vetoes trades and narrates. ai-hedge-fund v2 states it outright: "The LLM never touches the trade."
4. **Turnover and leverage drove most of the outcomes, more than "intelligence" did.** In Season 1, Gemini 2.5 Pro made 238 trades and paid $1,331 in fees (13% of its capital). In Season 1.5, Qwen made 1,418 trades where Grok made 158.
5. **At $100 of capital, LLM token cost is your largest "fee".** One TradingAgents run per ticker is estimated at $0.20-0.80. Budget the LLM like a trading cost and report results net of it.

---

## 1. Multi-agent LLM trading frameworks

| Project | Roles / architecture | Data | License | Stars (Oct 2026) | Results basis |
|---|---|---|---|---|---|
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | 4 analysts → bull/bear debate → trader → risk team → portfolio manager | yfinance, SEC EDGAR, FRED, Alpha Vantage, Polymarket, Reddit/StockTwits | Apache-2.0 | 111.9k | Paper backtest of about 3 months |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | Investor personas + quant models → portfolio → risk → execution (v2) | financialdatasets.ai | MIT | 64.1k | Backtester and paper trading only |
| [AI4Finance FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | Lead agent + 5 pipeline agents + bull/bear/judge; deterministic valuation operators | FMP, Finnhub, yfinance, SEC | Apache-2.0 | 8.2k | Research reports, no trading |
| [AI4Finance FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | LoRA fine-tuned finance LLMs (sentiment, Forecaster) | News datasets | MIT | 21.4k | NLP benchmarks; last major release Nov 2023 |
| [FinMem](https://github.com/pipiku915/FinMem-LLM-StockTrading) | Profiling + layered memory + reflection | Pickled price/news | MIT | 963 | Backtest, default window 2022-06-30 → 2022-10-11 |
| FinAgent (arXiv 2402.18485) | Multimodal (charts, text, numbers), dual-level reflection, tools | Prices, news, K-line images | — | — | Backtest |
| FinCon (NeurIPS 2024, [suchow.io/fincon](https://suchow.io/fincon/)) | Manager–analyst hierarchy; "conceptual verbal reinforcement" through risk self-critique | Prices, news, filings | — | — | Backtest on 8 stocks |
| [The-Swarm-Corporation/AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge) | Director → Quant → Risk → Execution | Jupiter API (Solana) | MIT | 6.4k | None published |
| [HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader) | Began as a live LLM benchmark (arXiv 2512.10971); now an "agent-native" platform (ai4trade.ai) | Alpha Vantage, yfinance fallback | MIT | 22.8k | Live and paper |
| [NoFxAiOS/nofx](https://github.com/NoFxAiOS/nofx) | LLM decision loop; Go runtime enforces risk limits the model cannot override | Exchange data, Claw402/Vergex | AGPL-3.0 | 13.1k | Leaderboard; no published figures |

### TradingAgents (the reference design)
- **Roles.** Four analysts (fundamentals, sentiment, news/macro, technical) now run in parallel (v0.5.2). Bull and bear researchers debate, and a research manager judges the debate. A trader proposes timing and size. A risk team (aggressive, neutral and conservative debaters, per the paper) reports to a portfolio manager, who approves or rejects the trade.
- **Configuration.** The defaults are `max_debate_rounds=1`, `max_risk_discuss_rounds=1`, `max_tool_rounds=20` and `holding_period_days=5`. A "quick" model serves the analysts, researchers and trader; a "deep" model serves the managers ([default_config.py](https://github.com/TauricResearch/TradingAgents/blob/main/tradingagents/default_config.py)).
- **Maturity.** Changelog: v0.2.4 (Apr 2026) added structured-output Trader and PM; v0.4.0 (Aug 2026) fixed look-ahead in FRED and social data; v0.5.0 (Sep 2026) added "point-in-time integrity across every dated path"; v0.7.0 (Oct 2026). A `run_backtest` scores decisions on realized alpha against a benchmark. Tauric also published a Trading-R1 technical report (Jan 2026).
- **Claimed results.** The paper reports roughly 3-month backtests on AAPL, GOOGL and AMZN. AAPL is reported at about 26.6% cumulative return with Sharpe 8.21 and max drawdown about 0.9%. The paper itself concedes that the Sharpe exceeds the expected empirical range.
- **Criticism of the results.** An independent recheck ([DEV post](https://dev.to/trow126/the-most-starred-llm-trading-paper-claims-buy-and-hold-lost-523-it-actually-gained-912-1jj6)) says the paper's buy-and-hold baseline has the wrong sign (−5.23% reported, about +9% actual) **[unverified; one blogger]**. Sources also disagree on the evaluation window (Jan–Mar 2024 vs Jun–Nov 2024).
- **Look-ahead bugs in the repo.** The issue tracker is a catalogue of subtle leaks, all closed:
  - [#1518](https://github.com/TauricResearch/TradingAgents/issues/1518): news published after close(d) was used for a decision scored at close(d).
  - [#1300](https://github.com/TauricResearch/TradingAgents/issues/1300): the fundamentals snapshot was stamped with `now()`.
  - [#1275](https://github.com/TauricResearch/TradingAgents/issues/1275): FRED returned revised data.
  - [#1220](https://github.com/TauricResearch/TradingAgents/issues/1220): current Reddit/StockTwits data leaked into historical runs.
  - [#1007](https://github.com/TauricResearch/TradingAgents/issues/1007): yfinance global news injected future articles.
- **Model-level leakage remains open.** [#805](https://github.com/TauricResearch/TradingAgents/issues/805) stays open, and the maintainer calls model-level contamination a "fundamental limitation". Proposed mitigations: blind backtests (hide ticker and dates), label every backtest cell that falls before the model's cutoff, and run recall tests. One commenter found detectable earnings recall in 10 of 12 models.
- **Cost.** Each run makes about 12+ LLM calls plus tool loops. A vendor estimate puts it at about **$0.20–0.80 per ticker-run** with gpt-4o/4o-mini and one debate round, and doubling the debate rounds roughly doubles the cost ([Railway](https://railway.com/deploy/trading-agents)) **[vendor estimate]**.

### ai-hedge-fund
- **v1.** Shipped a roster of famous-investor personas (Buffett, Munger, Graham, Lynch, Druckenmiller, Burry, Wood, Ackman, Damodaran and others), plus valuation, sentiment, fundamentals and technicals agents, a risk manager and a portfolio manager **[from prior knowledge; the current README no longer lists them]**.
- **v2 core** ([hedge_fund/README](https://github.com/virattt/ai-hedge-fund/blob/main/hedge_fund/README.md), work in progress). The pipeline is `Data (point-in-time) → Alpha models → Portfolio → Risk → Execution → Ledger`.
  - Every model returns a `Signal` in [−1, +1]. Five personas sit alongside a quant post-earnings-drift (PEAD) model.
  - Risk is enforced with hard clamps per position and on gross exposure.
  - The ledger is append-only and hash-chained. A book mismatch raises `BookMismatch` instead of being silently repaired.
  - Quotes worth keeping: *"The LLM never touches the trade." "Agents form views and narrate; deterministic code sizes and places orders; risk limits are hard gates." "Nothing is ever assessed and executed against the same close."*
  - Live brokers are only planned. Purged cross-validation and probability-of-backtest-overfitting (PBO) checks are planned but not built.

### FinMem, FinAgent and FinCon (the Stevens/academic line)
- **What they offer.** Memory and reflection ideas: layered memory with decay, dual-level reflection, and manager-propagated "beliefs."
- **Their results are short backtests that fall inside the models' training windows.** FinCon self-reports 25.08% cumulative return and Sharpe 0.646 on GOOG.
- **They failed the leakage test.** [Profit Mirage (arXiv 2510.07920)](https://alphaxiv.org/abs/2510.07920) found total-return decay from **50.18% (TradingAgents) to 71.85% (FinMem, the worst)** once evaluated past the knowledge window. The worst model left **82.13% of its predictions unchanged** under significant counterfactual input changes, which means it was recalling outcomes rather than reading its inputs. FinAgent and FinCon, which use external tools, held up somewhat better.
- **FINSABER re-tested all three** over the long run (see §4).

### Others and 2026 successors
- **FinRobot V2.** Its motto, "models reason, software computes," is the right one: DCF and other valuation math is done by 32 deterministic operators.
- **FinGPT.** Useful only as a source of a cheap sentiment classifier; it is effectively stale.
- **AutoHedge.** Solana-only, 37 commits, no results. More marketing than system.
- **NOFX.** Its risk features are worth copying: exchange-side SL/TP on every entry, drawdown auto-close, throttles and cooldowns, a "safe mode" after repeated model failures, and preflight checks. Watch for the AGPL license and its exchange-referral and commercial ties.
- **Other 2026 entries** (curated in [LLMQuant/awesome-trading-agents](https://github.com/LLMQuant/awesome-trading-agents)):
  - [Lumibot's built-in agent runtime](https://github.com/Lumiwealth/lumibot), covered in §2.
  - Hummingbot's **Condor** "AI harness".
  - HKUDS/Vibe-Trading and FinStep-AI/ContestTrade.
  - huygiatrng/AlpacaTradingAgent, a TradingAgents variant for Alpaca.
  - The official [alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server).

---

## 2. Classical OSS engines: use one instead of writing your own

| Engine | License | Stars | US stocks / Alpaca | Hyperliquid | Fit for daily-bar trend + live |
|---|---|---|---|---|---|
| [Freqtrade](https://github.com/freqtrade/freqtrade) (+FreqAI) | GPL-3.0 | 55.5k | No stocks | Spot + futures | Excellent for crypto only |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | LGPL-3.0 | 30.3k | Alpaca not listed | **Stable** adapter | Best backtest/live parity, steep learning curve, Python 3.13–3.14 |
| [Hummingbot](https://github.com/hummingbot/hummingbot) | Apache-2.0 | 20.5k | No | `hyperliquid`, `hyperliquid_perpetual` | Built for market making; overkill for us |
| [Jesse](https://github.com/jesse-ai/jesse) | MIT | 8.7k | No (crypto-only) | Not stated in README **[unverified]** | Good crypto backtester; has an MCP server |
| [vectorbt](https://github.com/polakowo/vectorbt) (OSS) | Apache-2.0 + Commons Clause | 9.4k | n/a | n/a | **Best for fast research sweeps**; no live trading |
| [backtrader](https://github.com/mementum/backtrader) | GPL-3.0 | 23.5k | IB/Oanda only | No | Stale (pyfolio deprecated); avoid for new work |
| [Lumibot](https://github.com/Lumiwealth/lumibot) | GPL-3.0 | 2.1k | **Alpaca**, IBKR, Tradier, Schwab | Not mentioned | Strong on the Alpaca side and has an LLM agent runtime |
| [QuantConnect LEAN](https://github.com/QuantConnect/Lean) | Apache-2.0 | 22.1k | Alpaca via [Lean.Brokerages.Alpaca](https://github.com/QuantConnect/Lean.Brokerages.Alpaca) | Not mentioned | Professional grade but heavy (C#, Docker CLI) |
| [CCXT](https://github.com/ccxt/ccxt) | MIT | 44.4k | Alpaca listed (Pro, not certified) | **Certified + Pro (WebSocket)** | Connectivity layer, not an engine |

**Hyperliquid notes from the engines' documentation:**
- **Freqtrade** ([exchange notes](https://github.com/freqtrade/freqtrade/blob/develop/docs/exchanges.md)):
  - Hyperliquid's API cannot download history beyond the most recent **5,000 candles**. For daily bars that is roughly 13 years, so it does not bind; Hyperliquid perps only date from about 2023 anyway.
  - "Hyperliquid does not support market orders"; CCXT emulates them as limit orders with up to about 5% slippage.
  - Stoploss-on-exchange works via stop-limit orders.
  - `walletAddress` must be the *main* wallet, and `privateKey` should be an API wallet's key.
- **Nautilus** ([Hyperliquid doc](https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/integrations/hyperliquid.md)):
  - Market orders are sent as IOC limits with a 50 bps buffer.
  - Leverage must be set on the venue, not through the adapter.
  - Order and fill history covers only the last 2,000 records.
  - Fills inferred during reconciliation carry no commission.
  - The testnet faucet only works after a mainnet deposit.
- **Lumibot.** Its newest feature is directly relevant: a built-in agent runtime (`self.agents.create`) that can **replay recorded LLM decisions inside backtests without new model calls**. It also has point-in-time controls on tool inputs, SQLite/Parquet memory and example bull/bear teams.

**Fit for our case** (daily-bar trend strategy, Alpaca + Hyperliquid, small Python codebase): no single OSS engine covers both venues well.
- **Freqtrade or Jesse** cover crypto only.
- **LEAN and Lumibot** cover Alpaca but not Hyperliquid.
- **Nautilus** covers Hyperliquid but lists no Alpaca adapter.

For daily bars, the pragmatic choice is:
- **vectorbt (or plain pandas) for research**;
- **one shared, pure-Python `signal(bars_up_to_t) → target` function** used by both a small next-open backtester and live trading;
- **thin adapters over the official SDKs.**

---

## 3. Hyperliquid-specific OSS

**[hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)** (MIT, about 1.9k stars, `pip install hyperliquid-python-sdk`).
- **Development Python version:** contributors are pinned to Python 3.10 because of dependency breakage.
- **Useful examples:** `basic_order`, `basic_market_order`, `basic_tpsl`, `basic_leverage_adjustment`, `basic_order_with_cloid` (idempotent client order IDs), `basic_schedule_cancel` (a **dead-man's switch**), `basic_agent`, `basic_sub_account`, `basic_vault`, `rounding.py`, `basic_ws`, and builder-DEX (HIP-3) examples.

**Agent (API) wallets** ([docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets)).
- **What they can do:** the master wallet approves an API wallet through an EIP-712 `ApproveAgent` message. The agent can sign trades but **cannot withdraw**.
- **Config:** set `secret_key` to the agent's key and `account_address` to the *master* address. Querying with the agent's address returns empty results, which is a classic bug.
- **Lifecycle:** do not reuse agent addresses after deregistration, because nonce state is pruned and old actions can be replayed. Only one unnamed agent can exist at a time.
- **Nonces:** the docs disagree on how many nonces are kept per address (20 vs 100) **[unverified]**.

**Order rules** ([tick/lot](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size)).
- **Price:** at most 5 significant figures and at most `6 − szDecimals` decimals for perps (8 for spot). Integer prices are always accepted.
- **Size:** rounded to the asset's `szDecimals`, which you read from `meta`; do not hard-code it.
- **Minimum notional:** **$10** ("Order must have minimum value of $10"). This matters at $100 of capital.
- **Order types:** limit (GTC, ALO post-only, IOC), market (an aggressive IOC), trigger TP/SL, reduce-only, TWAP.
- **Fees and funding:** hourly funding. Base-tier perp fees are about 0.045% taker and 0.015% maker **[unverified current tiers]**.

**Notable OSS Hyperliquid AI bots** (none has audited, published performance):
- [NOFX](https://github.com/NoFxAiOS/nofx) (AGPL, 13.1k stars; also trades HIP-3 tokenized equities).
- [Hyper-Alpha-Arena](https://github.com/HammerGPT/Hyper-Alpha-Arena) (Apache-2.0, 1.2k stars; testnet paper mode, 86 factors scored by IC, ICIR and decay).
- [wquguru/nof0](https://github.com/wquguru/nof0) (MIT, 2.8k stars; Go clone of Alpha Arena, backend about 70% done).
- [Gajesh2007/ai-trading-agent "Nocturne"](https://github.com/Gajesh2007/ai-trading-agent) (OpenRouter + TAAPI indicators via tool calls; exposes `/diary` endpoints).
- [kukapay/alpha-arena-mcp](https://github.com/kukapay/alpha-arena-mcp) (MCP tools for orders, positions and market state).
- [195440/nof1.ai](https://github.com/195440/nof1.ai) (AGPL; Gate/OKX; 20-minute loop with drawdown tiers: warn at 20%, no new positions at 30%, liquidate everything at 50%).

---

## 4. Benchmarks and live experiments

### Nof1 Alpha Arena
**Season 1** (Oct 18 – Nov 3, 2025; $10k real capital per model; Hyperliquid perps on major coins).

| Model | Result (sources disagree) |
|---|---|
| Qwen3 Max | **+22.3%** (about $12.2k; 43 trades, 30.2% win rate) |
| DeepSeek V3.1 | **+4.9%** |
| Claude Sonnet 4.5 | −30.8% to −42% |
| Grok 4 | −45% to −58% |
| Gemini 2.5 Pro | −45.6% to −56.7% |
| GPT-5 | −58.7% to −64.8% |

Sources: [forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/), [iweaver](https://www.iweaver.ai/blog/alpha-arena-ai-trading-season-1-results/), [gncrypto](https://www.gncrypto.news/news/qwen-wins-alpha-arena-season-1-with-22-percent-returns/), [upscale.trade](https://upscale.trade/blog/ai-trading-alpha-arena).

Observed behaviours:
- **Gemini overtraded.** 238 trades and **$1,331 in fees**, more than 13% of its capital.
- **Claude never hedged.** It ran a 100%-long book with no hedges or stop discipline.
- **Qwen won with concentration.** It stacked concentrated, high-leverage single positions.
- **Holding times diverged.** A mid-season snapshot showed DeepSeek's median hold at about 35h, versus about 5h for Claude and about 2h for Gemini.
- **Each model kept its "style" even when it was failing** ([buildix](https://www.buildix.trade/blog/nof1-alpha-arena-lessons-ai-trading-byok-strategy-advisor-2026)).
- **Commentators' verdict:** six models over two weeks in one market regime measures variance, not skill ([traderank](https://www.traderank.ai/blog/alpha-arena-alternatives-2026)).

Nof1's harness gave every model the same prompt every few minutes, with multi-timeframe indicators. Each reply was required as structured JSON: coin, signal, quantity, leverage, profit target, stop loss, **invalidation condition**, confidence, risk_usd and justification **[unverified; nof1.ai was unreachable for re-checking]**.

**Season 1.5** (Nov 19 – Dec 3, 2025): US-equity exposure (tokenized/tech names and NDX), 8 models (Kimi K2 and a "mystery model" added), and **four parallel modes**:
- **Monk Mode:** capital preservation.
- **Situational Awareness:** each model sees its rivals' PnL and rank.
- **Max Leverage:** forced maximum leverage, 20x on NDX and 10x on other names.
- A baseline mode.

Results:
- **Winner:** the mystery model, later revealed as **Grok 4.20**, with about +12% aggregate (about $4.8k across its four contests).
- **Best single entry:** Grok 4.20 in Situational Awareness (+34.6% in a later leaderboard snapshot).
- **Runners-up conflict across sources:** GPT-5.1 or DeepSeek ([forklog](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/), [gncrypto](https://www.gncrypto.news/news/mystery-model-alpha-arena-season-1-5-winner/), [traderank](https://www.traderank.ai/blog/alpha-arena-alternatives-2026)).
- **Overall:** Bloomberg (May 6, 2026; [FA-Mag syndication](https://www.fa-mag.com/news/ai-bots-auditioning-for-wall-street-trading-are-mostly-losing-86902.html)) reported that **across 32 result sets, only 6 finished in profit**. Models "trade too much, and make wildly different decisions when given identical instructions." Qwen placed **1,418 trades** where Grok 4.20 placed 158, under the same prompt.

**Season 2:** I found no public results. One source says it may be running privately; a CoinDesk mention of a "Season Two" around May 2026 is **[unverified]**.

### Academic and other live benchmarks
- **[StockBench](https://github.com/ChenYXxxx/stockbench)** (arXiv 2510.02209; Apache-2.0).
  - Setup: top-20 DJIA stocks, Mar–Jun/Jul 2025 (post-cutoff), daily prices, fundamentals and news.
  - Finding: **most agents fail to beat buy-and-hold**; static finance-QA skill does not transfer to trading.
- **[LiveTradeBench](https://github.com/ulab-uiuc/live-trade-bench)** (arXiv 2511.03628, UIUC; PolyForm Noncommercial).
  - Setup: 21 LLMs, 50 days live, US stocks plus Polymarket, portfolio-level allocation.
  - Findings: **LMArena scores do not predict trading results**; models showed distinct risk "personalities".
- **DeepFund** ("Time Travel is Cheating", NeurIPS 2025 D&B; [repo](https://github.com/HKUSTDial/DeepFund), MIT).
  - Setup: live data published after each model's cutoff.
  - Finding: even DeepSeek-V3 and Claude-3.7-Sonnet posted **net losses**.
- **AI-Trader** (arXiv 2512.10971, HKU).
  - Setup: 6 LLMs across US stocks, A-shares and crypto at several trading frequencies.
  - Findings: general capability does not equal trading skill, most agents had poor returns and risk control, and **risk control determined cross-market robustness**.
- **InvestorBench** (arXiv 2412.18174): 13 LLM backbones across stocks, crypto and ETFs; **all struggle in volatile regimes**.
- **FINSABER** ([repo](https://github.com/waylonli/FINSABER), arXiv 2505.07078, **KDD 2026 oral**). This is the key long-horizon test.
  - Setup: about 20 years (S&P 500, 2000–2025) and 100+ symbols, with FinMem, FinAgent and FinCon re-tested.
  - Finding: earlier LLM advantages "deteriorate significantly." LLM strategies are **too conservative in bull markets and too aggressive in bear markets**.
  - Its execution model is worth copying: `next_open` execution, per-share commission, 5 bps slippage, a liquidity cap of 2.5% of 20-day volume, and **`llm_cost_as_trade_cost=True`**.
- **"The Alpha Illusion"** (arXiv 2605.16895, May 2026).
  - Setup: reproduced TradingAgents and QuantAgent over one year.
  - Finding: gross alpha turns into net losses or underperformance against buy-and-hold once **commission, token cost, spread and impact** are included.
  - It proposes a P1–P6 reporting protocol: temporal integrity, frictions, counterfactual robustness, calibration, numerical execution, and **multi-agent disaggregation** (does each agent add value?).
  - It recommends using LLMs as *information interfaces* that feed separate calibration, risk and execution modules.
- **Others.**
  - HKU's live FX "Agentic Trader" ranking: higher trading frequency did not mean higher returns ([HKUBS](https://www.hkubs.hku.hk/aimodelrankings_en/abstract/FX_Trading.html)).
  - KellyBench, a simulated 2023-24 betting season: all 8 frontier models lost money; the best was Claude Opus 4.6 at −11% ([Kyunghyang](https://www.khan.co.kr/en/article/202604121638027)).
- **Real-money documentary precedent: the [ChatGPT Micro-Cap Experiment](https://github.com/LuckyOne7777/LLM-Trading-Lab)** (7.5k stars). It started in June 2025 with **$100**, runs a 6-month micro-cap portfolio, and posts daily CSV updates, automated stop-losses, benchmarks (Russell 2000, S&P 500) and Substack write-ups.
  - Early result: +23.8% in 4 weeks versus about +3.9% for the Russell 2000, with about half the gain from one stock (CADL) ([Daily Galaxy](https://dailygalaxy.com/2025/08/student-gave-chatgpt-100-to-invest-in-stock-market-four-weeks-later-his-portfolio-outperformed-wall-street/)).
  - Final six-month figures: **[unverified]**; they are in its `evaluation/paper.pdf`.
  - It is the closest analogue to our project, both in format and in its weakness (concentration luck).

### Concrete lessons for agent design
1. **Fees and turnover decide outcomes.** Cap trades per week, enforce a minimum holding period, and add hysteresis to signals.
2. **Leverage dominates variance.** Use 1x, or at most 2x, on Hyperliquid perps.
3. **Pick models on cost and reliability, not leaderboard rank**, since general intelligence did not predict returns.
4. **The same prompt produces very different behaviour across models.** Fix the policy in code, not in a persona.
5. **Risk control is the part that transfers across markets.** Make it deterministic.
6. **Only post-cutoff live or forward results count.** Short windows mostly measure noise, so pre-register the evaluation horizon.
7. **Always compare against buy-and-hold *and* the same strategy with the LLM layer switched off.**

---

## 5. Pitfalls practitioners keep reporting, and the fixes

| Pitfall | Evidence | Mitigation |
|---|---|---|
| **Look-ahead from data plumbing** | TradingAgents #1518/#1300/#1275/#1220/#1007 (future news, `now()` timestamps, revised FRED data, live social data in historical runs) | A point-in-time data contract (filter on publish or filing time, not period); decide at close(t) and fill at open(t+1); store as-of timestamps on every datum |
| **Look-ahead from model memory** | Profit Mirage (50–72% return decay past cutoff); LAP test (arXiv 2512.23847); FinCAD (arXiv 2605.24564: up to −67% in-sample correction); [index-level data shows more memorization](https://www.sciencedirect.com/science/article/pii/S0165176525004392); ChronoGPT ([2502.21206](https://alphaxiv.org/abs/2502.21206)) finds bias modest for next-day news returns | Evaluate LLM layers only after the model's cutoff; blind tickers and dates; label pre-cutoff cells; record-and-replay live decisions instead of re-querying history |
| **Overtrading and fees** | Gemini's 238 trades and $1,331 fees; Qwen's 1,418 trades in S1.5; HKU FX | Throttles and cooldowns (NOFX); fee-aware entry thresholds; daily bars only |
| **Excess leverage** | Alpha Arena losers; Max Leverage mode | Hard leverage clamp in code; risk sized as a percentage of equity per trade |
| **Prompt sensitivity and input insensitivity** | "Wildly different decisions" on identical prompts (Bloomberg); 82% of predictions unchanged under input perturbation (Profit Mirage) | Counterfactual tests (perturb inputs and check the decision changes); low temperature; majority vote across several samples for vetoes |
| **Hallucinated numbers** | FinRobot's "software computes" design; Alpha Illusion's "numerical execution" check | The LLM never emits prices or sizes; code precomputes every feature; the schema allows only bounded enums or multipliers |
| **Invisible costs** | Alpha Illusion; FINSABER's LLM-cost flag | Count tokens as a trading cost; report net-of-LLM results |
| **Short evaluation windows** | Sharpe standard error over weeks is about as large as the return itself | Pre-register a horizon of at least 6–12 months; report confidence intervals |
| **Memory and reflection as pseudo-alpha** | FinMem had the worst decay | Use memory for journaling and lessons, not as a signal; keep it time-stamped and small |

**Patterns that work:**
- Deterministic quant signal, with the LLM as a filter or veto (it may scale down or skip, never flip direction or scale up).
- Structured outputs validated with Pydantic, with fail-closed handling: an invalid reply means no trade. Add NOFX-style "safe mode" after N failures.
- A required **invalidation condition** that code then monitors.
- An append-only, hash-chained decision journal (ai-hedge-fund v2).
- Exchange-side stops and a dead-man's switch.

---

## Concrete recommendations for our build

### What to borrow
1. **The ai-hedge-fund v2 contract.**
   - Every agent returns a `Signal` in [−1, +1] plus a rationale.
   - Deterministic code blends signals, sizes positions and places orders.
   - Risk rules are hard gates; the hash-chained ledger is append-only; any book mismatch halts the bot.
   - Never assess and execute on the same close.
2. **The TradingAgents bull/bear debate, as a bounded pre-mortem only.** Run it only when the deterministic trend signal *changes* on a symbol, with 1 round and a cheap model. Copy its structured-output Trader/PM pattern, and use its closed look-ahead issues as a test checklist.
3. **FINSABER's execution and cost model.** Next-open fills, commission, 5 bps slippage, a liquidity cap, and LLM cost counted as a trade cost. Evaluate over long windows against buy-and-hold.
4. **The Alpha Arena output schema, cut down.** `{action ∈ {approve, reduce_half, veto}, invalidation_condition (from an enum of machine-checkable conditions), confidence (0–1), rationale}`.
5. **NOFX-style runtime guards.** Exchange-side SL on every entry, drawdown kill switch, cooldowns, safe mode after repeated LLM failures, and preflight checks before the bot starts.
6. **Lumibot's record-and-replay.** Persist every LLM decision with its input hash, so backtests replay the decisions instead of re-calling the model.
7. **The ChatGPT Micro-Cap Experiment's documentary format.** Daily CSVs, a public log of human interventions, and fixed benchmarks.

### What to avoid
1. **Avoid persona zoos.** Buffett-style agents are slow and costly, rely on memorized lore, and don't fit a daily trend strategy.
2. **Don't let an LLM invent prices, sizes or leverage.**
3. **Don't run LLM loops on minute cadences.**
4. **Use no leverage above 2x.**
5. **Don't backtest the LLM layer on any period before the model's cutoff and present it as evidence.**
6. **Don't treat memory or reflection modules as alpha.**
7. **Don't build on unmaintained or copyleft code you would have to distribute.**
   - backtrader is stale (GPL-3.0).
   - NOFX and open-nof1.ai are AGPL-3.0.
   - Lumibot is GPL-3.0; that is fine as an unmodified dependency, but be aware of it.
   - live-trade-bench is non-commercial.
   - AutoHedge has nothing behind it: no results, 37 commits, Solana only.

### Which libraries to depend on
- **Execution:**
  - [`alpaca-py`](https://github.com/alpacahq/alpaca-py) (Apache-2.0, Python ≥3.10; stocks, crypto, paper trading).
  - [`hyperliquid-python-sdk`](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) (MIT; use an API wallet, `cloid`, `schedule_cancel`, TP/SL triggers).
  - Wrap both behind one `Broker` protocol of about 150 lines.
- **Data:**
  - Alpaca bars for stocks.
  - [`ccxt`](https://github.com/ccxt/ccxt) (MIT; Hyperliquid certified) for crypto OHLCV, with a longer-history proxy from a major spot venue for backtests.
  - Parquet or DuckDB storage with as-of timestamps.
- **Research and backtest:** `vectorbt` OSS (Commons Clause, fine for a non-commercial study), or pandas plus your own next-open simulator that shares the live `signal()` function.
- **LLM layer:**
  - Pydantic schemas and provider-native structured outputs.
  - One cheap "quick" model for the analyst and skeptic, and at most one stronger model for the final veto.
  - Prompt caching, and calls only on signal-change events.
- **Optional:**
  - NautilusTrader, if crypto-side complexity grows (its Hyperliquid adapter is stable).
  - Freqtrade, if you ever split crypto into its own bot.

### Budget and evaluation rules for a $100 account
- **LLM spend is a first-class cost.** At $100, even $3/month is a 3%/month hurdle. Cap the spend in code, and publish results both gross and net of LLM cost.
- **Minimum position size:** about $10–25 per position. Hyperliquid's $10 notional minimum, plus room for stops, means 2–4 concurrent crypto positions at most. Alpaca fractional shares (about $1 minimum **[unverified]**) handle the stock side. For under $25k, use a cash account to sidestep the pattern-day-trader (PDT) rule.
- **Run three portfolios side by side from day one:**
  - (a) buy-and-hold SPY/BTC;
  - (b) the deterministic trend strategy without the LLM (shadow);
  - (c) the same strategy with the LLM filter.
  - The documentary's real research question is whether (c) beats (b) net of costs. Pre-register the horizon and the metrics.
- **Before going live:** test on Alpaca paper and the Hyperliquid testnet, run counterfactual-perturbation tests on the LLM layer, and run a blinded-ticker test.
