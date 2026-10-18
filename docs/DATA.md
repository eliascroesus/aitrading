# Data provenance

Every series the system uses is cached as parquet under `data/cache/<class>/<SYMBOL>.parquet`
with a JSON sidecar recording source, fetch time and date span. Backtests read
only the cache; paper/live runs refresh it from the network first.

## Offline sources (used for the published backtests)

| Dataset | What | Span | Caveats |
|---|---|---|---|
| Qlib US daily (`qlib_data_us_1d_latest.zip`, Yahoo-derived) | OHLCV + adjustment factor for 8,994 US symbols incl. SPY/QQQ and ^GSPC | 1999-12-31 → 2020-11-10 | Prices are stored as a total-return series normalised to 1.0 on the first day; `close / factor` recovers dollar prices (verified: AAPL → $115.97 on 2020-11-10). **Survivorship-biased:** nearly all symbols were alive in Nov 2020; names delisted earlier are missing. We mitigate with point-in-time membership but cannot recover the dead names. |
| S&P 500 point-in-time membership CSV | daily list of index members | 1996 → 2025-08 | Community-maintained; ticker spellings normalised (`BRK.B` → `BRK-B`). |
| CoinMetrics community CSV | BTC, ETH daily reference price and reported spot volume | 2010/2015 → 2026 | Close-only, so ATR falls back to close-to-close ranges and intraday stop fills are approximated at the close. |
| CBOE VIX daily CSV | VIX close | 1990 → | Used only by the regime filter's YELLOW state. |
| US market 1885+ (SPY-like total-return proxy) | monthly-then-daily index proxy | 1885 → 2025 | Crude; only for long-horizon regime sanity checks, never for returns. |

`scripts/bootstrap_data.sh` fetches all of these (the history only with
`--history`) and seeds the parquet cache; the GitHub Actions workflows call it
on a cold cache:

* Qlib US daily — `github.com/SunsetWolf/qlib_dataset/releases/download/v0/qlib_data_us_1d_latest.zip` (≈ 450 MB)
* S&P 500 point-in-time membership — `github.com/fja05680/sp500` (`S&P 500 Historical Components & Changes (08-01-2025).csv`), fallback `github.com/hanshof/sp500_constituents`
* Current constituents with company names (for anonymisation) — `github.com/datasets/s-and-p-500-companies`
* VIX — `github.com/datasets/finance-vix`
* BTC/ETH — `raw.githubusercontent.com/coinmetrics/data/master/csv/{btc,eth}.csv`

## Network sources (paper/live)

| Need | Source | Notes |
|---|---|---|
| US stock/ETF bars | Alpaca Data (IEX live, SIP for history), yfinance fallback | yfinance is rate-limited and unofficial; cache everything. |
| Crypto bars, funding, meta | Hyperliquid info API (`candleSnapshot` ≤ 5,000 candles) | Hyperliquid's terms classify US persons as restricted; the operator must set `HL_OPERATOR_NOT_RESTRICTED_PERSON=true` to enable it. |
| Fundamentals/earnings | SEC EDGAR companyfacts (descriptive User-Agent required), Finnhub free tier, yfinance calendar | Earnings dates feed the blackout rule. |
| News | Alpaca News (Benzinga), Finnhub company news, Google News RSS | Reaches only the quarantined reader. |
| X/social sentiment | Optional, batched Grok `x_search` or X API pay-per-use | Off by default; cost is charged to the ledger when on. |

## Data quality guard (`hive.data.quality`)

Yahoo-derived histories carry corrupted prints around delistings and
acquisitions: `CBE` (Cooper Industries, acquired Nov-2012) printed a ×1,000
close on its last days, which turned one backtest trade into +105,794% and
produced a fictitious 29% CAGR in a first-pass experiment. The guard now
truncates every series at its first implausible row before any signal sees
it, in backtests and in the live cycle alike:

* a one-day move > 50% (75% for crypto) **that reverts** to within 30% of the
  pre-move price within 5 bars — a spike;
* such a move within the last 5 bars of a series — unverifiable, the
  delisting-day case;
* an OHLC row that is inconsistent or non-positive.

Genuine persistent moves (Bitcoin's +55% day in Nov-2010, a financial's
rebound in 2008) are kept. With this rule 35 of ~800 S&P 500 series are
truncated (several are real 2008/2020 extremes — a conservative loss) and no
ETF or crypto series is affected. The first-pass outputs on uncleaned data are
kept in `reports/experiments/contaminated-uncleaned-data/` for transparency.

## Point-in-time discipline

* `as_of(bars, t)` is the only sanctioned way to slice time; signals are
  computed from frames that end at *t* and tests assert that extending the
  frame never changes earlier rows.
* Decisions made on the close of *t* fill at the open of *t+1*.
* News and fundamentals carry a publish/filing timestamp and are filtered on
  it, not on the period they describe.
* LLM backtests are only credible after the model's training cutoff; the
  deterministic strategy is evaluated over the full history, the LLM filter
  only forward.

## Sandbox note

The build environment's egress policy blocked every market-data host
(Yahoo, Stooq, Alpaca, Hyperliquid, CoinGecko, SEC, Finnhub). The offline
datasets above are what could be fetched from GitHub-hosted mirrors; to refresh
to 2026 and to run paper trading, the hosts in `.env.example` must be allowed.
