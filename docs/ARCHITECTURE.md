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
| News reader (quarantined) | cheap model, low effort, no tools | raw headlines/filings and social posts (StockTwits, Reddit) with ticker names **anonymised** | `event_type ∈ enum`, `sentiment ∈ [-1,1]`, `materiality`, `attention_spike`, `source_ids` — never raw text |
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
  | hospice | < $40 | close-only, cheapest model, human alerted, no sub-agents |
  | survival | $40–90 | half risk; 1 paper sub-agent |
  | base | $90–250 | normal; 2 paper sub-agents |
  | growth | $250–1k + 30 net-positive days | 3 paper sub-agents, larger research budget |
  | hive | > $1k + 60 days + human approval | 5 paper sub-agents, second strategy sleeve |

* **Research pot.** Unspent daily budget accrues for seven days into a pot the
  Sunday jobs draw on (`SurvivalLedger.research_budget`); their costs are booked
  under `<date>-research`, so the operator and the auditor never take the day's
  decision budget and the day's decisions never starve the weekly research.
* **Capital follows results.** Every Sunday `hive allocate` reads the realised
  P&L of each desk (asset class) from the decision journals over the last 42
  days and moves the crypto sleeve's share of equity one step (5 points) toward
  the desk that earned, inside 20–60%. It is the prop-firm scaling plan applied
  to desks (`docs/research/05-incentives.md`), done in code, never by the LLM.
* **The news desk.** Each run, the quarantined reader checks the day's headlines
  for every open position; a material negative event closes it at the next
  open. Only bounded fields leave the reader; the cost comes out of the day's
  research budget.
* **Sub-agents (children)** are paper portfolios: they cost nothing but compute,
  so the tiers gate how many may run, not whether. Each runs in shadow for
  ≥ 14 days, is scored by value of information against the parent, is retired
  automatically when it loses, and gets a live allocation only when a human
  promotes it. No child may spawn a child.

**What the agents are told.** Every prompt states the firm's objective plainly:
grow the one bankroll, net of every cost including the agent's own calls; more
equity buys more research budget and more sub-agents, less equity takes them
away, and the death floor is permanent. The portfolio manager and the operator
also receive the ledger's live numbers (`prompts.firm_status`: equity, drawdown,
tier, what the next tier unlocks, today's budget, runway). This is the study
owner's decision, revised on 2026-10-18 from an earlier "neutral objective"
stance. The research record (`RESEARCH.md`) shows survival framing makes LLM
agents bend rules **when they hold tools that can bend them**; here they hold
none. The LLM can only approve, halve or veto a trade code has already sized,
propose parameters code validates against pre-registered bounds, and recommend.
Every limit is enforced outside the prompt, the auditor is briefed to look for
incentive gaming (approve-all, bound-edge proposals, in-sample-only wins), and
the prompt never says "whatever it takes": it says the limits are not
negotiable and that cash is a valid decision.

## The operator and the hive (how the system iterates on itself)

```
weekly: hive operate
  ledger/journal summary + current tunables + pre-registered bounds (config/bounds.toml)
        │
        ▼  LLM (decision model, budgeted): ≤ 3 proposals {name, hypothesis, overrides ≤ 3 params}
  code validates every override against the bounds → rejects anything else
        │
        ▼  code backtests each variant in-sample and out-of-sample vs the parent
  verdict (code): OOS CAGR > parent AND OOS drawdown within tolerance AND IS return ≥ 0
        │
        ▼  if the survival tier allows a child → spawned as a SHADOW paper portfolio
daily: children trade their own paper books (stocks / crypto / all / meme sleeves)
  value of information = child return − parent return over the same days; losers retire
        │
        ▼  human: `hive hive promote <id> --approve` → overrides committed to strategy.toml
```

Three things create sub-agents. The **sweep** (`hive sweep`, daily, no LLM)
backtests untested cells of the pre-registered strategy space and seats the
winners. The **operator** (Sundays) proposes variants from the evidence and
**monitors**: news-channel sub-agents watching up to five topics or symbols
whose bounded daily reports reach the portfolio manager as "desk reports". A
**human** promotes a variant that has beaten the parent for the shadow period
(`promote-sub-agent` workflow); its overrides become the primary's, and it
frees its seat. Every sub-agent that beats the parent also raises the firm's
research pot by a quarter, up to double.

The operator designs sub-agents: it may pick the entry family (`entry_mode`)
and any knob listed in `config/bounds.toml`, for any sleeve, and it reads its
own research log (`state/research_log.jsonl`) and every variant ever tested
before proposing, so it builds on evidence rather than repeating itself. It
cannot touch anything outside the bounds file, cannot spend past the research
pot, and cannot promote anything to live. Every proposal, verdict and child
mark is in `state/hive.json` and on the dashboard.

## The dashboard

`hive ui build` renders `reports/dashboard/index.html` from the ledger, books,
journals, daily reports and backtest outputs: survival tiles (net equity,
tier, runway, AI budget, regime, integrity), the three published equity lines
indexed to 100, drawdown, cumulative AI spend, positions with stop status,
the latest run's decisions, the tier ladder, children and operator iterations,
backtests and every experiment cell, LLM call costs by role, recent activity.
No server: GitHub Pages serves it; `hive ui serve` runs it locally.
A pre-built example from a 33-day 2020 replay is in `docs/examples/dashboard-replay-2020/`.

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
