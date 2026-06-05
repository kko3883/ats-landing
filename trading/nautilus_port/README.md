# Four-Level FX — NautilusTrader port

A migration scaffold that lifts the **strategy** out of `fx_daemon.py` and drops it
onto a production-grade execution engine, leaving behind the hand-rolled
order/position/reconnection plumbing that made the daemon unstable.

> **Status:** skeleton, **import-validated against nautilus_trader 1.227.0** (2026-06-05) —
> imports, config structs, indicators, enums, the trailing-stop order signature, and the
> IB adapter config fields all load cleanly. NOT yet runtime-tested against live IB data;
> do the paper parallel-run first. Nautilus is Beta — re-verify if you bump the version.

## Why bother (what each Nautilus feature kills)

| Old daemon (hand-rolled) | Nautilus equivalent | Review bug it removes |
|---|---|---|
| `sync_positions_from_ibrk_async` never deleting closed positions | ExecEngine **reconciliation** on connect + background loop | **#1** wrong-way order on a flat position |
| Manual ATR trailing + new `StopOrder` per stacked entry, old one orphaned | Native **`TrailingStopMarketOrder`** (`reduce_only`) | **#2** oversized/orphan stop → reversal |
| `get_account_summary_async` re-subscribing every tick; no daily reset | Portfolio + account events from the engine | **#3** dead kill-switch (re-implement as a `RiskEngine` rule) |
| `positions` aliasing `old_state['positions']` | The **cache** is the single source of truth | brittle state desync |
| `tif='DAY'` stops expiring overnight | `stop_tif=GTC` in `FourLevelConfig` | naked positions during an outage |
| No backtest path at all | `run_backtest.py`, **same strategy code** | can't validate before going live |

## Files

- `strategy_four_level.py` — the strategy. 4-level signal engine + ATR trailing stop.
- `run_live.py` — TradingNode + IB adapter (paper on 4002 / live on 4001).
- `run_backtest.py` — backtest engine skeleton; wire in your 1h bars.

## Setup

```
python -m venv .venv && . .venv/bin/activate
pip install -U "nautilus_trader[ib]"
```

Python 3.11 or 3.12 recommended — the IB adapter had a 3.14 packaging hiccup in
early 2026, so don't get ahead of it.

## Run (paper first)

1. Start your dockerized IB Gateway (gnzsnz image) in **paper** mode, port 4002,
   on the home IP (NOT through gluetun).
2. Point the runner at it and launch:

```
export IB_ACCOUNT_ID=DU1234567
export IBG_HOST=127.0.0.1
export IBG_PORT=4002
python run_live.py
```

## Recommended migration path

1. **Backtest** `FourLevelStrategy` on historical 1h bars — confirm signals match
   the old daemon's intent.
2. **Parallel paper run** for ~1 week: this strategy on Nautilus (paper) alongside
   the existing `fx_daemon.py` (paper). Diff the signals and fills.
3. If stable, **cut over**; retire `fx_daemon.py`.
4. Re-add what the skeleton intentionally omits:
   - **Telegram alerts** — subscribe to `on_position_opened/closed` + order events.
   - **Daily-loss kill switch** — implement as a Nautilus `RiskEngine` rule (with a
     proper per-day reset, which the original lacked).
   - **Staggered entries** (`MAX_POSITIONS_PER_PAIR`) — if you re-add adds-to-position,
     cancel + replace the trailing stop on each add so size always matches the position.

## Known caveat (don't skip)

NautilusTrader's IB adapter has an open reconciliation gap
([#3655](https://github.com/nautechsystems/nautilus_trader/issues/3655)): positions
closed **manually in TWS** may not always be detected. It's far better than the old
daemon, but test that exact scenario on paper before trusting it live.

## Longbridge?

Not through Nautilus — there is **no Longbridge/longport adapter**. Keep your HK/US
**stock** trading as a separate standalone bot on the Longbridge SDK
(`longbridge_executor.py`). Only build a custom Nautilus adapter if you later want a
single engine managing FX + stocks with portfolio-level risk — that's a multi-week
project you'd maintain alone.
