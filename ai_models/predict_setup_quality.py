from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_models.features import (
    CRITICAL_FEATURES,
    PayloadDiagnostics,
    critical_completeness,
    encode_single_payload,
    evaluate_payload_quality,
    load_feature_schema,
    validate_feature_schema,
    validate_no_leakage_features,
)
from ai_models.model_registry import (
    FEATURE_SCHEMA_VERSION,
    MODEL_VERSION,
    ROOT_DIR,
)
from config.settings import (
    PYTORCH_AI_BLOCKING_MODE,
    PYTORCH_AI_DEBUG_FEATURES,
    PYTORCH_AI_MODEL_PATH,
    PYTORCH_AI_MODEL_TYPE,
    PYTORCH_AI_MODEL_VERSION,
    PYTORCH_AI_MIN_CRITICAL_COMPLETENESS,
    PYTORCH_AI_NEUTRAL_SL,
    PYTORCH_AI_NEUTRAL_TP1,
    PYTORCH_AI_SCHEMA_PATH,
    PYTORCH_MAX_SL_PROBABILITY,
    PYTORCH_MIN_TP1_PROBABILITY_TO_ALLOW,
    PYTORCH_MIN_TP1_PROBABILITY_TO_BOOST,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_MAX_CONFIDENCE = 0.95
_MIN_CONFIDENCE = 0.10
_WARNED_FAILURE_REASONS: set[str] = set()


@dataclass
class AIPrediction:
    tp1_probability: float = 0.50
    sl_probability: float = 0.50
    expired_probability: float = 0.0
    breakeven_probability: float = 0.0
    expected_pips: float = 0.0
    model_confidence: float = 0.0
    ai_recommendation: str = "allow"
    ai_label: str = "AI-ALLOWED SETUP"
    would_block: bool = False
    model_version: str = PYTORCH_AI_MODEL_VERSION or MODEL_VERSION
    model_type: str = PYTORCH_AI_MODEL_TYPE
    model_path: str = PYTORCH_AI_MODEL_PATH
    schema_path: str = PYTORCH_AI_SCHEMA_PATH
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    model_enabled: bool = False
    advisory_or_blocking: str = "advisory"
    reason: str = "neutral_fallback"
    confidence_tier: str = "low"
    missing_critical_features: list[str] = field(default_factory=list)
    missing_features_count: int = 0
    unknown_categories_count: int = 0
    schema_match: bool = True
    raw_logits: dict[str, float] = field(default_factory=dict)
    last_prediction_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def neutral_prediction(
    reason: str,
    *,
    diag: PayloadDiagnostics | None = None,
    raw_logits: dict[str, float] | None = None,
    model_path: str | Path | None = None,
    schema_path: str | Path | None = None,
) -> AIPrediction:
    return AIPrediction(
        tp1_probability=PYTORCH_AI_NEUTRAL_TP1,
        sl_probability=PYTORCH_AI_NEUTRAL_SL,
        expected_pips=0.0,
        model_confidence=_MIN_CONFIDENCE,
        ai_recommendation="allow",
        ai_label="AI-ALLOWED SETUP",
        would_block=False,
        model_version=PYTORCH_AI_MODEL_VERSION or MODEL_VERSION,
        model_type=PYTORCH_AI_MODEL_TYPE,
        model_path=str(model_path or PYTORCH_AI_MODEL_PATH),
        schema_path=str(schema_path or PYTORCH_AI_SCHEMA_PATH),
        model_enabled=False,
        advisory_or_blocking="blocking" if PYTORCH_AI_BLOCKING_MODE else "advisory",
        reason=reason,
        confidence_tier="low",
        missing_critical_features=list(diag.missing_critical_features) if diag else [],
        missing_features_count=len(diag.missing_features) if diag else 0,
        unknown_categories_count=len(diag.unknown_categories) if diag else 0,
        schema_match=bool(diag.schema_match) if diag else True,
        raw_logits=dict(raw_logits or {}),
        last_prediction_at=datetime.now(timezone.utc).isoformat(),
    )


def _warn_once(reason: str, message: str, *args: Any) -> None:
    if reason in _WARNED_FAILURE_REASONS:
        return
    _WARNED_FAILURE_REASONS.add(reason)
    logger.warning(message, *args)


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


def _recommend(tp1: float, sl: float, *, completeness: float) -> str:
    if completeness < PYTORCH_AI_MIN_CRITICAL_COMPLETENESS:
        return "allow"
    if sl > PYTORCH_MAX_SL_PROBABILITY:
        return "caution"
    if tp1 >= PYTORCH_MIN_TP1_PROBABILITY_TO_BOOST:
        return "boost"
    if tp1 >= PYTORCH_MIN_TP1_PROBABILITY_TO_ALLOW:
        return "allow"
    return "caution"


def _would_block(tp1: float, sl: float, *, completeness: float) -> bool:
    if completeness < PYTORCH_AI_MIN_CRITICAL_COMPLETENESS:
        return False
    return tp1 < PYTORCH_MIN_TP1_PROBABILITY_TO_ALLOW or sl > PYTORCH_MAX_SL_PROBABILITY


def _label_for_prediction(recommendation: str, would_block: bool) -> str:
    if recommendation == "boost":
        return "AI-CONFIRMED SETUP"
    if recommendation == "allow":
        return "AI-ALLOWED SETUP"
    if would_block:
        return "AI-WOULD-BLOCK SETUP"
    return "AI-CAUTION SETUP"


def _calibrated_confidence(tp1: float, completeness: float, schema_match: bool, unknown_count: int, total_features: int) -> tuple[float, str]:
    distance_factor = min(abs(tp1 - 0.5) * 2.0, 1.0)
    schema_factor = 1.0 if schema_match else 0.5
    unknown_penalty = 1.0 - min(unknown_count / max(total_features, 1), 1.0)
    raw = (completeness * 0.5) + (distance_factor * 0.3) + (schema_factor * 0.1) + (unknown_penalty * 0.1)
    confidence = max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, raw))
    if confidence >= 0.80 and completeness >= 0.95 and schema_match and unknown_count == 0:
        tier = "high"
    elif confidence >= 0.55:
        tier = "medium"
    else:
        tier = "low"
    return round(confidence, 4), tier


def _log_debug(diag: PayloadDiagnostics, *, tensor_shape: tuple[int, ...], tp1: float, sl: float, expected_pips: float, raw_logits: dict[str, float]) -> None:
    if not PYTORCH_AI_DEBUG_FEATURES:
        return
    logger.info(
        "AI FEATURE DEBUG: missing_features=%d unknown_categories=%d tensor_shape=%s tp1_probability=%.4f sl_probability=%.4f expected_pips=%.2f",
        len(diag.missing_features),
        len(diag.unknown_categories),
        tensor_shape,
        tp1,
        sl,
        expected_pips,
    )
    logger.info("MODEL RAW OUTPUTS: %s", raw_logits)
    logger.info("MODEL PROBABILITIES: tp1=%.4f sl=%.4f expected_pips=%.2f", tp1, sl, expected_pips)
    if diag.missing_features:
        logger.info("AI FEATURE DEBUG MISSING: %s", diag.missing_features)
    if diag.unknown_categories:
        logger.info("AI FEATURE DEBUG UNKNOWN: %s", diag.unknown_categories)
    logger.info(
        "AI FEATURE DEBUG NORMALISED RANGE: z_min=%.3f z_max=%.3f completeness=%.3f schema_match=%s",
        diag.z_min,
        diag.z_max,
        diag.completeness,
        diag.schema_match,
    )


def predict_setup_quality(
    setup_payload: dict[str, Any],
    *,
    model_path: str | Path | None = None,
    schema_path: str | Path | None = None,
) -> AIPrediction:
    try:
        selected_model_path = _resolve_project_path(model_path or PYTORCH_AI_MODEL_PATH)
        selected_schema_path = _resolve_project_path(schema_path or PYTORCH_AI_SCHEMA_PATH)
        if not selected_model_path.exists():
            _warn_once(
                "model_missing",
                "PyTorch AI approved model missing: %s; using neutral advisory fallback.",
                selected_model_path,
            )
            return neutral_prediction("model_missing", model_path=selected_model_path, schema_path=selected_schema_path)
        if not selected_schema_path.exists():
            _warn_once(
                "feature_schema_missing",
                "PyTorch AI approved feature schema missing: %s; using neutral advisory fallback.",
                selected_schema_path,
            )
            return neutral_prediction("feature_schema_missing", model_path=selected_model_path, schema_path=selected_schema_path)
        import torch
        from ai_models.setup_outcome_model import SpencerSetupOutcomeModel

        payload_obj = torch.load(selected_model_path, map_location="cpu")
        schema = load_feature_schema(selected_schema_path)
        schema_issues = validate_feature_schema(schema)
        if payload_obj.get("feature_schema_version") != schema.get("schema_version"):
            logger.warning(
                "PyTorch AI feature schema mismatch: model=%s live=%s",
                payload_obj.get("feature_schema_version"),
                schema.get("schema_version"),
            )
            return neutral_prediction("feature_schema_mismatch", model_path=selected_model_path, schema_path=selected_schema_path)
        if schema_issues:
            logger.warning("PyTorch AI feature schema issues: %s", schema_issues)

        diag = evaluate_payload_quality(setup_payload, schema)
        if PYTORCH_AI_DEBUG_FEATURES:
            logger.info(
                "AI FEATURE DEBUG RAW PAYLOAD: %s",
                {k: setup_payload.get(k) for k in list(setup_payload.keys())[:50]},
            )
            logger.info(
                "AI FEATURE DEBUG ROW: %s",
                {k: diag.feature_row.get(k) for k in list(diag.feature_row.keys())[:50]},
            )

        completeness_critical = critical_completeness(diag)
        if completeness_critical < PYTORCH_AI_MIN_CRITICAL_COMPLETENESS:
            logger.warning(
                "AI PREDICTION WARNING: critical feature missing — neutral fallback (completeness=%.2f, missing=%s)",
                completeness_critical,
                diag.missing_critical_features,
            )
            return neutral_prediction("critical_features_missing", diag=diag, model_path=selected_model_path, schema_path=selected_schema_path)

        x, diag = encode_single_payload(setup_payload, schema, return_diagnostics=True)
        model = SpencerSetupOutcomeModel(input_dim=int(payload_obj["input_dim"]))
        model.load_state_dict(payload_obj["state_dict"])
        model.eval()
        with torch.no_grad():
            out = model(torch.tensor(x, dtype=torch.float32))
            tp1_logit = float(out["tp1_logit"][0].item())
            sl_logit = float(out["sl_logit"][0].item())
            tp1 = float(torch.sigmoid(out["tp1_logit"])[0].item())
            sl = float(torch.sigmoid(out["sl_logit"])[0].item())
            expected_pips = float(out["expected_pips"][0].item())

        raw_logits = {
            "tp1_logit": round(tp1_logit, 4),
            "sl_logit": round(sl_logit, 4),
            "expected_pips_raw": round(expected_pips, 4),
        }

        _log_debug(
            diag,
            tensor_shape=tuple(x.shape),
            tp1=tp1,
            sl=sl,
            expected_pips=expected_pips,
            raw_logits=raw_logits,
        )

        total_features = len(schema.get("categorical_features", [])) + len(schema.get("numeric_features", []))
        confidence, tier = _calibrated_confidence(
            tp1=tp1,
            completeness=completeness_critical,
            schema_match=diag.schema_match,
            unknown_count=len(diag.unknown_categories),
            total_features=total_features,
        )
        expired = max(0.0, min(1.0, 1.0 - max(tp1, sl)))
        recommendation = _recommend(tp1, sl, completeness=completeness_critical)
        would_block = _would_block(tp1, sl, completeness=completeness_critical)
        ai_label = _label_for_prediction(recommendation, would_block)
        return AIPrediction(
            tp1_probability=round(tp1, 4),
            sl_probability=round(sl, 4),
            expired_probability=round(expired, 4),
            breakeven_probability=0.0,
            expected_pips=round(expected_pips, 2),
            model_confidence=confidence,
            ai_recommendation=recommendation,
            ai_label=ai_label,
            would_block=would_block,
            model_version=str(payload_obj.get("model_version", PYTORCH_AI_MODEL_VERSION or MODEL_VERSION)),
            model_type=PYTORCH_AI_MODEL_TYPE,
            model_path=str(selected_model_path),
            schema_path=str(selected_schema_path),
            feature_schema_version=str(schema.get("schema_version", FEATURE_SCHEMA_VERSION)),
            model_enabled=True,
            advisory_or_blocking="blocking" if PYTORCH_AI_BLOCKING_MODE else "advisory",
            reason="ok" if not diag.unknown_categories else "ok_with_unknown_categories",
            confidence_tier=tier,
            missing_critical_features=list(diag.missing_critical_features),
            missing_features_count=len(diag.missing_features),
            unknown_categories_count=len(diag.unknown_categories),
            schema_match=bool(diag.schema_match),
            raw_logits=raw_logits,
            last_prediction_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        _warn_once("prediction_failed", "PyTorch AI prediction failed; falling back neutral: %s", exc)
        return neutral_prediction("prediction_failed", model_path=model_path or PYTORCH_AI_MODEL_PATH, schema_path=schema_path or PYTORCH_AI_SCHEMA_PATH)


def _row_to_live_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Strip label fields from a training row so it looks like a live payload."""
    payload = dict(row)
    for key in ("tp1_hit", "sl_hit", "final_result", "pips_result", "outcome_class", "target_tp1_before_sl", "protected_after_tp1"):
        payload.pop(key, None)
    return payload


def _debug_sample(dataset_path: Path, row_index: int, *, model_path: Path | None = None, schema_path: Path | None = None) -> dict[str, Any]:
    import pandas as pd

    if not dataset_path.exists():
        return {"status": "fail", "error": f"dataset not found: {dataset_path}"}
    df = pd.read_csv(dataset_path)
    if row_index < 0 or row_index >= len(df):
        return {"status": "fail", "error": f"row {row_index} out of range (rows={len(df)})"}
    raw_row = df.iloc[row_index].to_dict()
    payload = _row_to_live_payload(raw_row)

    schema = load_feature_schema(_resolve_project_path(schema_path or PYTORCH_AI_SCHEMA_PATH))
    schema_issues = validate_feature_schema(schema)
    leakage_ok = True
    leakage_error = ""
    try:
        validate_no_leakage_features(schema.get("feature_columns", []))
        validate_no_leakage_features([*schema.get("categorical_features", []), *schema.get("numeric_features", [])])
    except ValueError as exc:
        leakage_ok = False
        leakage_error = str(exc)
    if schema_issues:
        return {"status": "fail", "error": "feature_schema_invalid", "issues": schema_issues}

    diag_live = evaluate_payload_quality(payload, schema)
    live_x, _ = encode_single_payload(payload, schema, return_diagnostics=True)

    train_df = df.iloc[[row_index]]
    from ai_models.features import encode_features

    train_bundle = encode_features(train_df, schema=schema)
    train_x = train_bundle.x

    shape_match = live_x.shape == train_x.shape
    diff = (live_x - train_x).max() if shape_match else None

    prediction = predict_setup_quality(payload, model_path=model_path, schema_path=schema_path).to_dict()

    result: dict[str, Any] = {
        "row_index": row_index,
        "schema_version": schema.get("schema_version"),
        "feature_count": int(live_x.shape[1]) if len(live_x.shape) == 2 else 0,
        "leakage_check": "LEAKAGE CHECK PASSED" if leakage_ok else leakage_error,
        "protected_after_tp1_excluded": "protected_after_tp1" not in schema.get("numeric_features", [])
        and "protected_after_tp1" not in schema.get("categorical_features", [])
        and all(not str(col).startswith("protected_after_tp1") for col in schema.get("feature_columns", [])),
        "live_tensor_shape": list(live_x.shape),
        "training_tensor_shape": list(train_x.shape),
        "shape_match": shape_match,
        "max_abs_diff": float(abs(diff)) if diff is not None else None,
        "missing_features": diag_live.missing_features,
        "missing_critical_features": diag_live.missing_critical_features,
        "unknown_categories": diag_live.unknown_categories,
        "completeness": round(diag_live.completeness, 4),
        "schema_match": diag_live.schema_match,
        "prediction": prediction,
    }
    tolerance = 1e-4
    if shape_match and (diff is not None) and abs(float(diff)) <= tolerance and not diag_live.missing_critical_features:
        result["status"] = "pass"
        result["message"] = "FEATURE PIPELINE CHECK PASSED"
        if leakage_ok:
            result["leakage_message"] = "LEAKAGE CHECK PASSED"
    else:
        result["status"] = "mismatch"
        result["message"] = "FEATURE PIPELINE CHECK MISMATCH"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Spencer setup quality predictor + sanity check.")
    parser.add_argument("--debug-sample", type=str, default="", help="Path to training dataset CSV for sanity check.")
    parser.add_argument("--row", type=int, default=0, help="Row index to debug.")
    parser.add_argument("--payload-json", type=str, default="", help="Inline JSON payload to predict.")
    parser.add_argument("--model", type=str, default="", help="Optional model checkpoint path.")
    parser.add_argument("--schema", type=str, default="", help="Optional feature schema path.")
    args = parser.parse_args()
    model_path = Path(args.model) if args.model else None
    schema_path = Path(args.schema) if args.schema else None

    if args.debug_sample:
        result = _debug_sample(Path(args.debug_sample), args.row, model_path=model_path, schema_path=schema_path)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result.get("status") == "pass" else 1)

    if args.payload_json:
        try:
            payload = json.loads(args.payload_json)
        except Exception as exc:
            print(json.dumps({"status": "fail", "error": f"invalid payload JSON: {exc}"}))
            sys.exit(2)
        prediction = predict_setup_quality(payload, model_path=model_path, schema_path=schema_path).to_dict()
        print(json.dumps(prediction, indent=2, default=str))
        return

    parser.error("Provide either --debug-sample <csv> or --payload-json <json>.")


if __name__ == "__main__":
    main()
