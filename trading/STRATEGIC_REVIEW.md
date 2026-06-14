# ATS Strategic Review — June 2026

> **Last updated: 14 June 2026** — Progress update after implementation sprint.
> See [Prioritized Action Items](#prioritized-action-items) for current status.

## Executive Summary

The ATS has a **solid foundation** — the 4-stage screening pipeline is institutional-grade, the
7-indicator system is well-designed with zero redundancy, and the dashboard integration is clean.

**The three original critical gaps have been resolved:**
1. ✅ **Regime detector** built — 5-factor classification (VIX, VIX momentum, HYG-TLT, US10Y-US2Y, SPY-QQQ), publishes to Supabase `regime` table, dashboard shows regime banner with activated groups.
2. ✅ **Conflict resolution** — GO/WATCH/WAIT badges on dashboard cards, aligned/caution/conflict zones, sorted feed.
3. ✅ **ATR-adaptive sizing** — replacing static percentages with volatility-scaled stop-loss, take-profit, entry zones, and position sizing.

**Remaining work** is primarily operational: US cron, portfolio tracking, Base Yield bucket, signal lifecycle.

---

## 1. Strategy Alignment: The Regime Detector Gap

### What's Built

| Component | Status | Coverage |
|-----------|--------|----------|
| 5-group VIX beta classification | ✅ Done | 264 US + 49 HK |
| Cross-sectional RS within groups | ✅ Done | Top/bottom 10% per group |
| 7-dimension hourly indicators | ✅ Done | All signals + HK watchlist |
| Strategy-to-group mapping (YAML) | ✅ Done | 4 strategies, mapped to groups |
| HK intraday signal monitor | ✅ Done | 3 strategies, 5-min polling |

### What's Missing: The Regime Detector

The autonomous-trading skill describes a **regime-switching meta-strategy** as "the system's real
edge." You've built the **what** (which stocks belong in which group) but not the **when** (which
group to activate based on current market regime).

The regime detector requires:
- **VIX level**: <15 = trending, 15-35 = choppy, >35 = crisis
- **HYG-TLT credit spread**: wide = credit stress → choppy/crisis
- **SPY-QQQ 20-day correlation**: >0.7 = single-threaded market → choppy (everything moves together, no alpha from selection)
- **VIX backwardation**: present + spread wide = crisis

**Recommendation**: Build `regime_detector.py` — a simple script that runs daily, checks these 4
conditions, and publishes the current regime to a Supabase `market_regime` table. The dashboard
already has a regime display slot. This unlocks:
- Knowing WHICH VIX-beta groups to prioritize (trending → high_beta_growth; choppy → neutral/defensive; crisis → defensive long + high_beta short)
- Suppressing strategies in wrong regimes (e.g., Donchian breakout in choppy markets)
- A "Regime: TRENDING — Prioritize: high_beta_growth + moderate_growth" banner on the dashboard

### Strategy Activation Map (From Skill)

```
Trending:  Donchian Breakout, Golden Cross, BB Squeeze ACTIVE
           Pullback/Mean Reversion SUPPRESSED

Choppy:    Pullback Entry, Mean Reversion ACTIVE
           Trend-followers SUPPRESSED

Crisis:    Only defensive longs + high-beta shorts
           Everything else SUPPRESSED. Cash is a position.
```

The YAML strategy config already has `watchlist_groups` mapped — you just need the gate logic
that checks current regime before activating each strategy.

---

## 2. Daily Screening vs Hourly Indicators: The Conflict Problem

### How They Currently Interact

The daily screener and hourly indicators are **displayed side-by-side** on the dashboard but
operate **independently**:

- **Screener says**: "2382.HK — LONG candidate, RS Z-score +2.2, high_beta_growth"
- **Indicators say**: "2382.HK — Strong Sell (composite -5), RSI overbought, MACD bearish"

The dashboard shows both. The user must reconcile them manually. This is the single biggest UX gap.

### Why They Can Diverge (And Why That's Normal)

| Layer | Timeframe | What It Measures | Signal Type |
|-------|-----------|-----------------|-------------|
| Screener | Daily bars, 124-day betas, 21-day RS | Structural positioning — "is this stock worth owning in this macro regime?" | **Strategic** (weeks) |
| Indicators | 1h bars, 14-26 period | Tactical timing — "is NOW a good entry point?" | **Tactical** (hours/days) |

They SHOULD disagree sometimes. A stock can be a great long candidate structurally but
temporarily overbought — that means "wait for the pullback," not "ignore the screener."

### Recommended Conflict Resolution Framework

Implement a **3-zone signal** on the dashboard:

```
Zone 1: ALIGNED (screener + indicators agree)
  → GREEN border on card, "GO" badge
  → Example: Screener=LONG, Indicators=Strong Buy
  → Action: Enter position

Zone 2: CAUTION (one neutral, one directional)
  → YELLOW border, "WATCH" badge
  → Example: Screener=LONG, Indicators=Hold (RSI mid-range, waiting)
  → Action: Add to watchlist, check next hour

Zone 3: CONFLICT (opposing signals)
  → RED border, "WAIT" badge
  → Example: Screener=LONG, Indicators=Strong Sell
  → Action: Wait for indicator to flip. The screener signal is valid for days,
    the indicator signal is valid for hours. Patience wins.
```

**Implementation**: Add a SQL view or dashboard-side logic that joins `watchlist_hk` / `signals`
with the latest `indicator_signals` row per ticker and computes alignment. This is ~30 lines of
dashboard JavaScript.

### Weighting Heuristic

The daily screener has a longer half-life than hourly indicators:
- **Screener confidence**: decays over ~5 trading days (re-screened daily, but structural)
- **Indicator confidence**: decays over ~4 hours (re-calculated hourly)

When in doubt, **trust the screener for direction, use indicators for timing**. Never go LONG on
a SHORT screener candidate just because indicators flash buy — that's chasing noise.

---

## 3. Entry and Stop-Loss Mechanisms

### Current State

The HK intraday monitor already computes SL/TP as static percentages:

```python
# hk_monitor.py, hardcoded per strategy
hk_pullback:     SL = -6%,  TP = +10%
hk_breakout:     SL = -7%,  TP = +12%
hk_golden_cross: SL = -8%,  TP = +15%
```

The US signal engine does the same from YAML config:
```yaml
mag7_pullback:      SL = -5%,  TP = +8%
donchian_breakout:  SL = -7%
golden_cross:       SL = -8%
```

### The Problem: Static Percentages

A 7% stop on a stock with ATR(14) = 1.2% daily range is ~6 ATR wide — that's generous.
The same 7% on a stock with ATR(14) = 4.5% daily range (e.g., NVDA) is only ~1.5 ATR —
you'll get stopped out by normal noise.

The autonomous-trading skill explicitly calls this out:
> "Adaptive ATR position sizing is the single highest-ROI improvement. Replace fixed sizing
> with `size = (risk_per_trade × capital) / (ATR_multiplier × ATR(14))`. Keeps risk constant
> across all volatility regimes — improves Sharpe by ~0.1–0.2."

### Recommended Entry/Stop-Loss System

**Step 1: Compute ATR(14) on daily bars** (already have daily data from screener pipeline)

**Step 2: ATR-Adaptive Stop Loss**
```
Stop Loss = Entry Price − (ATR(14) × multiplier)
Multiplier by strategy type:
  Pullback/Mean Reversion: 1.5× ATR  (tighter — mean reversion should happen fast)
  Breakout/Momentum:        2.5× ATR  (wider — trends need room to breathe)
  Golden Cross:             2.0× ATR  (moderate)
```

This means:
- Low-vol stock (ATR=1%): SL at 1.5-2.5% below entry
- High-vol stock (ATR=5%): SL at 7.5-12.5% below entry

**Step 3: Entry Zones (Not Single Prices)**

Instead of "enter at $X," provide entry zones based on indicator data:

```
Entry Zone = [Bollinger Lower Band, VWAP]  for pullback entries
Entry Zone = [Prev Day High, Prev Day High + 0.5× ATR]  for breakout entries
```

The dashboard already shows Bollinger %B — surface the actual band price levels.

**Step 4: Position Sizing**
```
Position Size = (Account Risk % × Portfolio Value) / (ATR(14) × Multiplier × Price)
```
For a $60K account risking 1% per trade ($600), on a $100 stock with ATR=2% ($2):
- Pullback: $600 / (1.5 × $2) = 200 shares = $20K position
- Breakout: $600 / (2.5 × $2) = 120 shares = $12K position

### Dashboard Enhancement: Entry Card

Each signal card should show:
```
2382.HK  [GO]  LONG  |  high_beta_growth
Price: 83.80
ATR(14): 2.35 (2.8%)
━━━━━━━━━━━━━━━━━━━━━━
Entry Zone: 80.28 – 83.80  (Bollinger Lower – Current)
Stop Loss:  80.28  (−4.2%, 1.5× ATR)
Take Profit: 93.86  (+12.0%, 5.0× ATR)
Risk/Reward: 1:2.9
Suggested Size: 150 shares ($12,570)
```

This gives the user everything needed to execute a trade manually in the Longbridge app.

---

## 4. Missing Macro Factors

### Currently Used
- VIX (volatility/fear gauge)
- DXY (USD strength, affects EM/HK flows)

### Should Add

| Factor | Why It Matters | Data Source | Difficulty |
|--------|---------------|-------------|------------|
| **HYG-TLT spread** | Credit risk appetite. Widening = stress, narrowing = risk-on. The single best real-time recession indicator. | yfinance (`HYG`, `TLT`) | Trivial |
| **US10Y-US2Y spread** | Yield curve. Inversion → recession signal. Steepening → recovery. Drives sector rotation. | yfinance (`^TNX`, `^IRX` replacement) | Easy |
| **Hang Seng / HSI Volatility (VHSI)** | HK-specific fear gauge. Better than VIX for HK stocks. | Longbridge or yfinance (`^VHSI`) | Medium |
| **CNY/USD (USD/CNH)** | Yuan strength drives HK stock flows. Weak yuan → capital flight from HK. | yfinance (`CNH=X`) | Trivial |

### Regime Detector Enhancement

The current screener regresses returns against VIX + DXY. Adding HYG-TLT and US10Y-US2Y as
additional macro factors would improve the beta estimation — especially for HK stocks where
China credit conditions matter more than US volatility.

---

## 5. Architecture Gaps & Improvements

### Gap 1: No US Screener Cron Job

Only HK has a daily screener cron (`0 9 * * 1-5`). The US screener must be run manually or
wasn't set up. Add:
```
US daily screener:  0 8 * * 1-5  → python -m watchlist.screener --markets us
```
This runs at 8am HKT (8pm ET previous day — post-market data is available by then).

### Gap 2: No Portfolio State Tracking

The system doesn't know your actual positions (GOOG.US 18, AMZN.US 94, 9868.HK 1000,
1810.HK 3400, SOXX.US 12). Without this:
- Can't compute realized P&L
- Can't prevent duplicate signals for already-held positions
- Can't enforce `max_total_positions: 8` (in global config but not enforced)
- Can't do correlation guard (are you too concentrated in one VIX beta group?)

**Recommendation**: Add a `positions` table to Supabase and a simple sync script that
pulls current positions from Longbridge once daily. Or, since you execute manually, build
a simple position input form on the dashboard.

### Gap 3: Base Yield Bucket Not Implemented

The 3-bucket system design says 50% of capital in Base Yield (SPY overnight gap, FX carry,
QQQ covered calls). This bucket was deferred as "structural, no watchlist needed." It's
the most reliable return source and should be prioritized.

### Gap 4: Signal Lifecycle Management

Signals are created but never marked as "executed," "expired," or "stopped out." The `signals`
table grows indefinitely. Need:
- `status` column: pending → executed → closed (SL/TP hit) → expired
- Auto-expire signals older than 5 trading days
- Link signals to positions for performance tracking

### Gap 5: US Intraday Equivalent

HK has intraday monitoring every 5min. US stocks are only screened daily. For a swing
trader, this is fine — you're not day-trading US stocks from HK timezone. But consider
adding a "pre-market check" that runs at ~20:30 HKT (30min before US open) to scan
US watchlist candidates with the indicator engine using pre-market data.

---

## 6. Recommended User Workflow

```
DAILY ROUTINE (09:00 HKT)
├── 1. CHECK REGIME
│   Dashboard banner shows: "Regime: CHOPPY — Prioritize: neutral + moderate_defensive"
│   This tells you which screener groups to focus on.
│
├── 2. SCAN SCREENER RESULTS
│   Filter dashboard by regime-active groups. Look for [GO] cards (aligned signals).
│   Ignore [WAIT] cards — the screener signal is valid for days, you can wait.
│
├── 3. DRILL INTO ALIGNED CARDS
│   Click card → TradingView chart. Check:
│   - Is price in the entry zone? (between Bollinger Lower and VWAP for pullbacks)
│   - Is volume confirming? (above 20-day average)
│   - Is the macro backdrop supportive? (VIX stable/falling for longs)
│
└── 4. EXECUTE IN LONGBRIDGE APP
    Use suggested position size from dashboard.
    Set stop-loss order immediately after entry.
    Log the trade in the dashboard (future feature).

HOURLY CHECK (during market hours)
├── Refresh dashboard — indicator bars update every hour
├── Check if any [WATCH] cards flipped to [GO]
├── Check if any [GO] positions hit stop-loss or take-profit levels
└── No impulsive entries on [WAIT] cards

END OF DAY (16:30 HKT)
├── Review open positions against updated screener
├── If screener flips a held stock from LONG to SHORT → exit
└── Journal: what worked, what didn't
```

---

## Prioritized Action Items — Progress Tracker

> Status legend: ✅ Done &nbsp;&nbsp; 🔶 In Progress &nbsp;&nbsp; ❌ Not Started

### Immediate (this week)

1. ✅ **Build regime detector** (`regime_detector.py`): 5-factor classification (VIX, VIX momentum, HYG-TLT, SPY-QQQ, US10Y-US2Y) → Supabase `regime` table. Dashboard shows regime banner.
   - Effort: ~3 hours (actual) — initially 4-factor, upgraded to 5-factor on 14 June
   - Impact: Unlocks strategy gating, gives daily direction

2. ✅ **Add conflict resolution to dashboard**: Aligned/Caution/Conflict badges per card based on screener + indicator agreement. GO/WATCH/WAIT with color-coded borders and sorted feed.
   - Effort: ~1 hour of dashboard JS
   - Impact: Biggest UX improvement, answers "what do I do now?"

3. ❌ **Add US daily screener cron job**: Same pattern as HK.
   - Effort: 5 minutes
   - Impact: US signals stay current automatically

### Short-term (next 2 weeks)

4. ✅ **ATR-adaptive stop-loss and position sizing**: Replaced hardcoded percentages with ATR-based calcs. Entry zones, stop-loss, take-profit, risk/reward, and suggested size all surfaced as expandable entry cards on dashboard.
   - Effort: ~4 hours
   - Files: `indicators/calculate.py` (v2), `pages/dashboard/index.js` (EntryCard component), `supabase/migrations/20260613000000_add_atr_and_bb.sql`
   - Impact: "Single highest-ROI improvement" per the trading skill

5. ✅ **Add HYG-TLT and US10Y-US2Y to macro factors**: Both systems updated.
   - **Regime detector**: 5-factor classification now uses yield curve inversion → risk_off gating. `fetch_yield_curve()` added.
   - **Screener**: Multi-factor OLS regression expanded from 2-factor (VIX+DXY) to up to 4-factor (VIX + DXY + HYG/TLT credit + 10Y-3M yield curve). Graceful degradation if data unavailable.
   - Files: `config.py`, `regime_detector.py`, `macro_betas.py`, `data_source.py`, `strategies/registry.py`
   - Effort: ~3 hours (actual)
   - Impact: Better regime classification, better HK stock selection

6. ✅ **Portfolio position tracking**: Full position table with cost basis, P&L, VIX zone, and allocation %. Dashboard shows live positions panel with concentration gauge. Signal dedup (HELD badge) flags already-held stocks.
   - Migration: `supabase/migrations/20260614000000_create_portfolio.sql`
   - Sync: `trading/regime/sync_positions.py` v2 — Longbridge → Supabase with VIX zone lookup from screener
   - Dashboard: Portfolio panel (total value, P&L, per-position table, VIX zone concentration gauge)
   - Dedup: Signal cards show blue HELD badge + reduced opacity for existing positions
   - Effort: ~3 hours (actual)
   - Impact: System knows your holdings, prevents duplicate signals, shows concentration risk

### Medium-term (next month)

7. ❌ **Base Yield bucket**: SPY overnight gap strategy (mechanical, validated by 30+ years of data)
8. ❌ **Signal lifecycle management**: pending → executed → closed → expired states
9. ❌ **Performance analytics**: Win rate by strategy, Sharpe by bucket, drawdown tracking

---

### Progress Summary (14 June 2026)

| Priority | Total Items | Done | Remaining |
|----------|:-----------:|:----:|:---------:|
| Immediate | 3 | 2 | 1 (US cron) |
| Short-term | 3 | 3 | 0 |
| Medium-term | 3 | 0 | 3 |
| **Total** | **9** | **5** | **4** |

---

## Bottom Line

The ATS is architecturally sound. The screening pipeline is rigorous. The indicator system is
well-designed. The missing pieces are **integration** (making the layers talk to each other) and
**operationalization** (turning data into actionable trade instructions). The regime detector and
conflict resolution framework are the two highest-leverage additions right now — together they
transform the system from "here's some data" to "here's what to do today."
