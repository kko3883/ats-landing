# ATS Operations — Getting Live

> **Last updated: 14 June 2026** — First operational checklist.
> Read this after completing the STRATEGIC_REVIEW action items.

## System Architecture (What Runs Where)

```
┌─────────────────────────────────────────────────────────────────┐
│  YOUR MAC (this machine)                                        │
│                                                                 │
│  /Users/kelvinko/dev/ats-landing/                               │
│  ├── trading/              ← All Python screening/indicator code│
│  ├── pages/dashboard/      ← Next.js dashboard                  │
│  ├── supabase/migrations/  ← Database schema                    │
│                                                                 │
│  CRON (macOS):                                                   │
│    08:00 HKT → daily_cron.sh us      (US screener)              │
│    09:00 HKT → daily_cron.sh all     (regime+US+HK+portfolio)   │
│                                                                 │
│  IB Gateway 10.47.app  →  port 4002 (paper)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                    HTTPS / REST API
                              │
┌─────────────────────────────────────────────────────────────────┐
│  SUPABASE CLOUD (nwatzlrmoefluymhqgwi.supabase.co)              │
│                                                                 │
│  Tables:  signals, indicator_signals, regime, portfolio         │
│  RLS:     anon read / service_role write                        │
│  Realtime:  signals + portfolio live                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                    ANON KEY (public read)
                              │
┌─────────────────────────────────────────────────────────────────┐
│  VERCEL (to deploy)  or  localhost:3000                         │
│                                                                 │
│  Dashboard: regime banner → signal feed → entry cards → portfolio│
└─────────────────────────────────────────────────────────────────┘
                              │
                   MANUAL EXECUTION
                              │
┌─────────────────────────────────────────────────────────────────┐
│  LONGBRIDGE APP (your phone/desktop)                            │
│                                                                 │
│  You execute trades manually based on dashboard signals.        │
│  sync_positions.py pulls your positions back to Supabase.       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Step 1: Install Dependencies Not Yet On This Mac

### 1a. Longbridge CLI (for HK stock quotes + kline data + position sync)

```bash
# Install pipx first if you don't have it
brew install pipx
pipx ensurepath

# Install Longbridge CLI
pipx install longbridge-cli

# Verify
longbridge --help
```

Then login:
```bash
longbridge login
```
This opens a browser for OAuth. Use your Longbridge account credentials.

### 1b. Verify Python dependencies

```bash
cd ~/dev/ats-landing
pip3 install yfinance pandas numpy requests pyyaml 2>&1 | tail -5
```

(These are already installed if the screener has ever run successfully.)

---

## Step 2: Apply Database Migrations

Your Supabase project has new migrations that need to be applied to the cloud database.

### Option A: Via Supabase Dashboard (easiest)
1. Go to [https://supabase.com/dashboard/project/nwatzlrmoefluymhqgwi](https://supabase.com/dashboard/project/nwatzlrmoefluymhqgwi)
2. Click **SQL Editor** in the left nav
3. Open a new query
4. Copy-paste the contents of each `.sql` file from `supabase/migrations/` that starts with `20260614`:
   - `20260614000000_create_portfolio.sql`
   - `20260614000001_add_signal_lifecycle.sql`
5. Run each one

### Option B: Via Supabase CLI (if installed)
```bash
cd ~/dev/ats-landing
npx supabase link --project-ref nwatzlrmoefluymhqgwi
npx supabase db push
```

---

## Step 3: Install the Daily Cron

macOS blocks automated crontab writes. You must do this manually:

```bash
crontab /Users/kelvinko/dev/ats-landing/trading/ats.crontab
```

macOS will pop up a dialog: **"Terminal wants to access calendar and contacts"** → Click **Allow**.

Verify it installed:
```bash
crontab -l
```
You should see two entries at 08:00 and 09:00 HKT weekdays.

Logs will appear at: `~/.hermes/trading/logs/daily_cron.log`

---

## Step 4: Start IB Gateway (Paper Trading)

You have the Gateway app installed at `/Users/kelvinko/Desktop/IB Gateway 10.47/`.

### 4a. First-time setup

1. Double-click `IB Gateway 10.47` on your Desktop to launch
2. **Login with your SECOND IBKR username** — the one created specifically for the Gateway. Do NOT use your main trading login (it will boot your web/mobile session).
3. Set **Trading Mode = Paper**
4. Set **API Port = 4002**
5. Check **"Enable ActiveX and Socket Clients"**
6. Under **Configuration → API → Settings**, make sure:
   - Socket port: 4002
   - "Allow connections from localhost only" is checked
   - "Read-Only API" is OFF (you want to place paper orders)
7. Click **Login**

IBKR Mobile will fire a **2FA push** to your phone — approve it.

You should see the Gateway window show "Connected" with a green status.

### 4b. To verify the API is alive

In a new terminal:
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:4002
```
Should return something (even an error page is fine — it means the port is listening).

---

## Step 5: Deploy the Dashboard

### Option A: Vercel (recommended — gives you a URL)

1. Install Vercel CLI if needed:
   ```bash
   npm install -g vercel
   ```

2. From the repo root:
   ```bash
   cd ~/dev/ats-landing
   vercel --prod
   ```

3. On first run, Vercel will ask:
   - Link to existing project? Create new.
   - Set the environment variables when prompted (or in the Vercel dashboard):
     - `NEXT_PUBLIC_SUPABASE_URL` = `https://nwatzlrmoefluymhqgwi.supabase.co`
     - `NEXT_PUBLIC_SUPABASE_ANON_KEY` = *(get this from Supabase dashboard → Project Settings → API → anon/public key)*

4. After deployment, your dashboard will be at `ats-landing.vercel.app` (or similar).

### Option B: Local dev server (for testing)

```bash
cd ~/dev/ats-landing
npm run dev
```
Then open `http://localhost:3000/dashboard` in your browser.

Make sure `.env.local` has:
```
NEXT_PUBLIC_SUPABASE_URL=https://nwatzlrmoefluymhqgwi.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<your-anon-key>
```

---

## Step 6: First Screener Run

Run the screener once manually to populate the dashboard:

```bash
cd ~/dev/ats-landing

# Run the regime detector
python3 trading/regime/regime_detector.py

# Run the US screener
python3 -m watchlist.screener --markets us

# Run the HK screener  
python3 -m watchlist.screener --markets hk

# Run the signal engine (converts watchlist → actionable signals)
python3 -m watchlist.signal_engine --markets us
```

After this, your dashboard should show:
- A regime banner (risk_on / choppy / risk_off / crisis)
- Signal cards with GO/WATCH/WAIT badges
- Entry plans when you click a card (stop loss, take profit, entry zone, suggested size)

---

## Step 7: Sync Your Portfolio

If you have existing positions in Longbridge:

```bash
cd ~/dev/ats-landing
python3 trading/regime/sync_positions.py
```

This pulls your positions, maps VIX zones from the screener, computes P&L and allocation %, and writes to Supabase. The dashboard's Portfolio panel will show your holdings.

---

## Daily Routine (After Setup)

The cron handles everything automatically at 08:00 and 09:00 HKT weekdays. Your manual workflow:

1. **09:05 HKT** — Open the dashboard
2. Check the regime banner (top of page)
3. Scan signal cards — look for **🟢 GO** badges
4. Click a GO card → review the entry plan (zone, stop, size)
5. If the plan looks good → execute in Longbridge app
6. Hourly: refresh dashboard, check if WATCH cards flipped to GO

The cron also auto-expires signals older than 5 days, so your feed stays clean.

---

## NAS Deployment (Future — for automated FX execution)

When you're ready to run the NautilusTrader FX bot on your Synology NAS, follow `trading/nautilus_port/DEPLOY.md`. That gives you automated FX execution (EUR/USD, AUD/JPY, NZD/JPY) with the 4-level signal engine. For now, the screener + dashboard + manual Longbridge execution is fully operational.