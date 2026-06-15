# ATS User Manual — June 2026

> How to use the Automated Trading System day-to-day.

## What the ATS Does

The ATS is a 3-bucket trading system that:

| Bucket | Allocation | Strategy | Auto-Trade? |
|--------|:---------:|----------|:----------:|
| **Base Yield** | 50% | SPY overnight gap, FX carry, QQQ covered calls | ❌ Signal only — you execute manually |
| **Alpha** | 35% | Donchian breakout, golden cross, pullback/mean reversion | ❌ Signal only |
| **FX (Nautilus)** | Separate account | 4-level signal engine on AUD/JPY, EUR/USD, NZD/JPY | ✅ Auto on IBKR paper |

Signals appear on the dashboard. You review them and execute trades manually in your broker app. The FX daemon runs autonomously on the NAS.

---

## Daily Routine

### Morning (09:00–09:05 HKT)

1. **Open the dashboard:** `https://ats.coolpaperplane.win/dashboard`
2. **Check the regime banner** (top of page):
   - 🟢 **TRENDING** → Focus on breakout/momentum strategies
   - 🟡 **CHOPPY** → Focus on pullback/mean reversion
   - 🔴 **RISK OFF** → Only defensive positions
   - ⚫ **CRISIS** → Cash is a position
3. **Scan signal cards** — look for **🟢 GO** badges
4. **Click a GO card** → review the entry plan:
   - Entry zone (price range)
   - Stop loss (ATR-adaptive)
   - Take profit (target 1 & 2)
   - Risk/reward ratio
   - Suggested position size
5. **Execute** in your broker app (Longbridge for HK stocks, IBKR for US stocks)

### Hourly (during market hours)

- Refresh the dashboard
- Check if **🟡 WATCH** cards flipped to **🟢 GO**
- Check if any open positions hit stop-loss or take-profit

### End of Day (16:30 HKT)

- Review open positions against updated dashboard signals
- If a held stock flips from LONG to SHORT → consider exiting
- Journal: what worked, what didn't

---

## How to Check System Status

### NAS Docker Containers

```bash
ssh nas
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
```

You should see 3 containers running:
- `ib-gateway` — **(healthy)** ← most important
- `ats-fx-daemon` — Running
- `ats-telegram-bot` — Running

### Gateway API Port

```bash
ssh nas
sudo docker exec ib-gateway bash -c 'exec 3<>/dev/tcp/127.0.0.1/4002 && echo "OK"'
```

Should print `OK`. If "Connection refused", see Troubleshooting.

### FX Daemon

```bash
ssh nas
sudo docker logs ats-fx-daemon --tail 20
```

Look for `OrderUpdated` or `Portfolio Updated` — means it's connected and monitoring.

### Dashboard

Open `https://ats.coolpaperplane.win/dashboard`. If it shows "Loading..." for more than 5 seconds, check Supabase status at `https://status.supabase.com`.

---

## Signal Card Legend

| Badge | Color | Meaning | Action |
|-------|-------|---------|--------|
| **GO** | 🟢 Green | Screener + indicators agree | Enter position |
| **WATCH** | 🟡 Yellow | One neutral, one directional | Add to watchlist |
| **WAIT** | 🔴 Red | Opposing signals | Wait for alignment |
| **HELD** | 🔵 Blue | Already in portfolio | No duplicate entry |

---

## FX Daemon Management

### Restart the Stack
```bash
ssh nas
cd /volume1/docker/ats-landing/trading/nautilus_port
sudo docker-compose down
sudo docker-compose up -d
```

### View Live FX Trading
```bash
ssh nas
sudo docker logs -f ats-fx-daemon
```
Press `Ctrl+C` to stop viewing.

### Pause FX Trading
```bash
ssh nas
sudo docker stop ats-fx-daemon
```

### Resume FX Trading
```bash
ssh nas
sudo docker start ats-fx-daemon
```

---

## Running Signal Scans Manually

The daily cron handles these automatically, but you can run them anytime:

### Regime Detector
```bash
python3 trading/regime/regime_detector.py
```

### US Screener
```bash
python3 -m watchlist.screener --markets us
```

### Base Yield Strategies
```bash
python3 trading/base_yield.py           # All 3 strategies
python3 trading/base_yield.py --strategy spy_gap    # Just SPY gap
python3 trading/base_yield.py --dry-run             # Preview only
```

### Performance Analytics
```bash
python3 trading/perf_analytics.py       # Full report
python3 trading/perf_analytics.py --strategy donchian_breakout
```

---

## Weekly Maintenance

### Sunday IBKR Reset

Every Sunday, IBKR resets all sessions. You'll get a **2FA push on your phone** from IBKR Mobile — just tap **Approve**. The gateway handles everything else automatically.

### Monthly Check

1. Check disk space on NAS: `ssh nas "df -h /volume1"`
2. Verify all containers are healthy: `sudo docker ps`
3. Check recent signals on the dashboard
4. Review daemon logs for any errors: `sudo docker logs ats-fx-daemon --tail 100 | grep -i error`

---

## Troubleshooting

### Gateway shows "unhealthy" or keeps restarting

1. Check if you have 2 sessions (Mac + NAS competing):
   ```bash
   ssh nas
   sudo docker logs ib-gateway 2>&1 | grep -i "multiple\|already"
   ```
2. If "multiple login" — stop the NAS gateway, log into the Mac Gateway to release the session, then restart NAS:
   ```bash
   # On NAS
   cd /volume1/docker/ats-landing/trading/nautilus_port
   sudo docker-compose stop ib-gateway
   # Log into Mac Gateway, wait 30 seconds, then:
   sudo docker-compose start ib-gateway
   ```

### Port 4002 not open

1. The gateway may need a fresh VNC login (first-time setup):
   - Start Mac Gateway, log in with paper account, enable API
   - Settings sync to cloud via s3store=true → NAS pulls automatically
2. Or restart the gateway on NAS:
   ```bash
   sudo docker-compose restart ib-gateway
   ```

### Dashboard shows "No signals"

1. Check if cron is running: look at `~/.hermes/trading/logs/daily_cron.log`
2. Run the screener manually: `python3 -m watchlist.screener --markets us`
3. Check Supabase at the SQL editor for recent rows in the `signals` table

### Telegram bot not sending alerts

1. Check if the bot is running: `sudo docker ps | grep telegram`
2. Verify the Telegram token is valid in `.env`
3. Check bot logs: `sudo docker logs ats-telegram-bot --tail 20`

### FX daemon disconnected from IB

1. Check if the gateway is healthy: `sudo docker ps | grep ib-gateway`
2. Check daemon logs: `sudo docker logs ats-fx-daemon --tail 30`
3. If you see "Connection refused" — restart both:
   ```bash
   ssh nas
   cd /volume1/docker/ats-landing/trading/nautilus_port
   sudo docker-compose restart ib-gateway
   sleep 30
   sudo docker-compose restart ats-fx-daemon
   ```

### NAS disk nearly full

```bash
ssh nas
df -h /volume1
# Clean up old Docker images:
sudo docker system prune -a
```

### Need to log into the Mac Gateway (for cloud sync)

If the NAS gateway loses its API settings:
1. Close the NAS gateway: `sudo docker-compose stop ib-gateway`
2. Launch Mac Gateway: `open "/Users/kelvinko/Desktop/IB Gateway 10.47"`
3. Login with paper account, verify API is enabled on port 4002
4. Close Mac Gateway, wait 60s, restart NAS gateway:
   ```bash
   sudo docker-compose start ib-gateway
   ```

---

## Key URLs

| What | URL |
|------|-----|
| Dashboard | `https://ats.coolpaperplane.win/dashboard` |
| Supabase | `https://supabase.com/dashboard/project/nwatzlrmoefluymhqgwi` |
| Vercel | `https://vercel.com/kko3883s-projects/ats-landing` |

## Key Commands Quick Reference

```bash
# System status
ssh nas && cd /volume1/docker/ats-landing/trading/nautilus_port && sudo docker ps

# Restart everything
sudo docker-compose down && sudo docker-compose up -d

# View daemon
sudo docker logs -f ats-fx-daemon

# View gateway
sudo docker logs -f ib-gateway

# Run base yield
cd ~/dev/ats-landing && python3 trading/base_yield.py

# Run performance report
cd ~/dev/ats-landing && python3 trading/perf_analytics.py

# Deploy dashboard updates
cd ~/dev/ats-landing && vercel --prod