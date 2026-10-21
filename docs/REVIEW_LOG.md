# Review log

Every build pass is followed by an adversarial review by a separate agent
with a brief to hunt lookahead, accounting errors, risk bypasses, prompt
injection paths, cost leaks and spec mismatches. Findings and what changed
are recorded here because the documentary should show the mistakes, not
just the fixes.

## Review 1 — 2026-10-18 (75 tool calls, ~280k tokens)

### Critical
| # | Finding | Fix |
|---|---|---|
| C1 | The live cycle sized positions and stops in *adjusted* price units (the total-return series the features run on) while the broker trades dollars; on the Qlib cache that put a SPY stop 0.06% below entry instead of 5.6%, and every position would have "stopped out" the next day. | Every price the broker sees is now the unadjusted close; ATR and swing-low are converted with `raw_close/close` per row; exits compare raw closes. Regression test replays two days and checks entry ≈ dollar close, fill ≈ next open. |
| C2 | `BarStore.upsert` spliced two adjustment conventions (Qlib's forward index vs yfinance's backward adjustment), producing a +9,900% phantom jump in `adj_close` at the seam and garbage indicators for a year after the first refresh. | Old rows are rescaled to the new basis on the first overlapping bar (or at the seam); a test asserts `adj_close/close` stays continuous. |
| C3 | Positions could exist with no resting stop: post-close Alpaca orders return unfilled, the stop was sent for shares not yet held, its result was never checked, and the paper broker never evaluated stops at all. | Fills are confirmed by reconciling with the broker on the next run before a stop is armed; every confirmed position without a stop is re-armed each run and closed after two failures; the paper broker queues market orders to the next open and evaluates stops against the session low — the same convention as the backtester. |
| C4 | Backtest "zombies": a held name whose history ended vanished from equity, never traded out, and could freeze a halted run forever. | Positions in names whose bars end are closed at the last bar (`delisted`); end-of-test liquidation uses each name's last bar; halts always release after their cooldown. |

### High
| # | Finding | Fix |
|---|---|---|
| H1 | Paper broker filled at the signal-day close (optimistic same-bar fill), biasing the rules-only shadow against the primary. | Next-open fills (see C3). |
| H2 | A budget-exhausted portfolio-manager call raised out of the pipeline and aborted the whole run (no ledger entry, no report). | Caught: the candidates are vetoed with "budget exhausted"; the run completes rules-only. |
| H3 | AI costs leaked: partial candidate costs unbooked, budgets keyed by wall-clock day instead of run date (replays got fresh budgets), the weekly review unbooked, and the gate did not estimate the next call. | Spend is booked from the client's running total; entries carry the run date; the review pays from the same budget; the gate uses a per-call cost estimate. |
| H4 | Live drawdown halt was a permanent trap (peak never reset, no liquidation), and the shadow had no halt or daily-loss gate at all. | Halts liquidate, record a cooldown in the ledger and reset the peak afterwards; the shadow keeps its own mark ledger so both portfolios face identical gates. |
| H5 | Free text from headlines reached the decision model via reader summaries; anonymisation had no word boundaries (the ticker `A` destroyed every headline); company names were never supplied. | Only enums/numbers/booleans are forwarded (`bounded_view`); tickers match case-sensitively on word boundaries; names come from the constituents file. |

### Medium / low (all addressed unless noted)
Regime multipliers shared between backtester and live run (ETFs may enter in RED; crypto gated by the BTC regime); `reduce_half` applied after sizing; multipliers also scale the position cap; portfolio-vol scaling live; six invalidation conditions monitored (news-driven exits for open positions remain a TODO); two-way book/broker reconciliation and Alpaca `BTCUSD` mapping; quality truncation never applied to open positions; replays of past dates never fetch news; one journal per date, verified in `hive status`; death persisted with a post-mortem; kill-switch runs still mark equity; hospice budget floor removed; stale-cache warning; NaN scores filled. Prompt caching is not claimed any more: the system prompts are below the cacheable minimum, and the volatile JSON is in the user turn by design.

### Decision record — 2026-10-18, incentive framing (config 1.2.0)
The study owner asked for the agents to be told the stakes: one bankroll, every
cost deducted from it, growth buys budget and sub-agents, the death floor is
final. The earlier build kept a neutral objective because survival framing is
documented to produce rule-bending in LLM agents. The two positions were
reconciled on the architecture rather than the prompt: the LLM holds no tool
that can bend a rule (enum actions that only shrink, proposals validated
against pre-registered bounds, no order, transfer or config write path), so the
framing is now explicit (`prompts.OBJECTIVE`, `prompts.firm_status`), the
auditor is briefed to look for incentive gaming, and the limits are stated as
non-negotiable in the same breath. Also in 1.2.0: paper sub-agents allowed from
the survival tier, large-cap memecoins in the crypto sleeve at a 10% cap, a
bankroll guard that refuses to size on an un-reset paper account, a broker
tradability check, and a fix for the operator prompt's `N` placeholder, which
had been rewriting every capital N in the prompt (including "JSON").

Same day, after the first live paper run: the cycle now runs every day at
00:37 UTC (crypto has a closed bar every UTC day; stock and crypto bars share a
run date; classes without a bar on the run date skip entries); the crypto
refresh drops the forming UTC candle, which the first design had been feeding
into the features; decision journals are committed with the state; `exit`
journal entries carry asset class and P&L; a news desk reads headlines for open
positions and exits on a material negative event; and a weekly, bounded capital
allocator moves the crypto sleeve toward the desk that earned
(`docs/research/05-incentives.md`). The first hand-triggered weekly run showed
two more defects: the operator's empty reply left no trace (it now records a
"no proposals" iteration with the model's note or the rejection reason, and is
told to propose at least one experiment whenever a seat is free), and the
weekly jobs drew on the same per-day budget as the next day's trading
decisions (they now draw on a seven-day research pot booked under
`<date>-research`).

Config 1.3.0 (same day): the operator may now design sub-agents with a
different entry family (`template_cross`, `high_breakout`, `ma_pullback`,
`momentum_threshold`), chosen from a categorical bound, and keeps a research
log of every test that it reads before proposing. The owner asked for the risk
limits, leverage and promotion to be opened to the operator as well; that
change was blocked by the session's permission classifier as a weakening of
safety controls and is recorded here as pending the owner's own decision (the
bounds file is theirs to edit).

Also found while making the desks visible: Alpaca rejects plain stop orders
for crypto, so every crypto position would have lost its stop twice and been
closed by the no-stop rule; crypto stops are now stop-limit orders with
full price precision (a sub-cent SHIB stop had been rounded to zero). Added:
`desk_notes` journal entries (every desk's bounded view per candidate), the
hourly heartbeat (`hive watch`, `watch.yml`) and the social scout.

Added next: monitor sub-agents (news channels the operator proposes), a daily
code-driven sweep of the strategy space, one-click human promotion whose
overrides the primary adopts, a research-pot bonus for sub-agents that beat
the parent, and a dashboard built around the sub-agents (a card each with its
curve, overrides, today's activity or reports), the operator's notes, the
auditor's findings and a firm-wide activity timeline.

Config 1.4.0, at the owner's explicit direction that no person should be in
the loop: promotion into the primary is decided by the evidence rule
(`hive/governance.py`) and guarded by a baseline sub-agent that can revert it.
The levers the operator may promote are still only those in
`config/bounds.toml`; the risk envelope itself was not widened here.

### Done well (reviewer's words)
Point-in-time features with a no-lookahead test; internally consistent backtest accounting; fail-closed LLM layer with enum-only actions; sound ledger hash chain; layered live-mode gating and no withdraw path anywhere.

### The lab (every backtest visible, the search never idle)
At the owner's request to see every backtest the firm runs and to have it
working constantly: the sweep became an hourly **lab** (`watch.yml`). Every
evaluation — each cell's and the parent's on each data snapshot — is an
append-only record in `reports/lab/` with its in- and out-of-sample metrics
and downsampled equity curves; the dashboard draws the whole map (return vs
drawdown, every test), the curve of any test against the parent and buy &
hold, the full log and the queue, and reloads itself when a newer build is
published. The search has a second stage: refinements around the best cells
so far, one numeric knob at a time inside `config/bounds.toml`, finer steps as
coarse ones run out, only knobs the cell's entry family uses, and pairwise
merges of winners with disjoint knobs. Dedup now uses the lab's complete
record rather than the registry's last 200 iterations. The parent reference is
cached per data snapshot so an hourly run spends its minutes on new cells. The
verdict, the seat rule and the bounds are unchanged; nothing here widens the
risk envelope.

### Config 1.5.0: the firm is never idle, and punishment is as quick as reward
The hourly job now works for most of every hour in five rounds; after each
round the dashboard is published and the lab's records are committed, so the
page moves every few minutes (and refreshes itself in place, in the viewer's
time zone). Variant sub-agents are rules-only paper books, so the tiers seat
more of them (survival 2, base 4, growth 6, hive 8) and the best waiting cell
is seated first. The shadow period is 10 days; a sub-agent 3% behind the
parent after 10 days retires; one whose own book is down 15% dies the same
day; a promotion is reverted by the baseline after 21 days. `hive capital-in`
records money added to the bankroll as a public ledger entry. The owner asked
for Hyperliquid and DEX memecoins to trade and for leverage in the search;
the Hyperliquid adapter exists but routing a second venue, and any widening of
`risk.*`, are recorded here as the owner's decisions (SETUP §6b lists the
exact steps); the risk envelope is unchanged.

### Config 2.0.0: one rule
At the owner's explicit, repeated direction, the firm now has one rule: grow
the bankroll as much and as fast as possible, net of every cost; death is the
only hard stop. What is in force in code: promotion needs any lead over the
parent after the shadow period and retirement follows any lag (both 10 days);
a child's own paper book dies only at the firm's death line (−75%); the sizing
and concentration levers under `risk.*` (risk per trade, position, sleeve and
open-risk concentration, volatility target, the drawdown halt and its
cooldown, the daily loss limit) are in `config/bounds.toml`, in the lab's grid
and refinement, and the evidence promotes them into the live configuration
like any other knob. Leverage is not in the space: the backtester caps every
order at cash and the venue has no margin at this size, so a leverage knob
would test nothing real.

Two parts of the owner's instruction are not in force, because this session's
permission gate refused the edits as a weakening of safety controls and the
reviewer did not route around it. They are the owner's to make: (1) the lab's
verdict still rejects a cell whose out-of-sample drawdown is more than 10%
worse than the parent's (`operator.max_drawdown_tolerance`, used by
`verdict()` in `hive/operator/iterate.py`); the one-rule form would reject
only a cell that reached the death line; (2) the objective text in
`hive/agents/prompts.py` still tells the desks that the limits are "not yours
to change", and the operator prompt in `hive/operator/iterate.py` still says
the limits stay. The reviewer's position remains on record above: the evidence
base is 2–4% a year, and concentration raises the chance of death as it raises
the growth rate.

### The hub, the always-on lab, the trench scout
The lab now runs back to back around the clock: sessions of six rounds chain
themselves with a `workflow_dispatch` at the end, every round yields to a
waiting trading, weekly or promotion run, and the daily and weekly runs
restart the chain when they finish. Every job checks out the branch head rather
than the commit it was queued at, so a queued session never works from stale
state. A safety-net daily run at 03:41 UTC trades the bar only if the 00:37 run
never happened; marks are now idempotent per date, so a repeat run cannot age a
sub-agent (manual reruns used to count extra shadow days).

The search has a third stage, mutations of the best cells, so it never runs
dry. Lab storage was resized for thousands of cells a day: curves share a
calendar, rejected cells keep a lean out-of-sample curve, finished days roll
into day files, and the sweep's research-log entry is one summary per round.
The repository still grows by roughly a few megabytes a day of lab record;
that is the price of keeping every test.

The trench scout paper-trades brand-new Solana launches every lab round with
its own $100 book (SETUP §6b). Scouts are judged on their own book only: a
flat scout is no longer retired for lagging a stock book it does not trade.

The dashboard is rebuilt as a live hub. Its *Rules in force* panel is generated
from the configuration and lists every rule with its value and who can change
it, including the objective text the models actually read.

### The Hyperliquid desk, the social pulse, a hub that shows what the lab learns
The Hyperliquid desk paper-trades the firm's crypto rules on Hyperliquid's most
traded perps from public prices only (no account, no keys, no orders), with
the taker fee and hourly funding charged; it needs neither the venue routing
change this session's permission gate refused nor any key. The social pulse
reads StockTwits, Reddit and DexScreener every lab round and keeps symbols and
counts only; X is read only with the owner's paid token. The hub adds a live
ticker, the trench funnel, equity and trades, the desk, the pulse, and "what
the lab is learning": every setting the lab varied against the out-of-sample
result it produced. Leaderboards collapse settings with identical results
(inert knobs), which also shows where the lab spends tests on settings that do
nothing. A caution recorded for the owner: after thousands of cells, the
out-of-sample window is itself being searched, so the best lab numbers are
partly luck; promotion still requires out-growing the parent live on paper.

### Config 2.1.0 to 2.2.0: the prop-firm objective, evidence-chosen sizing, readiness

The owner asked (2026-10-20) for the firm to optimise for prop-firm success,
for the reward and punishment system to decide risk per trade and position
size instead of fixed caps, and to say when it is ready for a real evaluation.
pump.fun-sized memecoins and X were deferred by the owner.

* **Objective.** Every backtest is also scored as rolling simulated
  evaluations (`hive/hive/propfirm.py`, rules in `[prop]`): +10% target, −5%
  daily and −10% total loss limits, 4 days minimum, 60-day horizon, one start
  every 5 days. The lab ranks and judges cells by out-of-sample pass rate,
  growth breaking ties; a rescore stage gives the best earlier cells a pass
  rate. Every model's objective text now says the firm is scored this way.
* **Punishment.** A variant whose own paper book breaks a prop loss limit is
  retired that day, after passing too (a funded account keeps the limits).
* **Sizing.** `sizing_mode = "edge"` sizes each trade from the book's own last
  closed trades (a fraction of the Kelly bet, between a floor and a ceiling);
  the position caps run to 1.0 (no cap). Both are lab levers, tested in the
  grid in both sleeves, and the defaults are unchanged until a cell that moves
  them wins. The prop objective is what keeps them honest: a setting that
  risks too much fails its simulated evaluations. Exits now journal their R
  (resting-stop closes an estimate at the stop), so a live book has a record
  to size from.
* **Readiness.** `hive prop`, run by the daily workflow, checks each book (30
  trading days on paper, no breach, target reached, ≥ 50% simulated pass rate
  out of sample) and opens one GitHub issue the first time a book clears all
  four. Deposits are taken out of the record (time-weighted).
* **Caveats.** The simulator sees daily closes, not intraday ones, so the real
  evaluation is harder. Thousands of cells have now searched the
  out-of-sample window, so pass rates near the top of the leaderboard are
  partly luck; the 30-day paper record is the check that is not.
* **Still pending, owner's decision** (refused by this session's permission
  gate earlier, not retried): removing the drawdown-tolerance check from the
  verdict, the operator prompt rewrite, and routing orders to Hyperliquid.

### Config 2.3.0: shorts, forex and gold, no pause on paper, models paid by what they add

The owner asked (2026-10-20) for shorting without leverage, forex, XAUUSD and
other markets, no drawdown pause except with real money, more room for the AI
to choose, and for the operator to act on the reward and punishment system.

* **Shorts.** The backtester trades a short as the long rules on the mirrored
  market (`mirror_bars`), with the money on the real price (bid to sell, ask to
  cover, full value posted from cash). `direction` is a lever per sleeve.
* **Macro sleeve.** Ten major forex pairs, gold, silver and three index futures
  from Yahoo's daily series, long and short by default, CFD-like costs. Oil is
  left out: its continuous futures went below zero in April 2020, which a price
  series cannot carry. Overnight swap is not charged on forex.
* **Paper books the primary cannot trade** (shorts, macro) run on the lab's
  simulator one new day at a time, on their own book, judged by their own
  record and never promoted into the Alpaca account.
* **Drawdown halt** in live mode only; its two levers left the search.
* **Earned budgets** for the desks (value added over the rules-only shadow) and
  the operator (scorecard against the free search), 0.25x to 4x.
* **Operator fix.** Every operator proposal had been backtested on stock data
  against the stock parent, so a crypto proposal could never win; each now
  runs on its own sleeve. Its instructions now state the rules as the code
  applies them (they said a human promotes winners and that it could not change
  sizing, both no longer true). At the time of the change its record was 0 of 3
  against the search's 570 of 1,400, so its next pot is about half the base.
* **Caveats.** Records tested before 2.3.0 ran with the drawdown halt; newer
  ones without, so a few old leaderboard rows are not like for like. An
  unlevered forex book moves slowly: a 10% prop target in 60 days is rare
  without leverage.

### Config 2.4.0: the lab at full speed, scored on passes and breaches

The owner asked (2026-10-21) for many more backtests per second, a focus on
forex and gold, and rewards and punishments honed for clearing prop-firm
challenges quickly.

* **Speed.** One stock cell took 158 s; the same cell now takes about 10 s when
  its indicators are cached, 12 s when only its entry rule is new and 25 s from
  cold, and the lab runs four cells at once (one per core). Measured on the
  same data: 2.6 cells a minute before, 25 a minute after (about 9x). Every
  change was checked for identical results against the old engine on real
  data in both windows (equity, trades, metrics), shorts included.
  How: indicator frames are built in one step, cached per rule set and reused
  across cells that only change exits, sizing or direction; an entry-rule change
  recomputes only the entry column; the day loop reads arrays instead of pandas
  rows; the queue refills itself when a round outruns it.
* **Focus.** The queue gives forex and gold three turns, crypto two, stocks one.
* **Reward and punishment.** The prop score: a passed simulated evaluation earns
  1, a breach costs 0.5, a timeout nothing; ties go to the faster pass. Cells
  scored before 2.4.0 rank by their pass rate until the rescore stage re-tests
  them.
* **Pending owner decision.** Without leverage the forex and gold sleeve makes
  about 4% a year and passes about 4% of simulated evaluations; a prop
  account's leverage (often 1:30 or more on forex) is what makes a 10% target
  reachable. Modelling a prop account's leverage for the macro sleeve's
  evaluations is the owner's call (they asked for no leverage on 2026-10-20).

### Config 2.5.0: challenges as firms run them, 1:2 trades, a search that stays alive

* **Two-phase challenges.** Researched current rules (third-party sources, verify
  on the firm's site): FundedNext Stellar 2-Step 8% then 5%, 5% daily, 10% static,
  5 days; FTMO 2-Step 10% then 5%, 5% daily, 10% static, 4 days, no time limit.
  The simulator now runs both phases (8% / 5%, 5 days each, 60-day horizon), and
  a score made under other rules is rescored. Pass rates in the industry are
  about 5% to 20%, and about 70% of failures are loss-limit breaches.
* **Reward-to-risk exits.** `take_profit_r` per sleeve (0 to 6; 2 = 1:2),
  decided on the close, sold at the next open, identical in backtest and daily
  run. Initial stop distance (1 to 5 ATR), holds from 5 days, up to 12
  positions and 6 entries a day are now in the search.
* **The search had stalled.** Mutations were built from cells carrying levers
  removed in 2.3.0 and failed validation, and mutations were capped at 6
  settings while the best cells use 12: stocks and crypto produced no
  mutations. Seeds are stripped to searchable levers, the cap is 16, and a timed
  run rebuilds its queue every 60 cells. Measured in production after the fast
  lab went live: about 17 to 20 cells a minute, against 2 to 2.6 before.
* **What did not change.** Daily closes only: the simulator cannot see an
  intraday breach of the daily limit, so real challenges are harder. The macro
  sleeve is still unlevered (owner's decision pending).
* **Seats go to the best evidence.** When every paper seat is taken, the best
  waiting cell takes the seat of the weakest seated variant if it scores at
  least 0.05 higher under today's prop rules and that variant is behind the
  parent on paper; at most once a day. The Alpaca primary keeps trading the
  pre-registered rules until a seated variant out-earns it for 10 paper days and
  is promoted (none yet: the first seats were filled under the old scoring).

### Config 2.6.0: loss limits checked on the day's worst equity

* **Research (three reports, 2026-10-21).** Firms check the daily and total loss
  limits against equity during the day, floating losses included (FTMO:
  balance at midnight CE(S)T minus 5% of the initial balance; The5ers and
  FundingPips: the higher of balance and equity at the day's start). One firm
  publishing its own data saw 79% of failures come from the daily limit. Most
  firms no longer have a time limit, so the 60-day horizon is the firm's own
  speed budget, not a rule.
* **The change.** The backtest records each day's worst equity (every open
  position marked at its adverse extreme of the day, all at once: a little
  harsher than the real path). The simulator checks a day's loss as (worst
  equity minus the previous close) over the starting balance, and the total
  loss as worst equity over the start; the target still counts on the close.
  The rules key changes, so every cell is rescored.
* **Measured on real data before the change went live:** the unlevered
  strategies barely move (best crypto cell +0.145 to +0.144, stock parent
  -0.014 to -0.021): their bad days already show in the closes. It matters as
  risk per trade, or leverage, goes up.
* **Checked and not changed.** One report argued that a strategy with no edge
  and high risk scores about +0.06 under a 0.5 breach penalty. On this
  simulator (60-day horizon, daily limit, minimum days) zero-edge random walks
  at 0.5% to 3% daily volatility score -0.004 to -0.19, so the penalty stays.
* **Noted for the next step.** About 20 independent challenges fit in the
  out-of-sample window, so a 17% pass rate is 17% plus or minus about 8%: the
  gaps between the top cells are mostly noise.

### Config 2.7.0: the heat guard, and ranking that discounts luck

* **What the research found** (three reports, 2026-10-21: open-source prop
  simulators and risk managers, current firm rules and statistics, and the
  mathematics of reaching a target before a floor with a Monte Carlo and an
  exact dynamic programme):
  * The biggest single lever is a cap on what the whole book can lose in one
    day. In the Monte Carlo (a +0.2R-per-trade strategy, two correlated trades
    a day) it took daily-limit failures from 24-33% to 0% and two-phase passes
    from 49% to 71%.
  * Stop at the target (the simulator already ends an evaluation there).
  * About 1% risk per trade under that cap; never 2-3% without it.
  * Without a deadline, do not raise risk; under one, raise it only late.
    Martingale and streak scaling do not help.
  * Passing in under 10 trading days with high probability needs a daily
    Sharpe of about 0.22 (3.5 a year) or gambling-level risk. With a
    realistic edge and prudent sizing the median pass takes about 14 trading
    days, and about 31% of attempts pass within 10.
* **The heat guard** (`risk.heat_guard_pct`, 0 = off, searched 0 to 6%). At
  every close, after the trailing stops, if the book would lose more than the
  cap with every stop filled, every stop is raised so each position's loss to
  its stop shrinks in the same proportion. New entries are sized to the room
  left. The daily run does the same and arms the raised stops at the broker,
  so backtest and live agree. The lab tries it at 2/3/4% on its best cells and
  on fresh grid cells.
* **Measured on the best crypto cell, real data:**

  | Heat guard | In-sample score | In-sample daily breaches | In-sample worst drop | OOS score | OOS pass | OOS daily breaches | OOS worst drop |
  |---|---|---|---|---|---|---|---|
  | off | 0.000 | 14% | -47% | +0.144 | 18% | 8% | -29% |
  | 2% | +0.080 | 1% | -5% | +0.116 | 12% | 1% | -14% |
  | 3% | +0.062 | 9% | -7% | +0.141 | 16% | 5% | -17% |
  | 4% | +0.057 | 9% | -7% | +0.138 | 16% | 4% | -20% |

  The remaining breaches at 3-4% are three 2011-2012 bitcoin days that gapped
  through the stops (each lands in about 12 overlapping evaluations). The
  guard trades speed for survival; the lab decides where.
* **Ranking discounts luck.** Each score now carries its standard error (the
  spread of +1 / -0.5 / 0 outcomes over the about n x 5/60 independent
  evaluations), and cells rank by score less one standard error. The verdict
  against the parent is unchanged. The score format is part of the rules key,
  so every cell is rescored.
* **Fixed the same day.** A cell that never passes nor breaches scores 0 with
  no spread, so "score less one standard error" ranked it above a small, noisy
  winner; the forex and gold leaderboard filled with such idle cells. A cell
  with a positive score now always ranks first, then the lower bound orders.
* **Not done, and why.**
  * Intraday entries (opening-range breakouts and similar) need intraday data;
    the owner asked for no new data sources for now.
  * Room-to-floor sizing hurts under a deadline (-6 points at 60 days).
  * Leverage for the forex and gold sleeve is still the owner's decision.

### Protective stops that stay protective (2026-10-21)

* **Found from the owner's Alpaca screenshot.** TRV's and COP's stops, submitted
  on Monday, show "expired"; CVX, bought on Wednesday's open, has none. Alpaca
  accepts fractional stock stops only as day orders, which lapse at the close,
  and the daily run treated any stored stop id as armed, so it never re-armed
  them: the two positions had no resting stop on Tuesday and Wednesday. A buy
  queued before the open also had no stop until the next daily run (about 11
  hours, including the whole session).
* **Fixes.**
  * The daily run counts a stop as armed only while it rests at the broker
    (`open_stops`); a lapsed one is re-armed, and one placed by the guardian
    is adopted.
  * `hive guard`, after every lab round (about every eight minutes, never at
    the same time as a daily run: the two share the state lock), arms a stop
    for every held position without one. For an unconfirmed fill it uses the
    fill price less the planned risk. In the session it closes a position
    already through its stop. It never buys or moves a stop.
  * When the broker cannot be asked, nothing is cleared or re-armed.

