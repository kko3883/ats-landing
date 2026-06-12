# ATS Build Brief — Autonomous Trading System

**For:** Claude Code, working in the ATS repo
**Owner:** Kelvin Ko (kko3883)
**Date:** 12 June 2026
**Status of project:** design approved; paper-trading infrastructure live; NOTHING trades live until the Gate (section 9) passes.

This brief is the single authoritative spec. It folds in the original design (three buckets + Market Thermometer), every decision made since, and — new in this document — the full **research and signalling layer**, which is the heart of the build. The honest premise of this project: the engineering is the easy part; the edge is unproven. The system is therefore built research-first, and every strategy must earn its way into execution through the validation pipeline defined in Phase 3.

-----

## 1. Purpose and philosophy

Achieve risk-adjusted compounding across macro regimes while hard-limiting drawdowns. The system does not predict; it measures the present regime and gates strategy behavior accordingly. Its deeper purpose is **discipline enforcement**: the owner knows from experience that manual trading fails at the emotional override moment, so rules are encoded and the machine holds the line. Nothing in this document is investment advice; every signal below is a **hypothesis to be validated**, not a truth to be implemented blindly.

## 2. Capital architecture (the three buckets)

1. **Bucket 1 — Lazy asset pool (capital base + collateral).** Idle capital sits in SGOV/BIL by default, GLD under inflation/geopolitical stress, FLOT under aggressively rising rates. Never sold to fund trades: high loan-value acts as margin cushion while yielding.
2. **Bucket 2 — Active sandbox.** Trend-Catcher in trending regimes; Cycle-Catcher in range regimes; flat/defensive in shock. Bi-directional (longs, inverse ETFs, or shorts).
3. **Bucket 3 — Long-term compounder.** Ring-fenced secular holdings; exits only on weekly-bar trend breaks (weekly 200 EMA filter). Out of scope for automation in v1 except exposure reporting.

A **net exposure controller** aggregates positions across buckets before order routing to avoid offsetting commissions.

## 3. Tech stack and house rules (non-negotiable)

- Python managed with **uv**; **Polars** (never pandas); **Parquet** local store under `data/`.
- All datetimes normalized to **UTC on ingest**.
- Indicators are **pure functions** using rolling windows and `.shift(1)` — zero look-ahead, enforced by tests.
- One-and-done caching: check local Parquet before hitting any API; append only new rows.
- Config-driven: one `config.yaml` for universes, parameters, thresholds. No magic numbers in code.
- Secrets in env files, gitignored; broker credentials live on the NAS only; FRED API key via env.
- Deterministic backtests: same inputs → identical outputs; seedable where randomness exists.
- Tests with pytest for every indicator, the leak guard, and the backtest accounting.
- R&D runs on the Mac; execution runs on the NAS (IB Gateway container, already deployed, paper mode).

## 4. Phase 1 — Data layer (`ingest_engine.py`)

Sources, daily bars, free tier:
- **FRED:** `BAMLH0A0HYM2` (HY credit spread), `DTWEXBGS` (trade-weighted USD).
- **yfinance:** `^VIX`, **`^VIX3M`** (NOT `^VXV` — that ticker is retired), `SPY`, `QQQ`, `IWM`, `SGOV`, `BIL`, `GLD`, `FLOT`.

Deliverables: fetchers per source, clean Parquet naming (`data/<source>/<ticker>.parquet`), incremental append, a `refresh_all()` entry point, and a data-quality report (gaps, stale series, NaN counts). Acceptance: re-running twice produces zero duplicate rows; all frames UTC.

## 5. Phase 2 — Market Thermometer (`regime_detector.py`)

- **Volatility score:** VIX/VIX3M ratio (contango vs backwardation) blended with realized-vol percentile of SPY.
- **Trend score:** 14-period ADX or a normalized multi-window momentum z-score on SPY and QQQ (implement both; research decides).
- **Output:** continuous Regime Score in [-100, +100] plus state flag in {CLEAR_TREND_BULL, CLEAR_TREND_BEAR, BORING_RANGE_MARKET, SYSTEMIC_SHOCK}.
- **Hysteresis required:** state transitions need N consecutive days beyond threshold (configurable) to prevent regime flip-flopping — whipsaw at the gating layer destroys everything downstream.
- Acceptance: a labeled historical timeline (2007 to present) that the owner can eyeball: 2008, 2020, 2022 must show as shock/bear; 2017 as trend-bull; 2015 and 2023 chop as range.

## 6. Phase 3 — RESEARCH AND SIGNALLING (the core of this brief)

This phase is where the project lives or dies. Build it before any execution wiring beyond what already exists.

### 6.1 Backtest engine (`backtest/`)

Vectorized Polars engine with these mandatory properties:
- Signals computed on data through day T trade at day T+1 open (or T close with explicit flag) — the `.shift(1)` discipline made structural.
- **Costs always on:** commission per trade (configurable, IBKR tiered default), slippage model (fraction of ATR, default 0.1x), borrow cost for shorts.
- Position accounting: cash, margin usage vs Bucket 1 collateral, per-position entry/stop/size history.
- Outputs: equity curve, trade ledger, and the full metrics block (CAGR, Sharpe, Sortino, max drawdown, longest underwater period, exposure %, turnover, win rate, payoff ratio, trade count).
- A **leak test** in CI: shuffle future data after T and assert signal at T is unchanged.

### 6.2 Signal library (`signals/`) — initial hypotheses

Each signal is a pure function returning {-1, 0, +1} or a sized weight, with parameters from config. Implement these as the v1 candidate set:

**Trend-Catcher (active only when state = CLEAR_TREND_*):**
- T1: Donchian breakout — close above N-day high (N=55 default) long; below N-day low short/inverse.
- T2: Time-series momentum — sign of 6-and-12-month return, z-scored, traded on SPY/QQQ/IWM.
- Exit for both: ATR(14) trailing stop, Stop = Price - k*ATR with k starting 3.0 and tightening to 2.0 as profit exceeds 1R, 2R; stop never retreats.

**Cycle-Catcher (active only when state = BORING_RANGE_MARKET):**
- C1: RSI(2) mean reversion — long below 10 at weekly support zone, exit at mid-band; short above 90 at resistance.
- C2: Bollinger reversion — entry at 2-sigma weekly band touch with declining ADX, exit at the mid-line. Low frequency by design.

**Shock (state = SYSTEMIC_SHOCK):** all active signals forced flat; Bucket 1 rotation rules apply (credit spread > threshold → GLD share rises; rate-of-change of policy proxy → FLOT).

These are hypotheses. The research pipeline decides which survive; deleting a failed signal is a success outcome.

### 6.3 Validation methodology (the rules of evidence)

Every signal/strategy must pass ALL of the following before it is eligible for paper deployment:

1. **Walk-forward:** train/validate on rolling windows (e.g., fit parameters on 4y, test on 1y, roll). Report out-of-sample metrics only; in-sample numbers are noise.
2. **Holdout:** final 2 years never touched during development; one shot at the end.
3. **Parameter sensitivity:** performance surface across +-30% of each parameter. A strategy that only works at N=55 but dies at 45 and 65 is curve-fit — reject.
4. **Regime-stratified results:** metrics broken out per Thermometer state. A trend signal must earn its keep in trend states and must not bleed badly in others after gating.
5. **Benchmarks:** must beat buy-and-hold SPY and a 60/40 on risk-adjusted terms (Sharpe and max-DD) net of costs over the OOS period, or articulate exactly why it still belongs (e.g., crisis convexity).
6. **Monte Carlo on the trade ledger:** bootstrap trade sequences (>=5000 resamples) to produce a drawdown distribution. The 95th-percentile drawdown is the "normal expected" figure that calibrates the circuit breaker (section 7) — the breaker must sit beyond it.
7. **Edge statement:** a written paragraph per surviving strategy answering: who is on the other side of this trade and why does the anomaly persist? No edge statement, no deployment. This is the explicit gate the owner set.

### 6.4 Research reporting

Each research run emits a markdown report under `research/reports/` (date-stamped): config snapshot, OOS metrics, sensitivity heatmap, regime breakdown, MC drawdown distribution, verdict (PASS / FAIL / REVISE) and the edge statement. The owner reads reports, not code.

## 7. Phase 4 — Portfolio and risk layer (`risk/`)

- **Position sizing by risk, not size:** risk per trade = position size x stop distance = fixed fraction of active capital (default 0.5–1.0%, config).
- **Portfolio heat:** cap on summed simultaneous open risk (default 4–6%), with a correlation haircut — positions in correlated instruments count overlapping.
- **Gap risk:** stops are protective, not guarantees; sizing must assume a 2x stop-distance gap as the realistic loss tail.
- **Drawdown circuit breaker:** halt all new entries and de-risk to Bucket 1 when drawdown from peak exceeds the threshold (25–30%, finalized after 6.3 step 6 produces the expected-DD figure). **OPEN DECISION the owner must make before this phase completes:** breaker measured on whole fund vs active trading capital only. Default for build: active capital only, flagged in config, easy to switch.
- **Volatility Shock Interrupt:** if intraday realized vol > 3 sigma within the first 30 minutes, halt new signals, defensive defaults. (Requires intraday data — implement as a stub interface in v1, daily-bar proxy until an intraday feed is chosen.)
- Re-entry after any halt is manual-approval only (Telegram), never automatic.

## 8. Phase 5 — Execution and operations

- **Paper first, always.** IB Gateway (ib-gateway-docker: IBC + socat) already runs on the NAS in `TRADING_MODE=paper` against the dedicated second IBKR account (no-withdrawal restriction). Known fix in flight: mount Jts/`TWS_SETTINGS_PATH` to a persistent volume so the weekly Sunday forced-logout survives; credentials in a gitignored env file.
- **Order management (`execution/`):** ib_insync (or current maintained equivalent) client; every position carries a **server-side resting stop** so a host/network drop cannot leave exposure unprotected; reconciliation loop compares broker state to local state and alerts on divergence; idempotent order submission.
- **Scheduling:** daily post-close pipeline (ingest → thermometer → signals → orders for next open) via Prefect on the NAS or launchd on the Mac — owner decides at build time; either way Uptime Kuma watches it and a Telegram summary goes out via the existing Hermes path.
- **State:** trade state and task runs to Parquet locally; optional mirror to the existing Supabase trade-state tables.
- **Kill switch:** one command/Telegram action that flattens paper positions and stops the scheduler.

## 9. Gate to live trading (all must be true; none are today)

1. Edge statement written and OOS-validated per 6.3 for every deployed strategy.
2. Circuit-breaker scope decided and implemented; threshold calibrated beyond the MC expected-DD.
3. Risk layer (heat caps, per-type stops) fully encoded — zero discretionary inputs in the daily loop.
4. Gateway survives two consecutive weekly logout cycles unattended.
5. Server-side resting stops verified working in paper through at least one full month of paper operation matching backtest expectations (slippage audit).

## 10. Build order and working agreement

1. Phase 1 data layer → 2. Thermometer with eyeball-able historical timeline → 3. Backtest engine + leak tests → 4. Signal library → 5. Validation pipeline + first research reports → 6. Risk layer → 7. Paper execution wiring → 8. One month paper burn-in.

Working agreement with the owner: he is a vibe coder on iPad/SSH — keep CLI ergonomics simple, summaries short, and reports in markdown. Decisions he owes the build: (a) circuit-breaker scope, (b) Prefect-on-NAS vs launchd-on-Mac for scheduling, (c) approval of which v1 signal hypotheses to research first. Surface these as questions at the relevant phase; do not guess. Discussion before code applies at phase boundaries; within an approved phase, build autonomously with tests.

## 11. Repo conventions

- This brief lives at the repo root as `BRIEF.md`; treat it as the spec of record, update it when decisions land.
- `data/` gitignored. `research/reports/` committed (they are the audit trail). Open question inherited from infra: whether the IB Gateway compose file lives here or in the homelab repo — flag, do not decide unilaterally.
- gitleaks pre-commit. Conventional commits. No secrets in chat, code, or commits, ever.

## 12. Decision log

**2026-06-12 (owner + build review):**

- **Project location:** the ATS Python project lives at the **repo root** of `ats-landing` (uv-managed `pyproject.toml` alongside the Next.js app). Top-level modules/dirs per the phase names in this brief.
- **Phase 5 execution refined: NautilusTrader, not ib_insync.** ib_insync is unmaintained (author deceased 2024; `ib_async` is the community fork). The brief's "current maintained equivalent" clause is satisfied by the NautilusTrader deployment already live on the NAS (`trading/nautilus_port/`, IB Gateway docker, Telegram control plane). The daily pipeline emits target positions; a thin Nautilus strategy executes them. **Gate addition:** verify Nautilus stop orders on IB rest server-side (not locally emulated) before live.
- **Thermometer supersedes `trading/regime/regime_detector.py`** but inherits its Supabase `regime` table contract so the dashboard and screener gating keep working. The old detector is retired only after the new Thermometer passes the 2007–present timeline acceptance (section 5).
- **Legacy `trading/` stack stays untouched and running** (watchlist screener, HK/Longbridge tooling, fx daemons). Out of v1 scope; watchlist becomes a Bucket 2 universe-selection candidate after the Gate. New code never depends on macOS keychain — env files only.
- **Dev platform note:** build machine is Windows; R&D target is Mac, execution target is NAS. All new code platform-agnostic (uv-managed Python, pathlib, env files).
- **FRED API key pending** — owner registering; FRED fetcher built and mock-tested, exercised live once the key lands in `.env`.
