#!/usr/bin/env python3
"""
Live trading entrypoint for the Hierarchical Multi-Timeframe Equity Engine.

Connects to Longbridge for streaming M1/M15/D1 data and IB Gateway on the NAS
for order execution via ib_insync.  Runs Layer 1→2→3 in an asyncio loop.

Usage:
    python equity_engine/run_live.py
    python equity_engine/run_live.py --paper           # IB Gateway paper (7497)
    python equity_engine/run_live.py --no-execute       # Signal-only mode (no orders)

Environment variables:
    IBG_HOST              IB Gateway host (default: kko-nas.tail9a4917.ts.net)
    IBG_PORT_PAPER        Paper trading port (default: 7497)
    IBG_PORT_LIVE         Live trading port (default: 7496)
    IB_ACCOUNT_ID         IB paper/live account ID
    LONGBRIDGE_APP_KEY    Longbridge API key
    LONGBRIDGE_APP_SECRET Longbridge API secret
    SUPABASE_ANON_KEY     Supabase anon key for regime reads
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Add parent to path for direct execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from equity_engine.config import EngineConfig, STATE_FILE, TRADES_LOG, IBG_PORT_LIVE, IBG_CLIENT_ID, IB_ACCOUNT_ID
from equity_engine.data.longbridge_stream import Bar, LongbridgeStreamer, StreamConfig
from equity_engine.data.adjustments import check_overnight_gap, is_open_cooldown, compute_atr
from equity_engine.layer1_macro.regime_client import RegimeClient
from equity_engine.layer1_macro.daily_filter import DailyMacroFilter, compute_sma
from equity_engine.layer1_macro.universe import UniverseManager
from equity_engine.layer2_tactical.feature_engine import FeatureEngine
from equity_engine.layer2_tactical.xgb_model import XGBInference
from equity_engine.layer2_tactical.entry_manager import EntryManager
from equity_engine.layer3_micro.micro_volatility import MicroVolatility
from equity_engine.layer3_micro.trailing_stop import TrailingStopManager
from equity_engine.layer3_micro.exit_manager import ExitManager
from equity_engine.execution.ib_bridge import IBBridge
from equity_engine.execution.risk_controller import RiskController
from equity_engine.execution.state_tracker import StateTracker, PositionRecord

logger = logging.getLogger("equity_engine")

# ── Engine orchestrator ─────────────────────────────────────────────────────

class EquityTradingEngine:
    """Main engine that orchestrates all three layers."""

    def __init__(self, config: EngineConfig, paper: bool = True, execute: bool = True):
        self._cfg = config
        self._paper = paper
        self._execute = execute
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Layer 1
        self._regime_client = RegimeClient(
            supabase_url=config.SUPABASE_URL,
            supabase_anon_key=config.SUPABASE_ANON_KEY,
        )
        self._macro_filter = DailyMacroFilter(
            sma_period=config.layer1.sma_period,
            d1_lookback=config.layer1.d1_lookback,
            max_gap_atr_mult=config.risk.max_gap_atr_mult,
        )
        self._universe = UniverseManager(
            seed_tickers=config.seed_universe,
            use_screener=config.use_screener,
            screener_path=config.screener_path,
        )

        # Layer 2
        self._feature_engine = FeatureEngine(
            rsi_period=config.layer2.rsi_period,
            atr_period=config.layer2.atr_period,
            volume_z_period=config.layer2.volume_z_period,
        )
        self._xgb = XGBInference(
            model_path=config.layer2.model_path,
            threshold=config.layer2.entry_prob_threshold,
        )
        self._entry_mgr = EntryManager(
            prob_threshold=config.layer2.entry_prob_threshold,
            max_risk_per_trade=config.risk.max_risk_per_trade,
            slippage=config.risk.slippage,
        )

        # Layer 3
        self._trailing_mgr = TrailingStopManager(
            base_trail_mult=config.layer3.base_trail_mult,
            tighten_mult=config.layer3.tighten_mult,
            loosen_mult=config.layer3.loosen_mult,
            tighten_vol_z=config.layer3.tighten_vol_z,
            loosen_vol_z=config.layer3.loosen_vol_z,
            max_flat_hours=config.layer3.max_flat_hours,
            min_move_pct=config.layer3.min_move_pct,
        )
        self._exit_mgr = ExitManager(
            max_flat_hours=config.layer3.max_flat_hours,
            min_move_pct=config.layer3.min_move_pct,
            slippage=config.risk.slippage,
        )
        self._micro_vol: dict[str, MicroVolatility] = {}

        # Execution
        ib_port = config.ib_port if paper else IBG_PORT_LIVE
        self._ib = IBBridge(
            host=config.ib_host,
            port=ib_port,
            client_id=IBG_CLIENT_ID,
            account_id=IB_ACCOUNT_ID,
        )
        self._risk_ctrl = RiskController(
            max_risk_per_trade=config.risk.max_risk_per_trade,
            max_positions=config.risk.max_positions,
            daily_loss_limit=config.risk.daily_loss_limit,
            pdt_equity_threshold=config.risk.pdt_equity_threshold,
        )
        self._state = StateTracker(STATE_FILE)

        # Data streaming
        self._streamer = None  # type: Optional[LongbridgeStreamer]
        self._m15_buffers: dict[str, list[Bar]] = {}
        self._m1_buffers: dict[str, list[Bar]] = {}
        self._d1_close_cache: dict[str, list[float]] = {}

        # Cached indicators
        self._sma200: dict[str, float] = {}
        self._atr15: dict[str, float] = {}
        self._approved: set[str] = set()
        self._last_daily_run: datetime | None = None

        # Stats
        self._m15_count: int = 0
        self._m1_count: int = 0
        self._entries_fired: int = 0
        self._exits_fired: int = 0

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self):
        """Initialize and start the engine."""
        logger.info("=" * 60)
        logger.info("  Equity Trading Engine - Starting")
        logger.info(f"  Mode: {'PAPER' if self._paper else 'LIVE'}")
        logger.info(f"  Execution: {'ENABLED' if self._execute else 'DISABLED (signal-only)'}")
        logger.info(f"  Universe: {len(self._cfg.seed_universe)} seed symbols")
        logger.info(f"  IB Gateway: {self._cfg.ib_host}:{self._cfg.ib_port}")
        logger.info("=" * 60)

        # Connect to IB Gateway
        if self._execute:
            connected = await self._ib.connect()
            if not connected:
                logger.warning("IB Gateway not available — continuing in signal-only mode")
                self._execute = False
            else:
                equity = await self._ib.get_account_equity()
                self._risk_ctrl.update_equity(equity)
                self._state.update_equity(equity)
                self._entry_mgr.update_equity(equity)
                logger.info(f"Account equity: ${equity:,.2f}")

        # Start Longbridge streaming
        stream_cfg = StreamConfig(
            symbols=self._universe.get_candidates(),
            periods=["1m", "15m", "1d"],
        )
        self._streamer = LongbridgeStreamer(stream_cfg)
        await self._streamer.start()

        # Initialize micro-volatility trackers
        for sym in stream_cfg.symbols:
            self._micro_vol[sym] = MicroVolatility(lookback=self._cfg.layer3.micro_lookback)

        # Main loop
        self._running = True
        await self._main_loop()

    async def stop(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self._running = False
        self._shutdown_event.set()

        if self._streamer:
            await self._streamer.stop()
        if self._execute:
            await self._ib.cancel_all_orders()
            await self._ib.disconnect()

        self._state.save()
        logger.info("Engine stopped.")

    # ── Main loop ──────────────────────────────────────────────────────

    async def _main_loop(self):
        """Process bars from the streamer queue until stopped."""
        logger.info("Engine loop started — waiting for bars...")

        while self._running:
            try:
                bar = await self._streamer.get_bar(timeout=1.0)
                if bar is None:
                    await asyncio.sleep(0.1)
                    continue

                if bar.period == "15m":
                    await self._handle_m15_bar(bar)
                elif bar.period == "1m":
                    await self._handle_m1_bar(bar)
                elif bar.period == "1d":
                    self._handle_d1_bar(bar)

                # Periodic state save
                if self._m15_count % 4 == 0:
                    self._state.save()
                    self._append_trade_log_raw({"ts": datetime.now(timezone.utc).isoformat(),
                                                  "event": "heartbeat",
                                                  "equity": self._risk_ctrl.portfolio_equity,
                                                  "positions": self._state.position_count})

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _handle_m15_bar(self, bar: Bar):
        """Process a 15-minute bar: Layer 2 evaluation."""
        self._m15_count += 1
        sym = bar.symbol

        # Maintain buffer
        if sym not in self._m15_buffers:
            self._m15_buffers[sym] = []
        self._m15_buffers[sym].append(bar)
        if len(self._m15_buffers[sym]) > self._cfg.layer2.m15_buffer_bars:
            self._m15_buffers[sym].pop(0)

        # Warmup check
        if len(self._m15_buffers[sym]) < self._cfg.layer2.m15_warmup_bars:
            return

        # Daily SMA200 update (once per day per symbol)
        bar_date = bar.timestamp.date()
        if self._last_daily_run is None or self._last_daily_run.date() < bar_date:
            self._last_daily_run = bar.timestamp
            self._update_daily_sma()

        # Regime check
        regime = self._regime_client.fetch_regime()
        if not regime.allow_new_entries:
            return

        # Skip if not approved (Layer 1)
        if sym not in self._approved:
            return

        # Skip if already holding
        if self._state.is_held(sym):
            return

        # Feature computation
        bars = self._m15_buffers[sym]
        closes = np.array([b.close for b in bars], dtype=float)
        highs = np.array([b.high for b in bars], dtype=float)
        lows = np.array([b.low for b in bars], dtype=float)
        volumes = np.array([b.volume for b in bars], dtype=float)

        fv = self._feature_engine.compute(
            symbol=sym, closes=closes, highs=highs, lows=lows, volumes=volumes,
            sma200=self._sma200.get(sym),
        )

        # ATR(15)
        if len(highs) >= 15:
            self._atr15[sym] = compute_atr(highs, lows, closes, period=14)

        # XGBoost inference
        result = self._xgb.predict(fv.to_array())

        logger.debug(
            f"M15 {sym}: price={bar.close:.2f} prob={result.probability:.3f} "
            f"RSI={fv.rsi_14:.1f} ATR%={fv.atr_pct:.4f} "
            f"VWAP%={fv.vwap_distance_pct:.4f} vol_z={fv.volume_zscore:.2f}"
        )

        if result.exceeds_threshold and self._atr15.get(sym, 0) > 0:
            await self._generate_entry(sym, result.probability, float(bar.close),
                                       self._atr15[sym], bar)

    async def _handle_m1_bar(self, bar: Bar):
        """Process a 1-minute bar: Layer 3 trailing stop update."""
        self._m1_count += 1
        sym = bar.symbol

        if not self._state.is_held(sym):
            return

        # Maintain M1 buffer
        if sym not in self._m1_buffers:
            self._m1_buffers[sym] = []
        self._m1_buffers[sym].append(bar)
        if len(self._m1_buffers[sym]) > self._cfg.layer3.m1_buffer_bars:
            self._m1_buffers[sym].pop(0)

        # Micro-volatility
        mv = self._micro_vol.get(sym)
        if mv is None:
            return
        metrics = mv.update(float(bar.close), float(bar.high), float(bar.low), bar.volume)

        # Regime
        regime = self._regime_client.fetch_regime()

        # Update trailing stop
        trail_state = self._trailing_mgr.update(
            sym,
            current_price=float(bar.close),
            current_high=float(bar.high),
            current_low=float(bar.low),
            micro_metrics=metrics,
            regime_tighten=regime.tighten_stops,
        )

        if trail_state.trail_adjusted_count % 10 == 0 and trail_state.trail_adjusted_count > 0:
            logger.debug(
                f"Trail {sym}: {trail_state.current_trail:.2f} "
                f"({trail_state.current_mult:.2f}× ATR, "
                f"high={trail_state.highest_price:.2f})"
            )

        # Check exits
        pos = self._state.get_position(sym)
        if pos is None:
            return

        exit_order = self._exit_mgr.evaluate_position(
            symbol=sym, side=pos.side, entry_price=pos.entry_price,
            current_price=float(bar.close), position_qty=pos.quantity,
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
            await self._execute_exit(exit_order)

    def _handle_d1_bar(self, bar: Bar):
        """Process a daily bar: update price cache."""
        sym = bar.symbol
        if sym not in self._d1_close_cache:
            self._d1_close_cache[sym] = []
        self._d1_close_cache[sym].append(float(bar.close))
        # Keep bounded
        if len(self._d1_close_cache[sym]) > self._cfg.layer1.d1_lookback:
            self._d1_close_cache[sym].pop(0)

    def _update_daily_sma(self):
        """Compute SMA(200) for all universe symbols from D1 cache."""
        for sym in self._universe.get_candidates():
            closes = self._d1_close_cache.get(sym, [])
            if len(closes) >= self._cfg.layer1.sma_period:
                sma_val = float(np.mean(closes[-self._cfg.layer1.sma_period:]))
                last_price = closes[-1]
                self._sma200[sym] = sma_val
                if last_price > sma_val:
                    self._approved.add(sym)
                else:
                    self._approved.discard(sym)
        logger.info(
            f"Daily SMA200 update: {len(self._approved)}/{len(self._universe.get_candidates())} approved"
        )

    async def _generate_entry(self, sym: str, prob: float, price: float,
                               atr: float, bar: Bar):
        """Generate and submit an entry order."""
        # Overnight gap / cooldown check
        if is_open_cooldown(bar.timestamp, self._cfg.risk.open_cooldown_minutes):
            logger.debug(f"Entry skipped {sym}: open cooldown")
            return

        order = self._entry_mgr.generate_entry(
            symbol=sym, prob=prob, current_price=price, atr=atr,
            sma200=self._sma200.get(sym, price), latest_bar=bar,
        )
        if order is None:
            return

        risk_check = self._risk_ctrl.check_entry(
            sym, order.entry_price, order.stop_loss,
            order.quantity, self._state.position_count,
        )
        if not risk_check.allowed:
            logger.info(f"Entry blocked {sym}: {risk_check.reason}")
            return

        qty = risk_check.adjusted_quantity

        if self._execute:
            confirm = await self._ib.place_limit_order(
                sym, order.side, qty, order.entry_price,
            )
            if confirm.status == "FILLED" or confirm.status == "PENDING":
                self._entries_fired += 1
                logger.info(
                    f"▶ ENTRY: {sym} {order.side} {qty}sh "
                    f"@ {order.entry_price:.2f} SL={order.stop_loss:.2f} "
                    f"prob={prob:.2%}"
                )
                if confirm.status == "FILLED":
                    self._register_entry(sym, confirm.fill_price, order.stop_loss,
                                         qty, atr)
            else:
                logger.error(f"Entry rejected: {sym} — {confirm.message}")
        else:
            self._entries_fired += 1
            logger.info(
                f"▶ SIGNAL (no-execute): {sym} {order.side} {qty}sh "
                f"@ {price:.2f} SL={order.stop_loss:.2f} prob={prob:.2%}"
            )

    async def _execute_exit(self, exit_order):
        """Submit an exit order."""
        self._exits_fired += 1
        if self._execute:
            confirm = await self._ib.place_market_order(
                exit_order.symbol, exit_order.side, exit_order.quantity,
            )
            logger.info(
                f"◀ EXIT: {exit_order.symbol} {exit_order.quantity}sh "
                f"@ ~{exit_order.exit_price:.2f} — {exit_order.exit_reason}"
            )
            self._state.remove_position(exit_order.symbol)
            self._trailing_mgr.unregister_position(exit_order.symbol)
            self._risk_ctrl.record_fill(exit_order.symbol, exit_order.side)
        else:
            logger.info(
                f"◀ EXIT SIGNAL (no-execute): {exit_order.symbol} "
                f"{exit_order.quantity}sh — {exit_order.exit_reason}"
            )
            self._state.remove_position(exit_order.symbol)
            self._trailing_mgr.unregister_position(exit_order.symbol)

    def _register_entry(self, sym, fill_price, stop_loss, qty, atr):
        """Register a filled entry in state tracker."""
        now = datetime.now(timezone.utc)
        record = PositionRecord(
            symbol=sym, side="LONG", entry_price=fill_price,
            entry_time=now.isoformat(), stop_loss=stop_loss,
            trailing_stop=stop_loss, quantity=qty, atr15=atr,
            highest_price=fill_price,
        )
        self._state.add_position(record)
        self._trailing_mgr.register_position(
            sym, "LONG", fill_price, stop_loss, atr, now,
        )
        self._risk_ctrl.set_positions(self._state.held_symbols)

    def _append_trade_log_raw(self, record: dict):
        """Append a raw record to the trade log."""
        try:
            with open(TRADES_LOG, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            pass


# ── Entrypoint ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Equity Trading Engine — Live")
    parser.add_argument("--paper", action="store_true", default=True,
                        help="Use paper trading (default)")
    parser.add_argument("--live", action="store_true",
                        help="Use live trading (DANGER — real money)")
    parser.add_argument("--no-execute", action="store_true",
                        help="Signal-only mode — no orders sent")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    # Logging setup
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = EngineConfig.from_defaults()
    paper = not args.live

    engine = EquityTradingEngine(config, paper=paper, execute=not args.no_execute)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown():
        logger.info("Received shutdown signal")
        loop.call_soon_threadsafe(lambda: engine._shutdown_event.set())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda s, f: _shutdown())

    try:
        loop.run_until_complete(engine.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(engine.stop())
        loop.close()

    logger.info(f"Sessions stats: {engine._m15_count} M15, {engine._m1_count} M1, "
                f"{engine._entries_fired} entries, {engine._exits_fired} exits")


if __name__ == "__main__":
    main()