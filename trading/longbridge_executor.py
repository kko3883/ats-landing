"""
Longbridge execution layer for the ATS system.

Handles:
  - Real-time quotes (replaces yfinance)
  - Order submission (replaces IBKR)
  - Portfolio & position tracking
  - Paper trading for testing

Uses the Longbridge CLI for auth (OAuth 2.0), SDK for API calls.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

TRADING_DIR = Path.home() / ".hermes" / "trading"


# ── Quote Data ──────────────────────────────────────────────────────────────


def fetch_quotes(symbols: list[str]) -> pd.DataFrame:
    """
    Fetch real-time quotes for a list of symbols.

    Symbols format: 'AAPL.US', '700.HK', 'TSLA.US'
    Returns a DataFrame with columns: symbol, last, change_pct, volume, turnover
    """
    if not symbols:
        return pd.DataFrame()

    result = subprocess.run(
        ["longbridge", "quote", *symbols, "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Longbridge quote failed: {result.stderr}")

    data = json.loads(result.stdout)
    rows = []
    for q in data:
        rows.append({
            "symbol": q["symbol"],
            "last": float(q["last"]),
            "change_pct": float(q.get("change_percentage", 0)),
            "volume": int(q.get("volume", 0)),
            "turnover": float(q.get("turnover", 0)),
            "high": float(q.get("high", 0)),
            "low": float(q.get("low", 0)),
            "open": float(q.get("open", 0)),
            "prev_close": float(q.get("prev_close", 0)),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return pd.DataFrame(rows)


def fetch_klines(
    symbol: str,
    period: str = "1d",
    count: int = 120,
) -> pd.DataFrame:
    """
    Fetch historical candlestick data.

    Args:
        symbol: e.g. 'AAPL.US', '700.HK'
        period: '1m', '5m', '15m', '30m', '60m', '1d', '1w', '1M'
        count: number of candles to fetch (max 1200)

    Returns DataFrame with OHLCV data.
    """
    result = subprocess.run(
        ["longbridge", "kline", symbol, "--period", period, "--count", str(count), "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Longbridge kline failed: {result.stderr}")

    data = json.loads(result.stdout)
    if not data:
        return pd.DataFrame()

    rows = []
    for k in data:
        rows.append({
            "timestamp": k["timestamp"],
            "open": float(k["open"]),
            "high": float(k["high"]),
            "low": float(k["low"]),
            "close": float(k["close"]),
            "volume": int(k["volume"]),
            "turnover": float(k["turnover"]),
        })

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    return df


# ── Portfolio ────────────────────────────────────────────────────────────────


def get_portfolio() -> list[dict]:
    """
    Get current portfolio positions and account balance.
    Returns list of positions from Longbridge.
    """
    result = subprocess.run(
        ["longbridge", "portfolio", "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Longbridge portfolio failed: {result.stderr}")

    return json.loads(result.stdout)


def get_portfolio() -> list[dict]:
    """
    Get current portfolio overview — total assets, P/L, holdings, cash breakdown.
    """
    result = subprocess.run(
        ["longbridge", "portfolio", "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Longbridge portfolio failed: {result.stderr}")
    return json.loads(result.stdout)


def get_positions() -> list[dict]:
    """
    Get current stock (equity) positions across all sub-accounts.
    """
    result = subprocess.run(
        ["longbridge", "positions", "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Longbridge positions failed: {result.stderr}")
    return json.loads(result.stdout)


def get_assets() -> dict:
    """
    Get account assets — net assets, cash, buy power, margins, per-currency breakdown.
    """
    result = subprocess.run(
        ["longbridge", "assets", "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Longbridge assets failed: {result.stderr}")
    return json.loads(result.stdout)


# ── Orders ───────────────────────────────────────────────────────────────────


def place_order(
    symbol: str,
    side: str,        # "buy" or "sell"
    order_type: str,  # "market" or "limit"
    quantity: int,
    price: Optional[float] = None,
    tag: Optional[str] = None,
) -> dict:
    """
    Place an order through Longbridge CLI.

    Args:
        symbol: e.g. 'AAPL.US'
        side: 'buy' or 'sell'
        order_type: 'market' or 'limit'
        quantity: number of shares
        price: required for limit orders
        tag: optional label (e.g. 'alpha_gen_DELL_pullback')

    Returns order response dict.
    """
    cmd = [
        "longbridge", "order", "submit",
        "--symbol", symbol,
        "--side", side,
        "--type", order_type,
        "--quantity", str(quantity),
        "--format", "json",
    ]
    if price is not None:
        cmd.extend(["--price", str(price)])
    if tag:
        cmd.extend(["--tag", tag])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"Order submission failed: {result.stderr}")

    return json.loads(result.stdout)


def cancel_order(order_id: str) -> dict:
    """Cancel an open order by ID."""
    result = subprocess.run(
        ["longbridge", "order", "cancel", "--order-id", order_id, "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Cancel order failed: {result.stderr}")
    return json.loads(result.stdout)


def get_orders(status: Optional[str] = None) -> list[dict]:
    """Get order history. Status filter: 'open', 'filled', 'cancelled', 'rejected'."""
    cmd = ["longbridge", "order", "list", "--format", "json"]
    if status:
        cmd.extend(["--status", status])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"Order list failed: {result.stderr}")
    return json.loads(result.stdout)


# ── ATS Integration ──────────────────────────────────────────────────────────


def data_source() -> dict:
    """
    Adapter for the ATS signal engine.
    Replaces yfinance for fetching price data.

    Returns a dict mapping symbols to their current quote data.
    """
    # Read the watchlist to know which symbols to fetch
    wl_file = Path.home() / ".hermes" / "trading" / "watchlist" / "watchlist.json"
    if not wl_file.exists():
        print("  No watchlist found. Run screener first.")
        return {}

    with open(wl_file) as f:
        watchlist = json.load(f)

    # Collect all symbols from all groups
    symbols = set()
    for market_data in watchlist.values():
        if isinstance(market_data, dict):
            for group in market_data.get("groups", {}).values():
                for s in group.get("long_candidates", []):
                    symbol = s.get("symbol", "")
                    if not symbol.endswith(".US"):
                        symbol = f"{symbol}.US"
                    symbols.add(symbol)
                for s in group.get("short_candidates", []):
                    symbol = s.get("symbol", "")
                    if not symbol.endswith(".US"):
                        symbol = f"{symbol}.US"
                    symbols.add(symbol)

    if not symbols:
        return {}

    symbols_list = sorted(symbols)
    print(f"  Fetching {len(symbols_list)} quotes via Longbridge...")

    df = fetch_quotes(symbols_list)
    if df.empty:
        return {}

    result = {}
    for _, row in df.iterrows():
        sym = row["symbol"].replace(".US", "")
        result[sym] = {
            "price": row["last"],
            "change_pct": row["change_pct"],
            "volume": row["volume"],
            "high": row["high"],
            "low": row["low"],
            "open": row["open"],
        }

    return result


# ── CLI ──────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "quote":
        symbols = sys.argv[2:] if len(sys.argv) > 2 else ["AAPL.US", "TSLA.US"]
        df = fetch_quotes(symbols)
        print(df.to_string(index=False))

    elif cmd == "kline":
        symbol = sys.argv[2] if len(sys.argv) > 2 else "AAPL.US"
        df = fetch_klines(symbol)
        print(f"{symbol}: {len(df)} candles")
        print(df.tail(5).to_string(index=False))

    elif cmd == "portfolio":
        data = get_portfolio()
        print(json.dumps(data, indent=2, default=str))

    elif cmd == "positions":
        data = get_positions()
        print(json.dumps(data, indent=2, default=str))

    elif cmd == "assets":
        data = get_assets()
        print(json.dumps(data, indent=2, default=str))

    elif cmd == "order":
        action = sys.argv[2] if len(sys.argv) > 2 else "list"
        if action == "submit":
            sym = sys.argv[3]
            side = sys.argv[4]
            qty = int(sys.argv[5])
            price = float(sys.argv[6]) if len(sys.argv) > 6 else None
            result = place_order(sym, side, "limit" if price else "market", qty, price)
            print(json.dumps(result, indent=2, default=str))
        elif action == "list":
            data = get_orders()
            print(json.dumps(data, indent=2, default=str))
        elif action == "cancel":
            oid = sys.argv[3]
            result = cancel_order(oid)
            print(json.dumps(result, indent=2, default=str))

    elif cmd == "test":
        # Quick test: fetch quotes for watchlist symbols
        data = data_source()
        print(f"Fetched {len(data)} symbols")
        for sym, info in list(data.items())[:5]:
            print(f"  {sym:6s} → ${info['price']:.2f} ({info['change_pct']:+.2f}%)")

    else:
        print("Usage: python longbridge_executor.py <command>")
        print("  quote [symbols...]    Fetch real-time quotes")
        print("  kline <symbol>        Fetch historical candles")
        print("  portfolio             Get portfolio positions")
        print("  account               Get account summary")
        print("  order submit ...      Place an order")
        print("  order list            List orders")
        print("  order cancel <id>     Cancel an order")
        print("  test                  Quick ATS integration test")
