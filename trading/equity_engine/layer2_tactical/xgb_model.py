"""
XGBoost model loading and inference wrapper.

Loads a pre-trained XGBoost classifier from JSON (saved by training/train_xgb.py)
and provides sub-millisecond inference for real-time M15 evaluation.

If no model file exists yet, returns a sensible default (probability = 0.5)
so the engine can still run while the model is being trained.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import xgboost as xgb
    HAVE_XGB = True
except ImportError:
    HAVE_XGB = False
    logger.warning("xgboost not installed — inference will use fallback (prob=0.5). "
                   "pip install xgboost for ML-based entries.")


@dataclass
class InferenceResult:
    """Result from XGBoost inference on one feature vector."""
    probability: float           # 0.0–1.0, probability of positive return over N bars
    exceeds_threshold: bool      # True if prob > configured threshold
    raw_log_odds: float = 0.0    # Raw model output before sigmoid


class XGBInference:
    """
    Loads an XGBoost model and runs inference on feature vectors.

    Handles missing model gracefully (fallback to 0.5 probability).
    """

    def __init__(
        self,
        model_path: str,
        threshold: float = 0.65,
    ):
        self._model_path = Path(model_path)
        self._threshold = threshold
        self._model: Optional[xgb.Booster] = None

        if HAVE_XGB:
            self._load_model()
        else:
            logger.info("XGBoost not available — using fallback inference")

    def _load_model(self):
        """Load model from JSON file.  No-op if file doesn't exist."""
        if not self._model_path.exists():
            logger.warning(
                f"Model file not found: {self._model_path}. "
                f"Run training/train_xgb.py to create one. "
                f"Inference will return prob=0.5 until model is trained."
            )
            return

        try:
            self._model = xgb.Booster()
            self._model.load_model(str(self._model_path))
            logger.info(f"XGBoost model loaded: {self._model_path} ({os.path.getsize(self._model_path)} bytes)")
        except Exception as e:
            logger.error(f"Failed to load XGBoost model: {e}")
            self._model = None

    def predict(self, features: np.ndarray) -> InferenceResult:
        """
        Run inference on one feature vector.

        Args:
            features: numpy array of shape (9,) — must match FEATURE_NAMES order

        Returns:
            InferenceResult with probability and threshold check
        """
        if self._model is not None and HAVE_XGB:
            return self._predict_xgb(features)
        else:
            return self._predict_fallback(features)

    def predict_batch(self, features_batch: np.ndarray) -> list[InferenceResult]:
        """
        Run inference on a batch of feature vectors.

        Args:
            features_batch: numpy array of shape (N, 9)

        Returns:
            List of InferenceResult, one per row
        """
        if self._model is not None and HAVE_XGB:
            dmatrix = xgb.DMatrix(features_batch)
            raw_preds = self._model.predict(dmatrix)
            results = []
            for raw in raw_preds:
                prob = float(1.0 / (1.0 + np.exp(-raw)))  # sigmoid for binary:logistic
                results.append(InferenceResult(
                    probability=prob,
                    exceeds_threshold=prob >= self._threshold,
                    raw_log_odds=float(raw),
                ))
            return results
        else:
            return [self._predict_fallback(row) for row in features_batch]

    def _predict_xgb(self, features: np.ndarray) -> InferenceResult:
        """Run inference using loaded XGBoost model."""
        dmatrix = xgb.DMatrix(features.reshape(1, -1))
        raw_pred = float(self._model.predict(dmatrix)[0])
        prob = float(1.0 / (1.0 + np.exp(-raw_pred)))  # sigmoid
        return InferenceResult(
            probability=prob,
            exceeds_threshold=prob >= self._threshold,
            raw_log_odds=raw_pred,
        )

    def _predict_fallback(self, features: np.ndarray) -> InferenceResult:
        """
        Fallback inference when no model is available.

        Returns prob=0.5 (neutral) so the engine doesn't fire entries
        until a trained model is loaded.  This is safe: no entries = no losses.
        """
        return InferenceResult(
            probability=0.5,
            exceeds_threshold=False,
            raw_log_odds=0.0,
        )

    def reload(self):
        """Reload model from disk (e.g., after retraining)."""
        if HAVE_XGB:
            self._load_model()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None