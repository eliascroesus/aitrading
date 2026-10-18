# Backtest results — trend-reset-v1 as pre-registered, and what the validation protocol found

**Bottom line first.** On 2001–2020 US data, no configuration of the "ROC reset
above a rising 200DMA" rule family beats buy-and-hold in either the in-sample
or the out-of-sample window, and the specific pullback-then-breakout *entry*
does not beat random entries inside its own trend template. What the family
reliably delivers is a third to a half of the index's drawdown at cash-like to
low-single-digit returns. The one robust structural improvement is to stop
exiting on the 50-day MA immediately after a pullback entry. Nothing here
supports "insane ROI"; the documentary's honest question is whether the AI
layer adds value on top of these rules *net of its own cost* — which can only
be measured forward.

All runs: $100 start, fractional shares, Alpaca stock cost model (0 commission,
5 bps slippage per side), next-open fills, intraday stops, point-in-time S&P 500
membership + 24 ETFs, SPY/VIX regime gate, 25% drawdown halt with 21-bar
cooldown, data-quality guard on. Data: Qlib US daily (Yahoo-derived,
1999-12-31 → 2020-11-10). Raw outputs: `reports/backtest-*/`,
`reports/experiments/clean/` (the first pass on uncleaned data is quarantined in
`reports/experiments/contaminated-uncleaned-data/`, see `DATA.md`).

**Survivorship caveat.** The price dataset contains only names alive in Nov-2020;
point-in-time membership limits but cannot remove the bias. That bias *helps*
long-only backtests — so the real-world numbers would be worse, not better.

Benchmarks (SPY total return): 2001–2020 CAGR 7.2%, Sharpe 0.45, max drawdown
−55%; 2001–2012 CAGR 2.8%; 2013–2020 CAGR 14.1%, Sharpe 0.87, max DD −34%.

## 1. The spec as written, full period 2001-01 → 2020-11

| | Strategy (spec) | SPY buy & hold |
|---|---|---|
| CAGR | **0.9%** | 7.2% |
| Sharpe / Sortino | 0.14 / 0.16 | 0.45 / 0.56 |
| Max drawdown | −31.7% | −55.2% |
| Trades / win rate / profit factor | 1,004 / 40% / 1.06 | — |
| Average hold | 17 days | — |
| Avg exposure / time in market | 56% / 69% | 100% |
| Drawdown halts | 1 (Dec-2018) | — |

The median hold of ~12 days says the system never rides the three-month trend
it was designed for: the setup buys a pullback that, by construction, sits
2–6 ATR under the recent high — i.e. near the 50-day MA — so "two closes below
SMA50" fires on noise days after entry (first-pass diagnosis: 344 such exits,
24% win rate, −$51 on a $100 book).

## 2. Pre-registered sensitivity (one-at-a-time, in-sample 2001–2012)

| Parameter | value | CAGR | Sharpe | MaxDD | trades | PF | hold |
|---|---|---|---|---|---|---|---|
| BASE | — | 1.7% | 0.25 | −16.0% | 440 | 1.18 | 17 d |
| impulse_window | 42 | 2.9% | 0.40 | −17.0% | 418 | 1.32 | 18 d |
| impulse_window | 90 | 2.0% | 0.29 | −19.2% | 454 | 1.20 | 17 d |
| reset_period | 15 | 0.5% | 0.10 | −20.6% | 442 | 1.05 | 17 d |
| reset_period | 30 | 1.9% | 0.28 | −15.8% | 449 | 1.18 | 17 d |
| impulse_min_z | 1.0 | 1.7% | 0.25 | −17.8% | 456 | 1.18 | 17 d |
| impulse_min_z | 2.0 | 2.2% | 0.31 | −17.0% | 429 | 1.22 | 16 d |
| trail_atr | 3 | 1.5% | 0.23 | −20.8% | 501 | 1.13 | 15 d |
| trail_atr | 5 | 3.2% | 0.42 | −19.1% | 414 | 1.32 | 18 d |

Every cell is mildly positive at a third of the index's drawdown; none is
good. Robust to its parameters, and robustly mediocre.

## 3. Post-hoc exit exploration (in-sample; **not pre-registered**)

| Change | CAGR | Sharpe | MaxDD | trades | PF | hold |
|---|---|---|---|---|---|---|
| BASE | 1.7% | 0.25 | −16.0% | 440 | 1.18 | 17 d |
| exit_closes_below_mid = 4 | 2.1% | 0.30 | −18.0% | 406 | 1.23 | 19 d |
| exit_closes_below_mid = 8 | 2.6% | 0.36 | −19.8% | 382 | 1.29 | 20 d |
| **SMA50 exit disabled** (SMA200 break only) | **3.6%** | **0.46** | −20.5% | 342 | 1.39 | 23 d |
| trail_atr = 6 / 8 / 10 | 2.1% / 1.7% / 1.5% | 0.30 / 0.25 / 0.24 | | | | |
| failure_stop_bars = 25 / off | 1.0% / 1.6% | 0.16 / 0.24 | | | | |
| never tighten the trail | 1.6% | 0.25 | | | | |
| max_hold = 252 | 1.7% | 0.25 | | | | |

One lever matters: do not exit on the 50-day MA. Everything else is noise.

## 4. Walk-forward grid (in-sample 2001–2012 → out-of-sample 2013–2020)

Cells: SMA50 exit {on, off} × trail {4, 5} ATR × impulse window {42, 63}.

| exit SMA50 | trail | window | IS CAGR | IS Sharpe | IS MaxDD | OOS CAGR | OOS Sharpe | OOS MaxDD | OOS halts |
|---|---|---|---|---|---|---|---|---|---|
| on | 4 | 42 | 2.9% | 0.40 | −17% | −0.6% | 0.01 | −36% | 1 |
| on | 4 | 63 (spec) | 1.7% | 0.25 | −16% | 0.2% | 0.07 | −32% | 1 |
| on | 5 | 42 | 2.5% | 0.36 | −15% | −1.9% | −0.11 | −34% | 1 |
| on | 5 | 63 | 3.2% | 0.42 | −19% | 2.3% | 0.26 | −33% | 1 |
| off | 4 | 42 | 1.4% | 0.20 | −21% | 0.0% | 0.06 | −34% | 1 |
| off | 4 | 63 | 3.6% | 0.46 | −21% | 1.6% | 0.19 | −30% | 1 |
| off | 5 | 42 | 2.6% | 0.36 | −20% | 1.3% | 0.17 | −31% | 1 |
| **off** | **5** | **63** | **3.9%** | **0.50** | −20% | **1.9%** | 0.22 | **−23%** | 0 |

Out of sample — a 14%-a-year bull market with the book ~80% invested — every
cell returns between −1.9% and +2.3% a year. The in-sample winner (SMA50 exit
off, 5-ATR trail, window 63) keeps the best drawdown (−23%) but earns 1.9%.
The exit fix transfers (OOS 0.2% → 1.9%, drawdown −32% → −23%); nothing else does.

## 5. Null tests — the pre-registered kill criterion

Same template, sizing and exits; entry days drawn at random among
template-passing bars at the same frequency as the real signal, 5 seeds.

| Config / window | Actual CAGR | Null CAGR (mean; range) | Null runs ≥ actual |
|---|---|---|---|
| Spec, full 2001–2020 | 0.9% | 2.3% (1.0% – 4.1%) | **5 of 5** |
| Exit-fixed v1.1, in-sample 2001–2012 | 3.9% | 0.3% (−1.0% – 2.6%) | 0 of 5 |
| Exit-fixed v1.1, out-of-sample 2013–2020 | 1.9% | 6.0% (4.2% – 9.1%) | **5 of 5** |

The spec entry fails its own criterion on the full period. The exit-fixed
variant beats random in-sample and loses to it out of sample — the signature
of a fitted artefact, not an edge. *When* you buy inside this template carries
no measurable value; the trigger (first up-days after a quiet dip) appears to
buy short-term bounces that then mean-revert, consistent with the reversal
literature in `RESEARCH.md`.

## 6. Alternative entries and wide exits (exploratory, not pre-registered)

| Variant | IS CAGR / Sharpe / MaxDD | OOS CAGR / Sharpe / MaxDD | Full CAGR |
|---|---|---|---|
| Template-cross entry (enter the day the template first passes), v1.1 exits | −1.2% / −0.09 / −26% | 8.8% / 0.77 / −16% | — |
| Template-cross, spec exits | 1.7% / 0.25 / −13% | 6.3% / 0.59 / −18% | — |
| Wide exits (10-ATR trail, no failure stop, 252-bar max), spec entry | −0.6% / −0.04 / −27% | 3.8% / 0.41 / −21% | 1.8% |
| Wide exits, template-cross entry | −0.3% / 0.00 / −26% | 1.1% / 0.15 / −31% | 3.7% |
| Wide exits, random entry | −1.6% / −0.15 / −31% | 8.8% / 0.84 / −14% | 0.9% |

The pattern is the same everywhere: whatever looks good out of sample looked
bad in sample and vice versa. The 2013–2020 numbers are mostly beta — the
less the rules interfere (random entries, wide trails), the closer they get to
the index's 14%.

## 7. Crypto sleeve (BTC + ETH, close-only data, 2015-01 → 2026-05)

| | Strategy | BTC buy & hold |
|---|---|---|
| CAGR | 4.4% | 62% |
| Sharpe / Sortino | 0.82 / 0.40 | 0.88 / 1.17 |
| Max drawdown | **−9.8%** | −83.8% |
| Trades / win rate / PF | 57 / 46% / 3.0 | — |
| Avg exposure / time in market | 2.5% / 12% | 100% |
| Fees paid on $100 | $6.47 | — |

With 1% risk and 3-ATR stops on 60–80%-vol assets, positions are ~8% of
equity, so the sleeve is a drawdown reducer, not a return engine. Close-only
data means intraday stops are approximated at the close.

## 8. Decision: trend-reset-v1.1 (pre-registered for the live study)

Adopted change, recorded in `config/strategy.toml` and `STRATEGY.md`: the
SMA50 trend-break exit is disabled (SMA200 break only) and the chandelier
trail is 5 ATR. Rationale: the only lever that improved both windows and the
only one with a mechanical explanation. Expected performance from history:
**roughly 2–4% a year with a −20% to −30% drawdown**, i.e. a low-volatility
proxy for the index with no demonstrated alpha. The entry rule is kept as the
trader's brief specifies it, with the explicit note that it has not beaten
random entries and is a candidate for replacement after the live phase.

What the live study measures: three lines daily — SPY/BTC buy-and-hold, the
rules-only shadow, and rules + LLM filter — all net of costs. The AI layer's
value can only be judged forward, after the models' training cutoff.

## 9. Reproduce

```
hive data seed --qlib-root <dir>                       # + data/raw files (DATA.md)
hive backtest --sleeve stocks --start 2001-01-01 --end 2020-11-10
hive backtest --sleeve crypto --start 2015-01-01
hive experiment --kind oat  --start 2001-01-01 --end 2012-12-31 --params "stocks.impulse_window=42,90;stocks.reset_period=15,30;stocks.impulse_min_z=1.0,2.0;stocks.trail_atr=3,5"
hive experiment --kind grid --start 2013-01-01 --end 2020-11-10 --params "stocks.exit_closes_below_mid=2,1000;stocks.trail_atr=4,5;stocks.impulse_window=42,63"
hive experiment --kind null --seeds 5 --start 2001-01-01 --end 2020-11-10
hive experiment --kind oat --entry-mode template_cross --start 2013-01-01 --end 2020-11-10 --params "stocks.exit_closes_below_mid=1000"
```
