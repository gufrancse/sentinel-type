"""
Module 5 — Anomaly Detection Model (Core ML Engine).

Trains one Isolation Forest per user on their enrollment samples
(keystroke + mouse dynamics). Isolation Forest is used deliberately
because it only needs "normal" behavior to learn from — we never have
impostor/attack data available for a real user, so a one-class model
is the right fit.

Each user's trained model + scaler is pickled to disk under
instance/ml_models/user_<id>.joblib together with the min/max of the
training anomaly scores, which we use to normalize a live score into
an intuitive 0-100 "match score" (Module 5 output spec).
"""

import os
import numpy as np
import joblib
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

MODEL_DIR = os.path.join("instance", "ml_models")

FEATURE_ORDER = [
    "averageDwellTime",
    "averageFlightTime",
    "typingDuration",
    "typingSpeedWPM",
    "averageMouseSpeed",
    "averageClickInterval",
]


def _model_path(user_id):
    return os.path.join(MODEL_DIR, f"user_{user_id}.joblib")

def delete_model(model_path):
    """Removes a user's saved model file, if it exists (used by profile reset)."""
    if model_path and os.path.exists(model_path):
        os.remove(model_path)

def _to_vector(sample: dict):
    return [float(sample.get(f, 0) or 0) for f in FEATURE_ORDER]


def train_and_save(user_id, samples):
    """
    samples: list of dicts, each with the FEATURE_ORDER keys
    (the same 10 enrollment samples used to build the average profile).
    """
    os.makedirs(MODEL_DIR, exist_ok=True)

    X = np.array([_to_vector(s) for s in samples])

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # contamination is nominal here — enrollment samples are all genuine.
    # n_estimators kept modest since training sets are tiny (~10 samples).
    model = IsolationForest(
        n_estimators=100,
        contamination=0.1,
        random_state=42
    )
    model.fit(X_scaled)

    train_scores = model.score_samples(X_scaled)
    score_min, score_max = float(train_scores.min()), float(train_scores.max())

    # Guard against a degenerate all-identical-samples case
    if score_max - score_min < 1e-9:
        score_max = score_min + 1e-6

    path = _model_path(user_id)
    joblib.dump(
        {
            "model": model,
            "scaler": scaler,
            "score_min": score_min,
            "score_max": score_max,
        },
        path
    )
    return path


def score_sample(user_id, sample: dict, model_path=None):
    """
    Returns a match score from 0-100, where higher = more like the
    user's normal behavior (matches Module 5's 0-100% spec).
    Falls back to None if no model exists yet (e.g. not enrolled).
    """
    path = model_path or _model_path(user_id)

    if not os.path.exists(path):
        return None

    bundle = joblib.load(path)
    model = bundle["model"]
    scaler = bundle["scaler"]
    score_min = bundle["score_min"]
    score_max = bundle["score_max"]

    x = np.array([_to_vector(sample)])
    x_scaled = scaler.transform(x)

    raw_score = model.score_samples(x_scaled)[0]

    normalized = (raw_score - score_min) / (score_max - score_min) * 100
    return round(float(np.clip(normalized, 0, 100)), 2)
