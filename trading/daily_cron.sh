#!/bin/bash
# ATS Daily Cron — runs every weekday morning (HKT)
# 
# Schedule:
#   08:00 HKT — US screener (8pm ET previous day, post-market data ready)
#   09:00 HKT — HK screener + regime detector + portfolio sync
#
# Crontab entry:
#   0 8 * * 1-5  /bin/bash /Users/kelvinko/dev/ats-landing/trading/daily_cron.sh us
#   0 9 * * 1-5  /bin/bash /Users/kelvinko/dev/ats-landing/trading/daily_cron.sh all
#
# Usage: daily_cron.sh [us|hk|all]

# set -e is intentionally OFF for cron resilience — individual steps capture
# their own exit codes so a single failure won't abort the whole pipeline.
set -uo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$HOME/.local/bin"
# Ensure the trading/ dir is importable (cron may not have it on sys.path)
export PYTHONPATH="${PYTHONPATH:-}:$(cd "$(dirname "$0")/.." && pwd)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$HOME/.hermes/trading/logs"
mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/daily_cron.log" || true
}

run_screener() {
    local market="$1"
    local rc=0
    log "━━━ Running ${market^^} screener ━━━"
    cd "$SCRIPT_DIR"
    python3 -m watchlist.screener --markets "$market" >> "$LOG_DIR/daily_cron.log" 2>&1 || rc=$?
    log "  ${market^^} screener complete (exit=$rc)"
    return $rc
}

run_regime() {
    local rc=0
    log "━━━ Running regime detector ━━━"
    python3 "$SCRIPT_DIR/regime/regime_detector.py" >> "$LOG_DIR/daily_cron.log" 2>&1 || rc=$?
    log "  Regime detector complete (exit=$rc)"
    return $rc
}

run_portfolio_sync() {
    local rc=0
    log "━━━ Running portfolio sync ━━━"
    python3 "$SCRIPT_DIR/regime/sync_positions.py" >> "$LOG_DIR/daily_cron.log" 2>&1 || rc=$?
    log "  Portfolio sync complete (exit=$rc)"
    return $rc
}

run_expire() {
    local rc=0
    log "━━━ Running signal expiry ━━━"
    python3 "$SCRIPT_DIR/regime/expire_signals.py" >> "$LOG_DIR/daily_cron.log" 2>&1 || rc=$?
    log "  Signal expiry complete (exit=$rc)"
    return $rc
}

log "========================================"
log "ATS Daily Cron — mode: ${1:-help}"
log "========================================"

case "${1:-}" in
    us)
        run_screener us
        ;;
    hk)
        run_screener hk
        ;;
    all)
        # Full morning run: regime + both screeners + portfolio sync + expiry
        run_regime
        run_screener us
        run_screener hk
        run_portfolio_sync
        run_expire
        ;;
    *)
        echo "Usage: daily_cron.sh [us|hk|all]"
        echo "  us   — Run US screener only (08:00 HKT)"
        echo "  hk   — Run HK screener only (09:00 HKT)"
        echo "  all  — Full run: regime + US + HK screener + portfolio sync (09:00 HKT)"
        exit 1
        ;;
esac

log "Done."
