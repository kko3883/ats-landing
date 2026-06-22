#!/bin/bash
# NAS Health Check for ATS-FX (Synology DSM 7.2)
# Run over SSH:  bash /volume1/docker/ats-fx/nas_health_check.sh
#
# Checks: Docker containers, IB Gateway port, fx-daemon state freshness,
#         recent log errors, and recent signals/trades output.
# Exit 0 = healthy, 1 = issues found.

set -u

# ── Config ─────────────────────────────────────────────────────────────────
COMPOSE_DIR="/volume1/docker/ats-fx"
STATE_FILE="${COMPOSE_DIR}/state/state.json"
TRADES_FILE="${COMPOSE_DIR}/state/trades.jsonl"
CONTROL_FILE="${COMPOSE_DIR}/state/control.json"
GATEWAY_PORT=4002          # paper API (inside gateway container)
SOCAT_PORT=4004            # socat relay the daemon connects to
STALE_SECONDS=600          # state older than 10 min = stale
LOG_TAIL_LINES=40

# Docker compose v1 on Synology
DC="sudo docker-compose"
[ -x "$(command -v docker-compose)" ] || DC="sudo docker compose"

issues=0
add_issue() { echo "  $1"; issues=$((issues+1)); }

echo "════════════════════════════════════════════════════════════"
echo "  ATS-FX NAS Health Check  ·  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "════════════════════════════════════════════════════════════"

# ── 1. Docker containers up ────────────────────────────────────────────────
echo ""
echo "▶ Docker containers"
for c in ib-gateway ats-fx-daemon ats-telegram-bot; do
    status=$(sudo docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "missing")
    health=$(sudo docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || echo "")
    if [ "$status" = "running" ]; then
        echo "  ✅ $c: running (${health:-no-healthcheck})"
    else
        add_issue "🔴 $c: ${status} (expected running)"
    fi
done

# ── 2. IB Gateway API port listening ───────────────────────────────────────
echo ""
echo "▶ IB Gateway API port"
# Probe via the socat relay port (what the daemon uses) on the host
if sudo docker exec ib-gateway bash -c "exec 3<>/dev/tcp/127.0.0.1/${GATEWAY_PORT}" 2>/dev/null; then
    echo "  ✅ Gateway API port ${GATEWAY_PORT} listening (inside container)"
else
    add_issue "🔴 Gateway API port ${GATEWAY_PORT} not reachable inside ib-gateway"
fi

# Check the host-published socat port (daemon connects here)
if sudo docker exec ats-fx-daemon bash -c "exec 3<>/dev/tcp/ib-gateway/${SOCAT_PORT}" 2>/dev/null; then
    echo "  ✅ Daemon can reach gateway socat port ${SOCAT_PORT}"
else
    add_issue "🔴 Daemon cannot reach gateway socat port ${SOCAT_PORT}"
fi

# ── 3. fx-daemon state freshness ───────────────────────────────────────────
echo ""
echo "▶ fx-daemon state freshness"
if [ -f "$STATE_FILE" ]; then
    mtime=$(stat -c %Y "$STATE_FILE" 2>/dev/null || stat -f %m "$STATE_FILE")
    now=$(date +%s)
    age=$((now - mtime))
    if [ "$age" -gt "$STALE_SECONDS" ]; then
        add_issue "⏰ State file stale — last updated ${age}s ago (> ${STALE_SECONDS}s)"
    else
        echo "  ✅ State file fresh — updated ${age}s ago"
    fi

    # Show last_tick if present (python json parse; fall back to raw)
    last_tick=$(sudo docker exec ats-fx-daemon python3 -c "import json;print(json.load(open('/state/state.json')).get('last_tick','?'))" 2>/dev/null || echo "?")
    echo "     last_tick: $last_tick"
else
    add_issue "⚠️ State file not found: $STATE_FILE"
fi

# ── 4. Recent daemon log errors ────────────────────────────────────────────
echo ""
echo "▶ fx-daemon recent log (tail ${LOG_TAIL_LINES})"
daemon_log=$(sudo docker logs --tail ${LOG_TAIL_LINES} ats-fx-daemon 2>&1)
echo "$daemon_log" | sed 's/^/     /' | tail -n 20

if echo "$daemon_log" | grep -qiE "error|exception|traceback|failed|disconnect"; then
    add_issue "⚠️ Errors/exceptions found in recent fx-daemon log"
fi
if echo "$daemon_log" | grep -qi "running data-only mode"; then
    add_issue "🟡 IBKR in data-only mode"
fi

# ── 5. Recent trades / signals ─────────────────────────────────────────────
echo ""
echo "▶ Recent trades"
if [ -f "$TRADES_FILE" ]; then
    trades_lines=$(wc -l < "$TRADES_FILE")
    echo "  trades.jsonl: ${trades_lines} lines"
    tail -n 5 "$TRADES_FILE" | sed 's/^/     /'
else
    echo "  ℹ️ No trades file yet (no fills)"
fi

# ── 6. Control switches (what the bot is telling the daemon) ───────────────
echo ""
echo "▶ Control switches"
if [ -f "$CONTROL_FILE" ]; then
    cat "$CONTROL_FILE" | sed 's/^/     /'
else
    echo "  ℹ️ No control.json (defaults apply)"
fi

# ── 7. Telegram bot recent log ─────────────────────────────────────────────
echo ""
echo "▶ telegram-bot recent log (tail 10)"
sudo docker logs --tail 10 ats-telegram-bot 2>&1 | sed 's/^/     /'

# ── 8. Disk / resource sanity ──────────────────────────────────────────────
echo ""
echo "▶ System"
echo "  Load:   $(uptime | sed 's/.*load average://')"
echo "  Disk:   $(df -h /volume1 | tail -1 | awk '{print $3"/"$2" used ("$5")"}')"
echo "  Mem:    $(free -m 2>/dev/null | awk '/Mem/{print $3"MB / "$2"MB used"}' || echo 'n/a')"

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
if [ "$issues" -eq 0 ]; then
    echo "  ✅ All healthy — no issues found."
    exit 0
else
    echo "  ❌ ${issues} issue(s) found — see above."
    exit 1
fi