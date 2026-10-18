# Strategy specification — "ROC reset above a rising 200DMA" (trend-reset-v1)

This is the pre-registered rule set implemented in `hive.signals` and driven
by `config/strategy.toml` (defaults in `hive.config`). Changing a number here
means changing the committed config, which the public git history records.

The brief from the trader: only trade names above a *rising* 200-day moving
average; look for a rate of change that has **reset to ~0 after being higher**
(a consolidation inside an uptrend); ride a ~3-month trend. The evidence review
(`RESEARCH.md`, `docs/research/02-strategy-evidence.md`) supports each
component and adds the pieces that the literature says carry most of the
edge: the exit, the sizing, and a regime gate.

All measures use the daily close. ROC is volatility-normalised so one
threshold works for stocks and coins:

```
r_t     = ln(C_t / C_{t-1})
sigma_d = std(r, 63 bars)
zROC_N  = ln(C_t / C_{t-N}) / (sigma_d * sqrt(N))
ATR     = Wilder ATR (20 bars stocks, 14 crypto); close-to-close fallback for close-only data
```

## 1. Universe

* **Stocks:** point-in-time S&P 500 members (survivorship-free membership file),
  price ≥ $5, ≥ 300 bars of history. Weekly refresh.
* **ETFs:** a fixed basket of liquid, unleveraged index/sector/asset ETFs
  (`universe.etf_symbols`).
* **Crypto:** BTC, ETH, SOL (spot preferred; perps only at ≤ 1.5× and only for
  eligible operators — see `DATA.md` on Hyperliquid's terms).
* **No memecoins in the core.** The optional aggressive sleeve is off (0%).

## 2. Trend template (all required)

| | Stocks/ETFs | Crypto |
|---|---|---|
| Close above long MA | SMA200 | SMA200 (365-day year) |
| Long MA rising over | 21 **and** 63 bars | 30 bars |
| Mid MA above long MA | SMA50 > SMA200 | same |
| Near the high | close ≥ 0.75 × 252-day high | close ≥ 0.60 × 365-day high |
| Long momentum positive | C[t−21]/C[t−252] − 1 > 0 | C[t−7]/C[t−180] − 1 > 0 |

## 3. Setup — "was higher, then reset"

| | Stocks/ETFs | Crypto |
|---|---|---|
| S1 impulse | max zROC_63 over bars t−73…t−10 ≥ 1.5 | max zROC_90 over t−97…t−7 ≥ 1.5 |
| S2 reset | −1.0 ≤ zROC_21 ≤ +0.3 | −1.0 ≤ zROC_30 ≤ +0.3 |
| S3 depth | 2 ≤ (63-day closing high − C)/ATR20 ≤ 6 | 2 ≤ … /ATR14 ≤ 7 (90-day high) |
| S4 quiet | SMA(vol,10)/SMA(vol,50) ≤ 0.9 | SMA(vol,7)/SMA(vol,30) ≤ 0.9 |

S1 is roughly "+20–25% in three months for a 30%-vol stock"; S2 is roughly
"−9% to +3% over the last month". A setup stays valid for 10 bars.

## 4. Trigger (within 10 bars of a setup)

close > max(high[t−1], high[t−2]) **and** close > EMA10 **and** ROC_5 > 0
(ROC_3 for crypto). Orders go in at the next open.

## 5. Sizing (code only)

* Initial stop = max(lowest low of the last 10 bars − 0.5·ATR, entry − 3·ATR).
* R = entry − stop. Risk **1% of equity** per trade → qty = 0.01·equity / R.
* Position cap: 20% of equity per stock, 30% per ETF or BTC/ETH, 15% altcoin.
* Max 6 positions, max 2 new entries per day, total open risk ("heat") ≤ 6%.
* If 20-day realised portfolio vol > 20% annualised, new risk is scaled by 20%/vol.
* Regime multiplier (below) and survival-tier multiplier apply on top.
* Fractional shares (Alpaca ≥ $1); Hyperliquid ≥ $10 notional.

## 6. Exits (first one that fires)

1. **Chandelier stop:** max(previous stop, highest close since entry − 4·ATR); 3·ATR after 63 bars. Checked against the day's low.
2. **Trend break:** 2 consecutive closes below SMA50, or 1 close below SMA200 → exit next open.
3. **Failure stop:** on bar 15 (20 crypto), exit if close < entry + 0.5R.
4. **Review:** from bar 63 (90 crypto), exit if the trend template no longer passes.
5. **Hard max hold:** 126 bars (180 crypto).
6. **Invalidation condition** chosen by the portfolio manager (live only).
7. **Material negative news** flagged by the quarantined reader (live only).

No profit target. Most of a trend system's profit comes from a few large
winners; capping them destroys the edge.

## 7. Regime gate (benchmark SPY; BTC for crypto)

| State | Condition | Effect |
|---|---|---|
| GREEN | close > SMA200 and SMA200 rising over 21 bars | normal |
| YELLOW | above SMA200 but MA falling, or VIX > 30 | risk × 0.5 |
| RED | close < SMA200 | no new stock/crypto longs; ETFs may still enter on their own template; stops keep managing exits |
| crash guard | 24-month return < 0 and 21-day vol > 30%, or 36-month return < 0 | no new momentum entries |

## 8. Fundamentals & news overlays (live; evidence-weighted)

* No new entry within 5 trading days before an earnings report.
* Net EPS revisions over 60 days must not be negative (when data is available).
* Junk filter: exclude negative operating cash flow with > 10% share dilution.
* News reader vetoes on material negative events (guidance cut, offering,
  investigation, accounting issue, fixed-price takeover) and on crowding
  (mentions > 3× 30-day average with a > 2·ATR one-day jump).
* Positive sentiment is a tie-breaker only. A 1-day sentiment signal never
  opens a 3-month trade.

## 9. What a $100 account should expect

Good long-only trend/momentum systems in the literature make roughly 10–30%
a year with 20–40% maximum drawdowns, before the decay that follows
publication. There is no evidence-based way to turn $100 into $1,000 quickly
without a matching probability of ruin (a fair game aimed at 10× before −90%
succeeds ~9% of the time). The configuration above is tuned for survival
first; the aggressive sleeve stays at 0% until the core has a live track record.

## 10. Validation protocol (required before live)

0. Data-quality guard applied to every series (`DATA.md`); any result whose
   profit concentrates in one trade is a data problem until proven otherwise.
1. Walk-forward backtest with costs: stocks 2001–2020 (offline data), crypto
   2015–2026; results in `BACKTEST_RESULTS.md`.
2. Robustness grid: impulse window 42/63/90, reset window 15/21/30, S1 threshold 1.0/1.5/2.0, trail 3/4/5 ATR.
3. **Null test:** same template, sizing and exits with random entry dates. The reset entry must beat it.
4. Three portfolios side by side from day one: buy-and-hold benchmark, rules-only, rules + LLM filter. The study's question is whether the LLM layer beats rules-only *net of its cost*.
5. Paper trade 4–8 weeks; human sign-off to go live.
