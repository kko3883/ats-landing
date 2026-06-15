# ATS Technical Specification — June 2026

> Technical reference for all settings, connections, ports, and architecture.
> No passwords or API keys included.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  MAC (kelvin-m5max, Tailscale 100.120.0.106)                    │
│                                                                 │
│  /Users/kelvinko/dev/ats-landing/                               │
│  ├── trading/              ← Python signal engine & scripts     │
│  ├── pages/dashboard/      ← Next.js dashboard source           │
│  ├── supabase/migrations/  ← Database schema (applied)          │
│                                                                 │
│  CRON (macOS):                                                   │
│    08:00 HKT → daily_cron.sh us      (US screener)              │
│    09:00 HKT → daily_cron.sh all     (regime+US+HK+portfolio)   │
│                                                                 │
│  IB Gateway 10.47.app  →  port 4002 (paper, on-demand)         │
│  ~/Jts/jts.ini         →  s3store=true, cloud sync enabled      │
└─────────────────────────────────────────────────────────────────┘
                              │
                    HTTPS / REST API
                              │
┌─────────────────────────────────────────────────────────────────┐
│  SUPABASE CLOUD (nwatzlrmoefluymhqgwi.supabase.co)              │
│                                                                 │
│  Tables:  signals, indicator_signals, regime, portfolio         │
│           watchlist_hk, yield_signals, perf_analytics            │
│  RLS:     anon read / service_role write                        │
│  Realtime: signals + portfolio live                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                    ANON KEY (public read)
                              │
┌─────────────────────────────────────────────────────────────────┐
│  VERCEL                                                         │
│                                                                 │
│  Dashboard:  ats.coolpaperplane.win/dashboard                   │
│  Source:     /pages/dashboard/index.js                          │
│  Framework:  Next.js 15 + Tailwind CSS                          │
│  Data:       Supabase realtime subscriptions                    │
└─────────────────────────────────────────────────────────────────┘
                              │
               NAS SSH via Tailscale (port 4002)
                              │
┌─────────────────────────────────────────────────────────────────┐
│  SYNOLOGY NAS (kko-nas, Tailscale 100.73.186.101)               │
│                                                                 │
│  SSH:        kelvinko@100.73.186.101  (alias: `ssh nas`)        │
│  Docker:     /volume1/docker/ats-landing/trading/nautilus_port/  │
│                                                                 │
│  Containers:                                                     │
│    ib-gateway       →  IB Gateway (paper), port 4002            │
│    ats-fx-daemon    →  NautilusTrader 4-level FX engine         │
│    ats-telegram-bot →  Trade notifications                      │
│                                                                 │
│  Auto-heal:     willfarrell/autoheal                            │
│  Disk:          /volume1 (14TB, currently 39% used)             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Network & Ports

| Component | Host | Port | Protocol | Direction | Notes |
|-----------|------|:----:|----------|-----------|-------|
| IB Gateway API | NAS (ib-gateway container) | 4002 | TCP | Inbound (localhost only) | TWS API port |
| Socat relay | NAS (ib-gateway container) | 4004 | TCP | Internal | Relays 4004→4002 for containers |
| VNC (x11vnc) | NAS (ib-gateway container) | 5900 | TCP | Inbound (localhost only) | Reachable via SSH tunnel |
| Supabase REST | supabase.co | 443 | HTTPS | Outbound | API calls from Mac + dashboard |
| Supabase Realtime | supabase.co | 443 | WSS | Outbound | Dashboard subscriptions |
| Dashboard | Vercel | 443 | HTTPS | Inbound | User-facing web app |
| SSH (NAS) | kko-nas | 22 | SSH | Inbound | Via Tailscale only |
| SSH (GitHub) | github.com | 443/22 | HTTPS/SSH | Outbound | Git push/pull |
| Tailscale | Various | 41641 | UDP/WireGuard | Both | VPN mesh network |
| Mac Gateway | kelvin-m5max | 4002 | TCP | Inbound (localhost only) | On-demand for cloud sync |

---

## Docker Containers (NAS)

### ib-gateway
| Setting | Value |
|---------|-------|
| Image | `ghcr.io/gnzsnz/ib-gateway:stable` |
| Container name | `ib-gateway` |
| Restart policy | `unless-stopped` |
| Port mapping | `127.0.0.1:4002:4002`, `127.0.0.1:5900:5900` |
| Healthcheck | `bash -c 'exec 3<>/dev/tcp/127.0.0.1/4002'` every 30s, 240s start period |
| Trading mode | `paper` |
| Auto-restart time | `11:59 PM` |
| 2FA timeout action | `restart` |
| Autoheal label | `autoheal=true` |

### ats-fx-daemon
| Setting | Value |
|---------|-------|
| Build | `Dockerfile` (Python 3.12 + nautilus_trader[ib]==1.227.0) |
| Container name | `ats-fx-daemon` |
| Restart policy | `unless-stopped` |
| Dependencies | `ib-gateway: service_healthy` |
| IBKR host | `ib-gateway` (Docker compose service name) |
| IBKR port | `4004` (socat relay) |
| Instruments | `AUD/JPY`, `EUR/USD`, `NZD/JPY` |
| State path | `/state/state.json` |
| Control path | `/state/control.json` |
| Trades path | `/state/trades.jsonl` |
| Timezone | `Asia/Hong_Kong` |

### ats-telegram-bot
| Setting | Value |
|---------|-------|
| Build | `Dockerfile.bot` (Python 3.12) |
| Container name | `ats-telegram-bot` |
| Restart policy | `unless-stopped` |
| State path | `/state/state.json` (read-only) |
| Control path | `/state/control.json` (read-write) |
| Trades path | `/state/trades.jsonl` (read-only) |
| Timezone | `Asia/Hong_Kong` |

---

## Environment Variables

### NAS `.env` (ib-gateway)
| Variable | Description | Example |
|----------|-------------|---------|
| `TWS_USERID` | Second IBKR username for Gateway | Set on NAS |
| `TWS_PASSWORD` | Gateway password | Set on NAS |
| `IB_ACCOUNT_ID` | Paper account ID (starts with DU) | `DUQ538194` |
| `VNC_SERVER_PASSWORD` | Password for x11vnc (first-time setup only) | Set on NAS |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather | Optional |
| `TELEGRAM_CHAT_ID` | Telegram chat ID from @userinfobot | Optional |

### Mac `.env.local` (Dashboard)
| Variable | Description | Example |
|----------|-------------|---------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL | `https://nwatzlrmoefluymhqgwi.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anonymous (public) key | Set on Mac |

### macOS Keychain (Supabase)
| Service | Account | Used By |
|---------|---------|---------|
| `ats-supabase` | `service_role` | `supabase_writer.py`, `base_yield.py`, `perf_analytics.py` |

---

## Supabase Tables

| Table | Purpose | Writable By | Columns |
|-------|---------|:-----------:|---------|
| `signals` | Trading signals (US/HK stocks) | service_role | id, ticker, direction, bucket, vix_zone, signal_json, status, created_at |
| `indicator_signals` | Hourly indicator readings | service_role | ticker, composite_score, rsi, macd, etc. |
| `regime` | Market regime classification | service_role | regime, vix, vix_momentum, hyg_tlt, spy_qqq_corr, yield_curve |
| `portfolio` | Holdings and P&L | service_role | ticker, quantity, cost_basis, pnl, allocation_pct |
| `watchlist_hk` | HK watchlist candidates | service_role | symbol, candidate_type, beta_vix, beta_dxy, rs_zscore, beta_group |
| `yield_signals` | Base Yield bucket signals | service_role | strategy, symbol, direction, signal_score, yield_pct, metadata |
| `perf_analytics` | Performance analytics reports | service_role | signal_stats, sharpe_estimates, drawdown_estimates |

RLS Policy: `anon` role can SELECT all tables. `service_role` can INSERT/UPDATE/DELETE.

---

## Python Scripts

### Signal Engine (`/trading/` on Mac)

| Script | Purpose | Runs On | Schedule |
|--------|---------|:-------:|----------|
| `regime/regime_detector.py` | 5-factor market regime classification | Mac | Daily 09:00 |
| `watchlist/screener.py` | US/HK stock screening pipeline | Mac | Daily 08:00/09:00 |
| `hk_signal.py` | HK stock signal generator | Mac | Manual/on-demand |
| `supabase_writer.py` | Publish signals to Supabase | Mac | After screener |
| `regime/sync_positions.py` | Sync Longbridge positions to Supabase | Mac | Daily 09:00 |
| `base_yield.py` | Base Yield bucket (3 strategies) | Mac | Manual/on-demand |
| `perf_analytics.py` | Performance analytics report | Mac | Manual/on-demand |

### FX Daemon (`/nautilus_port/` on NAS)

| Script | Purpose | Language |
|--------|---------|----------|
| `strategy_four_level.py` | 4-level signal engine | Python |
| `run_live.py` | NautilusTrader + IB adapter | Python |
| `run_backtest.py` | Backtest engine skeleton | Python |
| `telegram_bot.py` | Telegram trade alerts | Python |

---

## File Paths

| Path | Location | Description |
|------|:--------:|-------------|
| `~/dev/ats-landing/` | Mac | Git repo root |
| `~/dev/ats-landing/trading/` | Mac | All Python scripts |
| `~/dev/ats-landing/pages/dashboard/` | Mac | Next.js dashboard source |
| `~/Jts/jts.ini` | Mac | IB Gateway settings (cloud sync) |
| `~/.ssh/config` | Mac | SSH alias (`nas` → 100.73.186.101) |
| `~/.hermes/trading/` | Mac | Signal outputs, caches, logs |
| `/volume1/docker/ats-landing/trading/nautilus_port/` | NAS | Docker project root |
| `/volume1/docker/ats-landing/trading/nautilus_port/.env` | NAS | Credentials (chmod 600) |
| `/volume1/docker/ats-landing/trading/nautilus_port/docker-compose.yml` | NAS | Container definitions |
| `/home/ibgateway/Jts/jts.ini` | NAS (container) | Gateway runtime config |
| `/home/ibgateway/ibc/config.ini` | NAS (container) | IBC automation config |

---

## Jts.ini Settings

### Mac (`~/Jts/jts.ini`)
```ini
[IBGateway]
ApiOnly=true
TrustedIPs=127.0.0.1
LocalServerPort=4002

[Logon]
s3store=true        # Cloud sync — API settings persist
tradingMode=p       # Paper trading
Locale=en
UseSSL=true
TimeZone=Asia/Hong_Kong
```

### NAS (container-managed)
The container creates its own `jts.ini` on first boot. Key settings:
- `ApiOnly=true` — no TWS UI, API only
- `TrustedIPs=127.0.0.1` — localhost only
- `LocalServerPort=4002` — must match docker port mapping
- `s3store=true` — pulls API settings from IBKR cloud

---

## IBC Configuration (Container)

Generated by the gnzsnz entrypoint from environment variables:

| IBC Setting | Env Source | Value |
|-------------|-----------|-------|
| `IbLoginId` | `TWS_USERID` | From `.env` |
| `IbPassword` | `TWS_PASSWORD` | From `.env` |
| `TradingMode` | `TRADING_MODE` | `paper` |
| `AcceptIncomingConnectionAction` | `TWS_ACCEPT_INCOMING` | `accept` |
| `ReadOnlyApi` | `READ_ONLY_API` | `no` |
| `ExistingSessionDetectedAction` | — | `primary` |
| `AutoRestartTime` | `AUTO_RESTART_TIME` | `11:59 PM` |
| `ReloginAfterSecondFactorAuthenticationTimeout` | — | `yes` |
| `SecondFactorAuthenticationTimeout` | — | `180` seconds |
| `ExitAfterSecondFactorAuthenticationTimeout` | — | `no` |

---

## NautilusTrader Strategy Config

| Setting | Value |
|---------|-------|
| Strategy name | `FourLevelStrategy` |
| Instruments | `AUD/JPY.IDEALPRO`, `EUR/USD.IDEALPRO`, `NZD/JPY.IDEALPRO` |
| Account | `IB-DUQ538194` (paper) |
| Stop loss type | `TrailingStopMarketOrder` |
| Stop TIF | `GTC` |
| Signal levels | 4 (SMA/EMA structure → RSI exhaustion → entry signals) |
| Polling mode | Adaptive (120s calm → 5s near trigger) |
| Data source | IBKR 1h bars via ib_async |

---

## Database Migrations Applied

| Migration | Purpose | Date |
|-----------|---------|------|
| `20260614000000_create_portfolio.sql` | Portfolio position tracking table | June 14, 2026 |
| `20260614000001_add_signal_lifecycle.sql` | Signal status column (pending/executed/closed/expired) | June 14, 2026 |

---

## Cron Jobs

Installed via `crontab /Users/kelvinko/dev/ats-landing/trading/ats.crontab`:

| Time | Command | Purpose |
|:-----|---------|---------|
| 08:00 HKT weekdays | `daily_cron.sh us` | US stock screener |
| 09:00 HKT weekdays | `daily_cron.sh all` | Regime + US + HK + portfolio sync + base yield |

Log path: `~/.hermes/trading/logs/daily_cron.log`

---

## SSH Configuration

### `~/.ssh/config` (Mac)
```
Host nas
    HostName 100.73.186.101
    User kelvinko
    IdentityFile ~/.ssh/id_ed25519
```

### Key Info
| Attribute | Value |
|-----------|-------|
| Key type | ED25519 |
| Public key fingerprint | SHA256:mP0CTQrUsgltw+eYrZsw1OfDEfdXFY6Skrcblvxo1lU |
| NAS user | `kelvinko` |
| Tailscale IP | `100.73.186.101` |

---

## Tailscale Mesh

| Device | Tailscale IP | OS | Status |
|--------|:-----------:|----|:------:|
| kelvin-m5max | 100.120.0.106 | macOS | Active |
| kko-nas | 100.73.186.101 | Linux (Synology DSM) | Active |
| ipad-pro-11-gen-3 | 100.68.124.55 | iOS | Active |
| kelvins-macbook-pro | 100.123.8.99 | macOS | Active |
| kelvink-pc2 | 100.108.13.87 | Windows | Offline |
| iphone-kk-3883 | 100.103.54.41 | iOS | Offline |

---

## Git Repository

| Setting | Value |
|---------|-------|
| Remote | `https://github.com/kko3883/ats-landing.git` |
| Branch | `claude/nautilus-fx-deploy` |
| Local path | `/Users/kelvinko/dev/ats-landing/` |

---

## Software Versions

| Component | Version |
|-----------|---------|
| macOS | Tahoe (ARM64) |
| Synology DSM | Linux 4.4.302+ |
| Docker Compose | V1 (Synology Container Manager) |
| IB Gateway | 10.45.1g |
| IBC | 3.23.0 |
| NautilusTrader | 1.227.0 |
| Python | 3.12 (NAS), 3.14 (Mac) |
| Next.js | 15 |
| React | 19 |
| Supabase SDK | 2 (JS), via REST API (Python) |
| Tailscale | Latest |
| yfinance | 0.2.x |
| pandas | 2.x |
| numpy | 2.x |

---

## Performance Estimates

From `perf_analytics.py` (June 15, 2026):

| Strategy | Total Signals | Execution Rate | Status |
|----------|:------------:|:-------------:|--------|
| donchian_breakout | 6 | 0% | All expired |
| hk_breakout | 9 | 0% | All expired |
| hk_pullback | 18 | 0% | All expired |

| Bucket | Est. Sharpe | Note |
|--------|:----------:|------|
| Base Yield | 1.2 | Mechanical, low vol |
| Alpha | 0.8 | Higher vol, higher return |
| Convexity | 0.3 | Insurance — negative carry |

---

## Recovery Procedures

### Full System Restart
1. NAS: `sudo docker-compose down && sudo docker-compose up -d`
2. Wait 4 minutes for gateway healthcheck
3. Verify: `sudo docker ps` — all 3 containers `(healthy)` or `Up`
4. Dashboard: `https://ats.coolpaperplane.win/dashboard`

### IB Gateway Cloud Sync Lost
1. Stop NAS gateway: `sudo docker-compose stop ib-gateway`
2. Start Mac Gateway, login with paper account, verify API enabled
3. Wait 60s for cloud upload (s3store=true)
4. Stop Mac Gateway
5. Start NAS gateway: `sudo docker-compose start ib-gateway`

### Fresh NAS Deployment
1. SSH: `ssh nas`
2. Copy project: see `nautilus_port/DEPLOY.md`
3. Create `.env` from `.env.example`
4. `sudo docker-compose up -d --build`
5. VNC or Mac sync for API enable (first time only)

---

## Dashboard Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 15 (App Router) |
| Styling | Tailwind CSS 3 |
| Database | Supabase (PostgreSQL) |
| Realtime | Supabase Realtime (WebSocket) |
| Hosting | Vercel (Hobby plan) |
| Domain | `ats.coolpaperplane.win` (custom) |
| Auth | Supabase anon key (public read) |

### Dashboard Pages
| Route | Content |
|-------|---------|
| `/` | Landing page |
| `/dashboard` | Regime banner + signal feed + entry cards + portfolio panel |

### Dashboard Data Flow
```
Cron (Mac) → Python scripts → Supabase REST API (INSERT)
    ↓
Supabase Realtime (WebSocket)
    ↓
Dashboard (Next.js) → user sees live updates