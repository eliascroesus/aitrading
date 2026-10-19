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

### Done well (reviewer's words)
Point-in-time features with a no-lookahead test; internally consistent backtest accounting; fail-closed LLM layer with enum-only actions; sound ledger hash chain; layered live-mode gating and no withdraw path anywhere.
