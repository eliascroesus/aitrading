# HIVE architecture

HIVE is a survival-constrained, multi-agent trading system built for a public
study. One sentence summarises the design that the evidence supports
(see `RESEARCH.md`): **deterministic code finds, sizes and protects every trade;
LLM agents may only research, debate and veto.**

```
                 ┌──────────────────────────────────────────────────────────┐
                 │                    daily run (hive run)                   │
                 └──────────────────────────────────────────────────────────┘
  data/        ─►  regime  ─►  universe  ─►  signals  ─►  candidates (ranked)
  (point in       SPY/BTC      PIT S&P500     200DMA↑       top N, ≤ max_candidates
   time cache)    green/       + ETFs         ROC reset
                  yellow/red   + BTC/ETH/SOL  breakout
                                                  │
                      ┌───────────────────────────┴───────────────────────────┐
                      │                 LLM agent layer (optional)            │
                      │  quarantined news reader ─► bounded fields only       │
                      │  fundamental ▸ technical ▸ news/sentiment analysts    │
                      │  bull vs bear (1 round) ─► portfolio manager          │
                      │  output per candidate: approve | reduce_half | veto   │
                      │  + invalidation condition (machine-checkable enum)    │
                      └───────────────────────────┬───────────────────────────┘
                                                  │ intents (never sizes/prices)
                        risk engine (code): stops required, caps, heat, daily loss,
                        drawdown halt, regime multipliers, tier multipliers, kill switch
                                                  │ approved orders
                        execution: PaperBroker | AlpacaBroker | HyperliquidBroker
                                                  │ fills, positions, equity
                        survival ledger: equity − AI/data/infra costs, budget, tier, death
                                                  │
                        journal + report: decisions, costs, equity curve, hit rate, runway
```

## Layers (and the rule that keeps them honest)

| Layer | Package | Rule |
|---|---|---|
| Data | `hive.data` | Everything is a canonical *bars* frame; callers slice with `as_of()`. Parquet cache with provenance sidecars; point-in-time universe membership. |
| Signals | `hive.signals` | Pure functions of bars + `StrategyConfig`. No network, no LLM. Row *t* depends only on rows ≤ *t* (tested). |
| Backtest | `hive.backtest` | Signal at close *t*, fill at open *t+1*; stops on the day's low; costs on every fill; same config object as live. |
| Risk | `hive.risk` | Hard gates in code. LLM actions are `approve / reduce_half / veto` — they can only shrink. |
| Agents | `hive.agents` | Anthropic SDK, structured outputs, anonymised tickers in news prompts, bounded fields only between agents, cost accounting and estimate-based budget gating per call. |
| Execution | `hive.execution` | One `Broker` protocol; paper by default; live needs an explicit confirmation phrase; Hyperliquid uses an agent wallet that cannot withdraw. |
| Survival | `hive.survival` | Hash-chained ledger; equity net of AI costs; death floor; tiers with hysteresis; children start in shadow mode. |
| Orchestrator | `hive.orchestrator` | Wires the daily run; writes the journal and the report; respects the kill switch before any order. |

Dependencies point downwards only: a lower layer never imports a higher one.

## The agents

Six roles, all stateless per run, all returning schema-validated JSON (pydantic).
The number of candidates sent to the LLM is capped (`max_candidates_for_llm`)
and the daily token budget comes from the survival ledger.

| Agent | Model (configurable) | Input | Output |
|---|---|---|---|
| News reader (quarantined) | cheap model, low effort, no tools | raw headlines/filings with ticker names **anonymised** | `event_type ∈ enum`, `sentiment ∈ [-1,1]`, `materiality`, `source_ids` — never raw text |
| Fundamental analyst | cheap model | precomputed facts (earnings date, revisions, quality flags) | `signal ∈ [-1,1]`, `flags[]`, rationale |
| Technical analyst | cheap model | the feature row (numbers computed by code) | `signal`, `setup_quality`, rationale |
| Sentiment/catalyst analyst | cheap model | bounded news-reader output | `signal`, `crowding_flag`, `catalyst_window` |
| Bull vs Bear | cheap model, one round | the three analyst outputs | strongest case each way |
| Portfolio manager | decision model | everything above + risk snapshot | per candidate `approve/reduce_half/veto`, `invalidation_condition`, `confidence` |
| Reviewer/auditor | decision model, weekly | journal, trades, costs | drift findings, cost-per-decision, calibration (Brier), suggested config changes **as a proposal only** |

The PM's `invalidation_condition` is chosen from an enum the code can monitor
(e.g. `close_below_ma50_2d`, `earnings_miss`, `news_material_negative`); the
orchestrator checks it daily and exits when it fires.

## Survival mechanics (the "life" of the agent)

* **Costs are paid from the bankroll.** Every LLM call, data call and (optionally)
  infra charge is appended to the ledger and deducted from reported equity.
* **Daily AI budget** = `clamp(tier.daily_ai_budget_pct × equity, min, max)`. At
  $100 the base tier allows ~$0.05/day. When the budget is spent the run
  continues on deterministic signals alone (never borrows from tomorrow).
* **Death** is decided by code: equity ≤ `death_floor_usd` ($25) or a 75%
  drawdown from peak. The system cancels orders, flattens, writes a
  post-mortem and refuses to run again. No resurrection tier, no "seek any
  revenue" mode.
* **Tiers** (hysteresis 15%; upgrades need a net-positive streak):

  | Tier | Equity | Effect |
  |---|---|---|
  | hospice | < $40 | close-only, cheapest model, human alerted |
  | survival | $40–90 | half risk |
  | base | $90–250 | normal |
  | growth | $250–1k + 30 net-positive days | may spawn 1 child (news scout), shadow mode first |
  | hive | > $1k + 60 days + human approval | up to 3 children, second strategy sleeve |

* **Children** run in shadow mode for ≥ 14 days, get a live budget only if their
  value-of-information beats their cost, and are retired automatically when it
  doesn't. No child may spawn a child.

Why the prompts do **not** say "you will die": survival framing is documented
to produce rule-bending and deception in LLM agents (Vending-Bench, agentic
misalignment studies — see `RESEARCH.md`). The metaphor lives in the ledger and
the documentary, not in the agents' objective, which is stated as "maximise
risk-adjusted return net of costs; shutdown is an acceptable outcome".

## Safety baseline

* No transfer/withdraw tool exists anywhere in the codebase. Hyperliquid runs
  on an agent wallet; Alpaca keys are trading-only.
* The LLM never emits prices, sizes or leverage. Code precomputes every number.
* Untrusted text (news, posts, filings) reaches only the quarantined reader,
  which returns bounded fields; the decision model never sees raw text.
* Kill switch: the presence of `state/KILL` aborts before any order.
* Live mode requires `HIVE_MODE=live` **and** the confirmation phrase in
  `HIVE_LIVE_CONFIRM`.
* Append-only, hash-chained ledger and decision journal; tampering is detected
  on load.
* Fail closed: an invalid or missing LLM reply means *no trade*, never a default
  approve; repeated LLM failures put the run into safe mode (rules only).

## Running

```
cp .env.example .env            # fill keys; paper by default
hive data seed --qlib-root ...  # offline cache for backtests
hive backtest --sleeve stocks   # deterministic strategy, writes reports/
hive run                        # one daily cycle (paper); add --live only when ready
```

See `STRATEGY.md` for the exact rules and `DATA.md` for where every number comes from.
