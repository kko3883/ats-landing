"""
Layer 2: Tactical Trigger (15-Minute — Entry Machine)

Every 15 minutes, computes features for shortlisted stocks, runs XGBoost
inference to get entry probability, and generates orders when threshold
is exceeded.

Components:
  - feature_engine.py: RSI, ATR, VWAP distance, Volume Z-score calculation
  - xgb_model.py:     Model loading and inference wrapper
  - entry_manager.py: Entry order generation with ATR-based stop loss
"""