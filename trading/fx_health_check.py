#!/usr/bin/env python3
"""FX Health Check — daemon, gateway, state freshness, and IBKR position sync verification.
Only outputs if something is unhealthy. Silent exit = all good."""
import json, os, subprocess, sys
from pathlib import Path

BASE = Path(os.environ.get('HOME', '/Users/kelvinko')) / '.hermes' / 'trading'
STATE_FILE = BASE / 'fx_state.json'
LOG_FILE = BASE / 'fx_daemon.log'
VENV_PYTHON = str(BASE / 'venv' / 'bin' / 'python3')
IBKR_PORT = 4002

def is_running(name):
    r = subprocess.run(['pgrep', '-f', name], capture_output=True, text=True, timeout=5)
    return bool(r.stdout.strip())

def check_gateway():
    r = subprocess.run(['lsof', '-i', f':{IBKR_PORT}'], capture_output=True, text=True, timeout=5)
    return 'ESTABLISHED' in r.stdout

def get_ibrk_positions():
    """Connect to IBKR and fetch actual open positions. Returns dict or None."""
    code = '''
import asyncio, sys, json
sys.path.insert(0, r'{venv}')
import ib_async as ib
async def go():
    cl = ib.IB()
    cl.RequestTimeout = 8
    try:
        await cl.connectAsync('127.0.0.1', {port}, clientId=9990)
        positions = await cl.reqPositionsAsync()
        result = {{}}
        for p in positions:
            ls = p.contract.localSymbol
            pair_map = {{'EUR.USD': 'EUR/USD', 'AUD.JPY': 'AUD/JPY', 'NZD.JPY': 'NZD/JPY'}}
            pair = pair_map.get(ls)
            if not pair:
                continue
            size = int(p.position)
            if size == 0:
                continue
            direction = 'LONG' if size > 0 else 'SHORT'
            result[pair] = {{
                'direction': direction,
                'size': abs(size),
                'avgCost': float(p.avgCost) if p.avgCost else 0.0,
            }}
        cl.disconnect()
        print(json.dumps(result))
        return
    except Exception as e:
        print(f"ERR: {{e}}")
        sys.exit(1)
    finally:
        try: cl.disconnect()
        except: pass
asyncio.run(go())
'''.format(venv=VENV_PYTHON.replace("'", ""), port=IBKR_PORT)
    r = subprocess.run(
        [VENV_PYTHON, '-c', code],
        capture_output=True, text=True, timeout=15
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip())
    except:
        return None

# ── 1. Process + Gateway health ──
daemon = is_running('fx_daemon.py')
gateway = check_gateway()
issues = []

if not daemon:
    issues.append('🔴 FX Daemon is DOWN')
if not gateway:
    issues.append('🟡 IB Gateway (port 4002) not reachable')

# ── 2. State file freshness ──
state = {}
try:
    state = json.loads(STATE_FILE.read_text())
    lt = state.get('last_tick', '')
    if lt:
        from datetime import datetime
        ts = datetime.fromisoformat(lt)
        age = (datetime.now().astimezone() - ts).total_seconds()
        if age > 300:
            issues.append(f'⏰ State stale — last tick {int(age)}s ago')
except:
    issues.append('⚠️ Cannot read state file')

# ── 3. Log anomalies ──
if daemon:
    out = subprocess.run(['tail', '-20', str(LOG_FILE)], capture_output=True, text=True, timeout=5)
    log = out.stdout
    if 'running data-only mode' in log:
        issues.append('🟡 IBKR in data-only mode')
    if '💥 Tick error' in log:
        issues.append('⚠️ Recent tick errors in log')

# ── 4. IBKR position sync verification (only if gateway is up) ──
if gateway:
    ibrk_positions = get_ibrk_positions()
    if ibrk_positions is None:
        issues.append('⚠️ Cannot query IBKR positions (connection error)')
    else:
        state_positions = state.get('positions', {})

        # Positions in state but not in IBKR
        for pair, sp in state_positions.items():
            ibrk = ibrk_positions.get(pair)
            if ibrk is None:
                issues.append(f'⚠️ Position mismatch: {pair} in state ({sp["direction"]} {sp["size"]}) but NOT in IBKR')
            elif ibrk['direction'] != sp['direction']:
                issues.append(f'⚠️ Position mismatch: {pair} direction — state={sp["direction"]} vs IBKR={ibrk["direction"]}')
            elif ibrk['size'] != sp['size']:
                issues.append(f'⚠️ Position mismatch: {pair} size — state={sp["size"]} vs IBKR={ibrk["size"]}')

        # Positions in IBKR but not in state
        for pair, ip in ibrk_positions.items():
            if pair not in state_positions:
                issues.append(f'⚠️ Position mismatch: {pair} in IBKR ({ip["direction"]} {ip["size"]}) but NOT in state')

        # Unhealthy only if mismatches exist AND state has positions OR IBKR has positions
        all_issues = []
        for i in issues:
            if 'Position mismatch' in i:
                all_issues.append(i)
        if all_issues:
            # already added to issues above
            pass
        elif not issues:  # no mismatches AND state matches IBKR perfectly
            # Check if state and IBKR agree on having nothing
            if not state_positions and not ibrk_positions:
                pass  # both agree — no positions
            else:
                pass  # both agree on positions — no mismatch

# ── Output ──
if issues:
    print(f'🤖 **FX Health Alert**')
    for i in issues:
        print(f'  {i}')
    state_age = ''
    if state.get('last_tick'):
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(state['last_tick'])
            age = int((datetime.now().astimezone() - ts).total_seconds())
            state_age = f' · last tick: {int(age)}s ago'
        except:
            pass
    print(f'Daemon: {"🟢 Running" if daemon else "🔴 Down"} · Gateway: {"🟢 OK" if gateway else "🔴 Down"}{state_age}')
    positions = state.get('positions', {})
    if positions:
        print(f'\n📌 State positions:')
        for p, v in positions.items():
            print(f'  {v["direction"]} {p} size={v.get("size",0)} stop={v.get("stop_price","?")}')
    sys.exit(1)
else:
    # Silent — everything healthy
    sys.exit(0)
