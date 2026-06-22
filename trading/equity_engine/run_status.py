#!/usr/bin/env python3
"""
Interactive engine status viewer — no broker needed.

Reads the engine state file and trades log and prints a live dashboard.

Usage:
    python equity_engine/run_status.py              # one-shot status
    python equity_engine/run_status.py --watch 10    # refresh every 10 seconds
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Add parent to path for direct execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from equity_engine.config import STATE_FILE, TRADES_LOG

# ── Colour helpers ──────────────────────────────────────────────────────────

C = {
    "GREEN": "\033[92m",
    "RED": "\033[91m",
    "YELLOW": "\033[93m",
    "CYAN": "\033[96m",
    "BOLD": "\033[1m",
    "RESET": "\033[0m",
}

def c(text: str, colour: str) -> str:
    return f"{C[colour]}{text}{C['RESET']}"


# ── Data readers ────────────────────────────────────────────────────────────

def read_state() -> dict | None:
    """Read the engine state.json file."""
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def read_trades(tail: int = 50) -> list[dict]:
    """Read the last N trades from trades.jsonl."""
    if not TRADES_LOG.exists():
        return []
    try:
        with open(TRADES_LOG) as f:
            lines = f.readlines()
        trades = []
        for line in lines[-tail:]:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return trades
    except OSError:
        return []


def read_smoke_state() -> dict | None:
    """Read the smoke test state file."""
    path = Path("/tmp/eq_smoke_state.json")
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── Display ─────────────────────────────────────────────────────────────────

def print_header():
    print(c("╔══════════════════════════════════════════════════════════════╗", "CYAN"))
    print(c("║        EQUITY TRADING ENGINE — STATUS                       ║", "BOLD"))
    print(c("╚══════════════════════════════════════════════════════════════╝", "CYAN"))
    print()


def print_state(state: dict | None):
    """Print engine state."""
    if state is None:
        print(c("  No state file found — engine is not running.", "YELLOW"))
        print(f"  Expected at: {STATE_FILE}")
        return

    ts = state.get("timestamp", "unknown")
    equity = state.get("equity", 0)
    positions = state.get("positions", {})
    trade_count = state.get("trade_count", 0)

    print(f"  Last update:  {ts}")
    print(f"  Equity:       ${equity:,.2f}")
    print(f"  Positions:    {len(positions)} active")
    print(f"  Total trades: {trade_count}")
    print()

    if positions:
        print(f"  {'Symbol':<12s} {'Side':<6s} {'Entry':>10s} {'Stop':>10s} {'Trail':>10s} {'Qty':>6s} {'Held'}")
        print(f"  {'─' * 70}")
        for sym, pos in positions.items():
            entry = pos.get("entry_price", 0)
            stop = pos.get("stop_loss", 0)
            trail = pos.get("trailing_stop", 0)
            qty = pos.get("quantity", 0)
            bars = pos.get("bars_held", 0)
            side = pos.get("side", "LONG")
            notes = pos.get("notes", "")
            tag = f" [{notes}]" if notes else ""
            print(f"  {sym:<12s} {side:<6s} {entry:>10.2f} {stop:>10.2f} {trail:>10.2f} {qty:>6d} {bars:>4d}{tag}")
        print()
    else:
        print("  No active positions.")
        print()

    # Regime info from recent signals
    if "counters" in state:
        counters = state["counters"]
        print(f"  ── Counters ──")
        for k, v in counters.items():
            print(f"    {k}: {v}")
        print()


def print_trades(trades: list[dict]):
    """Print recent trade log entries."""
    if not trades:
        print("  No trades recorded yet.")
        return

    fills = [t for t in trades if t.get("event") in ("fill", "close", None) or "exit" in str(t.get("event", "")).lower()]
    entries = [t for t in trades if "entry" in str(t.get("event", "")).lower() or "ENTRY" in str(t.get("event", "")).upper()]
    exits = [t for t in trades if "exit" in str(t.get("event", "")).lower() or "EXIT" in str(t.get("event", "")).upper()]

    print(f"  ── Recent Trade Log ({len(trades)} entries) ──")
    recent = trades[-10:]
    for t in recent:
        ts = t.get("ts", "")[:19]
        event = t.get("event", "")
        sym = t.get("symbol", "")
        pnl = t.get("pnl", "")
        reason = t.get("reason", "")
        detail = ""
        if pnl:
            pnl_val = float(pnl) if not isinstance(pnl, str) else 0
            colour = "GREEN" if pnl_val > 0 else "RED"
            detail = c(f" PnL=${pnl_val:,.2f}", colour)
        if reason:
            detail += f"  [{reason}]"
        print(f"  {ts}  {event:12s}  {sym:10s}{detail}")
    print()


def print_file_locations():
    """Show where persistent state lives."""
    print(f"  ── File locations ──")
    print(f"  State file:   {STATE_FILE}")
    print(f"  Trades log:   {TRADES_LOG}")
    print(f"  Models dir:   {Path(__file__).parent / 'models'}")
    print(f"  Config dir:   {Path(__file__).parent / 'configs'}")
    print(f"  Data cache:   {Path.home() / '.hermes' / 'equity_engine' / 'hist_cache'}")
    print()


# ── Entrypoint ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Equity Engine Status Viewer")
    parser.add_argument("--watch", type=int, default=0,
                        help="Refresh interval in seconds (0 = one-shot)")
    parser.add_argument("--trades", type=int, default=50,
                        help="Number of trade log entries to show")
    args = parser.parse_args()

    if args.watch > 0:
        print(c("Watching engine state — Ctrl+C to exit", "CYAN"))
        try:
            while True:
                os.system("clear" if sys.platform != "win32" else "cls")
                print_header()
                state = read_state() or read_smoke_state()
                print_state(state)
                trades = read_trades(args.trades)
                print_trades(trades)
                print_file_locations()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        print_header()
        state = read_state() or read_smoke_state()
        print_state(state)
        trades = read_trades(args.trades)
        print_trades(trades)
        print_file_locations()


if __name__ == "__main__":
    main()