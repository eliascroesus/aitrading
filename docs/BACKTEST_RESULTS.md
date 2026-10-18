# Backtest results — trend-reset-v1 (as pre-registered) and what the validation protocol found

All runs: $100 starting equity, fractional shares, Alpaca stock cost model
(0 commission, 5 bps slippage per side), next-open fills, intraday stops,
point-in-time S&P 500 membership + 24 ETFs, SPY regime gate with VIX, 25%
drawdown halt with 21-bar cooldown. Data: Qlib US daily (Yahoo-derived,
1999-12-31 → 2020-11-10). **Survivorship caveat:** the price dataset contains
only names alive in Nov-2020; point-in-time membership limits but does not
remove the bias. Raw outputs: `reports/backtest-*/`, `reports/experiments/`.

## 1. The spec as written (full period 2001-01 → 2020-11)

| | Strategy (spec) | SPY buy & hold |
|---|---|---|
| CAGR | **0.1%** | 7.2% |
| Sharpe | 0.06 | 0.45 |
| Max drawdown | −32.6% | −55.2% |
| Trades | 1,007 | — |
| Win rate / profit factor | 39% / 1.01 | — |
| Average hold | 17 days | — |
| Avg exposure / time in market | 41% / 60% | 100% |

Where the money went (first run, before the halt-cooldown fix):

| Exit reason | n | win rate | avg R | P&L |
|---|---|---|---|---|
| trend break (2 closes < SMA50 or 1 < SMA200) | 344 | 24% | −0.14 | **−$50.7** |
| chandelier stop | 353 | 40% | +0.13 | +$33.5 |
| failure stop (day 15) | 175 | 65% | +0.07 | +$13.2 |
| max hold | 1 | — | +9.0 | +$4.4 |

The diagnosis is mechanical: the setup buys a pullback that, by construction,
sits 2–6 ATR below the recent high — i.e. near the 50-day MA — so the
"two closes below SMA50" exit fires on noise days after entry. The median
hold was 12 days; the strategy never got to ride the three-month trend it was
designed for. 53% of all profits came from the top 5% of trades, which is the
normal trend-following shape and the reason wide exits matter.

## 2. Pre-registered sensitivity (one-at-a-time, in-sample 2001–2012)

SPY over this window: CAGR ≈ 2.9%, max drawdown −55%.

| Parameter | value | CAGR | Sharpe | MaxDD | trades | PF |
|---|---|---|---|---|---|---|
| BASE | — | 1.6% | 0.23 | −16.0% | 441 | 1.16 |
| impulse_window | 42 | 2.9% | 0.40 | −16.5% | 418 | 1.33 |
| impulse_window | 90 | 2.0% | 0.29 | −19.2% | 454 | 1.19 |
| reset_period | 15 | 0.5% | 0.10 | −20.6% | 442 | 1.05 |
| reset_period | 30 | 2.0% | 0.29 | −16.6% | 445 | 1.18 |
| impulse_min_z | 1.0 | 1.6% | 0.20 | −19.9% | 455 | 1.17 |
| impulse_min_z | 2.0 | 2.1% | 0.30 | −17.0% | 428 | 1.21 |
| trail_atr | 3 | 1.7% | 0.25 | −20.8% | 501 | 1.15 |
| trail_atr | 5 | 2.6% | 0.35 | −19.3% | 420 | 1.26 |

Every cell is mildly positive with a third of the index's drawdown; no cell
is good. The rule set is robust to its parameters and robustly mediocre.

## 3. Post-hoc exit exploration (in-sample; **not pre-registered**)

| Change | CAGR | Sharpe | MaxDD | trades | PF | avg hold |
|---|---|---|---|---|---|---|
| BASE | 1.6% | 0.23 | −16.0% | 441 | 1.16 | 17 d |
| exit_closes_below_mid = 4 | 2.1% | 0.30 | −18.0% | 406 | 1.23 | 19 d |
| exit_closes_below_mid = 8 | 2.6% | 0.36 | −19.8% | 382 | 1.29 | 20 d |
| **SMA50 exit disabled** (SMA200 break only) | **3.7%** | **0.48** | −20.5% | 337 | 1.41 | 24 d |
| trail_atr = 6 / 8 / 10 | 2.0% / 1.7% / 1.6% | 0.29 / 0.25 / 0.25 | −19% / −16% / −21% | | | |
| failure_stop_bars = 25 / off | 1.2% / 1.6% | 0.19 / 0.24 | | | | |
| never tighten the trail | 1.2% | 0.19 | | | | |
| max_hold = 252 | 1.5% | 0.23 | | | | |

Only one lever matters: stop exiting on the 50-day MA. Everything else is noise.

## 4. Null test (full period, 5 seeds) — the pre-registered kill criterion

Same template, sizing and exits; entry days drawn at random among
template-passing bars at the same overall frequency as the real signal.

| | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| Spec entry (actual) | **0.1%** | 0.06 | −32.6% |
| Random entries, seed 0–4 | 1.0%, 3.6%, 3.9%, 1.5%, 3.9% (mean 2.8%) | 0.15–0.42 | −18% to −30% |

**5 of 5 random-entry runs beat the real entry rule.** By the protocol in
`STRATEGY.md` §10, the pullback-then-breakout trigger does not justify
itself: inside this trend template, *when* you buy carries no measurable
edge, and the specific trigger (buy the first up-days after a quiet dip)
appears to buy short-term reversals that then mean-revert. This agrees with
the literature summarised in `RESEARCH.md` (entries matter little; template,
exits and sizing carry the edge).

## 5. Crypto sleeve (BTC + ETH, close-only data, 2015-01 → 2026-05)

| | Strategy | note |
|---|---|---|
| Total return / CAGR | +76% / 5.1% | BTC buy & hold over the window is orders of magnitude higher |
| Sharpe / Sortino | 0.72 / 0.36 | |
| Max drawdown | −11.9% | BTC buy & hold: ≈ −80% |
| Trades / win rate / PF | 63 / 44% / 3.1 | |
| Avg exposure / time in market | 2.8% / 12% | 1% risk with 3-ATR stops on 60–80% vol assets = ~8% positions |
| Fees paid | $7.84 on $100 | Alpaca crypto taker 0.25% per side |

Close-only data means intraday stops are approximated at the close; the
sleeve is deliberately tiny under the survival sizing. It is a drawdown
reducer, not a return engine.

## 6. Walk-forward (in-sample 2001–2012 → out-of-sample 2013–2020)

_See section appended below once the out-of-sample grid completes._

## 7. What this means for the study

1. The strategy as briefed is **not deployable** for "insane ROI"; with the
   survival sizing it is a low-exposure, low-drawdown trend system that
   roughly tracks cash-plus over 2001–2020 and loses to its own null test.
2. The only robust improvement is to the **exit** (no SMA50 exit). Whether
   any entry rule beats random inside the template is tested out of sample
   in §6; the honest default if none does is the simplest one.
3. None of this measures the LLM layer: it can only be evaluated forward,
   against the rules-only shadow, net of its cost (`ARCHITECTURE.md`).
