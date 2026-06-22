#!/bin/bash
# NAS Health Check for ATS-FX (Synology DSM 7.2)
#
# Usage (SSH into NAS first):
#   cd /volume1/docker/ats-landing/trading/nautilus_port
#   bash nas_health_check.sh
#
# Or run from anywhere — script auto-detects its directory.
#
# Checks: Docker containers, IB Gateway port, fx-daemon state freshness,
#         recent log errors, and recent trades output.
# Exit 0 = healthy, 1 = issues found.

set -u

# ── Auto-detect paths ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_DIR="$SCRIPT_DIR"

# State lives in a Docker named volume "ats-state" — find its mountpoint
STATE_VOL="ats-state"
STATE_FILE=""
TRADES_FILE=""
CONTROL_FILE=""

GATEWAY_PORT=4002          # paper API (inside gateway container)
SOCAT_PORT=4004            # socat relay the daemon connects to
STALE_SECONDS=600          # state older than 10 min = stale
LOG_TAIL_LINES=40

# Docker on Synology DSM requires sudo for non-root users
# Find docker binary (not in default PATH for non-root SSH)
DOCKER=""
for d in /usr/local/bin/docker /var/packages/ContainerManager/target/usr/bin/docker /var/packages/Docker/usr/bin/docker; do
    if [ -x "$d" ]; then
        DOCKER="$d"
        break
    fi
done
# Fall back to PATH (maybe running as root)
if [ -z "$DOCKER" ]; then
    if command -v docker >/dev/null 2>&1; then
        DOCKER="docker"
    fi
fi

if [ -z "$DOCKER" ]; then
    echo "❌ Cannot find docker binary. Try: sudo bash $0"
    exit 1
fi

# Check if we need sudo
if [ "$(id -u)" -ne 0 ]; then
    if ! "$DOCKER" ps >/dev/null 2>&1; then
        DOCKER="sudo $DOCKER"
    fi
fi

DC=""
if command -v docker-compose >/dev/null 2>&1; then
    DC="sudo docker-compose"
elif "$DOCKER" compose version >/dev/null 2>&1; then
    DC="sudo $DOCKER compose"
fi

issues=0
add_issue() { echo "  $1"; issues=$((issues+1)); }

echo "════════════════════════════════════════════════════════════"
echo "  ATS-FX NAS Health Check  ·  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  Docker: $DOCKER"
echo "  Compose dir: $COMPOSE_DIR"
echo "════════════════════════════════════════════════════════════"

# ── Find the state volume mountpoint ────────────────────────────────────────
# Docker named volumes on Synology are at /volume1/@docker/volumes/<name>/_data
STATE_MNT=""
for vol_path in \
    "/volume1/@docker/volumes/${STATE_VOL}/_data" \
    "/volume2/@docker/volumes/${STATE_VOL}/_data"; do
    if [ -d "$vol_path" ]; then
        STATE_MNT="$vol_path"
        break
    fi
done

# Try via docker volume inspect as fallback
if [ -z "$STATE_MNT" ]; then
    STATE_MNT="$($DOCKER volume inspect -f '{{.Mountpoint}}' "$STATE_VOL" 2>/dev/null || echo "")"
fi

if [ -n "$STATE_MNT" ]; then
    STATE_FILE="${STATE_MNT}/state.json"
    TRADES_FILE="${STATE_MNT}/trades.jsonl"
    CONTROL_FILE="${STATE_MNT}/control.json"
fi

# ── 1. Docker containers up ────────────────────────────────────────────────
echo ""
echo "▶ Docker containers"
for c in ib-gateway ats-fx-daemon ats-telegram-bot; do
    status=$($DOCKER inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "missing")
    health=$($DOCKER inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || echo "")
    if [ "$status" = "running" ]; then
        echo "  ✅ $c: running (${health:-no-healthcheck})"
    else
        add_issue "🔴 $c: ${status} (expected running)"
    fi
done

# ── 2. IB Gateway API port listening ───────────────────────────────────────
echo ""
echo "▶ IB Gateway API port"
if $DOCKER exec ib-gateway bash -c "exec 3<>/dev/tcp/127.0.0.1/${GATEWAY_PORT}" 2>/dev/null; then
    echo "  ✅ Gateway API port ${GATEWAY_PORT} listening (inside container)"
else
    add_issue "🔴 Gateway API port ${GATEWAY_PORT} not reachable inside ib-gateway"
fi

if $DOCKER exec ats-fx-daemon bash -c "exec 3<>/dev/tcp/ib-gateway/${SOCAT_PORT}" 2>/dev/null; then
    echo "  ✅ Daemon can reach gateway socat port ${SOCAT_PORT}"
else
    add_issue "🔴 Daemon cannot reach gateway socat port ${SOCAT_PORT}"
fi

# ── 3. fx-daemon state freshness ───────────────────────────────────────────
echo ""
echo "▶ fx-daemon state freshness"
if [ -n "$STATE_FILE" ] && [ -f "$STATE_FILE" ]; then
    mtime=$(stat -c %Y "$STATE_FILE" 2>/dev/null || stat -f %m "$STATE_FILE")
    now=$(date +%s)
    age=$((now - mtime))
    if [ "$age" -gt "$STALE_SECONDS" ]; then
        add_issue "⏰ State file stale — last updated ${age}s ago (> ${STALE_SECONDS}s)"
    else
        echo "  ✅ State file fresh — updated ${age}s ago"
    fi
    last_tick=$($DOCKER exec ats-fx-daemon python3 -c "import json;print(json.load(open('/state/state.json')).get('last_tick','?'))" 2>/dev/null || echo "?")
    echo "     last_tick: $last_tick"
else
    add_issue "⚠️ State file not found (${STATE_FILE:-volume mountpoint not located})"
    # Try reading from inside the container as a fallback
    last_tick=$($DOCKER exec ats-fx-daemon python3 -c "import json;print(json.load(open('/state/state.json')).get('last_tick','?'))" 2>/dev/null || echo "?")
    if [ "$last_tick" != "?" ]; then
        echo "     (container has state.json — last_tick: $last_tick)"
        echo "     host mountpoint not found but container volume is accessible"
    fi
fi

# ── 4. Recent daemon log errors ────────────────────────────────────────────
echo ""
echo "▶ fx-daemon recent log (tail ${LOG_TAIL_LINES})"
daemon_log=$($DOCKER logs --tail ${LOG_TAIL_LINES} ats-fx-daemon 2>&1)
echo "$daemon_log" | sed 's/^/     /' | tail -n 20

if echo "$daemon_log" | grep -qiE "error|exception|traceback|failed|disconnect"; then
    add_issue "⚠️ Errors/exceptions found in recent fx-daemon log"
fi
if echo "$daemon_log" | grep -qi "running data-only mode"; then
    add_issue "🟡 IBKR in data-only mode"
fi

# ── 5. Recent trades ───────────────────────────────────────────────────────
echo ""
echo "▶ Recent trades"
if [ -n "$TRADES_FILE" ] && [ -f "$TRADES_FILE" ]; then
    trades_lines=$(wc -l < "$TRADES_FILE")
    echo "  trades.jsonl: ${trades_lines} lines"
    tail -n 5 "$TRADES_FILE" | sed 's/^/     /'
else
    # Try inside container
    trades_in_container=$($DOCKER exec ats-fx-daemon sh -c "wc -l < /state/trades.jsonl 2>/dev/null || echo 0" 2>/dev/null || echo "0")
    if [ "$trades_in_container" != "0" ] && [ "$trades_in_container" != "" ]; then
        echo "  trades.jsonl (in container): ${trades_in_container} lines"
        $DOCKER exec ats-fx-daemon tail -n 5 /state/trades.jsonl 2>/dev/null | sed 's/^/     /'
    else
        echo "  ℹ️ No trades file yet (no fills)"
    fi
fi

# ── 6. Control switches ────────────────────────────────────────────────────
echo ""
echo "▶ Control switches (control.json)"
if [ -n "$CONTROL_FILE" ] && [ -f "$CONTROL_FILE" ]; then
    cat "$CONTROL_FILE" | sed 's/^/     /'
else
    control_in_container=$($DOCKER exec ats-fx-daemon cat /state/control.json 2>/dev/null || echo "")
    if [ -n "$control_in_container" ]; then
        echo "$control_in_container" | sed 's/^/     /'
    else
        echo "  ℹ️ No control.json (defaults apply)"
    fi
fi

# ── 7. Telegram bot recent log ─────────────────────────────────────────────
echo ""
echo "▶ telegram-bot recent log (tail 10)"
$DOCKER logs --tail 10 ats-telegram-bot 2>&1 | sed 's/^/     /'

# ── 8. System resources ─────────────────────────────────────────────────────
echo ""
echo "▶ System"
echo "  Host:  $(hostname)"
echo "  Load:  $(uptime | sed 's/.*load average://')"
echo "  Disk:  $(df -h /volume1 | tail -1 | awk '{print $3"/"$2" used ("$5")"}')"
echo "  Mem:   $(free -m 2>/dev/null | awk '/Mem/{print $3"MB / "$2"MB used"}' || echo 'n/a')"

# ── 9. Quick Supabase signal check (if python3 + requests available) ────────
echo ""
echo "▶ Supabase signals (last 24h)"
SUPABASE_URL="${SUPABASE_URL:-https://nwatzlrmoefluymhqgwi.supabase.co}"
SUPABASE_ANON_KEY="${SUPABASE_ANON_KEY:-}"
if [ -n "$SUPABASE_ANON_KEY" ]; then
    signals=$(curl -s --max-time 10 \
        "${SUPABASE_URL}/rest/v1/signals?select=id,created_at,status&order=created_at.desc&limit=5" \
        -H "apikey: ${SUPABASE_ANON_KEY}" \
        -H "Authorization: Bearer ${SUPABASE_ANON_KEY}" 2>/dev/null || echo "")
    if [ -n "$signals" ] && [ "$signals" != "[]" ]; then
        echo "$signals" | python3 -m json.tool 2>/dev/null | sed 's/^/     /' || echo "     $signals"
    else
        add_issue "⚠️ No recent signals in Supabase (or query failed)"
    fi
else
    echo "  ℹ️ Set SUPABASE_ANON_KEY env var to check Supabase signals"
fi

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