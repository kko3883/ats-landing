# Equity Trading Engine — Deployment & Operations Guide

## Where the code lives

```
GitHub:  https://github.com/kko3883/ats-landing
Path:    trading/equity_engine/
Branch:  main
```

Clone to any machine:
```bash
git clone https://github.com/kko3883/ats-landing.git
cd ats-landing/trading
```

---

## Quickstart: Verify it works (30 seconds, no broker needed)

```bash
# Uses yfinance — free, no API keys
python equity_engine/run_smoke_test.py --symbols "AAPL.US,MSFT.US,NVDA.US" --days 15
```

Expected output:
```
  ── Layer 1 (Daily) ──
  Approved by SMA: 2/3  ✓
  ── Layer 2 (15-Minute) ──
  Feature computations:  424
  SMOKE TEST PASSED
```

---

## Running the engine

### 1. Signal-only mode (safest first run)

No broker connection needed.  Logs what the engine *would* do.

```bash
python equity_engine/run_live.py --no-execute --log-level DEBUG
```

### 2. Paper trading (needs IB Gateway + Longbridge)

```bash
# Set up environment (or export them)
export IBG_HOST=kko-nas.tail9a4917.ts.net
export IBG_PORT_PAPER=7497
export IB_ACCOUNT_ID=DUQ538194
export SUPABASE_ANON_KEY=eyJhbGciOi...

# Run paper trading
python equity_engine/run_live.py --paper
```

### 3. Live trading (real money — be certain first)

```bash
export IBG_PORT_LIVE=7496
python equity_engine/run_live.py --live
```

---

## Training the XGBoost model

The engine needs a trained model for real inference.  Without it, all probability scores are 0.5 (no entries).

```bash
# 1. Install ML dependencies
pip install xgboost optuna

# 2. Prepare the dataset (fetches 300 days of M15 data per symbol)
python equity_engine/training/prepare_dataset.py AAPL.US MSFT.US NVDA.US GOOGL.US AMZN.US

# 3. Train the model with Optuna hyperparameter optimization
python equity_engine/training/train_xgb.py --trials 100

# 4. Re-run smoke test — model will now return real probabilities
python equity_engine/run_smoke_test.py --symbols "AAPL.US,MSFT.US,NVDA.US" --days 60
```

The model saves to `equity_engine/models/xgb_entry_classifier.json`.  The live engine loads it automatically.

---

## Interacting with the engine

### Status dashboard

```bash
# One-shot status
python equity_engine/run_status.py

# Live dashboard — refreshes every 10 seconds
python equity_engine/run_status.py --watch 10
```

Shows:
- Active positions (symbol, entry price, stop loss, trailing stop)
- Account equity
- Recent trade log (fills, exits, PnL)

### Check logs

```bash
# Engine log (real-time)
tail -f ~/.hermes/equity_engine/trades.jsonl

# State snapshot
cat ~/.hermes/equity_engine/state.json | python3 -m json.tool
```

### Manual controls via state file

While the engine is running, you can modify `~/.hermes/equity_engine/state.json` manually:

```json
{
  "pause": true,
  "pause_reason": "manual override"
}
```

The engine reads this file periodically (the `risk_controller` checks pause status).

---

## Prerequisites

### Python packages

```bash
pip install pandas numpy yfinance pyarrow PyYAML
# Optional (for live trading):
pip install xgboost optuna ib_insync longbridge
```

### IB Gateway on NAS

The IB Gateway must be running on the NAS.  From the NAS:

```bash
cd /volume1/docker/ats-landing/trading/nautilus_port
sudo docker-compose up -d ib-gateway
```

Verify with:
```bash
nc -zv kko-nas.tail9a4917.ts.net 7497   # paper
nc -zv kko-nas.tail9a4917.ts.net 7496   # live
```

### Longbridge CLI

```bash
# Install
pip install longbridge

# Authenticate
longbridge login
```

### Supabase (for regime data)

The engine reads the `regime` table from Supabase to get the current market regime.  The existing `regime_detector.py` cron job writes this daily.

No additional setup needed — the engine reads from the same table as the dashboard.

---

## Cron setup (recommended)

### Weekly model retraining

```cron
# Runs every Saturday at 03:00 UTC
0 3 * * 6 cd /Users/kelvinko/dev/ats-landing/trading && python equity_engine/training/prepare_dataset.py && python equity_engine/training/train_xgb.py --trials 100
```

### Daily backtest verification

```cron
# Runs daily at 13:00 UTC (08:00 ET) — verify engine health before market open
0 13 * * 1-5 cd /Users/kelvinko/dev/ats-landing/trading && python equity_engine/run_smoke_test.py --symbols "AAPL.US,MSFT.US" --days 60 > ~/.hermes/equity_engine/daily_check.log 2>&1
```

---

## Architecture recap

```
Mac (your workstation)
├── equity_engine/run_live.py        # Main engine loop (asyncio)
├── equity_engine/training/          # Model training (weekly)
├── equity_engine/run_smoke_test.py  # Validation (daily)
├── equity_engine/run_status.py      # Interactive dashboard
│
├── Longbridge WebSocket ───────► M1 / M15 / D1 streaming bars
│   (cloud-native, no local process)
│
├── Supabase REST ──────────────► Regime reads (risk_on / choppy / etc.)
│
└── NAS (kko-nas.tail9a4917.ts.net)
    └── IB Gateway ─────────────► Trade execution (ib_insync)
        (dockerized, paper:7497 / live:7496)
```

---

## File layout

```
equity_engine/
├── config.py              # Central configuration
├── run_live.py            # Live trading entrypoint
├── run_smoke_test.py      # Validation test (no broker)
├── run_status.py          # Interactive dashboard
├── DEPLOY.md              # ← This file
├── REQUIREMENTS.txt
├── .gitignore
├── configs/
│   ├── seed_universe.json # Top 50 S&P stocks
│   └── trading_params.yaml
├── data/                  # Longbridge streaming + historical fetcher
├── layer1_macro/          # Daily SMA(200) + regime gate
├── layer2_tactical/       # 15-min feature engineering + XGBoost
├── layer3_micro/          # 1-min trailing stop + micro-volatility
├── execution/             # IB bridge + risk controller + state
├── training/              # Dataset prep + model training
├── backtest/              # Multi-timeframe backtesting engine
└── models/                # Saved model weights