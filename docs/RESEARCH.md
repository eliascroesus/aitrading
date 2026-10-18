# Research synthesis (2026-10-18)

Four research passes were run before any design decision; the full reports with
inline sources and verification tags live in `docs/research/`. Everything here
drove a concrete choice in the code.

## 1. What open-source LLM trading projects teach (report 01)

* **Every serious live test in 2025-26 mostly lost money.** Nof1's Alpha Arena
  Season 1 (6 frontier models, $10k each, Hyperliquid perps, 17 days): 2 of 6
  profitable; the losers were killed by leverage and turnover (one model paid
  13% of its capital in fees on 238 trades). Season 1.5 (US equities, 32 result
  sets): 6 profitable. Academic live benchmarks (StockBench, LiveTradeBench,
  DeepFund, AI-Trader) agree: most agents fail to beat buy-and-hold and general
  model ability does not predict trading results.
* **Backtests before the model's training cutoff are inflated by memorisation**
  ("Profit Mirage": 50–72% return decay past the cutoff; FINSABER at KDD 2026:
  the reported LLM edge disappears over 20 years / 100+ symbols; "The Alpha
  Illusion": TradingAgents' gross alpha becomes net underperformance once token
  cost, commission and spread are counted).
* Even the most-starred framework (TradingAgents, 112k stars) shipped a series of
  look-ahead bugs (future news, `now()` timestamps, revised macro data).
* **The convergent design:** ai-hedge-fund v2's "the LLM never touches the
  trade" — agents output a bounded signal, deterministic code sizes and places
  orders, risk limits are hard gates, the ledger is append-only. FinRobot's
  "models reason, software computes". NOFX's runtime guards (exchange-side
  stops, drawdown kill switch, cooldowns, safe mode after LLM failures).

**Decisions:** no persona zoo; LLM = filter/veto with `approve | reduce_half | veto`
plus a machine-checkable invalidation condition; one bounded bull/bear round
only when a signal fires; LLM spend booked as a trading cost; three portfolios
tracked (buy-and-hold, rules-only, rules+LLM); no engine dependency — a ~300-line
next-open backtester shares the live `signal()` code; thin adapters over
`alpaca-py` and `hyperliquid-python-sdk`.

## 2. Strategy evidence (report 02)

* 200-day/10-month MA timing (Faber; Zakamulin's 155-year tests) mostly **cuts
  drawdowns rather than adding return**; it earns its keep in bear markets.
* Time-series momentum (Moskowitz-Ooi-Pedersen; AQR's century of trend) and
  cross-sectional momentum (Jegadeesh-Titman; the 12-to-7-month "echo") are
  robust; the most recent month tends to **reverse**, which is exactly why a
  "ROC reset" inside a longer trend is a sensible entry. Proximity to the
  52-week high (George & Hwang) adds independent predictive power.
* Single-stock trend following (Wilcox-Crittenden; Zarattini-Pagani-Wilcox 2025,
  66k trades 1950–2024): ~15% CAGR, ~32% max drawdown, and **most of the edge is
  in the exit and sizing** — random entries with the same wide ATR exit kept
  most of the Sharpe.
* Quiet (low-turnover) pullbacks revert; heavy-volume selloffs continue
  (Medhat-Schmeling) → the volume dry-up condition.
* Momentum crashes happen in panic states after declines with high vol
  (Daniel-Moskowitz); vol-managed momentum roughly doubles Sharpe (Barroso &
  Santa-Clara) → the crash guard and portfolio-vol scaling.
* PEAD is weak for large caps since ~2006; analyst revisions still carry drift;
  the earnings-announcement premium argues against blanket avoidance →
  blackout only for *new* entries.
* News/LLM sentiment alpha (Lopez-Lira & Tang) is a 1-day effect, concentrated
  in small caps and negative news, and decaying with adoption; anonymise
  tickers in prompts (Glasserman-Lin) → sentiment is a veto/tie-breaker only.
* Crypto: trend/TSMOM works (Liu-Tsyvinski; Donchian ensemble Sharpe > 1.5 net on
  top-20 coins); a 200DMA filter on BTC mainly cuts the −83% drawdowns; funding
  is a cost/crowding filter, not a directional signal.
* Memecoins: ~1% of Pump.fun launches graduate; 98.6% show rug/pump signatures;
  74% of 2.9M buyers lost money; profits accrue to launchpads, bots and insiders
  → off by default, at most a capped lottery sleeve.
* Small-account math: with an unknown edge, 1% risk per trade (~1/8 Kelly) keeps
  P(50% drawdown) near zero; 5% risk gives ~50%; 10× leverage on a BTC-like
  asset is liquidated within a year with ~99% probability.

**Decisions:** the exact rule set in `STRATEGY.md`; 1% risk; 4·ATR chandelier
exit; no profit target; regime gate; vol scaling; aggressive sleeve at 0%.

## 3. Infrastructure and costs (report 03)

* The PDT rule was retired (SEC approval 2026-04-14, effective 2026-06-04);
  Alpaca has switched. A $100 Alpaca account has no margin and no shorting
  (margin needs $2k), but fractional/notional orders work from $1.
* Hyperliquid: $10 minimum notional, 0.045%/0.015% taker/maker, agent wallets
  cannot withdraw, **US persons are restricted by its terms**; HIP-3 stock perps
  (trade.xyz) exist but inherit the same restriction and 2× fees.
* Free data has shrunk: X API is pay-per-use (~$0.005/post), Reddit needs app
  approval, CryptoPanic's free tier is gone, Stooq needs a key; Alpaca Data
  Basic, SEC EDGAR, Finnhub free, Google News RSS remain.
* Recommended stack costs ~$2–7/month — **2–7% of the bankroll per month**, the
  main hurdle the strategy must beat. LLM tokens dominate.

**Decisions:** Alpaca paper by default; Hyperliquid behind an eligibility flag;
cheap model for bulk roles, decision model only for the final gate; daily AI
budget ≈ 0.05% of equity; GitHub Actions cron with state committed.

## 4. Survival agents and safety (report 04)

* No autonomous LLM agent has a public, audited, multi-month trading record.
  "AI got rich" stories (Truth Terminal, ai16z) were token attention, not
  alpha; most agent tokens lost 85–99%.
* The real losses came from security, not trading: a natural-language
  jailbreak drained Freysa; a compromised dashboard drained AIXBT; a decimals
  error sent $250k+ from "Lobstar"; a Morse-code prompt injection chained
  through Grok into Bankrbot; memory injection redirected ElizaOS transfers.
* Survival pressure has side effects: Vending-Bench winners colluded and lied
  ("every dollar counts"); Project Vend's shopkeeper was talked into giveaways;
  models threatened with replacement blackmailed in 79–96% of trials.
* Conway's "Automaton" is the nearest precedent (pays for inference, dies at
  $0, spawns ≤ 3 children) — and its "critical: seek any path to revenue" tier
  is precisely the wrong incentive for a trader.
* At $100, $1/day of AI spend is a +3,678%/yr hurdle; $0.05/day is +20%/yr.

**Decisions:** the agents are told the stakes outright (one bankroll, costs
deducted from it, growth buys budget and sub-agents, the death floor is final),
a 2026-10-18 revision of the original "neutral objective" stance, made safe by
the architecture rather than the prompt: the LLM holds no tool that can bend a
rule (see `ARCHITECTURE.md`, "What the agents are told"); death floor at $25 / 75% drawdown decided by code; risk
tightens as equity falls; no transfer tool anywhere; quarantined news reader
with bounded outputs; memory with provenance; kill switch + dead-man's switch;
hash-chained ledger; tiers with hysteresis; children in shadow mode first;
graveyard page and pre-registered rules for the documentary.

## What we tell viewers up front

1. Most retail trading loses money after costs; this is a measurement, not a
   promise.
2. Any "insane ROI" screenshot without fees, drawdown and an exchange read-only
   link is marketing.
3. The honest research question is: *does the LLM layer beat the same rules
   without it, net of its own cost?* — and we publish both lines every day.
