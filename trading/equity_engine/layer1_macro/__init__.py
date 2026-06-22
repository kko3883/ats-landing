"""
Layer 1: Macro Filter (Daily — Long-Term Trend Gatekeeper)

Defines the market regime and approves/disapproves assets for trading.
Runs once per day before market open.

Components:
  - daily_filter.py: SMA(200) trend filter, liquidity gate
  - regime_client.py: Reads regime from Supabase, maps to trading modes
  - universe.py:    Manages the approved shortlist (seed + screener)
"""