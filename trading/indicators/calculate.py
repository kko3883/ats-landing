"""
Indicator calculator — 7 independent indicators, no redundancy.

Each uses different math and measures a different dimension:
  1. MACD        — Trend direction (EMA crossover)
  2. ADX         — Trend strength (directional movement)
  3. RSI(14)     — Momentum speed (gain/loss velocity)
  4. Stochastic  — Range position (% of high-low range)
  5. MFI(14)     — Volume conviction (money flow ratio)
  6. OBV slope   — Accumulation (cumulative volume trend)
  7. Bollinger %B — Volatility extension (SD band position)

Aggregate: weighted vote → Strong Buy / Buy / Hold / Sell / Strong Sell
"""

import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ── Config ────────────────────────────────────────────────────────────────

TRADING_DIR = Path.home() / ".hermes" / "trading"
PROJECT_REF = "nwatzlrmoefluymhqgwi"
REST_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1"

# Bar size
BAR_SIZE = "1h"       # Longbridge format: 1m 5m 15m 30m 1h day week
MIN_BARS = 60  # Need enough data for all indicators (MACD needs ~33 + buffer)

# Sign colours for dashboard
SIGN_COLOURS = {
    "strong_buy": {"text": "text-emerald-300", "bg": "bg-emerald-900/40", "border": "border-emerald-500"},
    "buy": {"text": "text-emerald-400", "bg": "bg-emerald-900/20", "border": "border-emerald-700"},
    "hold": {"text": "text-yellow-300", "bg": "bg-yellow-900/20", "border": "border-yellow-700"},
    "sell": {"text": "text-red-400", "bg": "bg-red-900/20", "border": "border-red-700"},
    "strong_sell": {"text": "text-red-300", "bg": "bg-red-900/40", "border": "border-red-500"},
}


# ── Keychain helper ───────────────────────────────────────────────────────

def _get_keychain(service="ats-supabase", account="service_role") -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service, "-a", account],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _supabase_headers():
    key = _get_keychain()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ── Data: Fetch 60min OHLCV from Longbridge ───────────────────────────────

def fetch_60m_bars(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Fetch 60min bars for a list of symbols via Longbridge CLI.

    Returns {symbol: DataFrame with columns [open, high, low, close, volume]}.
    """
    result = {}
    for sym in symbols:
        try:
            r = subprocess.run(
                ["longbridge", "kline", sym, "--period", BAR_SIZE, "--count", str(MIN_BARS + 30), "--format", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                print(f"  ⚠ {sym}: Longbridge failed ({r.stderr[:60]})")
                continue
            data = json.loads(r.stdout)
            if not data:
                continue
            rows = []
            for k in data:
                rows.append({
                    "timestamp": pd.to_datetime(k["time"]),
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": int(k["volume"]),
                })
            df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
            if len(df) >= MIN_BARS:
                result[sym] = df
            else:
                print(f"  ⚠ {sym}: only {len(df)} bars (need {MIN_BARS})")
        except Exception as e:
            print(f"  ⚠ {sym}: {e}")
    return result


# ── Individual indicator functions ────────────────────────────────────────
# Each returns a dict: {value: float, signal: 'buy'|'sell'|'hold'}

def compute_macd(close: pd.Series) -> tuple[float, str]:
    """MACD(12,26,9) — Trend direction."""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal
    val = float(hist.iloc[-1])
    prev = float(hist.iloc[-2]) if len(hist) > 1 else 0
    if val > 0 and prev <= 0:
        sig = "buy"       # Bullish crossover
    elif val < 0 and prev >= 0:
        sig = "sell"      # Bearish crossover
    elif val > 0:
        sig = "buy"       # Positive momentum
    elif val < 0:
        sig = "sell"      # Negative momentum
    else:
        sig = "hold"
    return val, sig


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> tuple[float, str]:
    """ADX(14) — Trend strength (≥25 trending, <20 ranging)."""
    plus_dm = high.diff()
    minus_dm = low.diff()
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_series = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.where(plus_dm > minus_dm, 0).rolling(period).mean() / atr_series.replace(0, 1e-10))
    minus_di = 100 * (minus_dm.where(minus_dm > plus_dm, 0).rolling(period).mean() / atr_series.replace(0, 1e-10))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
    adx = dx.rolling(period).mean()
    val = float(adx.iloc[-1])
    if val >= 25:
        sig = "buy"   # Trending — can trade direction
    elif val <= 20:
        sig = "hold"  # Ranging — no strong trend
    else:
        sig = "hold"
    return val, sig


def compute_rsi(close: pd.Series, period: int = 14) -> tuple[float, str]:
    """RSI(14) — Momentum speed. <30 oversold (buy), >70 overbought (sell)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_g = gain.rolling(period).mean()
    avg_l = loss.rolling(period).mean()
    rs = avg_g / avg_l.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    val = float(rsi.iloc[-1])
    if val < 30:
        sig = "buy"
    elif val > 70:
        sig = "sell"
    else:
        sig = "hold"
    return val, sig


def compute_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                       k_period: int = 14, d_period: int = 3) -> tuple[float, float, str]:
    """Stochastic %K(%D) — Range position. <20 oversold, >80 overbought."""
    low_k = low.rolling(k_period).min()
    high_k = high.rolling(k_period).max()
    k = 100 * (close - low_k) / (high_k - low_k).replace(0, 1e-10)
    d = k.rolling(d_period).mean()
    val_k = float(k.iloc[-1])
    val_d = float(d.iloc[-1]) if len(d) > 0 else val_k
    if val_k < 20:
        sig = "buy"
    elif val_k > 80:
        sig = "sell"
    else:
        sig = "hold"
    return val_k, val_d, sig


def compute_mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series,
                period: int = 14) -> tuple[float, str]:
    """MFI(14) — Volume-weighted RSI. <20 oversold, >80 overbought."""
    typical = (high + low + close) / 3
    raw = typical * volume
    flow = raw.diff()
    pos = flow.where(flow > 0, 0).rolling(period).sum()
    neg = (-flow.where(flow < 0, 0)).rolling(period).sum()
    ratio = pos / neg.replace(0, 1e-10)
    mfi = 100 - (100 / (1 + ratio))
    val = float(mfi.iloc[-1])
    if val < 20:
        sig = "buy"
    elif val > 80:
        sig = "sell"
    else:
        sig = "hold"
    return val, sig


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """ATR(14) — Average True Range (volatility magnitude)."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def compute_obv_slope(close: pd.Series, volume: pd.Series, period: int = 14) -> tuple[float, str]:
    """OBV slope over N periods — accumulation/distribution."""
    obv = (volume * (close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0)))).cumsum()
    if len(obv) < period:
        return 0.0, "hold"
    y = obv.iloc[-period:].values.astype(float)
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    if slope > 0:
        sig = "buy"
    elif slope < 0:
        sig = "sell"
    else:
        sig = "hold"
    return float(slope), sig


def compute_bb_percent_b(close: pd.Series, period: int = 20, std: float = 2.0) -> tuple[float, str]:
    """Bollinger %B — position within bands. <0 (below lower) buy, >1 (above upper) sell."""
    mid = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    upper = mid + sigma * std
    lower = mid - sigma * std
    bb = (close - lower) / (upper - lower).replace(0, 1e-10)
    val = float(bb.iloc[-1])
    if val < 0:
        sig = "buy"
    elif val > 1:
        sig = "sell"
    else:
        sig = "hold"
    return val, sig


# ── Aggregation ───────────────────────────────────────────────────────────

def compute_composite(results: dict) -> tuple[int, str]:
    """Weighted vote from all indicators. Returns (score, label)."""
    weights = {
        "macd": 2,      # Trend direction (primary)
        "adx": 1,       # Trend strength (confirming)
        "rsi": 2,       # Momentum (primary)
        "stoch": 1,     # Range position (confirming)
        "mfi": 2,       # Volume momentum (primary)
        "obv": 1,       # Accumulation trend (confirming)
        "bb": 1,        # Volatility extension (edge case)
    }
    score = 0
    max_possible = sum(w for w in weights.values())
    for key, weight in weights.items():
        sig = results.get(key, {}).get("signal", "hold")
        if sig == "buy":
            score += weight
        elif sig == "sell":
            score -= weight
    # Normalize to [-7, +7] scale (max possible = 10, but cap at 7 for readability)
    normalized = round(score * 7 / max_possible)
    normalized = max(-7, min(7, normalized))
    if normalized >= 4:
        label = "strong_buy"
    elif normalized >= 1:
        label = "buy"
    elif normalized <= -4:
        label = "strong_sell"
    elif normalized <= -1:
        label = "sell"
    else:
        label = "hold"
    return normalized, label


# ── Main calculation for one symbol ───────────────────────────────────────

def calculate_for_symbol(df: pd.DataFrame) -> dict:
    """Run all 7 indicators on a DataFrame. Returns dict of results."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    macd_val, macd_sig = compute_macd(close)
    adx_val, adx_sig = compute_adx(high, low, close)
    rsi_val, rsi_sig = compute_rsi(close)
    stoch_k, stoch_d, stoch_sig = compute_stochastic(high, low, close)
    mfi_val, mfi_sig = compute_mfi(high, low, close, volume)
    obv_val, obv_sig = compute_obv_slope(close, volume)
    bb_val, bb_sig = compute_bb_percent_b(close)

    # ATR + entry/stop
    atr_val = compute_atr(high, low, close)
    last_price = float(close.iloc[-1])
    stop_loss = round(last_price - atr_val * 1.5, 2)
    take_profit = round(last_price + atr_val * 3.0, 2)

    results = {
        "macd": {"value": round(macd_val, 4), "signal": macd_sig},
        "adx": {"value": round(adx_val, 2), "signal": adx_sig},
        "rsi": {"value": round(rsi_val, 2), "signal": rsi_sig},
        "stoch": {"value": round(stoch_k, 2), "signal": stoch_sig},
        "mfi": {"value": round(mfi_val, 2), "signal": mfi_sig},
        "obv": {"value": round(obv_val, 2), "signal": obv_sig},
        "bb": {"value": round(bb_val, 4), "signal": bb_sig},
    }

    score, label = compute_composite(results)
    results["composite"] = {"score": score, "signal": label}
    results["atr"] = {"value": round(atr_val, 2)}
    results["stop_loss"] = {"value": stop_loss}
    results["take_profit"] = {"value": take_profit}

    return results


# ── Pub to Supabase ───────────────────────────────────────────────────────

def publish(symbol_rows: list[dict]):
    """Write indicator results to Supabase indicator_signals table."""
    if not symbol_rows:
        return 0
    headers = _supabase_headers()
    resp = requests.post(
        f"{REST_URL}/indicator_signals",
        headers={**headers, "Prefer": "return=representation"},
        json=symbol_rows,
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()
    count = len(result) if isinstance(result, list) else 0
    print(f"  Supabase: {count} indicator rows written")
    return count


# ── Helpers ───────────────────────────────────────────────────────────────

def _market_from_symbol(sym: str) -> str:
    """Infer market from symbol suffix."""
    if sym.endswith(".HK"):
        return "hk"
    elif sym.endswith(".US"):
        return "us"
    return "us"


def _ticker_name(sym: str, stock_names: dict) -> str:
    """Lookup human-readable name from stock_names dict."""
    # Stock names may be keyed by symbol with or without suffix
    return stock_names.get(sym, stock_names.get(sym.replace(".HK", "").replace(".US", ""), ""))


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Calculate 7-dimension indicators for tickers")
    parser.add_argument("symbols", nargs="*", help="Symbols to calculate (e.g. AAPL.US 700.HK)")
    parser.add_argument("--from-signals", action="store_true", help="Calculate for all tickers in signals table")
    parser.add_argument("--from-watchlist-hk", action="store_true", help="Calculate for HK watchlist tickers")
    parser.add_argument("--all", action="store_true", help="Combined: signals + HK watchlist")
    args = parser.parse_args()

    # Collect symbols
    symbols = list(args.symbols) if args.symbols else []

    if args.from_signals or args.all:
        headers = _supabase_headers()
        resp = requests.get(f"{REST_URL}/signals?select=ticker&limit=100", headers=headers, timeout=10)
        if resp.ok:
            for row in resp.json():
                t = row.get("ticker", "")
                if t and t not in symbols:
                    # Add market suffix if missing
                    if not t.endswith((".US", ".HK")):
                        t = f"{t}.US"
                    symbols.append(t)

    if args.from_watchlist_hk or args.all:
        headers = _supabase_headers()
        resp = requests.get(f"{REST_URL}/watchlist_hk?select=symbol&limit=100", headers=headers, timeout=10)
        if resp.ok:
            for row in resp.json():
                s = row.get("symbol", "")
                if s and s not in symbols:
                    symbols.append(s)

    if not symbols:
        print("No symbols to calculate. Provide symbols or use --from-signals / --from-watchlist-hk")
        sys.exit(1)

    # Deduplicate
    symbols = list(dict.fromkeys(symbols))
    print(f"Calculating indicators for {len(symbols)} symbols on {BAR_SIZE} bars...")

    # Load stock names for reference
    stock_names = {}
    try:
        with open(TRADING_DIR / "watchlist" / "hk_stock_names.json") as f:
            stock_names.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Fetch data
    bars = fetch_60m_bars(symbols)
    if not bars:
        print("No data fetched. Check your Longbridge connection.")
        sys.exit(1)

    print(f"  Data fetched for {len(bars)} symbols")

    # Calculate
    rows = []
    for sym, df in bars.items():
        try:
            results = calculate_for_symbol(df)
            mkt = _market_from_symbol(sym)
            name = _ticker_name(sym, stock_names)
            rows.append({
                "ticker": sym,
                "market": mkt,
                "bar_size": BAR_SIZE,
                "calculated_at": datetime.now(timezone.utc).isoformat(),
                "macd_value": results["macd"]["value"],
                "macd_signal": results["macd"]["signal"],
                "adx_value": results["adx"]["value"],
                "adx_signal": results["adx"]["signal"],
                "rsi_value": results["rsi"]["value"],
                "rsi_signal": results["rsi"]["signal"],
                "stoch_k": results["stoch"]["value"],
                "stoch_d": results["stoch"].get("d", 0),
                "stoch_signal": results["stoch"]["signal"],
                "mfi_value": results["mfi"]["value"],
                "mfi_signal": results["mfi"]["signal"],
                "obv_slope": results["obv"]["value"],
                "obv_signal": results["obv"]["signal"],
                "bb_percent_b": results["bb"]["value"],
                "bb_signal": results["bb"]["signal"],
                "composite_score": results["composite"]["score"],
                "composite_signal": results["composite"]["signal"],
                "atr_value": results["atr"]["value"],
                "stop_loss": results["stop_loss"]["value"],
                "take_profit": results["take_profit"]["value"],
                "ticker_name": name,
            })
            sig = results["composite"]["signal"]
            print(f"  {sym:20s} → {sig:>12s} (score: {results['composite']['score']:+d})")
        except Exception as e:
            print(f"  ⚠ {sym}: calculation error — {e}")

    if not rows:
        print("No results to publish.")
        return

    # Publish to Supabase
    n = publish(rows)
    print(f"Done. {n} indicator rows written for {len(set(r['ticker'] for r in rows))} symbols.")
    print()
    print("Dashboard will show these on the next page load.")


if __name__ == "__main__":
    main()
