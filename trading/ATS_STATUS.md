# ATS Build Status — June 15, 2026

> Auto-generated summary of the entire Automated Trading System build.

## Overall Progress

Per STRATEGIC_REVIEW.md: **7/9 priority items complete** (78%). The remaining two are
medium-term items (Base Yield bucket, Performance analytics).

| Priority | Items | Status |
|----------|:-----:|:------:|
| Immediate | 3 | ✅ All done |
| Short-term | 3 | ✅ All done |
| Medium-term | 3 | 🔶 1 of 3 done |

---

## System Components

### 1. IB Gateway + FX Daemon (NAS Docker) ✅ JUST COMPLETED

| Component | Status | Details |
|-----------|:------:|---------|
| `ib-gateway` container | ✅ Healthy | Paper account `DUQ538194`, port 4002 |
| `ats-fx-daemon` container | ✅ Running | NautilusTrader 4-level signal engine |
| `ats-telegram-bot` container | ✅ Running | Trade alerts |

- Trading `AUD/JPY`, `EUR/USD`, `NZD/JPY` on IBKR paper
- Portfolio: ~$1,084,080 HKD simulated
- Daily auto-restart at 11:59 PM
- Cloud-synced settings (no future VNC needed)

### 2. Regime Detector ✅

- 5-factor classification: VIX, VIX momentum, HYG-TLT spread, SPY-QQQ correlation, US10Y-US2Y yield curve
- Publishes to Supabase `regime` table
- Dashboard banner: risk_on / choppy / risk_off / crisis

### 3. Signal Engine & Conflict Resolution ✅

- 7-indicator system (zero redundancy)
- GO/WATCH/WAIT badges on dashboard cards
- Aligned/Caution/Conflict zones from screener + indicator agreement
- Sortable, filtered signal feed

### 4. ATR-Adaptive Entry Plans ✅

- ATR(14)-based stop-loss, take-profit, entry zones
- Risk/reward ratios surfaced
- Suggested position sizing per strategy type
- Expandable entry cards on dashboard

### 5. Macro Factors ✅

- VIX, DXY (existing)
- HYG-TLT credit spread (added)
- US10Y-US2Y yield curve (added)
- USD/CNH (CNY/USD) for HK stock flows

### 6. Portfolio Tracking ✅

- Full position table: cost basis, P&L, VIX zone, allocation %
- Dashboard portfolio panel with concentration gauge
- Signal deduplication: held positions show blue HELD badge
- Sync from Longbridge to Supabase (`sync_positions.py`)

### 7. Signal Lifecycle ✅

- Status column: pending → executed → closed → expired
- Auto-expire signals older than 5 trading days
- Dashboard shows only pending signals

### 8. Daily Cron ✅

| Time | Job |
|:-----|-----|
| 08:00 HKT | US screener (`daily_cron.sh us`) |
| 09:00 HKT | Regime detector + all screeners + portfolio sync |

Logs at `~/.hermes/trading/logs/daily_cron.log`

### 9. Dashboard ✅

- Next.js app (local or Vercel)
- Regime banner → signal feed → entry cards → portfolio panel
- Supabase realtime subscriptions

---

## Remaining Work

| # | Item | Priority | Effort |
|---|------|----------|--------|
| 1 | **Base Yield bucket** — SPY overnight gap, FX carry, QQQ covered calls | Medium | ~1-2 days |
| 2 | **Performance analytics** — Win rate by strategy, Sharpe by bucket, drawdown tracking | Medium | ~1-2 days |
| 3 | **HK signal engine integration** — Same GO/WATCH/WAIT treatment as US | Nice-to-have | ~4 hours |
| 4 | **US intraday pre-market check** — Scan US watchlist at 20:30 HKT | Nice-to-have | ~2 hours |

---

## Key Credentials (all stored in NAS `.env`)

| What | Value | Location |
|------|-------|----------|
| IBKR paper username | `kqpdtu887` | NAS `.env` |
| IBKR paper account | `DUQ538194` | NAS `.env` |
| Mac Gateway (s3store sync source) | `kqpdtu887` / Paper | Mac `Jts/jts.ini` |
| NAS SSH | `ssh nas` (kelvinko@100.73.186.101) | Mac `~/.ssh/config` |

## Quick Commands

```bash
# NAS — check all containers
ssh nas
cd /volume1/docker/ats-landing/trading/nautilus_port
sudo docker ps

# NAS — tail daemon logs
sudo docker logs -f ats-fx-daemon

# NAS — tail gateway logs
sudo docker logs -f ib-gateway

# NAS — restart stack
sudo docker-compose down && sudo docker-compose up -d

# Mac — launch IB Gateway locally (for cloud sync debugging)
open "/Users/kelvinko/Desktop/IB Gateway 10.47"