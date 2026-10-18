# Evidence Review and Strategy Spec: "ROC reset above a rising 200DMA" trading bot ($100 public study)

*Prepared 2026-10-18.*

**Evidence tags:**
- **[V]** means the claim matched a paper abstract or primary listing in this session's searches.
- **[S]** means it comes from a secondary summary (blog, CXO, Alpha Architect, press).
- **[U]** means it is from background knowledge and was **not re-verified this session**. Check [U] items before publishing.

**Method limits:**
- Full-text fetches failed (DNS errors on most hosts), and the web-search budget ran out. Numbers therefore come from abstracts and summaries, not paper tables.
- Market-data hosts (Yahoo, Stooq, Alpaca, Hyperliquid) were blocked, so **I could not backtest the exact rule set**.
- Section 5 is my own Monte Carlo and closed-form math.

---

## 0. Bottom line

1. **No paper tests the full rule "ROC resets near 0 while price is above a rising 200DMA".** Each part of it is well supported, though:
   - Trend and momentum persist at 3–12 months.
   - The most recent month tends to reverse, which is why academic momentum skips it.
   - Being near the 52-week high helps.
   - "Quiet" (low-turnover) pullbacks revert, while high-turnover selloffs continue.

   The combination is plausible, but its edge over *random entry inside the same trend filter* is unproven. That null test is the first thing to run.
2. **Most of a trend system's edge is in the exit and the sizing, not the entry.** In stock trend following, fewer than 7% of trades produce most of the profit. A re-run with random entries and the same 10-ATR exit reached a Sharpe of 0.50, against 0.62 for the real system ([S], [Harbourfront](https://harbourfrontquant.substack.com/p/does-trend-following-work-on-single)). So: no profit caps, wide trailing stops, and small fixed fractional risk.
3. **Realistic results for good long-only trend and momentum systems are about 10–30% CAGR with 20–40% maximum drawdown**, and less after costs and decay. There is no evidence-based way to turn $100 into $1,000 quickly without a large chance of ruin. A fair game aimed at 10x before a −90% loss succeeds only about 9% of the time.
4. **The news and sentiment alpha that has been documented works at a 1-day horizon and is decaying.** For 3-month swing trades, use LLMs as a **veto and catalyst classifier**, not as the main signal.
5. **Memecoins have a negative expected value for almost everyone.** Allow them only as a capped "lottery" bucket (≤3% of equity), if at all.

---

## 1. Evidence by component

### Trend filters (200DMA and slope)

**Faber (2007/2013), 10-month SMA timing.**
- Across 5 asset classes from 1973–2012, the timed portfolio returned about 10.5% a year against 9.9% for buy-and-hold, with much lower volatility and drawdown ([S], [CXO](https://www.cxoadvisory.com/technical-trading/long-term-outperformance-from-trends-defined-by-moving-averages); [QuantConnect thread](https://www.quantconnect.com/forum/discussion/8285/a-quantitative-approach-to-tactical-asset-allocation-gtaa-5-with-simplemovingaverage/)).
- Signals are checked only at month-end, so drawdowns between checks still happen.
- The main benefit is **drawdown control, not extra return**.

**Zakamulin (155 years of data).**
- MA rules give many false signals and outperform mainly in bear markets ([S], [SSRN 2677212](https://papers.ssrn.com/abstract=2677212)).
- They show **no statistically significant outperformance after about 1932**, although Sharpe ratios are higher ([S], [CXO horse race](https://www.cxoadvisory.com/technical-trading/long-run-moving-average-horse-race-for-timing-the-u-s-stock-market)).
- The simple momentum rule did best out of sample.

**Slope of the MA.**
- I found **no head-to-head study** of "price above the MA" versus "rising MA".
- The logic: a slope filter reacts later but flips less often, and practitioners add a dead-band near zero ([S], [LuxAlgo](https://www.luxalgo.com/library/concept/ma-slope-filter.md)).
- Treat the slope test as a whipsaw filter that needs its own backtest.

### Momentum

**Time-series momentum.**
- Moskowitz, Ooi and Pedersen (2012) found that the 12-month excess return predicts the next month's return in **all 58 futures** they tested. The effect lasts about 12 months, then partly reverses ([V], [SSRN 2089463](https://papers.ssrn.com/abstract=2089463)).
- Hurst, Ooi and Pedersen extend this to 67 markets from 1880–2016. Positions are scaled to 10% volatility, and the Sharpe ratio averages about 0.4 after 2/20 fees ([S], [AQR](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing)).

**Cross-sectional momentum.**
- Jegadeesh and Titman (1993) show that past winners beat past losers over 3–12 month holds ([V], [RePEc](https://ideas.repec.org/a/bla/jfinan/v48y1993i1p65-91.html)). The spread is roughly 1% a month [U].
- Novy-Marx (2012) finds that the **12-to-7-month** return carries most of the effect ([V], [AA](https://alphaarchitect.com/when-academics-disagree-on-momentum-investing/)). Goyal and Wahal do not find this "echo" outside the US.
- **Implication:** a stock with a strong 12–7 month return and a flat last 1–3 months is still a momentum winner. That supports the "reset" idea.

**52-week-high momentum (George & Hwang 2004).**
- Nearness to the 52-week high forecasts returns better than past returns do, and the effect does not reverse ([V], [abstract](https://academicnewsletter.sufe.edu.cn/info/361244)).
- Replications find it is profitable in 17 of 18 markets and largely independent of Jegadeesh–Titman momentum ([S]).
- **Implication:** keep pullbacks shallow, within about 25% of the 52-week high.

**Momentum quality.**
- Residual momentum (Blitz, Huij & Martens 2011) has a gross Sharpe of 0.90 against 0.45 for raw momentum ([S], [CXO](https://www.cxoadvisory.com/momentum-investing/idiosyncratic-pure-or-residual-momentum-as-a-stock-return-predictor)).
- "Frog in the pan": momentum is 5.94% for stocks with continuous information versus −2.07% for stocks with discrete information ([V], [Da, Gurun & Warachka](https://www3.nd.edu/~zda/Frog.pdf)).
- Consistency: winners with a high share of up-months earn more (Grinblatt & Moskowitz 2004) ([V], [RePEc](https://ideas.repec.org/p/cdl/anderf/qt1k67p66s.html)).
- **Implication:** prefer smooth, steady trends over one-gap stories.

### Single-stock trend following

**Wilcox and Crittenden (2005).**
- Entry on an all-time-high close; exit on a 10×ATR trailing stop.
- 17% of trades gained 50% or more ([S], [paper PDF](https://static.twentyoverten.com/593e8a9e7299b471eaecf644/HyWAN8TXM/Does-Trend-Following-Work-on-Stocks.pdf)).

**Zarattini, Pagani and Wilcox (2025).**
- 66,000+ trades from 1950–2024, with exit at 10×ATR(42).
- About **15% theoretical CAGR, about 6% alpha gross of fees, and about 32% maximum drawdown**. Profitability fell slightly after publication ([S], [Concretum](https://concretumgroup.com/does-trend-following-still-work-on-stocks/)).

### Pullbacks inside an uptrend

**Short-term reversal.**
- One-month returns reverse (Jegadeesh 1990) [U].
- Medhat and Schmeling find that **low-turnover** stocks reverse (about −1.41% a month), while **high-turnover** stocks *continue* ([S], [CEPR DP15857](https://repec.cepr.org/repec/cpr/ceprdp/DP15857.pdf)).
- **Implication:** require a volume dry-up during the pullback, and avoid pullbacks that happen on heavy volume.

**Connors RSI(2) above the 200DMA.**
- On the S&P 500 from 2016–2026: 71% win rate, average win 1.22%, average loss 2.10%. That works out to about 1.7% a year with low market exposure ([S], [Backtrex](https://backtrex.com/en/backtests/connors-rsi-2-sp-500)).
- The edge is widely believed to have decayed since the method was popularised [U].
- Lesson: "dip in trend" entries give high win rates but thin per-trade edge.

**Minervini Trend Template and VCP.**
- I found **no peer-reviewed or independent out-of-sample test**. What exists are community scripts ([edgerater](https://edgerater.com/tradingtemplates/georgelea_markminervini)).
- Its parts (price above a rising 150/200DMA, 50DMA above 200DMA, near the 52-week high) overlap with the trend and 52-week-high evidence above. The VCP pattern itself is unvalidated.

### Momentum crashes and volatility management

**Daniel and Moskowitz (2016).**
- Crashes happen in "panic" states: after market declines, when volatility is high, and during sharp rebounds.
- In July–August 1932 losers rose about 232% versus 32% for winners. In March–May 2009 losers rose about 163% versus 8% ([V], [JFE PDF](https://www.kentdaniel.net/papers/published/jfe_16.pdf)).
- Dynamic scaling roughly doubles the Sharpe ratio ([V], [NBER w20439](https://www.nber.org/papers/w20439)).

**Barroso and Santa-Clara (2015).**
- Scaling by 126-day realised volatility "nearly doubles" the Sharpe ratio and virtually eliminates crashes ([V], [UCP](https://ciencia.ucp.pt/en/publications/momentum-has-its-moments/)).

**Caveat on volatility management.**
- Cederburg et al. tested 103 strategies. Generic volatility-managed portfolios **do not beat** unmanaged ones out of sample ([S], [Alpha Architect](https://alphaarchitect.com/does-portfolio-timing-based-on-volatility-signals-outperform-buy-and-hold/)).
- DeMiguel et al. (2024, JF) partly rescue the idea with a multifactor approach ([V], [LBS](https://lbsresearch.london.edu/id/eprint/3716)).
- **Use volatility scaling mainly for momentum and crash control**, not as an alpha source.

### What this means for "ROC reset"

The setup is a three-speed decomposition:

| Speed | Measure | What it captures |
|---|---|---|
| Long-term trend | 200DMA rising; 12-1 return > 0 | TSMOM and cross-sectional momentum |
| Swing impulse | 63-day ROC was high | Recent strong move |
| Pullback | 21-day ROC near 0 | The 1-month reversal component |
| Trigger | 5-day ROC turns up | Re-entry timing |

Risks to manage:
1. The "reset" may be the start of trend exhaustion. Momentum reverses after about 12 months, so require 12-1 momentum still positive and the 52-week high close by.
2. Heavy-volume selloffs continue rather than revert.
3. A market panic or rebound (Daniel–Moskowitz) can crash the whole book at once.

---

## 2. Fundamental overlays

**PEAD / SUE.**
- Martineau (2022): for large caps, PEAD has been non-existent since about 2006, and it has recently faded for microcaps too ([V], [CFR PDF](https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf)).
- Two 2025 papers dispute this. Subrahmanyam attributes the disagreement to research design ([S], [UCLA Anderson](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/)).
- **Use:** a weak tie-breaker only, such as a post-earnings gap up with a beat on EPS and revenue. Announcement-return drift may be stronger than SUE drift [U].

**Estimate revisions.**
- Chan, Jegadeesh and Lakonishok (1996): past returns and past earnings surprises, including analyst revisions, each predict drift. Analysts revise sluggishly ([V], [NBER w5375](https://www.nber.org/papers/w5375)).
- Novy-Marx (2015) argues price momentum is largely explained by earnings momentum ([S]).
- **Use:** require no net EPS downgrades in the last 60 days, and rank-boost the top revision quintile.

**Holding through earnings.**
- The earnings-announcement premium is over 7% a year in announcement months, 1972–2004 ([V], [Frazzini & Lamont](https://www.nber.org/papers/w13090)).
- Past winners earn more around announcements during the first months after formation ([S], [Jegadeesh slides](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2022/09/Jegadeesh.pdf)).
- **Avoiding earnings altogether gives up part of the edge.** But a 3–6 position book cannot absorb a −20% gap well.
- **Compromise:** no *new* entry within 5 trading days before a report. Hold existing positions through it if open profit is at least 1R; otherwise cut to half size.

**Quality.**
- Gross profitability (Novy-Marx 2013) and QMJ (Asness, Frazzini & Pedersen 2019) earn significant risk-adjusted returns in the US and in 24 countries ([V], [RePEc](https://ideas.repec.org/a/spr/reaccs/v24y2019i1d10.1007_s11142-018-9470-2.html)).
- **Use:** as a junk filter (exclude negative cash flow combined with heavy dilution), not as a timing signal.

---

## 3. News and sentiment

**Lopez-Lira and Tang ([arXiv 2304.07619](https://arxiv.org/abs/2304.07619)), latest version Oct 2025** ([V]).
- GPT-4 headline scores for Oct 2021–May 2024 (after the model's training cutoff) give about **34 bp a day long-short before costs**.
- The roughly 90% hit rate applies to the *non-tradable initial reaction*.
- The drift that can be traded is concentrated in **small stocks and negative news**.
- Returns **decline as LLM adoption rises**.

**Biases in LLM backtests.**
- Glasserman and Lin find look-ahead bias and a "distraction" effect: anonymised headlines performed *better* in sample ([V], [arXiv 2309.17322](https://arxiv.org/abs/2309.17322)).
- **Anonymise tickers and company names in prompts, and backtest only on news dated after the model's cutoff.**

**Recent replications.**
- An LLM-conditioned S&P 500 momentum strategy raised the out-of-sample Sharpe from 0.79 to 1.06 (Jan 2024–Mar 2025, 2 bp costs) ([S], [arXiv 2510.26228](https://arxiv.org/html/2510.26228v1)).
- A leakage-aware audit finds that LLM-discovered factors do not clear significance once data-mining search is corrected for ([S], [arXiv 2608.27734](https://arxiv.org/pdf/2608.27734)).

**Social media.**
- Across Twitter, StockTwits and Seeking Alpha, **sentiment predicts positive next-day returns, but attention predicts negative next-day returns** ([V], [Cookson, Lu, Mullins & Niessner, JFE 2024](https://ideas.repec.org/a/eee/jfinec/v158y2024ics0304405x2400093x.html)).
- Beliefs formed in StockTwits echo chambers are followed by lower returns ([V], [RFS 2023](https://ideas.repec.org/a/oup/rfinst/v36y2023i2p450-500..html)).
- Robinhood herding events are followed by **−3% (−6% for extreme cases) 5-day abnormal returns** ([V], [Barber, Huang, Odean & Schwarz](https://www.smallake.kr/wp-content/uploads/2020/12/SSRN-id3715077.pdf)).
- StockTwits evidence at the daily horizon is mixed ([S], [LSE](https://blogs.lse.ac.uk/businessreview/?p=22489)).

**Use in the bot:**
1. **Veto** entries on material negative events: guidance cut, share offering, investigation, accounting problems, FDA rejection letter, being a fixed-price M&A target.
2. **Veto** entries on crowding: mention count more than 3× the 30-day average together with a 1-day jump of more than 2 ATR.
3. **Classify catalysts:** prefer steady, continuous news flow over a single discrete shock (frog-in-the-pan).
4. Use positive sentiment **only as a tie-breaker**.

Never let a 1-day sentiment signal open a 3-month trade.

---

## 4. Crypto

**Time-series momentum.**
- In crypto, weekly momentum is strong, especially in BTC. A one-standard-deviation weekly return predicts about +3.16% the following week. Attention proxies also forecast returns ([V], [Liu & Tsyvinski, NBER w24877](https://www.nber.org/papers/w24877); [VoxEU](https://cepr.org/voxeu/columns/risks-and-returns-cryptocurrencies)).
- Market, size and momentum factors explain the cross-section of coin returns ([V], [Liu, Tsyvinski & Wu, JF 2022](https://papers.ssrn.com/abstract=3379131)).

**Trend following.**
- An ensemble of Donchian channels with volatility-based sizing, run on the top 20 liquid coins from 2015–2025 on survivorship-free data: **Sharpe above 1.5 net of fees, 10.8% annual alpha versus BTC** ([V], [Zarattini, Pagani & Barbon, SSRN 5209907](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907)).
- A 20-day MA rule on 11 coins (2016–2018, excluding BTC) earned 8.76% a year in excess return ([V], [Grobys et al.](https://osuva.uwasa.fi/bitstream/10024/11193/2/Osuva_Ahmed_Grobys_Sapkota_2020.pdf)).
- On BTC, a 200DMA filter mainly **cuts drawdowns** (buy-and-hold has had about −83%) rather than raising CAGR ([S], [Cestrian](https://www.cestriancapitalresearch.com/i-tried-to-debunk-the-200-day-moving-average-here-is-the-twist/); [SystematicStrategies](https://systematicstrategies.substack.com/p/can-you-keep-bitcoins-upside-without)).
- Crypto trades 365 days a year, so a 200-day crypto MA covers about 0.55 years, versus about 0.8 years for a 200-session equity MA.

**Funding and perps.**
- Perp–spot deviations are large, move together across coins, and shrink as markets mature. Carry and arbitrage show high Sharpe ratios even after fees ([V], [He, Manela, Ross & von Wachter](https://arxiv.org/abs/2212.06888v6)).
- As a *directional* signal, funding showed **no significant predictive power** for next-8-hour BTC returns ([S], [BitMEX](https://www.bitmex.com/blog/xbtusd-funding-mean-reversion-strategy)).
- **Treat funding as a cost and crowding filter.** At 40% APR, a 90-day long pays about 10% in funding.

**Hyperliquid realities.**
- A dashboard snapshot showed only about 16.6% of 1,000 sampled traders were profitable. This is weak evidence ([S], [Coin Republic](https://www.thecoinrepublic.com/2025/06/16/hyperliquid-crypto-only-166-of-1000-traders-profitable-whats-going-on/)).
- During the Oct 10, 2025 cascade, auto-deleveraging closed **$2.1B of positions in 12 minutes**, and profitable traders were haircut by an estimated $45–52M more than necessary ([V], [arXiv 2512.01112](https://arxiv.org/abs/2512.01112)).
- Margin rules were changed after a $4M loss to the HLP pool ([S], [Cointelegraph](https://cointelegraph.com/news/hyperliquid-ups-margin-rules-4m-liquidation-loss)).
- **Venue and auto-deleveraging risk come on top of market risk.**

**Memecoins.**
- **Graduation rates are about 0.6–1.4%** (under 2%) ([V], [arXiv 2602.14860](https://arxiv.org/abs/2602.14860), [arXiv 2512.11850](https://arxiv.org/abs/2512.11850), [Solana Compass](https://solanacompass.com/news/pumpfun-launched-42000-tokens-in-one-day-fewer-than-2-will-ever-reach-a-dex)).
- Solidus Labs flagged **98.6% of Pump.fun tokens** for rug-pull or pump-and-dump signs. Only about 97,000 of 7 million tokens kept more than $1,000 of liquidity ([S], [The Defiant](https://thedefiant.io/news/defi/alarming-99-of-memecoin-launches-on-pumpfun-are-pump-and-dumps-or-rug-pulls-report)).
- Only **0.4% of wallets realised more than $10,000** (Jan 2025) ([S], [Decrypt](https://decrypt.co/300403/pump-fun-traders-millionaires)).
- Across 2.9 million buyers, **74% lost money**, the median buyer lost $13, and 1% of buyers took more than half the gains ([S], [Bitquery](https://bitquery.io/investigations/solana-memecoin-traders-lose-money)).
- Only **6.25%** of 304,000 Solana DEX traders were profitable over 90 days ([S], [Cointribune](https://www.cointribune.com/en/crypto-solana-memecoins-trap-nearly-94-of-traders)).
- The median memecoin peaks about 17 days after launch, then falls about 95% ([S], [Coin Metrics](https://coinmetrics.substack.com/p/state-of-the-network-issue-384)).
- On the LIBRA token, 86% of traders lost a combined $251M ([S], [The Block](https://www.theblock.co/post/342266/traders-of-solana-based-libra-memecoin-lost-251-million-nansen)).
- In short, the profits go to launchpads, bots and insiders.

---

## 5. Small-account math and risk of ruin (my own calculations)

**Kelly drawdowns.**
- With a known edge under geometric Brownian motion, the chance of *ever* falling to fraction x of starting equity at Kelly fraction c is x^(2/c−1).
- Full Kelly: P(−50%) = 50%. Half Kelly: 12.5% (and 0.8% for −80%), while keeping 75% of the growth rate. Quarter Kelly: P(−50%) = 0.8%, with 44% of the growth rate.
- In practice the edge is *estimated*, and momentum trades are *correlated*, so true Kelly is much lower than the per-trade number.

**Per-trade risk Monte Carlo.** Setup: 100 independent trades, 38% win rate, right-skewed winners, about +0.36R expectancy. Kelly is about 8.5% of equity at risk per trade.

| Risk per trade | Median end equity | P(max drawdown > 50%) | P(end < start) | If true edge is about 0: P(max drawdown > 50%) |
|---|---|---|---|---|
| 1% | 1.37× | 0% | 7.7% | 0% |
| 2% | 1.79× | 0.8% | 9.3% | 6.8% |
| 5% | 3.02× | 51% | 14.5% | 80.5% |
| 10% | 3.52× | 99% | 26% | 99.8% (and 46% chance of ending below 0.2×) |

→ **Use 1% risk per trade** (about 1/8 Kelly, because the edge is unknown). Move to 1.5–2% only after at least 50 live trades whose expectancy confidence interval is above zero.

**Leverage on a BTC-like asset.** Setup: fat-tailed daily returns (t-distribution, df=3), 3.2% daily volatility (about 61% annualised), held without stops. Results are the probability of liquidation:

| Leverage | 63 days, 0% drift | 63 days, +40%/yr drift | 252 days, 0% drift | 252 days, +40%/yr drift |
|---|---|---|---|---|
| 2× | 0.6% | 0.6% | 7% | 4% |
| 3× | 2.8% | 2.3% | 33% | 19% |
| 5× | 19% | 15% | 75% | 60% |
| 10× | 72% | 65% | 99% | 98% |
| 20× | 98% | 97% | 100% | 100% |

Intraday wicks make these numbers *worse*.

**Volatility drag.** Under GBM, growth = Lμ − L²σ²/2. With μ = 30% and σ = 60%: 1× gives +12% a year, **2× gives −12%, and 3× gives −72%**.

**"Fast start" reality.**
- In a fair game, P(reach 10× before −90%) ≈ 9.1% (optional stopping theorem).
- Fees, funding and slippage make the odds worse.
- **No method in the literature reliably compounds very quickly without a matching risk of ruin.**

The defensible fast start:
1. Get invested quickly through **ETFs that pass the trend template**: diversified, with low gap risk.
2. Run the 1% risk core.
3. Fence off a ≤15% aggressive sleeve that is allowed to go to zero.
4. For a meaningful study, consider fixed monthly contributions and report **time-weighted returns**.

**Mechanics [U, verify]:**
- Fractional-share brokers (for example Alpaca) have a minimum of about $1 per order. Hyperliquid's minimum order value is about $10.
- With $100, that allows about 4–6 equity positions and 1–3 crypto positions.
- Use a **cash account** to avoid PDT rules. The status of FINRA's PDT reform is unverified. Watch for good-faith violations under T+1 settlement.

---

## 6. Regime filters

- **SPY above its 200DMA:** Faber and Zakamulin, as above. It reduces drawdowns and adds little return.
- **Market states:** momentum earns **0.93% a month after UP markets and −0.37% after DOWN markets**, where the state is the 36-month market return, 1929–1995 ([V], [Cooper, Gutierrez & Hameed 2004](https://academicnewsletter.sufe.edu.cn/info/361185)). UK and Spanish replications are mixed.
- **Panic states** (after a decline and with high volatility) are when momentum crashes (Daniel–Moskowitz).
- **VIX:** I found no robust stand-alone evidence this session. Use it only as a sizing input, consistent with the volatility-management literature and its out-of-sample caveats.
- **BTC above its 200DMA as the altcoin gate:** consistent with crypto TSMOM and the market factor in Liu–Tsyvinski–Wu. The specific gate is untested [U].

---

## 7. Recommended exact strategy spec

### Common definitions
All computed at the daily close: 16:00 ET for stocks, 00:00 UTC for crypto.

- `r_t = ln(C_t / C_{t-1})`.
- `σ_d` = standard deviation of `r` over 63 bars.
- `ROC_N = ln(C_t / C_{t-N})`.
- **`zROC_N = ROC_N / (σ_d·√N)`**. This volatility-normalises ROC so one threshold works for both stocks and coins.
- `ATR_n` = Wilder ATR. `SMA` and `EMA` are standard.
- `R` = entry price − initial stop.
- Signals fire at the close; orders go at the next open as a limit at ≤ open + 0.5·ATR. Skip the trade if it is not filled that day.

### (a) US stocks and ETFs (core sleeve, 60–100% of equity)

**Universe**
- US common stocks with market cap ≥ $2B, price ≥ $5, 50-day median dollar volume ≥ $25M, and at least 300 bars of history.
- Plus about 30 liquid unleveraged ETFs: sector, industry, country, gold, Treasuries.
- Exclude leveraged and inverse ETFs.
- Refresh the universe weekly. Use survivorship-bias-free data for backtests.

**Trend template (all required)**
1. `C > SMA200`.
2. `SMA200_t > SMA200_{t-21}` **and** `SMA200_t > SMA200_{t-63}`.
3. `SMA50 > SMA200`.
4. `C ≥ 0.75·max(High, 252)`.
5. 12-1 momentum `C_{t-21}/C_{t-252} − 1 > 0` **and** in the top 30% of the universe on volatility-adjusted 12-1 momentum (`MOM12-1 / σ_252`).

**Setup: "was higher, then reset"**
- **S1, was higher:** `max(zROC_63 over t-63…t-10) ≥ 1.5`. That is roughly +20–25% in 3 months for a 30%-volatility stock.
- **S2, reset:** `−1.0 ≤ zROC_21 ≤ +0.3`. That is roughly −9% to +3% over the last month.
- **S3, depth:** drawdown from the 63-day closing high between 2·ATR20 and 6·ATR20 (about 5–15%).
- **S4, quiet pullback:** `SMA(Vol,10)/SMA(Vol,50) ≤ 0.9`.

**Trigger** (valid for 10 bars after S1–S4 are first met)
- `C > max(High_{t-1}, High_{t-2})`, **and** `C > EMA10`, **and** `ROC_5 > 0`.

**Filters**
- **Earnings:** no new entry if a report is due within 5 trading days.
- **Revisions:** net FY1 EPS revisions over 60 days must be ≥ 0.
- **Quality:** gross profit/assets above the universe median **or** TTM operating cash flow > 0. Exclude stocks whose share count grew more than 10% year on year.
- **LLM news veto and attention-spike veto:** as in Section 3. Anonymised prompt, JSON output, headlines from the last 5 days plus 8-Ks.

**Ranking** (fills free slots)
- Score = 0.5·rank(volatility-adjusted 12-1 momentum) + 0.2·rank(closeness to 52-week high) + 0.2·rank(60-day revisions) + 0.1·rank(quality).
- Add one tier if the last report (within 60 days) beat on EPS and revenue with a 3-day reaction of more than +1·ATR.

**Sizing**
- Risk 1.0% of equity per trade.
- Initial stop = `max(lowest low of last 10 bars − 0.5·ATR20, entry − 3·ATR20)`.
- Shares = 0.01·Equity / R.
- Position cap: 20% of equity for a single stock, 30% for an ETF.
- At most 6 positions, at most 2 per sector, and total open risk ("heat") ≤ 6%.
- If the portfolio's 20-day realised volatility exceeds 20%, scale new risk by 20% / realised volatility.

**Exits** (first one hit)
1. **Chandelier stop:** `stop = max(prev stop, highest close since entry − 4·ATR20)`. Tighten to 3·ATR after day 63.
2. **Trend break:** 2 consecutive closes below SMA50, or 1 close below SMA200.
3. **Failure time stop:** on day 15, exit if `C < entry + 0.5R`.
4. **Review at day 63:** keep the position only if the trend template still passes. Hard maximum of 126 days.
5. **Material negative news** flagged by the LLM: exit at the next open.
6. **Earnings:** if open profit is below 1R, cut to half size before the report.

There is **no fixed profit target**.

**Regime (SPY)**

| State | Condition | Action |
|---|---|---|
| Green | `C > SMA200` and SMA200 rising over 21 days | Normal |
| Yellow | Above SMA200 but SMA200 falling, **or** VIX > 30 | Risk 0.5% per trade, at most 3 new entries a week |
| Red | `C < SMA200` | No new stock longs (ETFs may still enter if they pass their own template); tighten all trailing stops to 3·ATR |
| Crash guard | SPY 24-month return < 0 **and** 21-day realised volatility > 30% annualised, **or** 36-month return < 0 | No new momentum entries |

**Cadence**
- Signals and stops: daily.
- Universe and rank refresh: weekly.
- Fundamentals: after each report.
- Performance review: every 25 trades.

### (b) Crypto (0–40% of equity)

**Universe**
- BTC, ETH, plus the top 15 coins by 30-day median USD volume (≥ $50M) that have been listed for at least 365 days.
- Exclude stablecoins, wrapped assets and liquid-staking tokens. Exclude memecoins from the core.

**Venue**
- Prefer spot. Hyperliquid perps are acceptable at ≤ 1.5× effective leverage with isolated margin and an exchange-side stop.

**Gates**
- **BTC gate** (required for any altcoin entry): BTC `C > SMA200` and `SMA200_t > SMA200_{t-30}`.
- **Per coin:** `C > SMA200`, rising SMA200 over 30 days, `C ≥ 0.6·max(High, 365)`, and in the top 50% on 90-day volatility-adjusted momentum.

**Setup**
- **S1:** `max(zROC_90 over t-90…t-7) ≥ 1.5`.
- **S2:** `−1.0 ≤ zROC_30 ≤ +0.3`.
- **S3:** drawdown from the 90-day closing high between 2·ATR14 and 7·ATR14 (about 10–35%).
- **S4:** 7-day volume / 30-day volume ≤ 0.9.

**Trigger**
- `C > max(High_{t-1}, High_{t-2})`, **and** `C > EMA10`, **and** `ROC_3 > 0`.

**Filters**
- Skip if annualised funding is above 30%.
- Skip if open interest rose more than 25% in 7 days while price fell [heuristic, untested].
- LLM veto on hacks, unlocks of more than 2% of supply within 14 days, and regulatory actions.

**Sizing**
- Risk 1% of total equity per trade.
- Initial stop = `max(10-day swing low − 0.5·ATR14, entry − 3·ATR14)`.
- Position cap: 30% of equity for BTC or ETH, 15% for altcoins. Crypto sleeve cap: 40% of equity. At most 3 positions.

**Exits**
- Chandelier stop at 4·ATR14 from the highest close.
- 2 consecutive closes below SMA50.
- Day-20 failure stop if `C < entry + 0.5R`.
- Review at day 90.
- If the BTC gate turns off, exit altcoins at the next close.

**Optional complement**
- A Donchian-ensemble trend sleeve in the style of SSRN 5209907: equal-weight breakout signals at 20, 55, 100 and 200 days, about 25% volatility target. These parameters are mine, not the paper's.

### (c) Aggressive sleeve (optional, hard caps)

**Caps**
- At most **15% of total equity**. It is never refilled from the core more than once per quarter.
- If the sleeve falls 50% from its peak, it is frozen for 30 days.

**A1: Leveraged trend on BTC, ETH and SOL perps (Hyperliquid)**
- Same setup and trigger as the crypto spec.
- Effective leverage ≤ 3×, isolated margin, exchange-side stop placed immediately.
- Risk ≤ 2% of *total* equity per trade.
- The liquidation price must sit at least 2× the stop distance beyond the stop.
- Funding below 25% APR. At most 2 positions.
- Leverage is used to *free up margin*, not to increase risk.

**A2: Memecoin lottery**
- ≤ 1% of equity per token, ≤ 3% in total, at most 3 open.

Entry conditions, all required:
- Graduated at least 72 hours ago. **Never buy on the bonding curve.**
- Pool liquidity ≥ $250k and 24-hour volume ≥ $1M.
- Mint and freeze authority revoked, and LP burned or locked.
- Top-10 holders (excluding LP and exchanges) ≤ 25%, and dev wallet ≤ 5%.
- A simulated sell succeeds (no honeypot).
- Price has retraced 30–60% and then reclaimed the 4-hour EMA20.

Exits:
- Sell 50% at +100%.
- Trail the rest 35% below its peak.
- Hard stop at −40%, which will often slip.
- 72-hour time stop. Maximum slippage 3%.

**Base case: expect this bucket to go to zero.** Log it as research data.

### Validation before going live (required)
1. Walk-forward backtest with costs: stocks 2005–2026 on survivorship-free data, crypto 2017–2026.
2. Robustness grid:
   - "Was higher" lookback 42, 63 or 90 bars.
   - Reset lookback 15, 21 or 30 bars.
   - S1 threshold 1.0, 1.5 or 2.0.
   - ATR multiple 3, 4 or 5.
3. **Null test:** the same template, sizing and exits with *random* entry dates. The ROC-reset entry has to beat this to justify itself.
4. Paper-trade for 4–8 weeks.
5. Publish every decision log, including LLM vetoes, so the study stays honest.

---
*Not investment advice. Items marked [S] and [U] need checking against the original papers before public citation.*
