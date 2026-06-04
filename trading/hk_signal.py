#!/usr/bin/env python3
"""
HK Stock Signal Generator v2
- Strong potential filtering (confidence >= 4)
- Specific entry price, stop loss, take profit
- ATR-based risk management
- Minimum 1:2 risk-reward
"""

import json, sys, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(os.environ.get('HOME', '/Users/kelvinko')) / '.hermes' / 'trading'
HKT = timezone(timedelta(hours=8))

sys.path.insert(0, str(BASE))
from watchlist.longbridge_data import fetch_longbridge_data

ALERT_FILE = BASE / 'hk_alert.json'

# Risk parameters
STOP_MULT = 1.5      # ATR × 1.5 for stop loss
TP1_MULT = 3.0       # ATR × 3.0 for first target
TP2_MULT = 4.5       # ATR × 4.5 for second target
MIN_RR = 2.0         # minimum 1:2 risk-reward
MIN_CONFIDENCE = 4   # strong conviction only
# Trend filter: require at least SMA20 alignment
REQUIRE_TREND_LONG = True    # LONG needs above_sma20 (unless RSI < 20 deeply oversold)
REQUIRE_TREND_SHORT = True   # SHORT needs below_sma20


def calc_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0


def find_support_resistance(close: pd.Series, lookback: int = 50) -> tuple[float, float]:
    """Find nearest support and resistance levels from recent pivots."""
    recent = close.tail(lookback)
    high = float(recent.max())
    low = float(recent.min())
    mid = (high + low) / 2
    current = float(recent.iloc[-1])

    # Simple: use recent range levels
    r1 = current + (high - current) * 0.382  # 38.2% extension
    s1 = current - (current - low) * 0.382
    return s1, r1


def analyze_stock(symbol: str, df: pd.DataFrame) -> dict | None:
    """Analyze a single HK stock with entry/stop/target."""
    close = df['Close']
    if len(close) < 50:
        return None

    current = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) > 1 else current
    volume = float(df['Volume'].iloc[-1])
    avg_volume = float(df['Volume'].tail(20).mean())

    # Trend
    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    sma20_prev = float(close.rolling(20).mean().iloc[-2]) if len(close) > 20 else sma20
    sma50_prev = float(close.rolling(50).mean().iloc[-2]) if len(close) > 50 else sma50

    above_sma20 = current > sma20
    above_sma50 = current > sma50
    golden_cross = sma20_prev <= sma50_prev and sma20 > sma50
    death_cross = sma20_prev >= sma50_prev and sma20 < sma50

    # RSI
    rsi = calc_rsi(close)

    # ATR
    atr = calc_atr(df)
    atr_val = round(atr, 3)
    atr_pct = round(atr / current * 100, 2) if current > 0 else 0

    # Volume
    vol_ratio = round(volume / avg_volume, 1) if avg_volume > 0 else 1.0
    volume_spike = vol_ratio > 1.5

    # Price change
    change_pct = round((current - prev_close) / prev_close * 100, 2)

    # Support / resistance
    s1, r1 = find_support_resistance(close)

    # ── Scoring ──
    signals = []
    long_score = 0
    short_score = 0

    # ── LONG signals ──
    if rsi < 20:
        signals.append(f"RSI {rsi:.0f} extremely oversold")
        long_score += 4
    elif rsi < 30:
        signals.append(f"RSI {rsi:.0f} deeply oversold")
        long_score += 3
    elif rsi < 35:
        signals.append(f"RSI {rsi:.0f} oversold")
        long_score += 2
    elif rsi < 40:
        signals.append(f"RSI {rsi:.0f} near oversold")
        long_score += 1

    if golden_cross:
        signals.append("Golden cross SMA20↑SMA50")
        long_score += 3
    if above_sma20:
        signals.append("Above SMA20")
        long_score += 2
    if above_sma50:
        signals.append("Above SMA50")
        long_score += 2
    if volume_spike and rsi < 35:
        signals.append(f"Vol {vol_ratio}x + oversold")
        long_score += 2

    # ── SHORT signals ──
    if rsi > 80:
        signals.append(f"RSI {rsi:.0f} extremely overbought")
        short_score += 4
    elif rsi > 75:
        signals.append(f"RSI {rsi:.0f} deeply overbought")
        short_score += 3
    elif rsi > 70:
        signals.append(f"RSI {rsi:.0f} overbought")
        short_score += 2
    elif rsi > 65:
        signals.append(f"RSI {rsi:.0f} near overbought")
        short_score += 1

    if death_cross:
        signals.append("Death cross SMA20↓SMA50")
        short_score += 3
    if not above_sma20:
        signals.append("Below SMA20")
        short_score += 2
    if not above_sma50:
        signals.append("Below SMA50")
        short_score += 2
    if volume_spike and rsi > 70:
        signals.append(f"Vol {vol_ratio}x + overbought")
        short_score += 2

    # Determine direction
    direction = None
    conf = 0

    # Direction logic:
    # LONG: oversold condition (RSI < 40) + trend context (above_sma20, or RSI < 20 extreme)
    # SHORT: overbought condition (RSI > 65) + trend context (below_sma20, or RSI > 80 extreme)

    if rsi < 40 and (above_sma20 or rsi < 20):
        direction = 'LONG'
        conf = min(long_score, 5)
    elif rsi > 65 and (not above_sma20 or rsi > 80):
        direction = 'SHORT'
        conf = min(short_score, 5)

    # ── Entry / Stop / Target calculation ──
    entry_price = current
    stop_price = entry_price
    tp1_price = entry_price
    tp2_price = entry_price
    risk_pct = 0.0
    reward1_pct = 0.0
    reward2_pct = 0.0
    rr_ratio = 0.0
    has_plan = False

    if direction == 'LONG':
        stop_price = round(current - (atr_val * STOP_MULT), 3)
        risk_pct = round((current - stop_price) / current * 100, 2)

        # Target 1: use ATR-based target primarily for consistency
        tp1_atr = round(current + (atr_val * TP1_MULT), 3)
        # Only use SMA20 as TP if it gives better R:R than ATR target
        tp1_sma = round(sma20, 3)
        if tp1_sma > current and (tp1_sma - current) > (tp1_atr - current):
            tp1_price = tp1_sma
        else:
            tp1_price = tp1_atr
        # Ensure TP1 is at least meaningful
        tp1_price = max(tp1_price, round(current + atr_val, 3))

        # Target 2: SMA50 or ATR × 4.5
        tp2_sma = round(sma50, 3)
        tp2_atr = round(current + (atr_val * TP2_MULT), 3)
        tp2_price = max(tp2_sma, tp2_atr) if tp2_sma > current else tp2_atr

        reward1_pct = round((tp1_price - current) / current * 100, 2)
        reward2_pct = round((tp2_price - current) / current * 100, 2)
        if risk_pct > 0:
            rr_ratio = round(reward1_pct / risk_pct, 1)

        # Verify plan is valid
        has_plan = (stop_price < current < tp1_price <= tp2_price)

    elif direction == 'SHORT':
        stop_price = round(current + (atr_val * STOP_MULT), 3)
        risk_pct = round((stop_price - current) / current * 100, 2)

        # Target 1: use ATR-based target primarily
        tp1_atr = round(current - (atr_val * TP1_MULT), 3)
        tp1_sma = round(sma20, 3)
        if tp1_sma < current and (current - tp1_sma) > (current - tp1_atr):
            tp1_price = tp1_sma
        else:
            tp1_price = tp1_atr
        tp1_price = min(tp1_price, round(current - atr_val, 3))

        # Target 2
        tp2_sma = round(sma50, 3)
        tp2_atr = round(current - (atr_val * TP2_MULT), 3)
        tp2_price = min(tp2_sma, tp2_atr) if tp2_sma < current else tp2_atr

        reward1_pct = round((current - tp1_price) / current * 100, 2)
        reward2_pct = round((current - tp2_price) / current * 100, 2)
        if risk_pct > 0:
            rr_ratio = round(reward1_pct / risk_pct, 1)

        has_plan = (stop_price > current > tp1_price >= tp2_price)

    # Filter: only show trades with minimum confidence AND R:R >= 2
    if not direction or conf < MIN_CONFIDENCE or rr_ratio < MIN_RR:
        return None

    conviction = "HIGH" if rr_ratio >= 3.0 else "MODERATE" if rr_ratio >= 2.0 else "SPECULATIVE"

    return {
        'symbol': symbol,
        'direction': direction,
        'confidence': conf,
        'conviction': conviction,
        'signals': signals,
        'price': round(current, 3),
        'entry': round(entry_price, 3),
        'stop': round(stop_price, 3),
        'tp1': round(tp1_price, 3),
        'tp2': round(tp2_price, 3),
        'risk_pct': risk_pct,
        'reward1_pct': reward1_pct,
        'reward2_pct': reward2_pct,
        'rr_ratio': rr_ratio,
        'rsi': round(rsi, 1),
        'sma20': round(sma20, 3),
        'sma50': round(sma50, 3),
        'atr_pct': atr_pct,
        'vol_ratio': vol_ratio,
        'change_pct': change_pct,
        'timestamp': datetime.now(HKT).isoformat(),
    }


def format_suggestion(s: dict) -> str:
    """Format as clean actionable trade suggestion."""
    emoji = '🟢' if s['direction'] == 'LONG' else '🔴'
    action = 'BUY' if s['direction'] == 'LONG' else 'SELL'

    lines = [
        f"{emoji} **{action} {s['symbol']}**  ⭐{'⭐' * (s['confidence'] - 1)} [{s['conviction']}]",
        f"⚠️  Risk: ${s['stop']} ({s['risk_pct']:.1f}%)",
        f"🎯  TP1: ${s['tp1']} ({s['reward1_pct']:.1f}%)",
        f"🎯  TP2: ${s['tp2']} ({s['reward2_pct']:.1f}%)",
        f"📊  R:R 1:{s['rr_ratio']}",
        f"💡  RSI {s['rsi']} | ATR {s['atr_pct']}% | Vol {s['vol_ratio']}x",
    ]
    return '\n'.join(lines)


def main():
    # Load symbols from watchlist
    watchlist_path = BASE / 'watchlist.json'
    if not watchlist_path.exists():
        symbols = ['0700.HK', '0981.HK', '1099.HK', '1928.HK', '2319.HK',
                   '2333.HK', '2382.HK', '2628.HK', '9626.HK', '9999.HK']
    else:
        w = json.loads(watchlist_path.read_text())
        symbols = set()
        for group, data in w['hk']['groups'].items():
            for c in data.get('long_candidates', []):
                symbols.add(c['symbol'])
            for c in data.get('short_candidates', []):
                symbols.add(c['symbol'])
        symbols = sorted(symbols)

    if not symbols:
        return

    # Fetch data
    end = datetime.now(HKT).strftime('%Y-%m-%d')
    df = fetch_longbridge_data(symbols, start='2025-06-01', end=end,
                                max_workers=6, label='HK signals')

    if df.empty:
        result = {'generated_at': datetime.now(HKT).isoformat(),
                  'suggestions': [], 'count': 0}
        ALERT_FILE.write_text(json.dumps(result, indent=2))
        return

    # Analyze
    results = []
    now_hkt = datetime.now(HKT)

    for sym in symbols:
        try:
            sym_df = df.xs(sym, level='Ticker', axis=1).dropna()
            if sym_df.empty:
                continue
            sig = analyze_stock(sym, sym_df)
            if sig:
                results.append(sig)
        except Exception:
            pass

    # Sort by conviction (R:R ratio), then confidence
    results.sort(key=lambda x: (x['rr_ratio'], x['confidence']), reverse=True)

    output = {
        'generated_at': now_hkt.isoformat(),
        'market': 'HK',
        'market_status': 'open' if (9 <= now_hkt.hour < 16 and now_hkt.weekday() < 5) else 'closed',
        'total_analyzed': len(results),
        'suggestions': results,
        'count': len(results),
    }

    ALERT_FILE.write_text(json.dumps(output, indent=2, default=str))

    # Output formatted alerts
    if results:
        header = f"📊 **HK Stock Signals** ({now_hkt.strftime('%Y-%m-%d %H:%M')} HKT)"
        status = 'open' if (9 <= now_hkt.hour < 16 and now_hkt.weekday() < 5) else 'closed'
        print(header)
        print(f"Market: {'🟢' if status=='open' else '🔴'} {status}")
        print()
        for s in results:
            print(format_suggestion(s))
            print()
    else:
        print(f"📊 **HK Stock Signals** ({now_hkt.strftime('%Y-%m-%d %H:%M')} HKT)")
        print(f"No actionable signals meet criteria (conf ≥ {MIN_CONFIDENCE}, R:R ≥ {MIN_RR:.0f}:1).")


if __name__ == '__main__':
    main()
