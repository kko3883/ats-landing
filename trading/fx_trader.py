"""
FX Trading Agent — IBKR Paper Account
Strategies:
  1. EUR/USD Trend — EMA crossover + RSI filter
  2. Carry Trade — Long AUD/JPY, long NZD/JPY (rate differential)
  
Uses yfinance for market data (free), IBKR for execution only.
"""
import json, time, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import yfinance as yf
import pandas as pd
import numpy as np

# ── Config ──
HKT = timezone(timedelta(hours=8))
STATE_FILE = Path(os.environ.get('HOME', '~')) / '.hermes' / 'trading' / 'fx_state.json'
TRADING_HOURS = (8, 0)    # 08:00 HKT (London open)
TRADING_HOURS_END = (5, 0) # 05:00 HKT next day (US close) — actually let's use 24h since FX is 24h

# ── EUR/USD Trend Strategy ──
def analyze_eurusd():
    """EMA crossover + RSI filter"""
    df = yf.download('EURUSD=X', period='5d', interval='1h', progress=False)
    if df.empty:
        return None
    
    close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()
    
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    
    current = close.iloc[-1]
    prev = close.iloc[-2]
    
    # Signal logic
    above_ema = current > ema20.iloc[-1] and current > ema50.iloc[-1]
    ema_cross = (ema20.iloc[-2] <= ema50.iloc[-2] and ema20.iloc[-1] > ema50.iloc[-1])  # golden cross
    rsi_ok = 30 < rsi.iloc[-1] < 70
    
    rsi_val = float(rsi.iloc[-1])
    ema20_v = float(ema20.iloc[-1])
    ema50_v = float(ema50.iloc[-1])
    ema20_prev = float(ema20.iloc[-2])
    ema50_prev = float(ema50.iloc[-2])
    
    signal = 'HOLD'
    confidence = 0
    
    if ema_cross and rsi_ok:
        signal = 'LONG'
        confidence = 3
    elif above_ema and rsi_val < 45:
        signal = 'LONG'
        confidence = 2
    elif not above_ema and rsi_val > 55:
        signal = 'SHORT'
        confidence = 2
    elif rsi_val < 30 and current > ema50.iloc[-1]:
        signal = 'LONG'
        confidence = 1
    elif rsi_val > 70 and current < ema50.iloc[-1]:
        signal = 'SHORT'
        confidence = 1
    
    return {
        'pair': 'EUR/USD',
        'price': float(current),
        'signal': signal,
        'confidence': confidence,
        'rsi': round(rsi_val, 1),
        'ema20_delta': round(current - ema20_v, 4),
        'ema50_delta': round(current - ema50_v, 4),
        'ema_cross': ema_cross,
        'timestamp': datetime.now(HKT).isoformat(),
    }

# ── Carry Trade Strategy ──
def analyze_carry():
    """Rate differential: long AUD/JPY, NZD/JPY."""
    pairs = ['AUDJPY=X', 'NZDJPY=X']
    signals = []
    
    for ticker in pairs:
        df = yf.download(ticker, period='5d', interval='1h', progress=False)
        if df.empty:
            continue
        
        close_s = df['Close']
        if isinstance(close_s, pd.DataFrame):
            close = close_s.iloc[:, 0]
        else:
            close = close_s
        
        current = float(close.iloc[-1])
        sma20_v = float(close.rolling(20).mean().iloc[-1])
        sma50_v = float(close.rolling(50).mean().iloc[-1])
        
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = float((100 - 100 / (1 + gain / loss)).iloc[-1])
        
        trend_up = current > sma20_v and sma20_v > sma50_v
        raw = ticker.replace('=X', '')
        # Normalize to IBKR sym_map format: AUDJPY → AUD/JPY
        if len(raw) == 6 and '/' not in raw:
            name = f"{raw[:3]}/{raw[3:]}"
        else:
            name = raw

        sig = 'HOLD'
        conf = 0
        if trend_up and rsi < 65:
            sig = 'LONG'
            conf = 2 if rsi < 55 else 1
        elif not trend_up and rsi > 50:
            sig = 'HOLD'
            conf = 0
            
        signals.append({
            'pair': name,
            'price': round(current, 4),
            'signal': sig,
            'confidence': conf,
            'rsi': round(rsi, 1),
            'trend': 'up' if trend_up else 'down',
        })
    
    return signals

# ── IBKR Execution ──
def place_trade(pair, signal, price, confidence):
    """Place a paper trade via IBKR API"""
    try:
        from ib_insync import IB, Forex, MarketOrder
        
        ib = IB()
        ib.connect('127.0.0.1', 4002, clientId=20)
        
        # Map pair to IBKR contract
        sym_map = {
            'EUR/USD': 'EURUSD',
            'AUD/JPY': 'AUDJPY',
            'NZD/JPY': 'NZDJPY',
            # Also support slash-less format from analyze_carry fallback
            'AUDJPY': 'AUDJPY',
            'NZDJPY': 'NZDJPY',
        }
        sym = sym_map.get(pair)
        if not sym:
            ib.disconnect()
            return None
        
        contract = Forex(sym)
        ib.qualifyContracts(contract)
        
        # Position sizing
        if 'JPY' in pair:
            size = 250000  # JPY pairs: 250k JPY (matches POSITION_SIZES in fx_daemon.py)
        elif 'EUR' in pair:
            size = 25000   # EUR pairs: 25k EUR min
        else:
            size = 50000
        
        action = 'BUY' if signal == 'LONG' else 'SELL'
        order = MarketOrder(action, size, account='DUQ538194')
        
        trade = ib.placeOrder(contract, order)
        ib.sleep(2)
        
        result = {
            'pair': pair,
            'action': action,
            'size': size,
            'price': price,
            'status': trade.orderStatus.status,
            'order_id': trade.orderStatus.orderId,
            'timestamp': datetime.now(HKT).isoformat(),
        }
        
        ib.disconnect()
        return result
        
    except Exception as e:
        return {'error': str(e), 'pair': pair, 'signal': signal}

# ── State Management ──
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {'eur_usd': {}, 'carry': [], 'positions': {}, 'last_run': None, 'history': []}

def save_state(state):
    state['last_run'] = datetime.now(HKT).isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))

def check_alerts(old, new_eur, new_carry):
    """Return alert messages for new signals"""
    alerts = []
    
    # EUR/USD change
    old_sig = old.get('eur_usd', {}).get('signal', 'HOLD')
    new_sig = new_eur.get('signal', 'HOLD')
    if new_sig != old_sig and new_sig != 'HOLD' and new_eur.get('confidence', 0) >= 2:
        direction = '📈 LONG' if new_sig == 'LONG' else '📉 SHORT'
        alerts.append(
            f"🚨 FX Signal — EUR/USD\n"
            f"Signal: {direction} (conf: {new_eur['confidence']})\n"
            f"Price: {new_eur['price']:.5f}\n"
            f"RSI: {new_eur['rsi']} | EMA20: {new_eur['ema20_delta']:+.5f} | EMA50: {new_eur['ema50_delta']:+.5f}"
        )
    
    # Carry signals
    for cs in new_carry:
        old_carry = [c for c in old.get('carry', []) if c.get('pair') == cs['pair']]
        if cs['signal'] == 'LONG' and cs['confidence'] >= 2:
            if not old_carry or old_carry[0].get('signal') != 'LONG':
                alerts.append(
                    f"🚨 Carry Trade — {cs['pair']}\n"
                    f"Signal: LONG (conf: {cs['confidence']})\n"
                    f"Price: {cs['price']:.3f}\n"
                    f"RSI: {cs['rsi']} | Trend: {cs['trend']}"
                )
    
    return alerts

# ── Main Loop ──
def run_once():
    print(f"=== FX Trading Agent ===")
    print(f"Run: {datetime.now(HKT).isoformat()}")
    
    state = load_state()
    
    # 1. EUR/USD analysis
    print("\n── EUR/USD Analysis ──")
    eur = analyze_eurusd()
    if eur:
        print(f"Price: {eur['price']:.5f}")
        print(f"Signal: {eur['signal']} (conf: {eur['confidence']})")
        print(f"RSI: {eur['rsi']} | EMA Cross: {eur['ema_cross']}")
        state['eur_usd'] = eur
    else:
        print("⚠️ No EUR/USD data")
    
    # 2. Carry analysis
    print(f"\n── Carry Trade Analysis ──")
    carry = analyze_carry()
    for c in carry:
        s = f"{c['pair']}: {c['signal']} @ {c['price']:.3f} (RSI: {c['rsi']}, trend: {c['trend']})"
        print(s)
    state['carry'] = carry
    
    # 3. Execute trades for high-confidence signals
    executions = []
    
    # Check EUR/USD
    if eur and eur['signal'] != 'HOLD' and eur['confidence'] >= 2:
        old_sig = state.get('eur_usd', {}).get('signal', 'HOLD')
        if eur['signal'] != old_sig:
            print(f"\n── Executing EUR/USD {eur['signal']} ──")
            result = place_trade(eur['pair'], eur['signal'], eur['price'], eur['confidence'])
            executions.append(result)
            if result and 'error' not in result:
                print(f"✅ Order placed: {result['action']} {result['size']} @ ~{result['price']}")
                print(f"   Status: {result['status']}")
            elif result and 'error' in result:
                print(f"❌ Trade failed: {result['error']}")
    
    # Check Carry trades
    for cs in carry:
        if cs['signal'] == 'LONG' and cs['confidence'] >= 2:
            old_carry = [c for c in state.get('carry', []) if c.get('pair') == cs['pair']]
            if not old_carry or old_carry[0].get('signal') != 'LONG':
                print(f"\n── Executing Carry {cs['pair']} LONG ──")
                result = place_trade(cs['pair'], cs['signal'], cs['price'], cs['confidence'])
                executions.append(result)
                if result and 'error' not in result:
                    print(f"✅ Order placed: {result['action']} {result['size']} @ ~{result['price']}")
                elif result and 'error' in result:
                    print(f"❌ Trade failed: {result['error']}")
    
    state['executions'] = state.get('executions', [])[-20:] + executions
    
    # 4. Check for alerts (based on state changes)
    old_state = {'eur_usd': state.get('eur_usd', {}), 'carry': state.get('carry', [])}
    alerts = check_alerts(old_state, eur or {}, carry)
    state['history'] = state.get('history', [])[-100:]  # keep last 100
    
    # 4. Save state
    save_state(state)
    print(f"\n✅ State saved to {STATE_FILE}")
    
    return alerts

# ── Entry Point ──
if __name__ == '__main__':
    alerts = run_once()
    if alerts:
        print("\n" + "=" * 40)
        print("ALERTS:")
        for a in alerts:
            print(f"\n{a}")
    else:
        print("\nNo new signals.")
