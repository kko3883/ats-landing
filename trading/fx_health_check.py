#!/usr/bin/env python3
"""FX Health Check — only outputs if daemon or gateway is unhealthy."""
import json, os, subprocess, sys
from pathlib import Path

BASE = Path(os.environ.get('HOME', '/Users/kelvinko')) / '.hermes' / 'trading'
STATE_FILE = BASE / 'fx_state.json'
LOG_FILE = BASE / 'fx_daemon.log'

def is_running(name):
    r = subprocess.run(['pgrep', '-f', name], capture_output=True, text=True, timeout=5)
    return bool(r.stdout.strip())

def check_gateway():
    r = subprocess.run(['lsof', '-i', ':4002'], capture_output=True, text=True, timeout=5)
    return 'ESTABLISHED' in r.stdout

daemon = is_running('fx_daemon.py')
gateway = check_gateway()

issues = []
if not daemon:
    issues.append('🔴 FX Daemon is DOWN')
if not gateway:
    issues.append('🟡 IB Gateway (port 4002) not reachable')

# Check if state is stale (>5min since last tick)
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

# Check daemon health via log
if daemon:
    out = subprocess.run(['tail', '-20', str(LOG_FILE)], capture_output=True, text=True, timeout=5)
    log = out.stdout
    if 'running data-only mode' in log:
        issues.append('🟡 IBKR in data-only mode')
    if '💥 Tick error' in log:
        issues.append('⚠️ Recent tick errors in log')

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
    # Show current state summary
    positions = state.get('positions', {})
    if positions:
        print(f'\n📌 Positions:')
        for p, v in positions.items():
            print(f'  {v["direction"]} {p} size={v.get("size",0)} stop={v.get("stop_price","?")}')
else:
    # Silent — everything healthy
    sys.exit(0)
