# ATS (Automated Trading System) — Complete Knowledge Base

> **Last updated:** June 21, 2026 (Sunday)
> **Status:** ✅ All systems operational after redeploy

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [System Components](#system-components)
3. [Infrastructure & Deployment](#infrastructure--deployment)
4. [Supabase Database Schema](#supabase-database-schema)
5. [Cron Jobs & Scheduling](#cron-jobs--scheduling)
6. [Dashboard](#dashboard)
7. [Known Issues & Fixed Bugs](#known-issues--fixed-bugs)
8. [Operational Runbook](#operational-runbook)
9. [Credentials & Access](#credentials--access)
10. [Nice-to-Have (Not Yet Built)](#nice-to-have-not-yet-built)

---

## Architecture Overview

The ATS is a multi-component automated trading system that runs across two machines:

```
┌─────────────────────────────────────────────────────┐
│  MAC (kelvinko's workstation)                        │
│                                                       │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Cron jobs │  │ Regime       │  │ Watchlist     │  │
│  │ (daily)   │  │ Detector     │  │ Screener      │  │
│  └─────┬─────┘  └──────┬───────┘  └───────┬───────┘  │
│        │               │                  │          │
│        └───────────────┼──────────────────┘          │
│                        │                              │
│                  Supabase REST API                    │
│               (nwatzlrmoefluymhqgwi)                  │
│                        │                              │
└────────────────────────┼──────────────────────────────┘
                         │
┌────────────────────────┼──────────────────────────────┐
│  NAS (KKO-NAS, 100.73.186.101)                        │
│                                                       │
│  ┌──────────────────────┐  ┌──────────────────────┐  │
│  │ ib-gateway            │  │ ats-fx-daemon        │  │
│  │ (IBKR Paper 4002)     │  │ (NautilusTrader)     │  │
│  └──────────┬───────────┘  └──────────┬───────────┘  │
│             │                         │               │
│             └─────────┬───────────────┘               │
│                       │                               │
│              ┌────────┴────────┐                      │
│              │ ats-telegram-bot │                      │
│              │ (Trade Alerts)   │                      │
│              └─────────────────┘                      │
│                                                       │
│  Docker: docker-compose up -d --build                  │
└───────────────────────────────────────────────────────┘
```

---

## System Components

### 1. IB Gateway (NAS Docker)

| Field | Value |
|-------|-------|
| Container | `ib-gateway` |
| Image | `ghcr.io/gnzsnz/ib-gateway:stable` |
| Mode | **Paper** (port 4002) |
| Account | `DUQ538194` |
| Username | `kqpdtu887` |
| Auto-restart | Daily at 11:59 PM HKT |
| 2FA handling | Auto-restart + relogin; push notification to IBKR mobile app |
| VNC | Port 5900 (localhost only, access via SSH tunnel) |
| Healthcheck | bash /dev/tcp probe on port 4002 |

### 2. FX Daemon (NAS Docker)

| Field | Value |
|-------|-------|
| Container | `ats-fx-daemon` |
| Engine | NautilusTrader v1.227.0 (Python 3.12) |
| Strategy | `FourLevelStrategy` (from `strategy_four_level.py`) |
| Pairs traded | NZD/JPY, EUR/USD, AUD/JPY |
| Indicators per pair | SMA(20,50), EMA(20,50), RSI(14), ATR(14) |
| Bar size | 1-HOUR MID |
| Connection | IB Gateway via port 4004 (gnzsnz socat relay) |
| State file | `/state/state.json` (shared volume with telegram-bot) |
| Account balance | ~1,250,016 HKD (paper) |

### 3. Telegram Bot (NAS Docker)

| Field | Value |
|-------|-------|
| Container | `ats-telegram-bot` |
| Reads | `/state/state.json`, `/state/trades.jsonl` |
| Writes | `/state/control.json` (control switches for daemon) |
| Shared volume | `ats-state` |

### 4. Regime Detector (Mac, `trading/regime/regime_detector.py`)

Classifies market regime using 5 factors:
- VIX level
- VIX momentum
- HYG-TLT credit spread
- SPY-QQQ correlation
- US10Y-US2Y yield curve

**Outputs:** `risk_on` / `choppy` / `risk_off` / `crisis` → Supabase `regime` table

**Known bug:** Occasionally produces NaN values that break JSON serialization (line 312). Needs NaN guard.

### 5. Signal Engine & Watchlist Screener (Mac)

- 7-indicator system (zero redundancy)
- GO/WATCH/WAIT badges on dashboard
- ATR(14)-based stop-loss, take-profit, entry zones
- Macro factors: VIX, DXY, HYG-TLT, US10Y-US2Y, USD/CNH

### 6. Portfolio Sync (Mac, `trading/regime/sync_positions.py`)

- Syncs Longbridge positions → Supabase `portfolio` table
- 5 positions currently tracked: GOOG.US, AMZN.US, 9868.HK + 2 more
- **Known issue:** v2 columns (`avg_cost`, `last_price`, `unrealized_pnl`, `allocation_pct`, `vix_zone`) are all NULL

### 7. Signal Lifecycle (Mac/Supabase)

- Status workflow: `pending` → `executed` → `closed` (SL/TP hit) / `expired`
- Auto-expire signals older than 5 trading days (`regime/expire_signals.py`)
- Dashboard shows only pending signals

### 8. Base Yield Bucket (Mac, `trading/base_yield.py`)

Three mechanical yield strategies:
- **SPY Overnight Gap** — Buy at close, sell at open (~7-8% annualized)
- **FX Carry** — AUD/JPY, NZD/JPY interest rate differential
- **QQQ Covered Calls** — Simulated weekly option writing

**Known issue:** `yield_signals` table does not exist in Supabase (migration was never applied).

### 9. Performance Analytics (Mac, `trading/perf_analytics.py`)

- Win rate, Sharpe ratio, drawdown estimates
- Queries Supabase signals table for lifecycle analytics
- Strategy and bucket breakdowns

---

## Infrastructure & Deployment

### Files on NAS

| Path | Purpose |
|------|---------|
| `/volume1/docker/ats-landing/` | Git checkout of ats-landing repo |
| `/volume1/docker/ats-landing/trading/nautilus_port/` | Docker Compose + Python files |
| `/volume1/docker/ats-landing/trading/nautilus_port/.env` | Credentials (root-owned, 600) |
| `/volume1/docker/ats-landing/trading/nautilus_port/docker-compose.yml` | Container definitions |

### Redeploy Commands (run on NAS SSH)

```bash
cd /volume1/docker/ats-landing
git stash
git pull origin main
cd trading/nautilus_port
sudo docker-compose up -d --build
```

### Verify Health

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ats|ib-'

# Expected output:
# ats-fx-daemon           Up X minutes
# ib-gateway              Up X minutes (healthy)
# ats-telegram-bot        Up X minutes
```

### View Logs

```bash
# FX Daemon (live trading activity)
sudo docker logs -f ats-fx-daemon

# IB Gateway (connection/auth issues)
sudo docker logs --tail 50 ib-gateway

# Telegram Bot
sudo docker logs --tail 30 ats-telegram-bot
```

### Check for 2FA Required

```bash
sudo docker logs ib-gateway --tail 20 | grep -i "2FA\|second factor\|authentication"
```

---

## Supabase Database Schema

**URL:** `https://nwatzlrmoefluymhqgwi.supabase.co`
**Project ref:** `nwatzlrmoefluymhqgwi`

### Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `regime` | Market regime classification | `regime_name`, `vix_level`, `activated_groups`, `created_at` |
| `signals` | Trading signals with lifecycle | `ticker`, `status`, `created_at`, `executed_at`, `closed_at`, `close_reason` |
| `indicator_signals` | Raw indicator outputs | `atr_value`, `stop_loss`, `take_profit`, `bb_upper`, `bb_lower`, `entry_zone_low`, `entry_zone_high`, `risk_reward` |
| `portfolio` | Position tracking | `ticker`, `bucket`, `position_qty`, `market_value`, `avg_cost`, `last_price`, `unrealized_pnl`, `allocation_pct`, `vix_zone`, `snapshot_at` |
| `yield_signals` | ❌ DOES NOT EXIST — migration never applied |

### Migrations (in order)

1. `20260601000001_enable_realtime.sql` — Supabase realtime for signals table
2. `20260601000004_enable_rls.sql` — Row-level security policies (anon SELECT)
3. `20260613000000_add_atr_and_bb.sql` — ATR, Bollinger Bands, entry zone columns
4. `20260614000000_create_portfolio.sql` — Portfolio table creation
5. `20260614000001_add_signal_lifecycle.sql` — Status, execution, close columns + indexes
6. `20260614000002_alter_portfolio_v2.sql` — v2 columns (avg_cost, last_price, etc.)

### Quick Queries (API)

```bash
# Latest regime
curl -s "https://nwatzlrmoefluymhqgwi.supabase.co/rest/v1/regime?select=*&order=created_at.desc&limit=1" \
  -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <ANON_KEY>"

# Recent signals
curl -s "https://nwatzlrmoefluymhqgwi.supabase.co/rest/v1/signals?select=id,ticker,status,created_at&order=created_at.desc&limit=10" \
  -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <ANON_KEY>"

# Portfolio count
curl -sI "https://nwatzlrmoefluymhqgwi.supabase.co/rest/v1/portfolio?select=count" \
  -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <ANON_KEY>" -H "Prefer: count=exact" \
  | grep content-range
```

---

## Cron Jobs & Scheduling

All cron jobs run on the **Mac** (not the NAS):

```cron
# Install with: crontab /Users/kelvinko/dev/ats-landing/trading/ats.crontab
0 8 * * 1-5  /bin/bash /Users/kelvinko/dev/ats-landing/trading/daily_cron.sh us
0 9 * * 1-5  /bin/bash /Users/kelvinko/dev/ats-landing/trading/daily_cron.sh all
```

| Time (HKT) | Day | Job |
|-------------|-----|-----|
| 08:00 | Mon-Fri | US screener only |
| 09:00 | Mon-Fri | Regime detector + all screeners + portfolio sync |

**Log:** `~/.hermes/trading/logs/daily_cron.log`

**Today (Sunday):** No runs scheduled. Next run: Monday June 22, 08:00 HKT.

---

## Dashboard

- **URL:** https://ats.coolpaperplane.win/dashboard
- **Hosting:** Vercel
- **Auth:** Cloudflare Access (Zero Trust) — behind `kkwko.cloudflareaccess.com` login
- **Features:** Regime banner → signal feed → entry cards → portfolio panel
- **Realtime:** Supabase realtime subscriptions

---

## Known Issues & Fixed Bugs

### Active Issues

| # | Issue | Severity | Fix |
|---|-------|----------|-----|
| 1 | `yield_signals` table missing in Supabase | Medium | Apply the missing migration to create the table |
| 2 | Portfolio v2 columns all NULL (`avg_cost`, `last_price`, etc.) | Medium | Debug `sync_positions.py` — it updates timestamps but not these columns |
| 3 | `regime_detector.py` crashes with `InvalidJSONError: NaN` | Low | Add NaN guard before `requests.post()` at line 312 |
| 4 | No fresh signals since June 10 (all expired) | Low | Expected — markets closed weekend. Monitor Monday. |
| 5 | Dashboard behind Cloudflare Access | Info | Adjust Zero Trust policy if public access needed |

### Resolved

| # | Issue | Resolution |
|---|-------|------------|
| 1 | All 3 ATS containers missing from NAS | Redeployed with `sudo docker-compose up -d --build` on June 21 |
| 2 | IB Gateway connection | Connected successfully after redeploy (v223, account DUQ538194) |

---

## Operational Runbook

### Weekly: IBKR 2FA Re-authentication

IBKR paper accounts require 2FA every ~7 days. The gateway's `docker-compose.yml` handles this with:
- `AUTO_RESTART_TIME: "11:59 PM"` — daily credential re-login
- `TWOFA_TIMEOUT_ACTION: restart` + `RELOGIN_AFTER_TWOFA_TIMEOUT: yes` — auto-retry on 2FA failure

**What you do:** When IBKR pushes a 2FA notification to your mobile app, tap **Approve**. That's it.

**Alternative:** VNC into the gateway:
```bash
ssh -L 5900:127.0.0.1:5900 nas
# Then open vnc://127.0.0.1:5900 with VNC_SERVER_PASSWORD from .env
```

### Daily: Check Daemon Health

```bash
ssh nas
sudo docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ats|ib-'
sudo docker logs --tail 20 ats-fx-daemon | grep -iE "error|exception|warning"
```

### Redeploy After Code Changes

```bash
ssh nas
cd /volume1/docker/ats-landing && git stash && git pull origin main
cd trading/nautilus_port && sudo docker-compose up -d --build
```

### Market Hours

FX markets open **Monday 05:00 Sydney / 03:00 HKT** and close **Friday 17:00 NY / Saturday 05:00 HKT**. The daemon idles outside these hours.

---

## Credentials & Access

| What | Value | Location |
|------|-------|----------|
| Supabase URL | `https://nwatzlrmoefluymhqgwi.supabase.co` | `.env.local` (Mac) |
| Supabase Anon Key | `eyJhbGciOiJIUzI1NiIs...` | `.env.local` (Mac) |
| IBKR Paper Username | `kqpdtu887` | NAS `.env` |
| IBKR Paper Account | `DUQ538194` | NAS `.env` |
| NAS SSH | `ssh nas` (kelvinko@100.73.186.101) | `~/.ssh/config` |
| NAS sudo password | (not stored in any config) | Manual entry |
| IB Gateway VNC password | `VNC_SERVER_PASSWORD` | NAS `.env` |
| Telegram Bot Token | `TELEGRAM_BOT_TOKEN` | NAS `.env` |
| Dashboard URL | `https://ats.coolpaperplane.win/dashboard` | Vercel + Cloudflare Access |

---

## Nice-to-Have (Not Yet Built)

| # | Item | Effort | Priority |
|---|------|--------|----------|
| 1 | HK signal engine integration — GO/WATCH/WAIT treatment like US | ~4 hours | Medium |
| 2 | US intraday pre-market check at 20:30 HKT | ~2 hours | Low |
| 3 | Apply `yield_signals` table migration | ~30 min | Low |
| 4 | Fix portfolio v2 column population | ~2 hours | Medium |

---

## File Index

```
ats-landing/
├── trading/
│   ├── ATS_KNOWLEDGE.md          ← THIS FILE
│   ├── ATS_STATUS.md             ← Previous status snapshot (June 15)
│   ├── ARCH.md                   ← Architecture decisions
│   ├── OPERATIONS.md             ← Operations guide
│   ├── STRATEGIC_REVIEW.md       ← Strategic priorities
│   ├── TECH_SPEC.md              ← Technical specification
│   ├── USER_MANUAL.md            ← User manual
│   ├── ats.crontab               ← Cron schedule
│   ├── daily_cron.sh             ← Daily cron runner
│   ├── base_yield.py             ← Yield strategies
│   ├── perf_analytics.py         ← Performance analytics
│   ├── supabase_writer.py        ← Supabase write helper
│   ├── regime/
│   │   ├── regime_detector.py    ← Regime classifier
│   │   ├── expire_signals.py     ← Signal expiry cleaner
│   │   └── sync_positions.py     ← Portfolio sync
│   ├── nautilus_port/
│   │   ├── docker-compose.yml    ← Container definitions
│   │   ├── Dockerfile            ← FX daemon image
│   │   ├── Dockerfile.bot        ← Telegram bot image
│   │   ├── .env                  ← Credentials (NAS only)
│   │   ├── run_live.py           ← Live trading entrypoint
│   │   ├── strategy_four_level.py← Nautilus strategy
│   │   ├── telegram_bot.py       ← Bot logic
│   │   └── REDEPLOY_COMMANDS.txt ← Quick redeploy reference
│   ├── watchlist/                ← Screener + signal engine
│   │   ├── config_strategies.yaml
│   │   ├── signal_engine.py
│   │   └── strategies/
│   └── indicators/
│       └── calculate.py
├── supabase/migrations/          ← Database schema
├── pages/dashboard/              ← Next.js dashboard
└── .env.local                    ← Supabase keys (Mac)