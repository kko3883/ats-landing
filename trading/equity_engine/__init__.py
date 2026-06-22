"""
Hierarchical Multi-Timeframe Trading Engine for Equities.

Three-layer architecture:
  Layer 1 (Daily):     Macro filter — SMA200 trend + regime gate
  Layer 2 (15-Minute): Tactical trigger — XGBoost entry probability
  Layer 3 (1-Minute):  Micro guard — dynamic trailing stop + exit signals

Data: Longbridge WebSocket streaming (M1, M15, D1)
Execution: IB Gateway via ib_insync (order routing only)
"""