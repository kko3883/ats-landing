#!/usr/bin/env python3
"""Format HK alert JSON to readable trade suggestions (stdout)."""
import json
from pathlib import Path

alert = Path.home() / '.hermes' / 'trading' / 'hk_alert.json'
data = json.loads(alert.read_text())

ts = data['generated_at'][:16]
status = '🟢 open' if data.get('market_status') == 'open' else '🔴 closed'

print(f"📊 **HK Stock Signals** ({ts})")
print(f"Market: {status}")
print()

for s in data.get('suggestions', []):
    emoji = '🟢' if s['direction'] == 'LONG' else '🔴'
    action = s['direction']
    stars = '⭐' * s['confidence']
    conv = s.get('conviction', '')

    risk_sign = '+' if s['direction'] == 'SHORT' else '-'
    rew_sign = '-' if s['direction'] == 'SHORT' else '+'

    print(f"{emoji} **{action} {s['symbol']}** {stars} [{conv}]")
    print(f"  Entry: ${s['entry']}  |  Stop: ${s['stop']} ({risk_sign}{abs(s['risk_pct']):.1f}%)")
    print(f"  TP1: ${s['tp1']} ({rew_sign}{s['reward1_pct']:.1f}%)  |  TP2: ${s['tp2']}")
    print(f"  R:R 1:{s['rr_ratio']}  |  RSI {s['rsi']}  |  {s['signals'][0]}")
    print()
