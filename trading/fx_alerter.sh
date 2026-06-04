#!/bin/bash
# FX Alert Deliverer — run by cron (no_agent=True)
# Reads fx_alert.json, outputs message, clears it.
# If no alert, exits silently (no output = no delivery).

ALERT_FILE="$HOME/.hermes/trading/fx_alert.json"

if [ ! -f "$ALERT_FILE" ]; then
    exit 0
fi

MESSAGE=$(python3 -c "
import json, sys
try:
    a = json.load(open('$ALERT_FILE'))
    msg = a.get('message', '')
    trade = a.get('trade_result', {})
    lines = [msg] if msg else []
    
    # Skip trade details for exit events (already in message)
    if a.get('type') != 'exit':
        if trade and 'skipped' not in trade and 'error' not in trade:
            lines.append(f\"Order: {trade['action']} {trade['size']} @ ~{trade['price']} ({trade['status']})\")
        elif trade and 'error' in trade:
            lines.append(f\"Execution error: {trade['error']}\")
    
    # Add stop loss info if available
    if trade and 'stop_price' in trade and trade['stop_price']:
        lines.append(f\"Stop loss: {trade['stop_price']} (trailing, ATR×2.5)\")
    
    print('\n'.join(lines))
except:
    sys.exit(0)
")

if [ -n "$MESSAGE" ]; then
    echo "$MESSAGE"
fi

rm -f "$ALERT_FILE"
exit 0
