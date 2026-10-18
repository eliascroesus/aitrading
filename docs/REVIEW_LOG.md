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

### Done well (reviewer's words)
Point-in-time features with a no-lookahead test; internally consistent backtest accounting; fail-closed LLM layer with enum-only actions; sound ledger hash chain; layered live-mode gating and no withdraw path anywhere.
