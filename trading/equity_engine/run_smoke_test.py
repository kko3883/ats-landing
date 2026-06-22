#!/usr/bin/env python3
"""
Smoke test — validates the full engine pipeline with real historical data.

Fetches bars from yfinance (free, no broker needed), runs through all
three layers, and prints a summary.  No orders are sent — pure paper simulation.

Usage:
    python equity_engine/run_smoke_test.py                    # default 3 stocks
    python equity_engine/run_smoke_test.py --days 30           # 30 days of history
    python equity_engine/run_smoke_test.py --symbols AAPL.US,MSFT.US,GOOGL.US
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# Add parent to path for direct execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from equity_engine.config import EngineConfig
from equity_engine.data.longbridge_stream import Bar, BarBuffer
from equity_engine.data.adjustments import compute_atr
from equity_engine.layer1_macro.daily_filter import DailyMacroFilter
from equity_engine.layer1_macro.regime_client import RegimeClient
from equity_engine.layer2_tactical.feature_engine import FeatureEngine, FEATURE_NAMES
from equity_engine.layer2_tactical.xgb_model import XGBInference
from equity_engine.layer2_tactical.entry_manager import EntryManager
from equity_engine.layer3_micro.micro_volatility import MicroVolatility
from equity_engine.layer3_micro.trailing_stop import TrailingStopManager
from equity_engine.layer3_micro.exit_manager import ExitManager
from equity_engine.execution.risk_controller import RiskController
from equity_engine.execution.state_tracker import StateTracker, PositionRecord

logger = logging.getLogger("smoke_test")

# ── Data fetching ────────────────────────────────────────────────────────────

def _parse_ts(ts_val) -> datetime:
    """Robustly parse a timestamp from yfinance (int, float, string, Timestamp, etc.)."""
    if isinstance(ts_val, (int, float)):
        # Unix timestamp (seconds or milliseconds)
        if ts_val > 1e12:
            ts_val = ts_val / 1000
        return datetime.fromtimestamp(ts_val, tz=timezone.utc)
    if isinstance(ts_val, str):
        # Try ISO format
        try:
            return datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
        # Try float string
        try:
            v = float(ts_val)
            if v > 1e12:
                v = v / 1000
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)
    if isinstance(ts_val, pd.Timestamp):
        return ts_val.to_pydatetime()
    if isinstance(ts_val, datetime):
        return ts_val
    # Last resort
    return datetime.now(timezone.utc)


def fetch_historical(symbols: list[str], days: int):
    """Fetch D1 + M15 + M1 bars from yfinance."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    data = {}
    for i, sym in enumerate(symbols, 1):
        ticker = sym.replace(".US", "")
        print(f"  [{i}/{len(symbols)}] {sym} ({ticker}) ...")

        sym_data = {}
        for interval, label in [("1d", "D1"), ("15m", "M15"), ("1m", "M1")]:
            # yfinance limits: 15m → 60d max, 1m → 7d max
            fetch_start = start
            if interval == "15m" and days > 60:
                fetch_start = end - timedelta(days=60)
            elif interval == "1m" and days > 7:
                fetch_start = end - timedelta(days=7)

            try:
                df = yf.download(ticker, start=fetch_start, end=end,
                                 interval=interval, progress=False)
                if df.empty:
                    print(f"    {label}: NO DATA")
                    sym_data[label] = None
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0].lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]

                df = df.reset_index()
                sym_data[label] = df
                print(f"    {label}: {len(df)} bars")
            except Exception as e:
                print(f"    {label}: ERROR — {e}")
                sym_data[label] = None
        data[sym] = sym_data
    return data


# ── Engine runner ────────────────────────────────────────────────────────────

def run_smoke_test(symbols: list[str], days: int = 30):
    """Run the full pipeline against historical data."""
    print("=" * 70)
    print(f"  EQUITY ENGINE — SMOKE TEST")
    print(f"  Symbols: {len(symbols)} stocks")
    print(f"  Period:  last {days} days")
    print(f"  Model:   XGBoost (falls back to prob=0.5 if no model file)")
    print("=" * 70)

    # Config
    cfg = EngineConfig.from_defaults()
    cfg.seed_universe = symbols

    # Override SMA period for short test windows
    if days < 200:
        cfg.layer1.sma_period = max(5, days // 2)
        cfg.layer1.d1_lookback = days
    cfg.layer2.m15_warmup_bars = 30  # lower warmup for faster feedback

    # Fetch data
    print("\n── Fetching historical data ──")
    data = fetch_historical(symbols, days)

    # Count available data
    available = {s: v for s, v in data.items() if v.get("M15") is not None}
    print(f"\n  Available symbols with M15 data: {len(available)}/{len(symbols)}")
    if not available:
        print("  FAILED: No data available.  Check internet connection.")
        return

    # Initialize all components
    print("\n── Initializing engine components ──")

    macro = DailyMacroFilter(sma_period=cfg.layer1.sma_period, d1_lookback=cfg.layer1.d1_lookback)
    regime_client = RegimeClient(default_regime="risk_on")
    feature_engine = FeatureEngine()
    xgb = XGBInference(model_path=cfg.layer2.model_path, threshold=cfg.layer2.entry_prob_threshold)
    print(f"  XGBoost model: {'LOADED' if xgb.is_loaded else 'FALLBACK (prob=0.5)'}")
    entry_mgr = EntryManager(
        prob_threshold=cfg.layer2.entry_prob_threshold,
        max_risk_per_trade=cfg.risk.max_risk_per_trade,
        portfolio_equity=100_000.0,
        slippage=cfg.risk.slippage,
    )
    trailing_mgr = TrailingStopManager()
    exit_mgr = ExitManager()
    micro_vols: dict[str, MicroVolatility] = {s: MicroVolatility(5) for s in available}
    risk_ctrl = RiskController()
    state = StateTracker(Path("/tmp/eq_smoke_state.json"))

    print("  All components initialized OK")

    # Run chronological simulation
    print("\n── Running chronological simulation ──")

    regime = regime_client.fetch_regime()
    print(f"  Regime: {regime.regime_name.upper()} (entries={'YES' if regime.allow_new_entries else 'NO'})")

    # Build bars and timeline
    m15_bars: dict[str, list[Bar]] = {}
    m1_bars: dict[str, list[Bar]] = {}
    d1_closes: dict[str, list[float]] = {}

    for sym, frames in available.items():
        m15_df = frames["M15"]
        if m15_df is not None:
            bars = []
            for _, row in m15_df.iterrows():
                ts = _parse_ts(row.get("date") or row.get("datetime") or row.name)
                bars.append(Bar(symbol=sym, period="15m", timestamp=ts,
                                open=float(row["open"]), high=float(row["high"]),
                                low=float(row["low"]), close=float(row["close"]),
                                volume=int(row.get("volume", 0))))
            m15_bars[sym] = sorted(bars, key=lambda b: b.timestamp)

        m1_df = frames.get("M1")
        if m1_df is not None:
            bars = []
            for _, row in m1_df.iterrows():
                ts = _parse_ts(row.get("date") or row.get("datetime") or row.name)
                bars.append(Bar(symbol=sym, period="1m", timestamp=ts,
                                open=float(row["open"]), high=float(row["high"]),
                                low=float(row["low"]), close=float(row["close"]),
                                volume=int(row.get("volume", 0))))
            m1_bars[sym] = sorted(bars, key=lambda b: b.timestamp)

        d1_df = frames.get("D1")
        if d1_df is not None:
            closes = [float(r["close"]) for _, r in d1_df.iterrows()]
            d1_closes[sym] = closes

    # Build unified event timeline
    events = []
    for sym, bars in m15_bars.items():
        for b in bars:
            events.append(("M15", b))
    for sym, bars in m1_bars.items():
        for b in bars:
            events.append(("M1", b))
    events.sort(key=lambda e: e[1].timestamp)

    print(f"  Timeline: {len(events)} events ({sum(1 for e in events if e[0]=='M15')} M15 + {sum(1 for e in events if e[0]=='M1')} M1)")

    # State tracking
    m15_bufs: dict[str, list[Bar]] = defaultdict(list)
    m1_bufs: dict[str, list[Bar]] = defaultdict(list)
    sma200_vals: dict[str, float] = {}
    atr15_vals: dict[str, float] = {}
    approved: set[str] = set()
    last_daily_run = None

    # Counters
    entries = 0
    exits = 0
    feature_count = 0
    signals_above = 0
    signals_below = 0
    blocked_holding = 0
    blocked_not_approved = 0
    blocked_regime = 0
    exit_stop = 0
    exit_decay = 0
    exit_crisis = 0

    empty_trades = []  # (sym, entry_price, exit_price, reason, pnl)

    # ── Run through events chronologically ──
    for i, (evt_type, bar) in enumerate(events):
        sym = bar.symbol

        if evt_type == "M15":
            # Buffer
            m15_bufs[sym].append(bar)
            if len(m15_bufs[sym]) > cfg.layer2.m15_buffer_bars:
                m15_bufs[sym].pop(0)

            if len(m15_bufs[sym]) < cfg.layer2.m15_warmup_bars:
                continue

            # Daily SMA update
            bar_date = bar.timestamp.date()
            if last_daily_run is None or last_daily_run.date() < bar_date:
                last_daily_run = bar.timestamp
                for s, closes in d1_closes.items():
                    if len(closes) >= cfg.layer1.sma_period:
                        sma_val = float(np.mean(closes[-cfg.layer1.sma_period:]))
                        sma200_vals[s] = sma_val
                        if closes[-1] > sma_val:
                            approved.add(s)

            # Regime gate
            if not regime.allow_new_entries:
                blocked_regime += 1
                continue

            # Layer 1 approval
            if sym not in approved:
                blocked_not_approved += 1
                continue

            # Already holding
            if state.is_held(sym):
                blocked_holding += 1
                continue

            # Feature computation
            bars_win = m15_bufs[sym]
            closes = np.array([b.close for b in bars_win])
            highs = np.array([b.high for b in bars_win])
            lows = np.array([b.low for b in bars_win])
            volumes = np.array([b.volume for b in bars_win], dtype=float)

            fv = feature_engine.compute(
                symbol=sym, closes=closes, highs=highs, lows=lows, volumes=volumes,
                sma200=sma200_vals.get(sym),
            )
            feature_count += 1

            # ATR
            if len(highs) >= 15:
                atr15_vals[sym] = compute_atr(highs, lows, closes, 14)

            # XGBoost inference
            result = xgb.predict(fv.to_array())

            if result.probability >= cfg.layer2.entry_prob_threshold:
                signals_above += 1
            else:
                signals_below += 1

            # Entry if threshold met and ATR available
            if result.exceeds_threshold and atr15_vals.get(sym, 0) > 0:
                order = entry_mgr.generate_entry(
                    symbol=sym, prob=result.probability,
                    current_price=float(bar.close), atr=atr15_vals[sym],
                    sma200=sma200_vals.get(sym, float(bar.close)),
                    latest_bar=bar,
                )
                if order:
                    entries += 1
                    qty = order.quantity
                    entry_px = order.entry_price
                    stop_px = order.stop_loss

                    # Simulate fill
                    record = PositionRecord(
                        symbol=sym, side="LONG", entry_price=entry_px,
                        entry_time=bar.timestamp.isoformat(), stop_loss=stop_px,
                        trailing_stop=stop_px, quantity=qty, atr15=atr15_vals[sym],
                        highest_price=entry_px,
                    )
                    state.add_position(record)
                    trailing_mgr.register_position(sym, "LONG", entry_px, stop_px,
                                                    atr15_vals[sym], bar.timestamp)

            # Progress indicator
            if i % 1000 == 0 and i > 0:
                pct = i / len(events) * 100
                print(f"  ... {pct:.0f}% ({i}/{len(events)} events, {entries} entries, {exits} exits)")

        elif evt_type == "M1":
            if not state.is_held(sym):
                continue

            m1_bufs[sym].append(bar)
            if len(m1_bufs[sym]) > cfg.layer3.m1_buffer_bars:
                m1_bufs[sym].pop(0)

            # Micro-volatility
            mv = micro_vols.get(sym)
            if mv is None:
                continue
            metrics = mv.update(float(bar.close), float(bar.high), float(bar.low), bar.volume)

            # Update trailing stop
            trail = trailing_mgr.update(
                sym, current_price=float(bar.close), current_high=float(bar.high),
                current_low=float(bar.low), micro_metrics=metrics,
                regime_tighten=regime.tighten_stops,
            )

            # Check exits
            pos = state.get_position(sym)
            if pos is None:
                continue

            exit_order = exit_mgr.evaluate_position(
                symbol=sym, side=pos.side, entry_price=pos.entry_price,
                current_price=float(bar.close), position_qty=pos.quantity,
                trailing_stop=trail.current_trail,
                bars_held=trail.bars_held, flat_bars=trail.flat_bars,
                highest_price=trail.highest_price,
                stop_breached=trail.stop_breached,
                time_decay_exit=trail.time_decay_exit,
                exit_reason=trail.exit_reason,
                regime_crisis=regime.exit_all,
            )

            if exit_order:
                exits += 1
                exit_px = exit_order.exit_price
                entry_px = pos.entry_price
                pnl = (exit_px - entry_px) * pos.quantity
                empty_trades.append((sym, entry_px, exit_px, exit_order.exit_reason, pnl))

                if "stop" in exit_order.exit_reason.lower():
                    exit_stop += 1
                elif "decay" in exit_order.exit_reason.lower():
                    exit_decay += 1
                elif "crisis" in exit_order.exit_reason.lower():
                    exit_crisis += 1
                else:
                    exit_decay += 1  # default bucket

                state.remove_position(sym)
                trailing_mgr.unregister_position(sym)

    # Close any lingering positions
    for sym in list(state.held_symbols):
        pos = state.get_position(sym)
        last_bar = m15_bufs[sym][-1] if m15_bufs.get(sym) else None
        if last_bar:
            exit_px = float(last_bar.close)
            pnl = (exit_px - pos.entry_price) * pos.quantity
            empty_trades.append((sym, pos.entry_price, exit_px, "end of test", pnl))
            exits += 1
            state.remove_position(sym)

    # ── Results ────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"  SMOKE TEST RESULTS")
    print(f"{'=' * 70}")
    print(f"")
    print(f"  ── Layer 1 (Daily) ──")
    print(f"  Symbols with D1 data: {len(d1_closes)}")
    print(f"  Approved by SMA(200): {len(approved)}")
    print(f"  SMA(200) values:")
    for sym in sorted(sma200_vals.keys())[:5]:
        d1p = d1_closes.get(sym, [0])[-1] if d1_closes.get(sym) else 0
        print(f"    {sym:12s}  price={d1p:>10.2f}  SMA200={sma200_vals[sym]:.2f}  {'✓' if sym in approved else '✗'}")
    if len(sma200_vals) > 5:
        print(f"    ... and {len(sma200_vals) - 5} more")

    print(f"")
    print(f"  ── Layer 2 (15-Minute) ──")
    print(f"  Feature computations:  {feature_count}")
    print(f"  Signals above {cfg.layer2.entry_prob_threshold}: {signals_above}")
    print(f"  Signals below threshold:  {signals_below}")
    print(f"  Blocked: {blocked_holding} holding, {blocked_not_approved} not approved, {blocked_regime} regime")
    print(f"  Entries generated:  {entries}")
    print(f"")
    if feature_count >= 5:
        print(f"  Sample feature vector:")
        print(f"    {'Feature':<25s} {'Value'}")
        print(f"    {'─' * 37}")
        for name in FEATURE_NAMES:
            print(f"    {name:<25s} — (computed per M15 evaluation)")

    print(f"")
    print(f"  ── Layer 3 (1-Minute) ──")
    print(f"  Exits:  {exits} total")
    print(f"    Stop breaches:  {exit_stop}")
    print(f"    Time decay:     {exit_decay}")
    print(f"    Crisis:         {exit_crisis}")

    print(f"")
    print(f"  ── Trade Summary ──")
    print(f"  Trades closed: {len(empty_trades)}")
    if empty_trades:
        wins = [t for t in empty_trades if t[4] > 0]
        losses = [t for t in empty_trades if t[4] <= 0]
        total_pnl = sum(t[4] for t in empty_trades)
        print(f"  Wins: {len(wins)}, Losses: {len(losses)}")
        print(f"  Win rate: {len(wins)/len(empty_trades)*100:.1f}%")
        print(f"  Total PnL: ${total_pnl:,.2f}")
        print(f"  Avg win: ${np.mean([t[4] for t in wins]) if wins else 0:,.2f}")
        print(f"  Avg loss: ${np.mean([t[4] for t in losses]) if losses else 0:,.2f}")
        print(f"")
        print(f"  Recent trades:")
        for t in empty_trades[-5:]:
            print(f"    {t[0]:12s}  entry={t[1]:>8.2f}  exit={t[2]:>8.2f}  PnL=${t[4]:>8.2f}  {t[3]}")

    print(f"")
    print(f"{'=' * 70}")
    print(f"  SMOKE TEST PASSED" if entries + exits > 0 or feature_count > 0 else "  WARNING: No activity detected — reduce warmup or extend days")
    print(f"{'=' * 70}")

    return {
        "feature_count": feature_count,
        "signals_above": signals_above,
        "signals_below": signals_below,
        "entries": entries,
        "exits": exits,
        "trades": len(empty_trades),
        "approved": len(approved),
    }


# ── Entrypoint ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Equity Engine Smoke Test")
    parser.add_argument("--symbols", default="AAPL.US,MSFT.US,NVDA.US")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    symbols = [s.strip() for s in args.symbols.split(",")]
    run_smoke_test(symbols, args.days)


if __name__ == "__main__":
    main()