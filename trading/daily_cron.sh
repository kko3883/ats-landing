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

set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$HOME/.local/bin"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$HOME/.hermes/trading/logs"
mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/daily_cron.log"
}

run_screener() {
    local market="$1"
    log "━━━ Running ${market^^} screener ━━━"
    cd "$REPO_DIR"
    python3 -m watchlist.screener --markets "$market" >> "$LOG_DIR/daily_cron.log" 2>&1
    log "  ${market^^} screener complete (exit=$?)"
}

run_regime() {
    log "━━━ Running regime detector ━━━"
    python3 "$SCRIPT_DIR/regime/regime_detector.py" >> "$LOG_DIR/daily_cron.log" 2>&1
    log "  Regime detector complete (exit=$?)"
}

run_portfolio_sync() {
    log "━━━ Running portfolio sync ━━━"
    python3 "$SCRIPT_DIR/regime/sync_positions.py" >> "$LOG_DIR/daily_cron.log" 2>&1
    log "  Portfolio sync complete (exit=$?)"
}

run_expire() {
    log "━━━ Running signal expiry ━━━"
    python3 "$SCRIPT_DIR/regime/expire_signals.py" >> "$LOG_DIR/daily_cron.log" 2>&1
    log "  Signal expiry complete (exit=$?)"
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
