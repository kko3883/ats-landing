#!/usr/bin/env python3
"""FX Summary — positions, signals, daemon health. Used by FX command + cron jobs."""
import json, time, os, subprocess, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(os.environ.get('HOME', '/Users/kelvinko')) / '.hermes' / 'trading'
STATE_FILE = BASE / 'fx_state.json'
LOG_FILE = BASE / 'fx_daemon.log'
HKT = timezone(timedelta(hours=8))

def now_hkt():
    return datetime.now(HKT).strftime('%H:%M:%S')

def is_process_running(name):
    try:
        r = subprocess.run(['pgrep', '-f', name], capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except:
        return False

def daemon_log_since(tail=100):
    if not LOG_FILE.exists():
        return []
    try:
        out = subprocess.run(['tail', f'-{tail}', str(LOG_FILE)], capture_output=True, text=True, timeout=5)
        return out.stdout.splitlines()
    except:
        return []

def check_gateway():
    """Check if IB Gateway/TWS port 4002 is open."""
    try:
        r = subprocess.run(
            ['lsof', '-i', ':4002'],
            capture_output=True, text=True, timeout=5
        )
        return 'ESTABLISHED' in r.stdout
    except:
        return False

# ── Read state ──
state = {}
if STATE_FILE.exists():
    try:
        state = json.loads(STATE_FILE.read_text())
    except:
        state = {}

positions = state.get('positions', {})
carry = state.get('carry', [])
eur = state.get('eur_usd', {})
current_prices = state.get('current_prices', {})
last_tick = state.get('last_tick', '')

# ── Daemon health ──
daemon_alive = is_process_running('fx_daemon.py')
gateway_alive = check_gateway()

# Determine IBKR connection status from log
log_lines = daemon_log_since(100)
ibkr_ok = any('connection established' in l.lower() or 'reconnected' in l.lower() for l in log_lines)
ibkr_fail = any('running data-only mode' in l for l in log_lines)
last_error = ''
for l in reversed(log_lines):
    if ('Error' in l and ('326' in l or '1100' in l or '202' in l)) or 'Trade failed' in l:
        last_error = l.strip()
        break

if daemon_alive and ibkr_ok and not ibkr_fail:
    ibkr_status = '🟢 Connected'
elif daemon_alive and ibkr_fail:
    ibkr_status = '🟡 Data-only'
elif daemon_alive:
    ibkr_status = '🟡 Reconnecting'
else:
    ibkr_status = '🔴 Offline'

health = '🟢 Healthy' if (daemon_alive and ibkr_ok) else \
         '🟡 Degraded' if daemon_alive else \
         '🔴 Down'

# Poll interval
poll = state.get('poll_interval', '?')
prox = state.get('proximity', '?')
last_tick_ago = ''
if last_tick:
    try:
        lt = datetime.fromisoformat(last_tick)
        diff = (datetime.now(HKT) - lt).total_seconds()
        last_tick_ago = f'{int(diff)}s ago'
    except:
        pass

# ── Build summary ──
lines = []
lines.append(f'🤖 **FX Status** · {now_hkt()} HKT')
lines.append(f'Daemon: {"🟢 Running" if daemon_alive else "🔴 Down"} · IBKR: {ibkr_status} · Poll: {poll}s · {last_tick_ago}')
if last_error:
    lines.append(f'⚠️ Last error: {last_error[:90]}')
lines.append('')
lines.append(f'**💰 Balance**')

bal = state.get('account_balance', {})
if bal:
    net_liq = bal.get('NetLiquidation')
    cash = bal.get('TotalCashValue')
    upnl = bal.get('UnrealizedPnL')
    bp = bal.get('BuyingPower')

    if net_liq is not None:
        lines.append(f'Net Liq: ${net_liq:,.0f}')
    if cash is not None:
        lines.append(f'Cash: ${cash:,.0f}')
    if upnl is not None:
        pnl_icon = '🟢' if upnl >= 0 else '🔴'
        lines.append(f'Unrealized PnL: {pnl_icon} ${upnl:+,.0f}')
    if bp is not None:
        lines.append(f'Buying Power: ${bp:,.0f}')
else:
    lines.append('N/A (IBKR account summary not available)')

lines.append('')

# Positions
if positions:
    lines.append(f'**📌 Positions ({len(positions)})**')
    for pair, p in positions.items():
        direction = '📈 LONG' if p['direction'] == 'LONG' else '📉 SHORT'
        size = p.get('size', 0)
        entry = p.get('entry_price', 0)
        stop = p.get('stop_price', '—')
        entries = p.get('entries', 1)
        cur = current_prices.get(pair, '?')

        # Entry time
        entry_time = p.get('entry_time', '')
        if entry_time and 'T' in str(entry_time):
            try:
                et = datetime.fromisoformat(str(entry_time))
                entry_time = et.strftime('%b %d %H:%M')
            except:
                pass

        # P&L estimate
        pnl_str = ''
        if cur != '?' and isinstance(cur, (int, float)) and entry:
            if p['direction'] == 'LONG':
                pnl_pct = (cur - entry) / entry * 100
            else:
                pnl_pct = (entry - cur) / entry * 100
            pnl_str = f' · PnL: {pnl_pct:+.2f}%'

        lines.append(f'{direction} {pair} ({entries}x{size:,})')
        lines.append(f'  Entry: {entry:.5f} ({entry_time}) · Stop: {stop} · Now: {cur}{pnl_str}')
    lines.append('')
else:
    lines.append('**📌 No open positions**')
    lines.append('')

# Signals
lines.append(f'**📊 Signals**')
all_signals = []
if eur:
    all_signals.append(eur)
all_signals.extend(carry)

for s in all_signals:
    pair = s.get('pair', '?')
    level = s.get('level', 0)
    signal = s.get('signal', 'HOLD')
    rsi = s.get('rsi', '?')
    price = s.get('price', '?')
    conf = s.get('confidence', 0)

    if signal == 'LONG':
        sig_icon = '🟢 LONG'
    elif signal == 'SHORT':
        sig_icon = '🔴 SHORT'
    else:
        sig_icon = '⚪ HOLD'

    cp = current_prices.get(pair, price)
    price_str = f'{price}' if price == cp else f'{price} (curr: {cp})'
    lines.append(f'{sig_icon} L{level} conf{conf} · {pair} · RSI{rsi} · {price_str}')

lines.append('')

# ── Output ──
output = '\n'.join(lines)
print(output)

# Also save a snapshot for the cron to read
output_path = BASE / 'fx_snapshot.txt'
output_path.write_text(output)
