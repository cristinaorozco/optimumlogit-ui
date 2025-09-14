import pandas as pd
import joblib
from pathlib import Path

_model = None

def _default_model_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]  # sube de src/ a raíz
    return repo_root / "freight_rate_pipeline.joblib"

def load_model(path: str | None = None):
    global _model
    if _model is None:
        model_path = Path(path) if path else _default_model_path()
        if not model_path.exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")
        _model = joblib.load(model_path)
    return _model

def predict_one(payload: dict, model_path: str | None = None) -> float:
    model = load_model(model_path)
    X = pd.DataFrame([payload])
    yhat = model.predict(X)[0]
    return float(yhat)