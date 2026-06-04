#!/bin/bash
# HK signal alert script — no_agent mode, zero LLM cost

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$HOME/.local/bin"
cd "$HOME/.hermes/trading"

source venv/bin/activate 2>/dev/null || . venv/bin/activate 2>/dev/null

# Run analysis silently
python3 hk_signal.py > /dev/null 2>&1

ALERT="$HOME/.hermes/trading/hk_alert.json"
if [ -f "$ALERT" ] && [ -s "$ALERT" ]; then
    COUNT=$(python3 -c "import json; d=json.load(open('$ALERT')); print(d.get('count',0))" 2>/dev/null)
    if [ "$COUNT" -gt 0 ]; then
        python3 "$HOME/.hermes/trading/hk_format_alert.py"
        exit 0
    fi
fi

# No signals — output nothing (silent = no spam)
# The scheduler treats empty stdout as "nothing to report"
