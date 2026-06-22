"""
Model training pipeline for the equity engine.

Components:
  - prepare_dataset.py: Fetch historical data, engineer features, create labeled dataset
  - train_xgb.py:       XGBoost training with Optuna hyperparameter optimization
"""