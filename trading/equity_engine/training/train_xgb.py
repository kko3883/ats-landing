"""
XGBoost classifier training with Optuna hyperparameter optimization.

Trains a binary classifier that predicts whether the return over the
next N M15 bars will be positive (target=1) or not (target=0).

Features: 9 financial indicators from feature_engine.py
Target: binary label, 1 = future return > 0

Uses time-series cross-validation (expanding window) to prevent
look-ahead bias.  Saves the best model to models/xgb_entry_classifier.json.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import xgboost as xgb
    import optuna
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False
    logger.warning("xgboost and/or optuna not installed. pip install xgboost optuna")

from ..config import MODELS_DIR
from .prepare_dataset import OUTPUT_DIR, FEATURE_NAMES


def load_dataset(dataset_path: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Load the training dataset from Parquet."""
    if dataset_path:
        path = Path(dataset_path)
    else:
        # Try latest symlink
        latest = OUTPUT_DIR / "training_dataset_latest.parquet"
        if latest.exists():
            path = latest
        else:
            # Find the most recent dataset file
            files = sorted(OUTPUT_DIR.glob("training_dataset_*.parquet"))
            if not files:
                print("No training dataset found. Run prepare_dataset.py first.")
                return None
            path = files[-1]

    print(f"Loading dataset: {path}")
    df = pd.read_parquet(path)
    print(f"  {len(df)} rows, {df['target'].sum()} positive ({df['target'].mean():.1%})")
    return df


def split_time_series(
    df: pd.DataFrame,
    n_splits: int = 5,
) -> list[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """
    Time-series split: expanding window.
    Returns list of (X_train, y_train, X_val, y_val) tuples.
    """
    df = df.sort_values("timestamp").reset_index(drop=True)
    n = len(df)
    splits = []

    for i in range(n_splits):
        # Train: first (i+1)/(n_splits+1) fraction
        train_end = int(n * (i + 1) / (n_splits + 1))
        val_end = min(int(n * (i + 2) / (n_splits + 1)), n)

        if train_end >= val_end or train_end == 0 or val_end <= train_end:
            continue

        X_train = df[FEATURE_NAMES].iloc[:train_end].values
        y_train = df["target"].iloc[:train_end].values
        X_val = df[FEATURE_NAMES].iloc[train_end:val_end].values
        y_val = df["target"].iloc[train_end:val_end].values

        splits.append((X_train, y_train, X_val, y_val))

    return splits


def objective(trial, X_train, y_train, X_val, y_val) -> float:
    """Optuna objective function — minimize validation error."""
    params = {
        "max_depth": trial.suggest_int("max_depth", 3, 10),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        "n_estimators": trial.suggest_int("n_estimators", 50, 500),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        "min_child_weight": trial.suggest_int("min_child_weight", 1, 20),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 1.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 1.0, log=True),
        "scale_pos_weight": trial.suggest_float("scale_pos_weight", 0.5, 5.0),
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "verbosity": 0,
        "n_jobs": -1,
        "tree_method": "hist",
    }

    model = xgb.XGBClassifier(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    # Predict on validation set
    y_pred_proba = model.predict_proba(X_val)[:, 1]
    y_pred = (y_pred_proba >= 0.5).astype(int)

    # Compute accuracy
    accuracy = (y_pred == y_val).mean()

    return accuracy  # Optuna maximizes by default


def train_xgb(
    df: Optional[pd.DataFrame] = None,
    n_trials: int = 100,
    n_splits: int = 5,
    output_path: Optional[str] = None,
) -> Optional[xgb.XGBClassifier]:
    """
    Train an XGBoost classifier with Optuna hyperparameter optimization.

    Args:
        df: Training DataFrame (loads latest dataset if None)
        n_trials: Number of Optuna trials
        n_splits: Time-series cross-validation splits
        output_path: Where to save the model JSON

    Returns:
        Trained XGBClassifier, or None on failure
    """
    if not HAVE_DEPS:
        print("xgboost and/or optuna not installed. Run: pip install xgboost optuna")
        return None

    if df is None:
        df = load_dataset()
        if df is None:
            return None

    # Handle NaN and Inf
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_NAMES + ["target"])
    print(f"  After NaN drop: {len(df)} rows")

    # Time-series split
    splits = split_time_series(df, n_splits)
    print(f"  Time-series splits: {len(splits)}")

    if not splits:
        print("  No valid splits generated.")
        return None

    # Use the first split for Optuna search
    X_train, y_train, X_val, y_val = splits[0]

    print(f"\n  Hyperparameter optimization ({n_trials} trials)...")

    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda trial: objective(trial, X_train, y_train, X_val, y_val),
        n_trials=n_trials,
        show_progress_bar=True,
    )

    print(f"\n  Best trial: accuracy={study.best_value:.4f}")
    print(f"  Best params: {json.dumps(study.best_params, indent=2)}")

    # ── Train final model on all data with best params ──────────────────

    # Combine all training data from all splits
    all_X = np.vstack([s[0] for s in splits] + [splits[-1][2]])
    all_y = np.hstack([s[1] for s in splits] + [splits[-1][3]])

    print(f"\n  Training final model on {len(all_X)} total rows...")

    best_params = study.best_params
    best_params.update({
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "verbosity": 1,
        "n_jobs": -1,
        "tree_method": "hist",
    })

    model = xgb.XGBClassifier(**best_params)
    model.fit(all_X, all_y, verbose=True)

    # ── Feature importance ──────────────────────────────────────────────

    importance = model.get_booster().get_score(importance_type="gain")
    print("\n  Feature importance (gain):")
    for name in FEATURE_NAMES:
        gain = importance.get(f"f{i}", importance.get(name, 0))
        print(f"    {name:25s} {gain:10.2f}")

    # ── Save model ──────────────────────────────────────────────────────

    output = Path(output_path or str(MODELS_DIR / "xgb_entry_classifier.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(output))

    print(f"\n  Model saved: {output} ({output.stat().st_size / 1024:.1f} KB)")

    # Write metadata
    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features": FEATURE_NAMES,
        "n_rows": int(len(all_X)),
        "n_features": len(FEATURE_NAMES),
        "best_accuracy": float(study.best_value),
        "best_params": study.best_params,
        "n_trials": n_trials,
    }
    meta_path = output.with_suffix(".meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  Metadata: {meta_path}")

    return model


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train XGBoost entry classifier")
    parser.add_argument("--dataset", help="Path to training dataset Parquet")
    parser.add_argument("--trials", type=int, default=100, help="Optuna trials")
    parser.add_argument("--output", help="Model output path")
    args = parser.parse_args()

    df = load_dataset(args.dataset) if args.dataset else load_dataset()
    if df is not None:
        train_xgb(df, n_trials=args.trials, output_path=args.output)