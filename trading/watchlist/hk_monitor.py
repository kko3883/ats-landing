"""
HK Intraday Signal Monitor — short-lived cron pattern.

Runs every 5 minutes during HK market hours (09:30-12:00, 13:00-16:00 HKT).
Each invocation:
  1. Checks if within trading hours
  2. Loads today's HK watchlist from Supabase
  3. Polls live quotes via Longbridge
  4. Loads cached daily kline for indicator computation
  5. Checks entry conditions per strategy (from config_strategies.yaml)
  6. Outputs signals — cron job delivers to Telegram

Usage:
    python -m watchlist.hk_monitor

Cron: */5 9-16 * * 1-5  (script self-limits to market hours)
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR as TRADING_DIR
from .strategies.registry import REGISTRY, evaluate_conditions
from .strategies.indicators import rsi, sma, ema, bb_width

CACHE_DIR = TRADING_DIR / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── HK Market Hours (HKT = UTC+8) ──────────────────────────────────────────

HKT_OFFSET = timedelta(hours=8)


def hkt_now() -> datetime:
    """Current time in Hong Kong timezone."""
    return datetime.now(timezone.utc) + HKT_OFFSET


def is_market_hours() -> tuple[bool, str]:
    """Check if current HKT time is within HK trading sessions.

    Returns (in_session, reason).
    Morning: 09:30-12:00
    Afternoon: 13:00-16:00
    """
    now = hkt_now()
    # Only check on weekdays
    if now.weekday() >= 5:
        return False, "Weekend"
    minute_of_day = now.hour * 60 + now.minute
    morning_start = 9 * 60 + 30
    morning_end = 12 * 60
    afternoon_start = 13 * 60
    afternoon_end = 16 * 60

    if morning_start <= minute_of_day <= morning_end:
        return True, "Morning session"
    elif afternoon_start <= minute_of_day <= afternoon_end:
        return True, "Afternoon session"
    elif minute_of_day < morning_start:
        return False, "Pre-market"
    elif morning_end < minute_of_day < afternoon_start:
        return False, "Lunch break"
    else:
        return False, "Market closed"


# ── Supabase Reader ────────────────────────────────────────────────────────


def _get_service_role() -> str:
    """Fetch Supabase service_role key from macOS Keychain."""
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", "ats-supabase", "-a", "service_role"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _supabase_get(url_suffix: str, params: dict | None = None) -> list[dict]:
    """GET from Supabase REST API with service_role."""
    import requests
    svc = _get_service_role()
    url = f"https://nwatzlrmoefluymhqgwi.supabase.co/rest/v1/{url_suffix}"
    resp = requests.get(url, headers={
        "apikey": svc,
        "Authorization": f"Bearer {svc}",
        "Accept": "application/json",
    }, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _supabase_post(url_suffix: str, rows: list[dict]) -> list[dict]:
    """INSERT into Supabase REST API with service_role."""
    import requests
    svc = _get_service_role()
    url = f"https://nwatzlrmoefluymhqgwi.supabase.co/rest/v1/{url_suffix}"
    resp = requests.post(url, headers={
        "apikey": svc,
        "Authorization": f"Bearer {svc}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }, json=rows, timeout=15)
    resp.raise_for_status()
    return resp.json()


def load_latest_hk_watchlist() -> list[dict]:
    """Load the most recent batch of HK watchlist candidates from Supabase."""
    rows = _supabase_get("watchlist_hk", {"limit": 50, "order": "id.desc"})
    if not rows:
        return []
    # Take only the most recent generated_at batch
    latest_gen = rows[0]["generated_at"]
    return [r for r in rows if r["generated_at"] == latest_gen]


# ── Longbridge Data ────────────────────────────────────────────────────────


def _run_longbridge(args: list[str]) -> str | None:
    """Run a longbridge CLI command, return stdout."""
    try:
        result = subprocess.run(
            ["longbridge"] + args,
            capture_output=True, text=True, timeout=20,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_live_quotes(symbols: list[str]) -> dict[str, dict]:
    """Fetch live quote for one or more symbols via longbridge CLI.

    Returns {symbol: {last_done, change_pct, volume, turnover, high, low, open}}.
    """
    if not symbols:
        return {}
    result = _run_longbridge(["quote"] + symbols + ["--format", "json"])
    if not result:
        return {}
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return {}

    quotes = {}
    if isinstance(data, list):
        for q in data:
            sym = q.get("symbol", "")
            if sym:
                quotes[sym] = {
                    "last_done": _safe_float(q.get("last_done")),
                    "open": _safe_float(q.get("open")),
                    "high": _safe_float(q.get("high")),
                    "low": _safe_float(q.get("low")),
                    "volume": _safe_float(q.get("volume")),
                    "turnover": _safe_float(q.get("turnover")),
                    "prev_close": _safe_float(q.get("prev_close")),
                    "change_pct": _safe_float(q.get("change_pct")),
                    "trade_status": q.get("trade_status", ""),
                }
    return quotes


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def load_daily_kline_file(symbol: str) -> pd.DataFrame | None:
    """Load cached daily kline for a symbol from parquet cache.

    Returns a DataFrame with columns ['close', 'volume'] (index=date).
    None if not cached or fetch fails.
    """
    import os
    # Check for parquet cache in various locations
    cache_paths = [
        TRADING_DIR / ".cache" / f"hk_prices.parquet",
    ]
    for cp in cache_paths:
        if cp.exists():
            try:
                data = pd.read_parquet(cp)
                if not isinstance(data.columns, pd.MultiIndex):
                    continue
                if data.columns.names == ["Price", "Ticker"]:
                    # Try to extract the symbol's Close + Volume
                    try:
                        close = data.xs("Close", level="Price", axis=1)
                        volume = data.xs("Volume", level="Price", axis=1)
                        if symbol in close.columns and symbol in volume.columns:
                            result = pd.DataFrame({
                                "close": close[symbol].dropna(),
                                "volume": volume[symbol].dropna(),
                            })
                            return result
                    except KeyError:
                        continue
                elif data.columns.names == ["Price", "Ticker"] or (
                    data.columns.get_level_values(0).dtype == "object" and
                    "Close" in data.columns.get_level_values(0)
                ):
                    close = data.xs("Close", axis=1, level=0)
                    try:
                        volume = data.xs("Volume", axis=1, level=0)
                    except KeyError:
                        volume = None
                    if symbol in close.columns:
                        result = pd.DataFrame({"close": close[symbol].dropna()})
                        if volume is not None and symbol in volume.columns:
                            result["volume"] = volume[symbol].dropna()
                        return result
            except Exception:
                continue

    # Fallback: fetch via Longbridge and cache
    result = _run_longbridge(["kline", "history", symbol, "--start", "2025-10-01", "--format", "json"])
    if not result:
        return None
    try:
        candles = json.loads(result)
        if not isinstance(candles, list):
            return None
        records = []
        for c in candles:
            try:
                dt = datetime.strptime(c["time"], "%Y-%m-%d %H:%M:%S")
                records.append({
                    "date": dt,
                    "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
            except (KeyError, ValueError):
                continue
        if not records:
            return None
        df = pd.DataFrame(records).set_index("date").sort_index()
        # Cache it
        df.to_parquet(TRADING_DIR / ".cache" / f"daily_{symbol.replace('.', '_')}.parquet")
        return df
    except (json.JSONDecodeError, ValueError):
        return None


# ── Strategy Config ─────────────────────────────────────────────────────────

HK_STRATEGIES = {
    "hk_pullback": {
        "name": "HK Pullback Entry",
        "bucket": "alpha_gen",
        "description": "Buy HK weakness. RSI oversold + near 50-SMA. Thresholds relaxed for HK volatility.",
        "entry": {
            "conditions": [
                {"indicator": "pullback", "rsi_period": 14, "rsi_threshold": 40,
                 "sma_period": 50, "sma_proximity": 0.04},
            ]
        },
        "sizing": {
            "stop_loss_pct": 0.06,
            "take_profit_pct": 0.10,
            "max_position_pct": 0.10,
        },
    },
    "hk_breakout": {
        "name": "HK Breakout",
        "bucket": "alpha_gen",
        "description": "HK 20-day high breakout with volume surge. Tightened for lower absolute prices.",
        "entry": {
            "conditions": [
                {"indicator": "breakout", "lookback": 20, "volume_multiple": 1.5},
            ]
        },
        "sizing": {
            "stop_loss_pct": 0.07,
            "take_profit_pct": 0.12,
            "max_position_pct": 0.12,
        },
    },
    "hk_golden_cross": {
        "name": "HK Golden Cross",
        "bucket": "alpha_gen",
        "description": "20/50 SMA crossover for HK stocks — trend confirmation on Hang Seng components.",
        "entry": {
            "conditions": [
                {"indicator": "sma_crossover", "fast": 20, "slow": 50},
                {"indicator": "price_above_sma", "period": 200},
            ]
        },
        "sizing": {
            "stop_loss_pct": 0.08,
            "take_profit_pct": 0.15,
            "max_position_pct": 0.10,
        },
    },
}

# Map indicator names to their functions (subset of registry)
HK_INDICATORS = {k: v for k, v in REGISTRY.items()}

# ── Signal Detection ────────────────────────────────────────────────────────


def check_symbol(symbol: str, quote: dict, ohlcv: pd.DataFrame | None,
                 strategy_name: str, strategy_cfg: dict) -> dict | None:
    """Run entry conditions for a single symbol against cached daily data.

    ohlcv: DataFrame with 'close' column (and optionally 'volume' column).
    Returns a signal dict if conditions pass, None otherwise.
    """
    if ohlcv is None or len(ohlcv) < 60:
        return None

    close = ohlcv["close"].dropna()
    volume = ohlcv["volume"].dropna() if "volume" in ohlcv.columns else None

    entry_cfg = strategy_cfg.get("entry", {})
    conditions = entry_cfg.get("conditions", [])

    passed, ctx = evaluate_conditions(conditions, close, volume)
    if passed:
        price = float(close.iloc[-1])
        sizing = strategy_cfg.get("sizing", {})
        signal = {
            "strategy_name": strategy_name,
            "symbol": symbol,
            "action": "enter_long",
            "direction": "long",
            "bucket": strategy_cfg.get("bucket", "alpha_gen"),
            "market": "hk",
            "price": price,
            "quote": quote.get("last_done") if quote else None,
            "context": ctx,
            "stop_loss": round(price * (1 - sizing.get("stop_loss_pct", 0.06)), 2) if sizing.get("stop_loss_pct") else None,
            "take_profit": round(price * (1 + sizing.get("take_profit_pct", 0.10)), 2) if sizing.get("take_profit_pct") else None,
        }
        return signal
    return None


# ── Dedup — avoid alerting on the same signal repeatedly ────────────────────


def _already_signaled(symbol: str, strategy: str, minutes_window: int = 60) -> bool:
    """Check if Supabase signals table has a matching signal from recent hours."""
    try:
        rows = _supabase_get("signals", {
            "limit": 5,
            "order": "created_at.desc",
        })
        for r in rows:
            if r.get("ticker") == symbol and r.get("signal_json", {}).get("strategy_name") == strategy:
                created = r.get("created_at", "")
                try:
                    sig_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_minutes = (datetime.now(timezone.utc) - sig_time).total_seconds() / 60
                    if age_minutes < minutes_window:
                        return True
                except ValueError:
                    continue
        return False
    except Exception:
        return False  # On error, allow signal


def save_signal(signal: dict):
    """Write signal to Supabase `signals` table.

    Map strategy bucket names to DB values (same mapping as supabase_writer.py).
    """
    bucket_map = {"alpha_gen": "alpha", "base_yield": "base_yield", "convexity": "convexity"}
    db_bucket = bucket_map.get(signal.get("bucket", ""), "alpha")

    row = {
        "ticker": signal["symbol"],
        "direction": "LONG",
        "bucket": db_bucket,
        "vix_zone": "hk",
        "signal_json": signal,
    }
    try:
        _supabase_post("signals", [row])
        print(f"  → Written to Supabase")
    except Exception as e:
        print(f"  → Supabase write failed: {e}")


# ── Alert Formatting ────────────────────────────────────────────────────────


def format_alert(signal: dict, strategy_name: str) -> str:
    """Format a signal as a readable alert message."""
    sym = signal["symbol"]
    price = signal.get("quote") or signal.get("price", 0)
    strategy = strategy_name.replace("hk_", "HK ").replace("_", " ")
    ctx = signal.get("context", {})
    sl = signal.get("stop_loss")
    tp = signal.get("take_profit")

    lines = [
        f"🚨 HK Signal — {strategy}",
        f"",
        f"**{sym}**",
        f"Price: {price:.2f}" if isinstance(price, (int, float)) else f"Price: {price}",
    ]

    # Add context details
    if ctx:
        for k, v in ctx.items():
            if isinstance(v, bool):
                lines.append(f"• {k}: {'✅' if v else '❌'}")
            elif isinstance(v, (int, float)):
                lines.append(f"• {k}: {v:.2f}")
            else:
                lines.append(f"• {k}: {v}")

    if sl:
        lines.append(f"Stop Loss: {sl:.2f} ({abs(round((sl/price - 1)*100, 1))}%)")
    if tp:
        lines.append(f"Take Profit: {tp:.2f} ({'+' if tp > price else ''}{round((tp/price - 1)*100, 1)}%)")

    lines.append("")
    lines.append("Bucket: alpha_gen · Market: HK")

    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    """Main entry point — called every 5 minutes by cron."""
    # 1. Check market hours
    in_session, reason = is_market_hours()
    if not in_session:
        print(f"⏰ {reason} — skipping")
        return

    print(f"⏰ HK market: {reason}")
    print(f"🕐 Time: {hkt_now().strftime('%H:%M:%S')} HKT")

    # 2. Load watchlist
    watchlist = load_latest_hk_watchlist()
    if not watchlist:
        print("No HK watchlist entries found in Supabase.")
        return

    symbols = sorted(set(r["symbol"] for r in watchlist))
    longs = [r for r in watchlist if r["candidate_type"] == "long"]
    shorts = [r for r in watchlist if r["candidate_type"] == "short"]
    print(f"📋 Watchlist: {len(longs)} long + {len(shorts)} short candidates")

    # 3. Fetch live quotes
    print(f"📊 Polling {len(symbols)} symbols...")
    quotes = get_live_quotes(symbols)
    print(f"   Received {len(quotes)} quotes")

    # 4. Load daily kline for each symbol
    #    We cache it at the start of each trading day, then reuse
    daily_cache_path = CACHE_DIR / "hk_daily_cache.parquet"
    daily_data: dict[str, pd.DataFrame] = {}

    cache_valid = False
    if daily_cache_path.exists():
        try:
            cache_df = pd.read_parquet(daily_cache_path)
            cache_date = cache_df.attrs.get("cached_date", "")
            today = hkt_now().strftime("%Y-%m-%d")
            if cache_date == today:
                for sym in symbols:
                    if isinstance(cache_df.columns, pd.MultiIndex):
                        try:
                            close_s = cache_df["close"][sym].dropna()
                            vol_s = cache_df["volume"][sym].dropna() if "volume" in cache_df.columns.get_level_values(0) else None
                            if len(close_s) >= 60:
                                df = pd.DataFrame({"close": close_s})
                                if vol_s is not None:
                                    df["volume"] = vol_s
                                daily_data[sym] = df
                        except KeyError:
                            continue
                    else:
                        if sym in cache_df.columns:
                            s = cache_df[sym].dropna()
                            if len(s) >= 60:
                                daily_data[sym] = s
                if daily_data:
                    cache_valid = True
                    print(f"📈 Daily kline cache hit ({len(daily_data)} symbols)")
        except Exception:
            pass

    if not cache_valid:
        # Fetch daily klines from scratch
        print("📈 Fetching daily kline data (no valid cache)...")
        for sym in symbols:
            ohlcv = load_daily_kline_file(sym)
            if ohlcv is not None and len(ohlcv) >= 60:
                daily_data[sym] = ohlcv
        # Cache it
        if daily_data:
            # Build MultiIndex DataFrame: columns = (field, symbol)
            close_dict = {sym: df["close"] for sym, df in daily_data.items() if "close" in df.columns}
            vol_dict = {sym: df["volume"] for sym, df in daily_data.items() if "volume" in df.columns}
            frames = {"close": pd.DataFrame(close_dict)}
            if vol_dict:
                frames["volume"] = pd.DataFrame(vol_dict)
            cache_df = pd.concat(frames, axis=1, keys=frames.keys())
            cache_df.attrs["cached_date"] = hkt_now().strftime("%Y-%m-%d")
            cache_df.to_parquet(daily_cache_path)
            print(f"   Cached {len(daily_data)} symbols")

    # 5. Run strategies
    signals_found = []

    for strategy_name, strategy_cfg in HK_STRATEGIES.items():
        if not strategy_cfg.get("enabled", True):
            continue

        print(f"\n🔍 {strategy_cfg['name']}...")

        # Check only long candidates for these strategies
        for entry in longs:
            sym = entry["symbol"]
            if sym not in daily_data:
                continue

            if _already_signaled(sym, strategy_name):
                continue

            signal = check_symbol(sym, quotes.get(sym), daily_data[sym],
                                  strategy_name, strategy_cfg)
            if signal:
                signals_found.append(signal)
                print(f"   ✅ Signal: {sym}")
                print(format_alert(signal, strategy_name))
                print()
                save_signal(signal)

    # 6. Summary
    if signals_found:
        print(f"\n{'=' * 50}")
        print(f"🚨 {len(signals_found)} signal(s) detected!")
        for s in signals_found:
            print(f"   {s['symbol']} — {s.get('strategy_name', '?')}")
    else:
        print(f"\n✅ No new signals this poll")

    # Output summary for cron delivery
    if signals_found:
        report_lines = [f"🚨 HK Monitor: {len(signals_found)} new signal(s)"]
        for s in signals_found:
            report_lines.append(f"  {s['symbol']} — {s.get('strategy_name', '?').replace('hk_', 'HK ').replace('_', ' ')} @ {s.get('quote') or s.get('price', '?')}")
        print("\n" + "\n".join(report_lines))


if __name__ == "__main__":
    main()
