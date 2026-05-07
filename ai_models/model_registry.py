from __future__ import annotations

from pathlib import Path


MODEL_VERSION = "setup_outcome_model_v1"
FEATURE_SCHEMA_VERSION = "spencer_setup_features_v1"

ROOT_DIR = Path(__file__).resolve().parents[1]
ML_DIR = ROOT_DIR / "data" / "ml"
MODEL_DIR = ML_DIR / "models"
CHECKPOINT_DIR = ML_DIR / "checkpoints"
FEATURE_SCHEMA_PATH = ML_DIR / "feature_schema.json"
NORMALIZATION_STATS_PATH = ML_DIR / "normalization_stats.json"
CATEGORY_MAPS_PATH = ML_DIR / "category_maps.json"
DEFAULT_MODEL_PATH = MODEL_DIR / "setup_outcome_model_v1.pt"
APPROVED_MODEL_NAME = "setup_approved_outcome_model_v1"
APPROVED_MODEL_PATH = MODEL_DIR / f"{APPROVED_MODEL_NAME}.pt"
APPROVED_FEATURE_SCHEMA_PATH = ML_DIR / "approved_feature_schema.json"
APPROVED_NORMALIZATION_STATS_PATH = ML_DIR / "approved_normalization_stats.json"
APPROVED_CATEGORY_MAPS_PATH = ML_DIR / "approved_category_maps.json"
APPROVED_6M_MODEL_NAME = "setup_approved_outcome_model_6m_v1"
APPROVED_6M_MODEL_PATH = MODEL_DIR / f"{APPROVED_6M_MODEL_NAME}.pt"
APPROVED_6M_FEATURE_SCHEMA_PATH = ML_DIR / "approved_6m_feature_schema.json"
APPROVED_6M_NORMALIZATION_STATS_PATH = ML_DIR / "approved_6m_normalization_stats.json"
APPROVED_6M_CATEGORY_MAPS_PATH = ML_DIR / "approved_6m_category_maps.json"
DEFAULT_DATASET_PATH = ML_DIR / "spencer_setup_outcomes.csv"


def ensure_ml_dirs() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    ML_DIR.mkdir(parents=True, exist_ok=True)


def model_path_for_name(model_name: str) -> Path:
    clean = str(model_name or "").strip()
    if not clean:
        clean = MODEL_VERSION
    if clean.endswith(".pt"):
        return MODEL_DIR / clean
    return MODEL_DIR / f"{clean}.pt"
