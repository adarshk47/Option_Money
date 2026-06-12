"""
Machine-learning overlay — RandomForest classifier predicting the
direction of the next N candles from engineered features. Used as one
weighted input to the recommendation engine (never a standalone signal).

Train:    python -m ai_engine.ml_model            (uses broker history)
Predict:  ml_model.predict_score(df) -> -1..+1
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from ai_engine.indicators import add_all_indicators
from backend.config import PROJECT_ROOT
from backend.logger import get_logger

log = get_logger(__name__)

MODEL_DIR = PROJECT_ROOT / "models"
MODEL_DIR.mkdir(exist_ok=True)

FEATURES = [
    "rsi", "macd_hist", "adx", "st_dir",
    "ema_gap", "vwap_gap", "bb_pos", "vol_ratio", "ret1", "ret3", "ret5",
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = add_all_indicators(df)
    f = pd.DataFrame(index=df.index)
    f["rsi"] = df["rsi"]
    f["macd_hist"] = df["macd_hist"]
    f["adx"] = df["adx"]
    f["st_dir"] = df["st_dir"]
    f["ema_gap"] = (df["ema9"] - df["ema20"]) / df["close"] * 100
    f["vwap_gap"] = (df["close"] - df["vwap"]) / df["close"] * 100
    bb_range = (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    f["bb_pos"] = (df["close"] - df["bb_lower"]) / bb_range
    f["vol_ratio"] = df["volume"] / df["vol_sma20"].replace(0, np.nan)
    f["ret1"] = df["close"].pct_change(1) * 100
    f["ret3"] = df["close"].pct_change(3) * 100
    f["ret5"] = df["close"].pct_change(5) * 100
    return f


class MLModel:
    def __init__(self, name: str = "direction_rf") -> None:
        self.path = MODEL_DIR / f"{name}.pkl"
        self._model = None
        if self.path.exists():
            try:
                self._model = joblib.load(self.path)
                log.info("Loaded ML model from %s", self.path)
            except Exception:
                log.exception("Failed to load ML model")

    def train(self, df: pd.DataFrame, horizon: int = 3,
              threshold_pct: float = 0.05) -> dict:
        """Label: does close move > threshold% up (+1) / down (-1) in `horizon` bars."""
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score

        feats = build_features(df)
        fwd = df["close"].shift(-horizon) / df["close"] - 1
        label = pd.Series(0, index=df.index)
        label[fwd > threshold_pct / 100] = 1
        label[fwd < -threshold_pct / 100] = -1

        data = feats.assign(label=label).dropna()
        data = data[data["label"] != 0]
        if len(data) < 200:
            return {"error": f"Not enough samples ({len(data)}) — fetch more history"}

        X, y = data[FEATURES], data["label"]
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25,
                                                  shuffle=False)
        model = RandomForestClassifier(n_estimators=300, max_depth=6,
                                       min_samples_leaf=20, random_state=42,
                                       n_jobs=-1)
        model.fit(X_tr, y_tr)
        acc = accuracy_score(y_te, model.predict(X_te))
        joblib.dump(model, self.path)
        self._model = model
        log.info("ML model trained: %d samples, test accuracy %.1f%%",
                 len(data), acc * 100)
        return {"samples": len(data), "test_accuracy": round(acc, 3)}

    def predict_score(self, df: pd.DataFrame) -> float:
        """Probability-weighted direction score in [-1, 1]; 0 when no model."""
        if self._model is None or df is None or len(df) < 60:
            return 0.0
        try:
            feats = build_features(df)[FEATURES].iloc[[-1]].dropna()
            if feats.empty:
                return 0.0
            proba = self._model.predict_proba(feats)[0]
            classes = list(self._model.classes_)
            p_up = proba[classes.index(1)] if 1 in classes else 0.0
            p_dn = proba[classes.index(-1)] if -1 in classes else 0.0
            return float(p_up - p_dn)
        except Exception:
            log.exception("ML prediction failed")
            return 0.0


ml_model = MLModel()


if __name__ == "__main__":
    # Train from broker history: 30 days of 5-minute NIFTY candles
    from backend.broker.angel_one import broker

    if broker.login():
        hist = broker.get_candles("NIFTY", "5min", days=30)
        print(ml_model.train(hist))
    else:
        print("Broker login failed — cannot fetch training data")
