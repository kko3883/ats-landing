"""
Backtesting harness for the multi-timeframe equity engine.

Replays historical D1, M15, and M1 bars chronologically through
all three layers, simulating fills with a slippage model.
"""