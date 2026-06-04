# Watchlist Screener Architecture

## Why This Design

A stock watchlist screener needs to turn 10,000+ stocks into a focused, actionable list
that feeds the 3-bucket trading system. The key design choices:

**1. Separate from the live daemon**
The screener runs daily (market close) as a cron job, not inside the trading loop.
This keeps it independent — no IBKR connection needed, easy to iterate and backtest.

**2. Tiered filtering, not a single black box**
Three passes (universe → factor score → strategy filter) so each stage is debuggable
and replaceable. You can tweak factor weights without touching the stock universe,
and vice versa.

**3. yfinance as the primary data source**
Free, covers both US and HK (`.HK` suffix), has data for all major stocks.
Caching is mandatory — yahoo rate-limits aggressive callers.

## Market Differences That Affect Screening

| Dimension | US | HK |
|-----------|-----|------|
| **Universe size** | ~6,000 listed, ~1,000 liquid | ~2,500 listed, ~300 liquid |
| **Sector mix** | Tech 30%, Finance 15%, Healthcare 15% | Finance 30%, Property 20%, China-Tech 15% |
| **Factor research** | Deeply validated (Jegadeesh, Fama-French, etc.) | Less published validation; momentum works but weaker |
| **Dividend culture** | Moderate (S&P yield ~1.5%) | Strong (HK yield ~3-4%, many REITs/property) |
| **Free float** | Generally high | Often low (family/state control); liquidity can be illusory |
| **yfinance reliability** | Excellent | Good for top 300, spotty for small caps |

## Data Flow

```
                                ┌─────────────────────┐
                                │  Stock Universe      │
                                │  (US: ~500, HK: ~200)│
                                └──────────┬──────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │  Data Source Layer   │
                                │  (yfinance + cache)  │
                                │  → OHLCV + financials│
                                └──────────┬──────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │  Tier 1: Liquidity   │
                                │  Pass-through filter │
                                │  (built into universe)│
                                └──────────┬──────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │  Tier 2: Factor Score│
                                │  Momentum + Quality  │
                                │  + LowVol + Value    │
                                │  → Top 50 per market │
                                └──────────┬──────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │  Tier 3: Bucket      │
                                │  Strategy Filters    │
                                │  Pullback / Breakout │
                                │  / Squeeze / Diverg  │
                                └──────────┬──────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │   watchlist.json     │
                                │   + Telegram summary │
                                └─────────────────────┘
```

## Free Data Source Comparison

| Source | US Coverage | HK Coverage | Free Tier | Reliability |
|--------|-------------|-------------|-----------|-------------|
| **yfinance** | Excellent | Good (top 300) | Unlimited | ✅ Best free option |
| Alpha Vantage | Good | Poor | 5 req/min | OK, but HK is weak |
| Polygon.io | Excellent | Fair | Paid | Too expensive for screening |
| IBKR reqHistoricalData | Good | Good | Requires running Gateway | Not free (requires IB) |
| HKEX website | N/A | Good (all stocks) | Free | Scrape-only, no API |

**Winner: yfinance.** Covers both markets, free, well-maintained Python library.

## File Layout

```
watchlist/
├── __init__.py            # Package
├── config.py              # Market configs, stock universes, factor weights
├── data_source.py         # yfinance wrapper with caching
├── factors.py             # 4-factor scoring (momentum, quality, low vol, value)
├── filters.py             # Per-bucket Tier 3 strategy filters
├── screener.py            # Main orchestrator — CLI entry point
├── unis/
│   ├── us_stocks.json     # US stock universe
│   └── hk_stocks.json     # HK stock universe
```

## Output Format

```json
{
  "generated_at": "2026-05-31T18:00:00Z",
  "regime": "trending",
  "us_market": {
    "top_stocks": [
      {
        "symbol": "MSFT",
        "score": 1.85,
        "factors": {"momentum": 2.1, "quality": 1.8, "low_vol": 1.2, "value": -0.3},
        "bucket2_filters": {"pullback": false, "breakout": true},
        "bucket3_filters": {"bb_squeeze": false, "divergence": false}
      }
    ]
  },
  "hk_market": { "...": "..." }
}
```
