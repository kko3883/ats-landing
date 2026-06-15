#!/usr/bin/env python3
"""
Base Yield Bucket — Mechanical yield strategies (50% of capital allocation).

Three validated strategies:
  1. SPY Overnight Gap     — Buy at close, sell at open (30+ years of data)
  2. FX Carry              — Long high-yield, short low-yield on AUD/JPY, NZD/JPY
  3. QQQ Covered Calls     — Simulated covered call writing on QQQ

Each runs daily and publishes signals to Supabase (yield_signals table).

Usage:
  python base_yield.py              # Run all strategies
  python base_yield.py --strategy spy_gap    # Single strategy
  python base_yield.py --dry-run             # Preview only, no Supabase write
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ── Paths ────────────────────────────────────────────────────────────────────

TRADING_DIR = Path.home() / ".hermes" / "trading"
TRADING_DIR.mkdir(parents=True, exist_ok=True)
YIELD_FILE = TRADING_DIR / "yield_signals.json"

HKT = timezone(timedelta(hours=8))

# ── Supabase config (mirrors supabase_writer.py) ─────────────────────────────

PROJECT_REF = "nwatzlrmoefluymhqgwi"
BASE_URL = f"https://{PROJECT_REF}.supabase.co"
REST_URL = f"{BASE_URL}/rest/v1"

# Table: yield_signals (create via Supabase SQL editor if needed)
# Columns: strategy, symbol, direction, signal_score, yield_pct, entry_price,
#          stop_price, tp_price, metadata, generated_at
TABLE = "yield_signals"


def _get_service_role_key() -> str:
    """Fetch the Supabase service_role key from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", "ats-supabase", "-a", "service_role"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        # Fallback: try env var
        return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _supabase_headers() -> dict:
    key = _get_service_role_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def write_to_supabase(signals: list[dict]) -> bool:
    """Write yield signals to Supabase."""
    if not signals:
        return True
    key = _get_service_role_key()
    if not key:
        print("  ⚠️  No Supabase service_role key — skipping write.")
        return False

    headers = _supabase_headers()
    url = f"{REST_URL}/{TABLE}"

    try:
        resp = requests.post(url, json=signals, headers=headers, timeout=15)
        if resp.status_code in (200, 201):
            print(f"  ✅ Wrote {len(signals)} yield signals to Supabase.")
            return True
        else:
            print(f"  ⚠️  Supabase returned HTTP {resp.status_code}: {resp.text[:200]}")
            return False
    except requests.RequestException as e:
        print(f"  ⚠️  Supabase write failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy 1: SPY Overnight Gap
# ═══════════════════════════════════════════════════════════════════════════════

def spy_overnight_gap(period: str = "3mo") -> list[dict]:
    """
    SPY Overnight Gap Strategy.

    Mechanics: Buy SPY at market close, sell at next market open.
    The gap between close and next open has been reliably positive over 30+ years
    (average ~0.03% per day, compounding to ~7-8% annualized).

    Signal is generated when:
      - VIX is below 35 (not in crisis mode)
      - Previous day's close was not a massive drop (> -3%)
      - The 5-day overnight gap average is positive

    Returns list of yield signal dicts.
    """
    print("\n📊 Strategy 1: SPY Overnight Gap")
    try:
        spy = yf.download("SPY", period=period, interval="1d", progress=False)
        vix = yf.download("^VIX", period="5d", interval="1d", progress=False)
    except Exception as e:
        print(f"  ❌ Data fetch failed: {e}")
        return []

    if spy.empty:
        print("  ❌ No SPY data.")
        return []

    # Flatten multi-level columns from yfinance
    spy.columns = spy.columns.get_level_values(0)
    vix.columns = vix.columns.get_level_values(0) if not vix.empty else vix.columns

    # Compute overnight gaps
    spy["gap"] = spy["Open"] - spy["Close"].shift(1)
    spy["gap_pct"] = (spy["gap"] / spy["Close"].shift(1)) * 100

    latest_gap_pct = float(spy["gap_pct"].iloc[-1])
    prev_close = float(spy["Close"].iloc[-2]) if len(spy) > 1 else float(spy["Close"].iloc[-1])
    avg_gap_5d = float(spy["gap_pct"].tail(5).mean())
    yesterday_change = float(((spy["Close"].iloc[-2] - spy["Open"].iloc[-2]) / spy["Open"].iloc[-2]) * 100) if len(spy) > 2 else 0

    current_vix = float(vix["Close"].iloc[-1]) if not vix.empty else 20.0

    print(f"  SPY Close: ${prev_close:.2f}")
    print(f"  Today's gap: {latest_gap_pct:+.2f}%")
    print(f"  5-day avg gap: {avg_gap_5d:+.2f}%")
    print(f"  VIX: {current_vix:.1f}")
    print(f"  Yesterday change: {yesterday_change:+.2f}%")

    signals = []
    score = 0

    # Scoring
    if current_vix < 35:
        score += 2
        print("  ✅ VIX < 35 (not crisis)")
    else:
        print("  ⚠️  VIX >= 35 — skip")
        return []

    if yesterday_change > -3.0:
        score += 2
        print("  ✅ No crash yesterday")
    else:
        print("  ⚠️  Yesterday drop > 3% — skip")
        return []

    if avg_gap_5d > 0:
        score += 2
        print("  ✅ 5-day gap average positive")
    else:
        score += 0
        print("  ⚠️  5-day gap average negative")

    if latest_gap_pct > -0.5:
        score += 1
        print("  ✅ No large gap-down today")

    # Generate signal
    confidence = "HIGH" if score >= 6 else "MODERATE" if score >= 4 else "LOW"
    annual_yield = round(avg_gap_5d * 252, 2) if avg_gap_5d > 0 else 0

    signal = {
        "strategy": "spy_overnight_gap",
        "symbol": "SPY",
        "direction": "LONG",
        "signal_score": score,
        "confidence": confidence,
        "yield_pct": round(avg_gap_5d, 4),
        "annualized_yield_pct": annual_yield,
        "entry_price": round(prev_close, 2),
        "stop_price": round(prev_close * 0.97, 2),   # -3% stop
        "tp_price": None,                              # No target — daily rhythm
        "vix_level": round(current_vix, 1),
        "metadata": {
            "avg_gap_5d_pct": round(avg_gap_5d, 4),
            "yesterday_change_pct": round(yesterday_change, 2),
            "generated_at": datetime.now(HKT).isoformat(),
        },
        "generated_at": datetime.now(HKT).isoformat(),
    }
    signals.append(signal)

    if score >= 4:
        print(f"  🟢 SIGNAL: {confidence} ({score}/7) — Annualized: {annual_yield}%")
    else:
        print(f"  🔴 NO SIGNAL: score {score}/7 below threshold")

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy 2: FX Carry
# ═══════════════════════════════════════════════════════════════════════════════

# Long high-yield (AUD, NZD), short low-yield (JPY)
CARRY_PAIRS = {
    "AUD/JPY": {"long_ccy": "AUD", "short_ccy": "JPY", "yfinance_sym": "AUDJPY=X"},
    "NZD/JPY": {"long_ccy": "NZD", "short_ccy": "JPY", "yfinance_sym": "NZDJPY=X"},
}

# Approximate central bank rates (updated periodically)
# AUD: 4.10%, NZD: 3.50%, JPY: 0.50%  (approx mid-2026 estimates)
CARRY_RATES = {
    "AUD": 4.10,
    "NZD": 3.50,
    "JPY": 0.50,
}


def fx_carry(period: str = "1mo") -> list[dict]:
    """
    FX Carry Strategy.

    Mechanics: Long high-interest currency (AUD, NZD), short low-interest (JPY).
    Earn the interest rate differential (carry) daily. The strategy is profitable
    in trending/choppy regimes but vulnerable to sharp JPY strengthening (risk-off).

    Signal is generated when:
      - Carry spread > 2% annualized
      - Pair is above 50-day SMA (trend intact)
      - VIX < 30 (risk appetite present)

    Returns list of yield signal dicts.
    """
    print("\n📊 Strategy 2: FX Carry (AUD/JPY, NZD/JPY)")
    try:
        vix = yf.download("^VIX", period="5d", interval="1d", progress=False)
        current_vix = float(vix["Close"].iloc[-1]) if not vix.empty else 20.0
    except Exception:
        current_vix = 20.0

    print(f"  VIX: {current_vix:.1f}")

    if current_vix > 30:
        print("  ⚠️  VIX > 30 — risk-off, skip carry")
        return []

    signals = []

    for pair_name, cfg in CARRY_PAIRS.items():
        sym = cfg["yfinance_sym"]
        print(f"\n  ── {pair_name} ──")

        try:
            df = yf.download(sym, period=period, interval="1d", progress=False)
        except Exception as e:
            print(f"  ❌ Data fetch failed: {e}")
            continue

        if df.empty:
            print(f"  ❌ No data for {sym}")
            continue

        # Flatten multi-level columns from yfinance
        df.columns = df.columns.get_level_values(0)

        close = df["Close"]
        current_price = float(close.iloc[-1])

        # 50-day SMA
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else float(close.mean())
        above_sma50 = current_price > sma50

        # 14-day ATR
        high, low = df["High"], df["Low"]
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else 0.01

        # Carry spread
        long_rate = CARRY_RATES[cfg["long_ccy"]]
        short_rate = CARRY_RATES[cfg["short_ccy"]]
        carry_spread = long_rate - short_rate

        # Recent momentum
        momentum_5d = float((close.iloc[-1] / close.iloc[-5] - 1) * 100) if len(close) >= 5 else 0

        print(f"  Price: {current_price:.4f}  |  SMA50: {sma50:.4f} ({'ABOVE' if above_sma50 else 'BELOW'})")
        print(f"  Carry spread: {carry_spread:.2f}%  ({long_rate}% - {short_rate}%)")
        print(f"  ATR: {atr:.4f}  |  5d momentum: {momentum_5d:+.2f}%")

        score = 0
        reasons = []

        if carry_spread > 2.0:
            score += 3
            reasons.append(f"Spread {carry_spread:.1f}% > 2%")
        elif carry_spread > 1.0:
            score += 1
            reasons.append(f"Spread {carry_spread:.1f}% > 1%")
        else:
            reasons.append(f"Spread {carry_spread:.1f}% too narrow")

        if above_sma50:
            score += 2
            reasons.append("Above SMA50")
        else:
            reasons.append("Below SMA50")

        if momentum_5d > 0:
            score += 1
            reasons.append("5d momentum positive")
        elif momentum_5d < -2:
            score -= 1
            reasons.append("5d momentum negative")

        # Stop: 2x ATR below entry
        stop_price = round(current_price - (atr * 2.0), 4)

        confidence = "HIGH" if score >= 5 else "MODERATE" if score >= 3 else "LOW"

        signal = {
            "strategy": "fx_carry",
            "symbol": pair_name,
            "direction": "LONG",
            "signal_score": score,
            "confidence": confidence,
            "yield_pct": round(carry_spread / 365, 6),  # Daily yield
            "annualized_yield_pct": round(carry_spread, 2),
            "entry_price": round(current_price, 4),
            "stop_price": stop_price,
            "tp_price": None,
            "metadata": {
                "long_rate": long_rate,
                "short_rate": short_rate,
                "carry_spread": round(carry_spread, 2),
                "sma50": round(sma50, 4),
                "atr_14": round(atr, 6),
                "momentum_5d_pct": round(momentum_5d, 2),
                "above_sma50": above_sma50,
                "reasons": reasons,
                "generated_at": datetime.now(HKT).isoformat(),
            },
            "generated_at": datetime.now(HKT).isoformat(),
        }

        if score >= 3:
            print(f"  🟢 SIGNAL: {confidence} ({score}/6)")
        else:
            print(f"  🔴 NO SIGNAL: {score}/6")

        signals.append(signal)

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy 3: QQQ Covered Calls (Simulated)
# ═══════════════════════════════════════════════════════════════════════════════

def qqq_covered_calls(period: str = "3mo") -> list[dict]:
    """
    QQQ Covered Call Strategy (Simulated).

    Mechanics: Hold QQQ, sell out-of-the-money calls weekly. Collect premium.
    The strategy works best in choppy/flat markets.
    Fast rallies cap gains (calls get assigned), crashes need protection.

    This generates a signal with the estimated delta and strike suggestion.
    You must manually approve before executing in a real account.

    Signal is generated when:
      - QQQ is in an uptrend or range (not crashing)
      - IV is elevated (good premium)
      - The suggested OTM strike offers meaningful yield

    Returns list of yield signal dicts.
    """
    print("\n📊 Strategy 3: QQQ Covered Calls")
    try:
        qqq = yf.download("QQQ", period=period, interval="1d", progress=False)
        vix = yf.download("^VIX", period="5d", interval="1d", progress=False)
    except Exception as e:
        print(f"  ❌ Data fetch failed: {e}")
        return []

    if qqq.empty:
        print("  ❌ No QQQ data.")
        return []

    # Flatten multi-level columns
    qqq.columns = qqq.columns.get_level_values(0)
    vix.columns = vix.columns.get_level_values(0) if not vix.empty else vix.columns

    close = qqq["Close"]
    current_price = float(close.iloc[-1])
    sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else current_price
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else current_price

    # 5-day momentum
    momentum_5d = float((close.iloc[-1] / close.iloc[-5] - 1) * 100) if len(close) >= 5 else 0

    # 14-day ATR
    high, low = qqq["High"], qqq["Low"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else 0.01
    atr_pct = (atr / current_price) * 100

    current_vix = float(vix["Close"].iloc[-1]) if not vix.empty else 20.0

    above_sma20 = current_price > sma20
    above_sma50 = current_price > sma50

    print(f"  QQQ: ${current_price:.2f}  |  SMA20: ${sma20:.2f}  |  SMA50: ${sma50:.2f}")
    print(f"  ATR: ${atr:.2f} ({atr_pct:.1f}%)  |  VIX: {current_vix:.1f}")
    print(f"  5d momentum: {momentum_5d:+.2f}%")

    # Suggest OTM strike: ~5-10% above current, depending on IV
    otm_pct = 5.0 if current_vix < 20 else 7.0 if current_vix < 30 else 10.0
    suggested_strike = round(current_price * (1 + otm_pct / 100), 2)

    # Estimated premium (rough: IV * sqrt(T/365) * price * delta_factor)
    # This is a very rough estimate — real pricing needs an options model
    weekly_iv = current_vix / 100 * (7 / 365) ** 0.5
    estimated_premium = round(current_price * weekly_iv * 0.4, 2)
    estimated_yield_weekly = round(estimated_premium / current_price * 100, 2)

    score = 0
    reasons = []

    if not above_sma50:
        reasons.append("Below SMA50 — too weak for CC")
        score = 0
    else:
        reasons.append("Above SMA50")
        score += 3

    if above_sma20 and current_vix < 35:
        reasons.append("Above SMA20 + VIX OK")
        score += 2
    elif current_vix > 35:
        reasons.append(f"VIX {current_vix:.0f} too high for CC")
        score -= 2

    if estimated_yield_weekly > 0.3:
        score += 2
        reasons.append(f"Yield {estimated_yield_weekly:.2f}%/wk > 0.3%")
    elif estimated_yield_weekly > 0.1:
        score += 1
        reasons.append(f"Yield {estimated_yield_weekly:.2f}%/wk")

    if momentum_5d < 5:
        score += 1  # Not rallying too fast (less chance of assignment)
        reasons.append("Not overextended")
    elif momentum_5d > 10:
        score -= 1  # Fast rally — CC limits upside
        reasons.append("Fast rally — CC caps gains")

    confidence = "HIGH" if score >= 6 else "MODERATE" if score >= 3 else "LOW"
    annualized_yield = round(estimated_yield_weekly * 52, 2) if estimated_yield_weekly > 0 else 0

    signal = {
        "strategy": "qqq_covered_call",
        "symbol": "QQQ",
        "direction": "COVERED_CALL",
        "signal_score": score,
        "confidence": confidence,
        "yield_pct": round(estimated_yield_weekly / 100, 6),
        "annualized_yield_pct": annualized_yield,
        "entry_price": round(current_price, 2),
        "stop_price": round(current_price * 0.92, 2),  # -8% stop
        "tp_price": round(suggested_strike, 2),
        "metadata": {
            "suggested_strike": suggested_strike,
            "otm_pct": round(otm_pct, 1),
            "estimated_weekly_premium": estimated_premium,
            "estimated_weekly_yield_pct": estimated_yield_weekly,
            "annualized_yield_pct_est": annualized_yield,
            "vix_level": round(current_vix, 1),
            "atr_14": round(atr, 2),
            "atr_pct": round(atr_pct, 2),
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "momentum_5d_pct": round(momentum_5d, 2),
            "reasons": reasons,
            "generated_at": datetime.now(HKT).isoformat(),
        },
        "generated_at": datetime.now(HKT).isoformat(),
    }

    if score >= 3:
        print(f"  🟢 SIGNAL: {confidence} ({score}/8) — Strike: ${suggested_strike}, Est yield: {estimated_yield_weekly:.2f}%/wk")
    else:
        print(f"  🔴 NO SIGNAL: {score}/8")

    return [signal]


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Base Yield Bucket — Mechanical yield strategies")
    parser.add_argument("--strategy", choices=["spy_gap", "fx_carry", "qqq_cc", "all"],
                        default="all", help="Which strategy to run")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only, do not write to Supabase")
    args = parser.parse_args()

    now = datetime.now(HKT)
    print(f"═══ Base Yield Bucket — {now.strftime('%Y-%m-%d %H:%M HKT')} ═══")

    all_signals = []

    if args.strategy in ("all", "spy_gap"):
        all_signals.extend(spy_overnight_gap())

    if args.strategy in ("all", "fx_carry"):
        all_signals.extend(fx_carry())

    if args.strategy in ("all", "qqq_cc"):
        all_signals.extend(qqq_covered_calls())

    # ── Summary ──
    print(f"\n{'─' * 60}")
    high = sum(1 for s in all_signals if s.get("confidence") == "HIGH")
    moderate = sum(1 for s in all_signals if s.get("confidence") == "MODERATE")
    low = sum(1 for s in all_signals if s.get("confidence") == "LOW")
    print(f"Total signals: {len(all_signals)} (HIGH: {high}, MODERATE: {moderate}, LOW: {low})")

    if all_signals:
        print(f"\nYield opportunities:")
        for s in all_signals:
            direction = s["direction"]
            emoji = "🟢" if s["confidence"] == "HIGH" else "🟡" if s["confidence"] == "MODERATE" else "⚪"
            print(f"  {emoji} {s['strategy']:25s} {s['symbol']:10s} {direction:15s} "
                  f"Score: {s['signal_score']}  |  Annualized: {s['annualized_yield_pct']:6.2f}%")

    # ── Save locally ──
    output = {
        "generated_at": now.isoformat(),
        "strategies": args.strategy,
        "signals": all_signals,
        "count": len(all_signals),
    }
    YIELD_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"\n📁 Saved to {YIELD_FILE}")

    # ── Write to Supabase ──
    if not args.dry_run and all_signals:
        write_to_supabase(all_signals)
    elif args.dry_run:
        print("  🔍 Dry run — skipping Supabase write.")

    print("\n✅ Base Yield bucket complete.")


if __name__ == "__main__":
    main()