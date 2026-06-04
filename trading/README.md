# ATS — Automated Trading System

FX trading daemon and signal analysis engine for IBKR paper account.

## Structure

| Path | Description |
|------|-------------|
| `fx_daemon.py` | Main async daemon — runs 24/7 with adaptive polling, 4-level signal system, real-time yfinance prices |
| `fx_trader.py` | Legacy sync trader (deprecated) |
| `fx_check.py` | Quick signal check script |
| `longbridge_executor.py` | HK stock executor via Longbridge API |
| `supabase_writer.py` | ATS dashboard data writer to Supabase |
| `hk_signal.py` | HK stock signal scanner |
| `indicators/` | Technical indicator modules |
| `regime/` | Market regime analysis |
| `watchlist/` | Watchlist definitions and scripts |

## Key Features

- **4-Level System**: SMA/EMA structure → RSI exhaustion → entry signals
- **Adaptive Polling**: 120s calm → 5s near trigger
- **Real-time Prices**: yfinance 5m bars with 8s timeout (no IBKR mkt data needed)
- **IBKR Integration**: Order execution, trailing stops, position sync via ib_async
- **Data-only Fallback**: Gracefully handles IBKR disconnects

## Environment

- Python 3.11+ with `ib_async`, `yfinance`, `pandas`, `numpy`
- IBKR TWS/IB Gateway on localhost:4002 (paper: DUQ538194)
- HK signals via Longbridge CLI
