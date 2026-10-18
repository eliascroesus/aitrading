"""The backtest engine. See package docstring for the fill conventions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from hive.backtest.costs import CostModel
from hive.backtest.metrics import summarize
from hive.config import StrategyConfig, TrendRules
from hive.data.bars import adjust_ohlc
from hive.data.universe import PointInTimeUniverse
from hive.signals.features import compute_features, initial_stop, rank_candidates
from hive.signals.regime import class_multipliers, regime_frame
from hive.signals.sizing import size_position

log = logging.getLogger(__name__)

# Optional hook: given (date, ranked candidate rows) return the subset approved.
# This is where the LLM layer plugs in during agent-assisted backtests.
CandidateFilter = Callable[[pd.Timestamp, pd.DataFrame], pd.DataFrame]


@dataclass
class Position:
    symbol: str
    asset_class: str
    qty: float
    entry_price: float
    entry_date: pd.Timestamp
    stop_price: float
    r_per_share: float
    highest_close: float
    fees: float = 0.0
    bars_held: int = 0
    failure_checked: bool = False


@dataclass
class BacktestResult:
    equity: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    metrics: dict[str, float]
    config: StrategyConfig
    daily_log: list[dict] = field(default_factory=list)


class Backtester:
    def __init__(
        self,
        bars_by_symbol: dict[str, pd.DataFrame],
        benchmark_bars: pd.DataFrame,
        config: StrategyConfig,
        cost: CostModel,
        *,
        asset_class_by_symbol: dict[str, str] | None = None,
        initial_equity: float | None = None,
        start: str | None = None,
        end: str | None = None,
        universe: PointInTimeUniverse | None = None,
        candidate_filter: CandidateFilter | None = None,
        fractional: bool = True,
        use_regime_filter: bool = True,
        vix: pd.Series | None = None,
        entry_mode: str = "signal",
        seed: int = 0,
        calendar: pd.DatetimeIndex | None = None,
    ):
        self.cfg = config
        self.cost = cost
        self.entry_mode = entry_mode
        self.seed = seed
        self.initial_equity = float(initial_equity or config.survival.starting_bankroll_usd)
        self.universe = universe
        self.candidate_filter = candidate_filter
        self.fractional = fractional
        self.use_regime_filter = use_regime_filter
        self.asset_class = {s: (asset_class_by_symbol or {}).get(s, "stock") for s in bars_by_symbol}

        self.rules: dict[str, TrendRules] = {}
        self.px: dict[str, pd.DataFrame] = {}
        self.features: dict[str, pd.DataFrame] = {}
        self.last_bar: dict[str, pd.Timestamp] = {}
        for sym, bars in bars_by_symbol.items():
            rules = config.rules_for(self.asset_class[sym])
            if len(bars) <= rules.ma_long:
                continue
            self.rules[sym] = rules
            self.px[sym] = adjust_ohlc(bars)
            self.features[sym] = compute_features(bars, rules)
            self.last_bar[sym] = bars.index[-1]
        self.periods_per_year = (
            365 if all(self.asset_class[s] == "crypto" for s in self.px) and self.px else 252
        )

        # Calendar: the benchmark's, or a supplied one (e.g. the union of all
        # symbols' bars for a mixed stock+crypto sleeve). The regime is
        # forward-filled onto it so weekend crypto bars see Friday's regime.
        cal = calendar if calendar is not None else benchmark_bars.index
        if start:
            cal = cal[cal >= pd.Timestamp(start)]
        if end:
            cal = cal[cal <= pd.Timestamp(end)]
        self.calendar = cal
        self.regime = regime_frame(benchmark_bars, config.regime, vix).reindex(cal, method="ffill")

        # Sparse index of entry signals: day -> symbols. Scanning every symbol
        # every day is O(days x symbols) of pandas row lookups and far too slow.
        self.signal_days: dict[pd.Timestamp, list[str]] = {}
        if self.entry_mode == "random_template":
            # Null test: random entry days among template-passing bars, at the
            # same overall frequency as the real signal.
            n_signal = sum(int(f["entry_signal"].sum()) for f in self.features.values())
            n_template = sum(int(f["template_ok"].sum()) for f in self.features.values())
            p = n_signal / n_template if n_template else 0.0
            rng = np.random.default_rng(self.seed)
            for sym, f in self.features.items():
                mask = f["template_ok"].to_numpy() & (rng.random(len(f)) < p)
                for day in f.index[mask]:
                    self.signal_days.setdefault(day, []).append(sym)
        elif self.entry_mode == "template_cross":
            # Alternative entry: the day the trend template first passes (a
            # fresh qualification above the rising long MA, near the highs).
            for sym, f in self.features.items():
                t = f["template_ok"].to_numpy()
                cross = t & ~np.r_[False, t[:-1]]
                for day in f.index[cross]:
                    self.signal_days.setdefault(day, []).append(sym)
        else:
            for sym, f in self.features.items():
                for day in f.index[f["entry_signal"].to_numpy()]:
                    self.signal_days.setdefault(day, []).append(sym)

    # ------------------------------------------------------------------
    def run(self) -> BacktestResult:
        cfg = self.cfg
        cash = self.initial_equity
        positions: dict[str, Position] = {}
        trades: list[dict] = []
        equity_hist: list[tuple[pd.Timestamp, float]] = []
        exposure_hist: list[tuple[pd.Timestamp, float]] = []
        pending_entries: list[dict] = []  # decided at close t, filled at open t+1
        pending_exits: dict[str, str] = {}  # symbol -> reason (kept until a bar exists)
        peak_equity = cash
        halted = False
        halt_bars_left = 0
        n_halts = 0
        daily_log: list[dict] = []
        day_start_equity = cash

        for day in self.calendar:
            # ---- 0. names whose history ended while held: close at the last bar
            for sym, pos in list(positions.items()):
                if day > self.last_bar[sym]:
                    last_close = float(self.px[sym]["close"].iloc[-1])
                    cash += self._close_position(pos, last_close, self.last_bar[sym], "delisted", trades)
                    del positions[sym]
                    pending_exits.pop(sym, None)

            # ---- 1. fills at today's open for earlier decisions --------------
            for sym, reason in list(pending_exits.items()):
                pos = positions.get(sym)
                if pos is None:
                    del pending_exits[sym]
                    continue
                bar = self._bar(sym, day)
                if bar is None:
                    continue  # no bar today (e.g. holiday for this venue): keep pending
                cash += self._close_position(pos, bar["open"], day, reason, trades)
                del positions[sym]
                del pending_exits[sym]

            for order in pending_entries:
                sym = order["symbol"]
                bar = self._bar(sym, day)
                if bar is None or sym in positions or len(positions) >= cfg.risk.max_positions:
                    continue
                price = self.cost.fill_price(bar["open"], "buy")
                # Re-anchor the stop to the actual fill, keeping the R distance.
                stop = price - order["r_per_share"]
                notional = min(order["notional"], cash)
                qty = notional / price
                if not self.fractional:
                    qty = float(int(qty))
                    notional = qty * price
                if qty <= 0 or notional < cfg.risk.min_order_notional_usd:
                    continue
                fee = self.cost.commission(notional)
                if notional + fee > cash:
                    continue
                cash -= notional + fee
                positions[sym] = Position(
                    symbol=sym, asset_class=self.asset_class[sym], qty=qty, entry_price=price,
                    entry_date=day, stop_price=stop, r_per_share=order["r_per_share"],
                    highest_close=price, fees=fee,
                )
            pending_entries = []

            # ---- 2. intraday stops on today's bar ----------------------------
            for sym, pos in list(positions.items()):
                bar = self._bar(sym, day)
                if bar is None:
                    continue
                if bar["low"] <= pos.stop_price:
                    fill = min(bar["open"], pos.stop_price)
                    cash += self._close_position(pos, fill, day, "stop", trades)
                    del positions[sym]
                    pending_exits.pop(sym, None)

            # ---- 3. mark to market at close; ratchet trailing stops ---------
            mv = 0.0
            for sym, pos in positions.items():
                bar = self._bar(sym, day)
                if bar is None:
                    mv += pos.qty * self._last_close_before(sym, day)
                    continue
                pos.bars_held += 1
                close = bar["close"]
                mv += pos.qty * close
                pos.highest_close = max(pos.highest_close, close)
                f = self.features[sym]
                rules = self.rules[sym]
                atr = f.at[day, "atr"] if day in f.index else np.nan
                if pd.notna(atr):
                    mult = rules.trail_atr if pos.bars_held < rules.trail_tighten_after_bars else rules.trail_atr_late
                    pos.stop_price = max(pos.stop_price, pos.highest_close - atr * mult)
            equity = cash + mv
            peak_equity = max(peak_equity, equity)
            equity_hist.append((day, equity))
            exposure_hist.append((day, mv / equity if equity > 0 else 0.0))

            # ---- 4. portfolio-level halts -----------------------------------
            dd = equity / peak_equity - 1.0
            daily_loss = equity / day_start_equity - 1.0 if day_start_equity > 0 else 0.0
            day_start_equity = equity
            if dd <= -cfg.risk.max_drawdown_halt_pct and not halted:
                halted = True
                halt_bars_left = cfg.risk.halt_cooldown_bars
                n_halts += 1
                log.warning("%s drawdown %.1f%% breached halt; liquidating", day.date(), dd * 100)
            if halted:
                for sym in positions:
                    pending_exits.setdefault(sym, "dd_halt")
                halt_bars_left -= 1
                if halt_bars_left <= 0:
                    halted = False
                    peak_equity = equity  # fresh drawdown window after the cooldown
                daily_log.append({"date": day, "equity": equity, "cash": cash, "n_pos": len(positions),
                                  "state": "halted", "n_pending": 0})
                continue
            daily_loss_block = daily_loss <= -cfg.risk.max_daily_loss_pct

            # ---- 5. rule exits decided at close, filled next open ------------
            for sym, pos in positions.items():
                if sym in pending_exits:
                    continue
                f = self.features[sym]
                rules = self.rules[sym]
                if day not in f.index:
                    continue
                row = f.loc[day]
                close = float(row["close"])
                failure_due = pos.bars_held >= rules.failure_stop_bars and not pos.failure_checked
                if failure_due:
                    pos.failure_checked = True
                if row["below_mid_streak"] >= rules.exit_closes_below_mid or (
                    bool(row["below_long"]) and rules.exit_closes_below_long <= 1
                ):
                    pending_exits[sym] = "trend_break"
                elif failure_due and close < pos.entry_price + rules.failure_stop_min_r * pos.r_per_share:
                    pending_exits[sym] = "failure_stop"
                elif pos.bars_held >= rules.review_bars and not bool(row["template_ok"]):
                    pending_exits[sym] = "review"
                elif pos.bars_held >= rules.max_hold_bars:
                    pending_exits[sym] = "max_hold"

            # ---- 6. new entries ---------------------------------------------
            reg = self.regime.loc[day] if day in self.regime.index else None
            mult = class_multipliers(reg, use_filter=self.use_regime_filter)
            state = str(reg["state"]) if (reg is not None and self.use_regime_filter) else "off"
            n_open_after_exits = len(positions) - len(pending_exits)
            slots = min(cfg.risk.max_positions - n_open_after_exits, cfg.risk.max_new_entries_per_day)
            open_risk = sum(
                max(p.qty * (self._close_or(p, day) - p.stop_price), 0.0) for p in positions.values()
                if p.symbol not in pending_exits
            ) / equity if equity > 0 else 0.0
            port_vol = self._portfolio_vol(equity_hist)
            n_crypto = sum(1 for p in positions.values() if p.asset_class == "crypto")
            crypto_exp = sum(p.qty * self._close_or(p, day) for p in positions.values()
                             if p.asset_class == "crypto") / equity if equity > 0 else 0.0
            if slots > 0 and max(mult.values()) > 0 and not daily_loss_block:
                cands = self._candidates(day, positions, mult)
                if self.candidate_filter is not None and not cands.empty:
                    cands = self.candidate_filter(day, cands)
                gross_now = mv / equity if equity > 0 else 0.0
                taken = 0
                for _, row in cands.iterrows():
                    if taken >= slots or gross_now >= cfg.risk.max_gross_exposure:
                        break
                    if open_risk >= cfg.risk.max_open_risk_pct:
                        break
                    sym = row["symbol"]
                    ac = self.asset_class[sym]
                    if ac == "crypto" and (n_crypto >= cfg.risk.crypto_max_positions
                                           or crypto_exp >= cfg.risk.crypto_sleeve_pct):
                        continue
                    rules = self.rules[sym]
                    price = float(row["close"])
                    stop = initial_stop(price, row, rules)
                    d = size_position(
                        equity=equity, price=price, stop_price=stop, risk=cfg.risk, asset_class=ac,
                        risk_multiplier=mult.get(ac, 0.0), portfolio_vol_ann=port_vol,
                        fractional=self.fractional,
                    )
                    if d.notional <= 0:
                        continue
                    if ac == "crypto":
                        room = max(cfg.risk.crypto_sleeve_pct - crypto_exp, 0.0) * equity
                        if d.notional > room:
                            continue
                        crypto_exp += d.notional / equity
                        n_crypto += 1
                    pending_entries.append({"symbol": sym, "notional": d.notional,
                                            "r_per_share": d.r_per_share})
                    gross_now += d.notional / equity
                    open_risk += d.qty * d.r_per_share / equity
                    taken += 1
            daily_log.append({"date": day, "equity": equity, "cash": cash, "n_pos": len(positions),
                              "state": state, "n_pending": len(pending_entries)})

        # Liquidate at each name's last available close so the final equity is
        # realised (and pays its exit costs); the last point becomes realised cash.
        last = self.calendar[-1]
        for sym, pos in list(positions.items()):
            px = self.px[sym].loc[:last]
            if len(px):
                cash += self._close_position(pos, float(px["close"].iloc[-1]), min(last, px.index[-1]),
                                             "end_of_test", trades)
                del positions[sym]
        if equity_hist and not positions:
            equity_hist[-1] = (last, cash)
        equity_s = pd.Series(dict(equity_hist), name="equity")
        exposure_s = pd.Series(dict(exposure_hist), name="exposure")
        trades_df = pd.DataFrame(trades)
        metrics = summarize(equity_s, trades_df, exposure_s, periods_per_year=self.periods_per_year)
        metrics["n_drawdown_halts"] = n_halts
        return BacktestResult(equity_s, exposure_s, trades_df, metrics, cfg, daily_log)

    # ------------------------------------------------------------------
    def _bar(self, sym: str, day: pd.Timestamp) -> pd.Series | None:
        px = self.px.get(sym)
        if px is None or day not in px.index:
            return None
        return px.loc[day]

    def _last_close_before(self, sym: str, day: pd.Timestamp) -> float:
        px = self.px[sym].loc[:day]
        return float(px["close"].iloc[-1]) if len(px) else 0.0

    def _close_or(self, pos: Position, day: pd.Timestamp) -> float:
        bar = self._bar(pos.symbol, day)
        return float(bar["close"]) if bar is not None else self._last_close_before(pos.symbol, day) or pos.entry_price

    def _portfolio_vol(self, equity_hist: list[tuple[pd.Timestamp, float]]) -> float | None:
        lb = self.cfg.risk.portfolio_vol_lookback
        if len(equity_hist) < lb + 1:
            return None
        vals = np.array([e for _, e in equity_hist[-(lb + 1):]], dtype=float)
        rets = np.diff(np.log(vals))
        return float(rets.std(ddof=1) * np.sqrt(self.periods_per_year)) if len(rets) > 1 else None

    def _candidates(
        self, day: pd.Timestamp, positions: dict[str, Position], mult: dict[str, float]
    ) -> pd.DataFrame:
        symbols = self.signal_days.get(day)
        if not symbols:
            return pd.DataFrame(columns=["symbol", "score"])
        allowed = self.universe.members(day) if self.universe is not None else None
        rows = []
        for sym in symbols:
            ac = self.asset_class[sym]
            if sym in positions or mult.get(ac, 0.0) <= 0:
                continue
            if allowed is not None and ac == "stock" and sym not in allowed:
                continue
            row = self.features[sym].loc[day]
            if pd.notna(row["atr"]) and row["atr"] > 0:
                if ac != "crypto" and row["raw_close"] < self.cfg.universe.min_price_usd:
                    continue
                rows.append({"symbol": sym, **row.to_dict()})
        if not rows:
            return pd.DataFrame(columns=["symbol", "score"])
        return rank_candidates(pd.DataFrame(rows))

    def _close_position(
        self, pos: Position, ref_price: float, day: pd.Timestamp, reason: str, trades: list[dict]
    ) -> float:
        price = self.cost.fill_price(ref_price, "sell")
        notional = pos.qty * price
        fee = self.cost.commission(notional)
        pnl = notional - fee - pos.qty * pos.entry_price - pos.fees
        trades.append({
            "symbol": pos.symbol, "asset_class": pos.asset_class, "entry_date": pos.entry_date,
            "exit_date": day, "entry_price": pos.entry_price, "exit_price": price, "qty": pos.qty,
            "pnl": pnl, "return_pct": (price / pos.entry_price - 1.0) * 100.0,
            "r_per_share": pos.r_per_share,
            "r_multiple": (price - pos.entry_price) / pos.r_per_share if pos.r_per_share else 0.0,
            "hold_days": pos.bars_held, "fees": pos.fees + fee, "reason": reason,
        })
        return notional - fee
