"""
Layer 3: Micro Guard (1-Minute — Risk & Exit Manager)

Manages live positions on a 1-minute loop:
  - Dynamic trailing stop (base + micro-volatility adjustments)
  - Volume acceleration detection
  - Time-decay exit (flat for 5 hours → exit)
  - Regime-based stop tightening
"""