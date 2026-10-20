"""Runtime settings (secrets, paths, modes) and the pre-registered strategy config.

Two kinds of configuration, kept deliberately separate:

* ``Settings``       — environment-driven: API keys, venue modes, directories.
                       Never committed. Loaded from ``.env`` / environment.
* ``StrategyConfig`` — the trading rules and risk limits. Committed to git as
                       ``config/strategy.toml`` so that every parameter is
                       pre-registered and auditable for the study.

The rule parameters follow docs/STRATEGY.md ("ROC reset above a rising 200DMA"),
which in turn cites the evidence in docs/RESEARCH.md.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

Mode = Literal["backtest", "paper", "live"]
AssetClass = Literal["stock", "etf", "crypto"]
LIVE_CONFIRMATION_PHRASE = "I_UNDERSTAND_THIS_TRADES_REAL_MONEY"


class Settings(BaseSettings):
    """Environment-driven settings. Secrets are ``SecretStr`` so they never log."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- run mode -----------------------------------------------------------
    hive_mode: Mode = "paper"
    # Live trading requires this exact phrase in the environment, on purpose.
    hive_live_confirm: str = ""
    hive_run_id: str | None = None

    # --- directories --------------------------------------------------------
    hive_data_dir: Path = REPO_ROOT / "data"
    hive_state_dir: Path = REPO_ROOT / "state"
    hive_reports_dir: Path = REPO_ROOT / "reports"
    hive_config_dir: Path = REPO_ROOT / "config"
    # If this file exists, every run exits before placing any order.
    hive_kill_switch_file: Path = REPO_ROOT / "state" / "KILL"

    # --- LLM ----------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    # Decision-making roles (portfolio manager, risk review) use this model.
    hive_llm_model: str = "claude-opus-5-5"
    # Bulk/quarantined roles (news summarisation, triage) use this model.
    hive_llm_cheap_model: str = "claude-haiku-5-5"
    hive_llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    hive_llm_enabled: bool = True
    # Fetch headlines / earnings dates over the network during a run (paper/live).
    hive_network_data: bool = True
    # Keyless social feeds (StockTwits, Reddit) for the quarantined reader.
    hive_social_data: bool = True

    # --- Alpaca (US stocks/ETFs; paper by default) --------------------------
    alpaca_api_key: SecretStr | None = None
    alpaca_secret_key: SecretStr | None = None
    alpaca_paper: bool = True

    # --- Hyperliquid (crypto perps; testnet by default) ---------------------
    # Use an *agent/API wallet* key, which cannot withdraw funds.
    hl_agent_private_key: SecretStr | None = None
    hl_account_address: str | None = None
    hl_testnet: bool = True
    # Hyperliquid's terms bar US persons; the operator must assert eligibility.
    hl_operator_not_restricted_person: bool = False

    # --- optional data providers -------------------------------------------
    finnhub_api_key: SecretStr | None = None
    # X (Twitter) is paid: with a bearer token the social pulse adds X post counts.
    x_bearer_token: SecretStr | None = None
    # SEC EDGAR requires a descriptive User-Agent with contact info.
    sec_user_agent: str = "hive-research-bot contact@example.com"

    @field_validator("hive_data_dir", "hive_state_dir", "hive_reports_dir", "hive_config_dir")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    # --- derived -------------------------------------------------------------
    @property
    def is_live(self) -> bool:
        return self.hive_mode == "live"

    def assert_live_allowed(self) -> None:
        """Refuse to run live unless the operator typed the confirmation phrase."""
        if self.is_live and self.hive_live_confirm != LIVE_CONFIRMATION_PHRASE:
            raise RuntimeError(
                "HIVE_MODE=live but HIVE_LIVE_CONFIRM does not equal the confirmation "
                f"phrase {LIVE_CONFIRMATION_PHRASE!r}. Refusing to trade real money."
            )

    def kill_switch_engaged(self) -> bool:
        return self.hive_kill_switch_file.exists()


# ---------------------------------------------------------------------------
# Strategy configuration (pre-registered, committed)
# ---------------------------------------------------------------------------


class TrendRules(BaseModel):
    """"ROC reset above a rising 200DMA" — one rule set per asset class.

    Three speeds: long-term trend template, a recent strong impulse, a quiet
    pullback that has decayed the short ROC to ~0, then a breakout trigger.
    All ROC measures are volatility-normalised (``zROC = ln-return / (sigma_d*sqrt(N))``)
    so one threshold works across stocks and coins.
    """

    bars_per_year: int = 252

    # --- trend template (all required) ---------------------------------------
    ma_long: int = 200
    ma_mid: int = 50
    ma_slope_lookbacks: list[int] = Field(default_factory=lambda: [21, 63])
    high_lookback: int = 252
    min_pct_of_high: float = 0.75
    mom_period: int = 252
    mom_skip: int = 21
    require_long_momentum: bool = True

    # --- setup: "was higher, then reset" -------------------------------------
    sigma_period: int = 63
    impulse_period: int = 63
    impulse_window: int = 63
    impulse_skip: int = 10
    impulse_min_z: float = 1.5
    reset_period: int = 21
    reset_z_min: float = -1.0
    reset_z_max: float = 0.3
    atr_period: int = 20
    pullback_high_lookback: int = 63
    pullback_min_atr: float = 2.0
    pullback_max_atr: float = 6.0
    volume_short: int = 10
    volume_long: int = 50
    volume_max_ratio: float = 0.9
    require_volume: bool = True

    # --- trigger --------------------------------------------------------------
    setup_valid_bars: int = 10
    trigger_ema: int = 10
    trigger_roc: int = 5

    # --- entry family (a sub-agent may run any of these; limits are unchanged) --
    #   reset_breakout     the pre-registered rule: impulse, reset, breakout trigger
    #   template_cross     enter the first bar the trend template is satisfied
    #   high_breakout      close above the prior breakout_lookback-bar high, inside the template
    #   ma_pullback        touched the mid MA within pullback_touch_bars, closes back above it
    #   momentum_threshold vol-adjusted 12-1 momentum >= momentum_min with the breakout trigger
    entry_mode: Literal["reset_breakout", "template_cross", "high_breakout", "ma_pullback",
                        "momentum_threshold"] = "reset_breakout"
    breakout_lookback: int = 55
    pullback_touch_bars: int = 5
    momentum_min: float = 0.5

    # --- stops & exits --------------------------------------------------------
    stop_swing_low_bars: int = 10
    stop_swing_low_atr_pad: float = 0.5
    stop_initial_atr: float = 3.0
    trail_atr: float = 5.0
    trail_atr_late: float = 3.0
    trail_tighten_after_bars: int = 63
    # v1.1: the SMA50 trend-break exit is disabled (a very large streak never
    # fires) because it whipsawed pullback entries in both walk-forward windows.
    exit_closes_below_mid: int = 1000
    exit_closes_below_long: int = 1
    failure_stop_bars: int = 15
    failure_stop_min_r: float = 0.5
    review_bars: int = 63
    max_hold_bars: int = 126
    earnings_blackout_bars: int = 5

    @classmethod
    def crypto(cls) -> TrendRules:
        return cls(
            bars_per_year=365, ma_slope_lookbacks=[30], high_lookback=365, min_pct_of_high=0.6,
            mom_period=180, mom_skip=7, impulse_period=90, impulse_window=90, impulse_skip=7,
            reset_period=30, atr_period=14, pullback_high_lookback=90, pullback_max_atr=7.0,
            volume_short=7, volume_long=30, trigger_roc=3, failure_stop_bars=20,
            review_bars=90, max_hold_bars=180, earnings_blackout_bars=0,
            # Crypto exits were not part of the walk-forward; keep the original spec.
            trail_atr=4.0, exit_closes_below_mid=2,
        )


class RegimeRules(BaseModel):
    """Benchmark regime: GREEN (normal), YELLOW (half risk), RED (no new longs)."""

    ma_long: int = 200
    slope_lookback: int = 21
    yellow_risk_multiplier: float = 0.5
    vix_yellow_threshold: float | None = 30.0
    # Crash guard (Daniel-Moskowitz "panic" state): no new momentum entries.
    crash_long_return_bars: int = 504
    crash_vol_bars: int = 21
    crash_vol_ann_threshold: float = 0.30
    crash_very_long_return_bars: int = 756


class RiskLimits(BaseModel):
    """Hard limits enforced in code. The LLM layer can only make these tighter."""

    max_positions: int = 6
    max_new_entries_per_day: int = 2
    max_position_pct_stock: float = 0.20
    max_position_pct_etf: float = 0.30
    max_position_pct_crypto: float = 0.30
    # Large-cap memecoins (universe.crypto_alt_symbols) get a smaller cap.
    max_position_pct_crypto_alt: float = 0.10
    max_gross_exposure: float = 1.0
    max_leverage: float = 1.0
    risk_per_trade_pct: float = 0.01
    # How a book picks its risk per trade. "fixed" risks risk_per_trade_pct on every
    # trade. "edge" lets the book's own closed trades decide: a fraction of the Kelly
    # bet implied by its last edge_lookback_trades results (in R), between
    # edge_min_risk_pct and max_risk_per_trade_pct; risk_per_trade_pct until it has
    # edge_min_trades. Both are levers; the lab's evidence picks.
    sizing_mode: Literal["fixed", "edge"] = "fixed"
    edge_fraction: float = 0.25
    edge_min_trades: int = 20
    edge_lookback_trades: int = 50
    edge_min_risk_pct: float = 0.0025
    max_risk_per_trade_pct: float = 0.05
    max_open_risk_pct: float = 0.06
    target_annual_vol: float = 0.20
    portfolio_vol_lookback: int = 20
    max_daily_loss_pct: float = 0.04
    max_drawdown_halt_pct: float = 0.25
    # After a drawdown halt: liquidate, wait this many bars, then resume with a
    # fresh drawdown window. Live, a halt also pages the operator.
    halt_cooldown_bars: int = 21
    min_order_notional_usd: float = 1.0
    # Crypto sleeve
    crypto_sleeve_pct: float = 0.40
    crypto_max_positions: int = 3
    crypto_max_leverage: float = 1.5
    # Aggressive sleeve (memecoins etc.) is off unless explicitly enabled.
    aggressive_sleeve_pct: float = 0.0

    def max_position_pct(self, asset_class: str) -> float:
        return {
            "stock": self.max_position_pct_stock,
            "etf": self.max_position_pct_etf,
            "crypto": self.max_position_pct_crypto,
        }.get(asset_class, self.max_position_pct_stock)


class Tier(BaseModel):
    name: str
    min_equity_usd: float
    max_children: int = 0
    min_net_positive_days: int = 0
    requires_human_approval: bool = False
    risk_multiplier: float = 1.0
    close_only: bool = False
    daily_ai_budget_pct: float = 0.0005


class SurvivalRules(BaseModel):
    """The bankroll is the AI's life. AI costs are paid from it.

    Death is decided by code at a floor *above* zero (fees and minimum order
    sizes make trading impossible below ~$20). Limits tighten as equity falls;
    there is no "Hail Mary" tier.
    """

    starting_bankroll_usd: float = 100.0
    death_floor_usd: float = 25.0
    death_drawdown_pct: float = 0.75
    daily_ai_budget_min_usd: float = 0.02
    daily_ai_budget_max_usd: float = 2.0
    tier_hysteresis_pct: float = 0.15
    # Paper sub-agents cost nothing but compute, so the tiers gate how many may run,
    # not whether; a live allocation to any of them is always a human decision.
    max_children: int = 8
    # A sub-agent earns promotion after this many shadow days ahead of the parent ...
    child_shadow_days: int = 10
    # ... and dies after child_retire_days behind it by child_retire_voi, or at once when its
    # own paper book is down child_max_drawdown_pct from where it started (config 1.5.0).
    child_retire_days: int = 10
    child_retire_voi: float = 0.0        # behind the parent at all after the period: retired
    child_max_drawdown_pct: float = 0.75  # the firm's own death line, nothing stricter
    tiers: list[Tier] = Field(
        default_factory=lambda: [
            Tier(name="hospice", min_equity_usd=0.0, close_only=True, risk_multiplier=0.0,
                 daily_ai_budget_pct=0.0002),
            Tier(name="survival", min_equity_usd=40.0, max_children=2, risk_multiplier=0.5,
                 daily_ai_budget_pct=0.0005),
            Tier(name="base", min_equity_usd=90.0, max_children=4, risk_multiplier=1.0,
                 daily_ai_budget_pct=0.0005),
            Tier(name="growth", min_equity_usd=250.0, max_children=6, min_net_positive_days=30,
                 risk_multiplier=1.0, daily_ai_budget_pct=0.0008),
            Tier(name="hive", min_equity_usd=1000.0, max_children=8, min_net_positive_days=60,
                 requires_human_approval=True, risk_multiplier=1.0, daily_ai_budget_pct=0.001),
        ]
    )


class OperatorRules(BaseModel):
    """The AI's bounded self-iteration loop (docs/ARCHITECTURE.md, "The operator")."""

    enabled: bool = True
    max_variants_per_iteration: int = 3
    insample_start: str = "2001-01-01"
    insample_end: str = "2012-12-31"
    outofsample_start: str = "2013-01-01"
    outofsample_end: str | None = None
    # A variant earns a shadow seat only if it beats the parent out of sample on
    # return without worsening the drawdown by more than this factor.
    max_drawdown_tolerance: float = 1.10
    # Governance by evidence, not by a person: a sub-agent that has beaten the parent
    # for the shadow period by at least promote_min_voi is promoted into the primary
    # by the operator itself; a baseline sub-agent running the pre-registered rules
    # keeps trading alongside, and if it beats the promoted configuration by
    # demote_min_lag over demote_after_days the promotion is reverted. The human
    # promote workflow remains as an override, never a requirement.
    # What the lab climbs: "prop" ranks and seats cells by their simulated prop-firm pass rate
    # out of sample (then growth); "growth" by out-of-sample CAGR.
    objective: Literal["prop", "growth"] = "prop"
    auto_promote: bool = True
    promote_min_voi: float = 0.0
    demote_after_days: int = 21
    demote_min_lag: float = 0.02
    bounds_file: str = "config/bounds.toml"
    # A news-channel monitor sub-agent expires after this many days unless re-proposed.
    monitor_ttl_days: int = 28


class MemeRules(BaseModel):
    """Memecoin scout sleeve: hard filters from docs/research/02 §7(c). Paper-only by default."""

    enabled: bool = False
    chain: str = "solana"
    min_age_hours: float = 72.0
    min_liquidity_usd: float = 250_000.0
    min_volume_24h_usd: float = 1_000_000.0
    max_24h_change_pct: float = -15.0   # has retraced
    min_6h_change_pct: float = 0.0      # ...and is turning back up
    min_1h_change_pct: float = 0.0
    require_rugcheck: bool = True
    per_token_pct: float = 0.01
    total_pct: float = 0.03
    max_open: int = 3
    hard_stop_pct: float = 0.40
    trail_pct: float = 0.35
    take_half_at_pct: float = 1.00
    time_stop_hours: float = 72.0
    slippage_pct: float = 0.03
    fee_pct: float = 0.01


class TrenchRules(BaseModel):
    """Trench scout: brand-new Solana launches, paper only, its own paper bankroll so its results
    read as percentages. Scans every lab round (every ten minutes or so while the lab runs);
    exits are software stops evaluated at the next scan, so they fill at the observed price."""

    chain: str = "solana"
    min_age_minutes: float = 20.0      # skip the first minutes (snipers, bundles)
    max_age_hours: float = 24.0        # older than a day is not a launch
    min_liquidity_usd: float = 15_000.0
    min_volume_1h_usd: float = 10_000.0
    min_buys_1h: int = 40
    min_buy_sell_ratio: float = 1.1    # more buyers than sellers in the last hour
    max_chg_1h_pct: float = 400.0      # do not buy the vertical candle
    min_chg_5m_pct: float = -10.0      # nor a token dumping right now
    require_rugcheck: bool = True
    rugged_liquidity_usd: float = 2_000.0   # liquidity this low on a held token: rugged, exit at zero
    vanish_after_misses: int = 3            # a held token without a quote for this many scans: exit at zero
    starting_cash_usd: float = 100.0
    per_token_pct: float = 0.05
    total_pct: float = 0.5
    max_open: int = 8
    hard_stop_pct: float = 0.30
    trail_pct: float = 0.25
    take_half_at_pct: float = 1.0
    time_stop_hours: float = 6.0
    slippage_pct: float = 0.05
    fee_pct: float = 0.01
    max_scan: int = 90
    max_rugchecks: int = 10


class PropRules(BaseModel):
    """A typical prop-firm evaluation (configurable; copy a specific firm's rules in before
    relying on them). The lab climbs the simulated pass rate; live paper records are read
    as evaluations for punishment and for readiness."""

    profit_target_pct: float = 0.10
    max_daily_loss_pct: float = 0.05      # from the previous close
    max_total_loss_pct: float = 0.10      # from the start, or from the peak when trailing
    trailing_total_loss: bool = False
    min_trading_days: int = 4
    eval_horizon_days: int = 60           # simulated evaluations end after this many days
    eval_step_days: int = 5               # a new simulated evaluation starts every this many days
    retire_on_breach: bool = True         # a variant whose paper record breaches is retired
    ready_min_forward_days: int = 30
    ready_min_backtest_pass_rate: float = 0.5


class PerpsDeskRules(BaseModel):
    """Hyperliquid paper desk: the firm's crypto rules on Hyperliquid's most traded perps, priced
    from its public data, on its own paper book. No account, no keys, no orders: a read-only
    simulation with Hyperliquid's taker fee and the hourly funding a long would have paid."""

    top_n: int = 25                    # the most traded perps by day volume
    min_history_days: int = 300        # the trend rules need about a year of daily bars
    starting_cash_usd: float = 100.0
    max_positions: int = 5
    taker_fee_pct: float = 0.00045
    slippage_bps: float = 5.0


class UniverseConfig(BaseModel):
    stock_universe: Literal["sp500", "etfs", "custom"] = "sp500"
    custom_symbols: list[str] = Field(default_factory=list)
    etf_symbols: list[str] = Field(
        default_factory=lambda: [
            "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "IEF", "XLK", "XLE", "XLF", "XLV",
            "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SMH", "XBI", "EEM", "EFA", "VNQ",
        ]
    )
    crypto_symbols: list[str] = Field(default_factory=lambda: ["BTC", "ETH", "SOL", "DOGE", "SHIB", "PEPE"])
    # Large-cap memecoins traded on the same spot venue under the same trend rules,
    # with the smaller position cap (risk.max_position_pct_crypto_alt).
    crypto_alt_symbols: list[str] = Field(default_factory=lambda: ["DOGE", "SHIB", "PEPE"])
    regime_stock_symbol: str = "SPY"
    regime_crypto_symbol: str = "BTC"
    min_price_usd: float = 5.0
    min_history_bars: int = 300
    max_candidates_for_llm: int = 8


class StrategyConfig(BaseModel):
    name: str = "trend-reset-v1"
    version: str = "2.2.0"
    stocks: TrendRules = Field(default_factory=TrendRules)
    crypto: TrendRules = Field(default_factory=TrendRules.crypto)
    regime: RegimeRules = Field(default_factory=RegimeRules)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    survival: SurvivalRules = Field(default_factory=SurvivalRules)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    operator: OperatorRules = Field(default_factory=OperatorRules)
    meme: MemeRules = Field(default_factory=MemeRules)
    trench: TrenchRules = Field(default_factory=TrenchRules)
    perps: PerpsDeskRules = Field(default_factory=PerpsDeskRules)
    prop: PropRules = Field(default_factory=PropRules)

    def rules_for(self, asset_class: str) -> TrendRules:
        return self.crypto if asset_class == "crypto" else self.stocks

    @classmethod
    def load(cls, path: Path | None = None) -> StrategyConfig:
        path = path or (REPO_ROOT / "config" / "strategy.toml")
        if not path.exists():
            return cls()
        with path.open("rb") as fh:
            return cls.model_validate(tomllib.load(fh))


def get_settings() -> Settings:
    return Settings()
