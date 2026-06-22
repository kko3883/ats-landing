"""
Event-driven multi-timeframe backtesting engine.

Replays bars chronologically through Layer 1 (daily), Layer 2 (15-min),
and Layer 3 (1-min), tracking portfolio equity, positions, and P&L.

Uses the mock IB bridge — no real broker connection needed.
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ..config import Layer1Config, Layer2Config, Layer3Config, RiskConfig
from ..data.longbridge_stream import Bar, BarBuffer
from ..layer1_macro.daily_filter import DailyMacroFilter, MacroSignal
from ..layer1_macro.regime_client import RegimeClient, RegimeState
from ..layer2_tactical.feature_engine import FeatureEngine, FeatureVector
from ..layer2_tactical.entry_manager import EntryManager, EntryOrder
from ..layer2_tactical.xgb_model import XGBInference, InferenceResult
from ..layer3_micro.micro_volatility import MicroVolatility, MicroMetrics
from ..layer3_micro.trailing_stop import TrailingStopManager, TrailState
from ..layer3_micro.exit_manager import ExitManager, ExitOrder
from ..execution.risk_controller import RiskController, RiskCheckResult
from ..execution.state_tracker import StateTracker, PositionRecord
from ..execution.ib_bridge import IBBridge, OrderConfirmation

logger = logging.getLogger(__name__)


@dataclass
class TradeLog:
    """Record of a completed trade."""
    symbol: str
    entry_time: str
    exit_time: str
    side: str
    quantity: int
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_dollar: float
    pnl_pct: float
    bars_held: int


@dataclass
class BacktestResult:
    """Aggregate backtest results."""
    trades: list[TradeLog] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


class BacktestEngine:
    """
    Multi-timeframe backtesting engine.

    Usage:
        engine = BacktestEngine(config)
        engine.load_data(m15_bars, d1_bars, m1_bars)
        result = engine.run()
    """

    def __init__(self, config):
        self._l1_cfg = config.layer1
        self._l2_cfg = config.layer2
        self._l3_cfg = config.layer3
        self._risk_cfg = config.risk
        self._seed = config.seed_universe

        # Layer 1
        self._macro_filter = DailyMacroFilter(
            sma_period=self._l1_cfg.sma_period,
            d1_lookback=self._l1_cfg.d1_lookback,
            max_gap_atr_mult=self._risk_cfg.max_gap_atr_mult,
        )
        self._regime_client = RegimeClient(default_regime="risk_on")

        # Layer 2
        self._feature_engine = FeatureEngine(
            rsi_period=self._l2_cfg.rsi_period,
            atr_period=self._l2_cfg.atr_period,
            volume_z_period=self._l2_cfg.volume_z_period,
        )
        self._xgb = XGBInference(
            model_path=self._l2_cfg.model_path,
            threshold=self._l2_cfg.entry_prob_threshold,
        )
        self._entry_mgr = EntryManager(
            prob_threshold=self._l2_cfg.entry_prob_threshold,
            atr_stop_mult=2.0,  # Backtest uses fixed 2× ATR
            max_risk_per_trade=self._risk_cfg.max_risk_per_trade,
            portfolio_equity=100_000.0,
            slippage=self._risk_cfg.slippage,
        )

        # Layer 3
        self._trailing_mgr = TrailingStopManager(
            base_trail_mult=self._l3_cfg.base_trail_mult,
            tighten_mult=self._l3_cfg.tighten_mult,
            loosen_mult=self._l3_cfg.loosen_mult,
        )
        self._exit_mgr = ExitManager(
            max_flat_hours=self._l3_cfg.max_flat_hours,
            min_move_pct=self._l3_cfg.min_move_pct,
            slippage=self._risk_cfg.slippage,
        )
        self._micro_vol: dict[str, MicroVolatility] = {}

        # Risk
        self._risk_ctrl = RiskController(
            max_risk_per_trade=self._risk_cfg.max_risk_per_trade,
            max_positions=self._risk_cfg.max_positions,
            daily_loss_limit=self._risk_cfg.daily_loss_limit,
            pdt_equity_threshold=self._risk_cfg.pdt_equity_threshold,
            slippage=self._risk_cfg.slippage,
        )

        # State
        self._state = StateTracker(Path("/tmp/bt_state.json"))
        self._equity = 100_000.0
        self._cash = 100_000.0

        # Buffers
        self._d1_buffers: dict[str, BarBuffer] = {}
        self._m15_buffers: dict[str, BarBuffer] = {}
        self._m1_buffers: dict[str, BarBuffer] = {}
        self._m15_raw: dict[str, list[Bar]] = {}
        self._m1_raw: dict[str, list[Bar]] = {}

        # D1 SMA200 cache (per symbol, updated daily)
        self._sma200: dict[str, float] = {}
        self._atr15: dict[str, float] = {}

        # Approved shortlist (from Layer 1)
        self._approved: set[str] = set()

        # Results
        self._trades: list[TradeLog] = []
        self._equity_curve: list[dict] = []
        self._last_daily_run: Optional[datetime] = None

    def load_data(
        self,
        symbols_data: dict[str, dict[str, pd.DataFrame]],
    ):
        """
        Load multi-timeframe historical data.

        Args:
            symbols_data: {symbol: {"D1": df, "M15": df, "M1": df}} 
                          DataFrames with columns: timestamp, open, high, low, close, volume
        """
        for sym, frames in symbols_data.items():
            # Initialize buffers
            self._d1_buffers[sym] = BarBuffer(maxlen=self._l1_cfg.d1_lookback)
            self._m15_buffers[sym] = BarBuffer(maxlen=self._l2_cfg.m15_buffer_bars)
            self._m1_buffers[sym] = BarBuffer(maxlen=self._l3_cfg.m1_buffer_bars)
            self._micro_vol[sym] = MicroVolatility(lookback=self._l3_cfg.micro_lookback)

            # Convert D1 bars
            d1_df = frames.get("D1")
            if d1_df is not None and not d1_df.empty:
                d1_df = d1_df.rename(columns={c: c.lower() for c in d1_df.columns})
                for _, row in d1_df.iterrows():
                    bar = Bar(
                        symbol=sym, period="1d",
                        timestamp=row.get("timestamp", row.name),
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume=int(row["volume"]),
                    )
                    self._d1_buffers[sym].add(bar)

            # Convert M15 bars
            m15_df = frames.get("M15")
            if m15_df is not None and not m15_df.empty:
                m15_df = m15_df.rename(columns={c: c.lower() for c in m15_df.columns})
                bars = []
                for _, row in m15_df.iterrows():
                    bar = Bar(
                        symbol=sym, period="15m",
                        timestamp=row.get("timestamp", row.name),
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume=int(row["volume"]),
                    )
                    bars.append(bar)
                self._m15_raw[sym] = sorted(bars, key=lambda b: b.timestamp)

            # Convert M1 bars
            m1_df = frames.get("M1")
            if m1_df is not None and not m1_df.empty:
                m1_df = m1_df.rename(columns={c: c.lower() for c in m1_df.columns})
                bars = []
                for _, row in m1_df.iterrows():
                    bar = Bar(
                        symbol=sym, period="1m",
                        timestamp=row.get("timestamp", row.name),
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume=int(row["volume"]),
                    )
                    bars.append(bar)
                self._m1_raw[sym] = sorted(bars, key=lambda b: b.timestamp)

        # Build unified timeline of all m15 bars across all symbols
        self._build_timeline()

    def _build_timeline(self):
        """Build a sorted timeline of all bars for chronological replay."""
        events = []
        for sym, bars in self._m15_raw.items():
            for bar in bars:
                events.append(("M15", bar))
        for sym, bars in self._m1_raw.items():
            for bar in bars:
                events.append(("M1", bar))
        # Sort by timestamp
        events.sort(key=lambda e: e[1].timestamp)
        self._events = events
        logger.info(f"Timeline built: {len(events)} events")
        # Count M15 events per symbol for progress
        self._total_m15 = sum(1 for e in events if e[0] == "M15")
        self._processed_m15 = 0

    def run(self) -> BacktestResult:
        """Execute the backtest chronologically."""
        regime = self._regime_client.fetch_regime()

        for i, (evt_type, bar) in enumerate(self._events):
            if evt_type == "M15":
                self._processed_m15 += 1
                self._process_m15_bar(bar, regime)
            elif evt_type == "M1":
                self._process_m1_bar(bar, regime)

            # Snapshot equity every 15 minutes
            if evt_type == "M15" or i % 100 == 0:
                self._snapshot_equity(bar.timestamp)

        # Close any remaining positions at last price
        self._liquidate_all()

        return self._build_result()

    def _process_m15_bar(self, bar: Bar, regime: RegimeState):
        """Process a 15-minute bar through Layer 1 and Layer 2."""
        sym = bar.symbol

        # Add to buffer
        self._m15_buffers[sym].add(bar)

        # Check if warm
        if not self._m15_buffers[sym].is_warm(self._l2_cfg.m15_warmup_bars):
            return

        # Daily SMA200 update (run once per bar date)
        bar_date = bar.timestamp.date()
        if self._last_daily_run is None or self._last_daily_run.date() < bar_date:
            self._last_daily_run = bar.timestamp
            self._update_daily(bar_date)

        # Skip if not approved
        if sym not in self._approved:
            return

        # Skip if already holding
        if self._state.is_held(sym):
            return

        # Feature computation
        m15_bars = self._m15_buffers[sym].get_bars()
        closes = np.array([b.close for b in m15_bars], dtype=float)
        highs = np.array([b.high for b in m15_bars], dtype=float)
        lows = np.array([b.low for b in m15_bars], dtype=float)
        volumes = np.array([b.volume for b in m15_bars], dtype=float)

        fv = self._feature_engine.compute(
            symbol=sym,
            closes=closes,
            highs=highs,
            lows=lows,
            volumes=volumes,
            sma200=self._sma200.get(sym),
        )

        # Compute ATR15
        atr_val = 0.0
        if len(highs) >= 15:
            from ..data.adjustments import compute_atr
            atr_val = compute_atr(highs, lows, closes, period=14)
        self._atr15[sym] = atr_val

        # XGBoost inference
        result = self._xgb.predict(fv.to_array())

        if result.exceeds_threshold and atr_val > 0:
            order = self._entry_mgr.generate_entry(
                symbol=sym,
                prob=result.probability,
                current_price=float(bar.close),
                atr=atr_val,
                sma200=self._sma200.get(sym, float(bar.close)),
                latest_bar=bar,
            )

            if order:
                risk_check = self._risk_ctrl.check_entry(
                    sym, order.entry_price, order.stop_loss,
                    order.quantity, self._state.position_count,
                )
                if risk_check.allowed:
                    qty = risk_check.adjusted_quantity
                    self._execute_entry(sym, order.entry_price, order.stop_loss,
                                        qty, atr_val, bar.timestamp)

    def _process_m1_bar(self, bar: Bar, regime: RegimeState):
        """Process a 1-minute bar through Layer 3 (trailing stops)."""
        sym = bar.symbol
        if not self._state.is_held(sym):
            return

        self._m1_buffers[sym].add(bar)

        # Micro-volatility
        mv = self._micro_vol.get(sym)
        if mv is None:
            return
        metrics = mv.update(float(bar.close), float(bar.high), float(bar.low), bar.volume)

        # Update trailing stop
        trail_state = self._trailing_mgr.update(
            sym,
            current_price=float(bar.close),
            current_high=float(bar.high),
            current_low=float(bar.low),
            micro_metrics=metrics,
            regime_tighten=regime.tighten_stops,
        )

        # Update state tracker
        self._state.update_trailing_stop(sym, trail_state.current_trail,
                                          trail_state.highest_price,
                                          trail_state.bars_held)

        # Check exits
        pos = self._state.get_position(sym)
        if pos is not None:
            exit_order = self._exit_mgr.evaluate_position(
                symbol=sym,
                side=pos.side,
                entry_price=pos.entry_price,
                current_price=float(bar.close),
                position_qty=pos.quantity,
                trailing_stop=trail_state.current_trail,
                bars_held=trail_state.bars_held,
                flat_bars=trail_state.flat_bars,
                highest_price=trail_state.highest_price,
                stop_breached=trail_state.stop_breached,
                time_decay_exit=trail_state.time_decay_exit,
                exit_reason=trail_state.exit_reason,
                regime_crisis=regime.exit_all,
            )

            if exit_order:
                self._execute_exit(sym, exit_order.exit_price, exit_order.quantity,
                                   exit_order.exit_reason, bar.timestamp)

    def _update_daily(self, date):
        """Run Layer 1 daily filter."""
        for sym in self._seed:
            d1_buf = self._d1_buffers.get(sym)
            if d1_buf is None or len(d1_buf) < self._l1_cfg.sma_period:
                continue

            d1_bars = d1_buf.get_bars()
            closes = pd.Series([b.close for b in d1_bars])

            sma200 = float(closes.rolling(self._l1_cfg.sma_period).mean().iloc[-1])
            if not np.isnan(sma200) and sma200 > 0:
                self._sma200[sym] = sma200

                price = d1_bars[-1].close
                if price > sma200:
                    self._approved.add(sym)
                else:
                    self._approved.discard(sym)

    def _execute_entry(self, sym, price, stop, qty, atr, ts):
        """Simulate an entry fill."""
        cost = price * qty
        if cost > self._cash:
            qty = int(self._cash / price)
            if qty < 1:
                return
            cost = price * qty

        self._cash -= cost
        self._equity = self._cash  # simplified (no unwinding of other positions)

        record = PositionRecord(
            symbol=sym, side="LONG", entry_price=price,
            entry_time=ts.isoformat(), stop_loss=stop,
            trailing_stop=stop, quantity=qty, atr15=atr,
            highest_price=price,
        )
        self._state.add_position(record)
        self._trailing_mgr.register_position(sym, "LONG", price, stop, atr, ts)
        self._risk_ctrl.set_positions(self._state.held_symbols)

        logger.info(f"BT ENTRY: {sym} {qty} @ {price:.2f} SL={stop:.2f}")

    def _execute_exit(self, sym, price, qty, reason, ts):
        """Simulate an exit fill."""
        pos = self._state.get_position(sym)
        if pos is None:
            return

        proceeds = price * qty
        self._cash += proceeds

        entry_price = pos.entry_price
        pnl_dollar = (price - entry_price) * qty
        pnl_pct = (price / entry_price - 1) if entry_price > 0 else 0

        self._equity = self._cash

        entry_ts = datetime.fromisoformat(pos.entry_time) if pos.entry_time else ts
        bars_held = (ts - entry_ts).total_seconds() / 60  # approximate

        trade = TradeLog(
            symbol=sym, entry_time=pos.entry_time, exit_time=ts.isoformat(),
            side=pos.side, quantity=qty, entry_price=entry_price,
            exit_price=price, exit_reason=reason,
            pnl_dollar=round(pnl_dollar, 2), pnl_pct=round(pnl_pct, 6),
            bars_held=int(bars_held),
        )
        self._trades.append(trade)

        self._state.remove_position(sym)
        self._trailing_mgr.unregister_position(sym)
        self._risk_ctrl.set_positions(self._state.held_symbols)
        self._risk_ctrl.record_fill(sym, "SELL")

        logger.info(
            f"BT EXIT: {sym} {qty} @ {price:.2f} "
            f"PnL=${pnl_dollar:.2f} ({pnl_pct:.2%}) — {reason}"
        )

    def _snapshot_equity(self, timestamp):
        """Record equity curve point."""
        self._equity_curve.append({
            "timestamp": timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
            "equity": round(self._equity, 2),
            "cash": round(self._cash, 2),
            "positions": self._state.position_count,
        })

    def _liquidate_all(self):
        """Close all remaining positions at last known prices."""
        for sym in list(self._state.held_symbols):
            pos = self._state.get_position(sym)
            m15_buf = self._m15_buffers.get(sym)
            if m15_buf and m15_buf.latest():
                price = float(m15_buf.latest().close)
                self._execute_exit(sym, price, pos.quantity, "end of backtest",
                                   m15_buf.latest().timestamp)

    def _build_result(self) -> BacktestResult:
        """Compile final backtest results."""
        trades = self._trades
        total_trades = len(trades)
        wins = [t for t in trades if t.pnl_dollar > 0]
        losses = [t for t in trades if t.pnl_dollar <= 0]
        win_rate = len(wins) / total_trades if total_trades > 0 else 0

        total_pnl = sum(t.pnl_dollar for t in trades)
        avg_win = np.mean([t.pnl_dollar for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl_dollar for t in losses]) if losses else 0

        # Sharpe ratio (from equity curve)
        if len(self._equity_curve) >= 2:
            equity_vals = [e["equity"] for e in self._equity_curve]
            returns = np.diff(equity_vals) / equity_vals[:-1]
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252 * 6.5 * 4))
            # Annualized: 252 trading days, 6.5h/day, 4 15-min bars/h
        else:
            sharpe = 0.0

        # Max drawdown
        equity_vals = [e["equity"] for e in self._equity_curve] if self._equity_curve else [100000]
        peak = np.maximum.accumulate(equity_vals)
        drawdowns = (equity_vals - peak) / peak
        max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

        # Profit factor
        gross_profit = sum(t.pnl_dollar for t in wins)
        gross_loss = abs(sum(t.pnl_dollar for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        summary = {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "total_return": round(total_pnl / 100_000, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "profit_factor": round(profit_factor, 2),
            "final_equity": round(self._equity, 2),
        }

        result = BacktestResult(
            trades=trades,
            equity_curve=self._equity_curve,
            summary=summary,
        )

        # Print summary
        print("\n" + "=" * 60)
        print("  BACKTEST RESULTS")
        print("=" * 60)
        for k, v in summary.items():
            print(f"  {k:20s}: {v}")
        print("=" * 60)

        return result