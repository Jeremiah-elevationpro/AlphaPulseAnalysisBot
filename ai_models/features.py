from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ai_models.model_registry import (
    CATEGORY_MAPS_PATH,
    FEATURE_SCHEMA_PATH,
    FEATURE_SCHEMA_VERSION,
    NORMALIZATION_STATS_PATH,
    ensure_ml_dirs,
)


CATEGORICAL_FEATURES: list[str] = [
    "symbol",
    "direction",
    "session_name",
    "h4_bias",
    "h1_bias",
    "dominant_bias",
    "scenario_type",
    "confirmation_type",
    "confirmation_grade",
    "market_condition",
]

NUMERIC_FEATURES: list[str] = [
    "bias_strength",
    "candidate_rank_score",
    "learning_score",
    "entry",
    "sl",
    "tp1",
    "tp2",
    "tp3",
    "risk_pips",
    "tp1_reward_pips",
    "tp2_reward_pips",
    "tp3_reward_pips",
    "tp1_rr",
    "tp2_rr",
    "tp3_rr",
    "reaction_level_present",
    "volatility_at_entry",
    "atr_m15",
    "atr_h1",
    "candle_body_ratio",
    "candle_wick_ratio",
    "displacement_strength",
    "distance_to_psych_level",
    "distance_to_watch_zone",
    "distance_to_invalidation",
    "recent_momentum_3",
    "recent_momentum_5",
    "recent_momentum_10",
    "structure_alignment_score",
]

LABEL_COLUMNS: list[str] = [
    "tp1_hit",
    "sl_hit",
    "final_result",
    "pips_result",
    "outcome_class",
    "target_tp1_before_sl",
    "protected_after_tp1",
]

PRE_ENTRY_FEATURES: list[str] = [
    "symbol",
    "direction",
    "session_name",
    "h4_bias",
    "h1_bias",
    "dominant_bias",
    "scenario_type",
    "confirmation_type",
    "confirmation_grade",
    "market_condition",
    "bias_strength",
    "candidate_rank_score",
    "learning_score",
    "entry",
    "sl",
    "tp1",
    "tp2",
    "tp3",
    "risk_pips",
    "tp1_reward_pips",
    "tp2_reward_pips",
    "tp3_reward_pips",
    "tp1_rr",
    "tp2_rr",
    "tp3_rr",
    "reaction_level_present",
    "volatility_at_entry",
    "atr_m15",
    "atr_h1",
    "candle_body_ratio",
    "candle_wick_ratio",
    "displacement_strength",
    "distance_to_psych_level",
    "distance_to_watch_zone",
    "distance_to_invalidation",
    "recent_momentum_3",
    "recent_momentum_5",
    "recent_momentum_10",
    "structure_alignment_score",
]

LEAKAGE_FEATURES: frozenset[str] = frozenset(
    {
        "protected_after_tp1",
        "tp1_hit",
        "tp2_hit",
        "tp3_hit",
        "sl_hit",
        "final_result",
        "result",
        "pips_result",
        "outcome_class",
        "target_tp1_before_sl",
        "closed_at",
        "review_notes",
    }
)

CRITICAL_FEATURES: frozenset[str] = frozenset(
    {
        "direction",
        "session_name",
        "h4_bias",
        "h1_bias",
        "scenario_type",
        "confirmation_type",
        "confirmation_grade",
        "candidate_rank_score",
        "learning_score",
        "risk_pips",
        "tp1_rr",
        "tp2_rr",
        "tp3_rr",
        "structure_alignment_score",
    }
)

UNKNOWN_TOKEN: str = "unknown"
NUMERIC_DEFAULTS: dict[str, float] = {name: 0.0 for name in NUMERIC_FEATURES}
NUMERIC_DEFAULTS["bias_strength"] = 0.55  # neutral-moderate
NUMERIC_DEFAULTS["reaction_level_present"] = 0.0
NUMERIC_DEFAULTS["structure_alignment_score"] = 0.0
NUMERIC_DEFAULTS["candle_body_ratio"] = 0.5
NUMERIC_DEFAULTS["candle_wick_ratio"] = 0.0
NUMERIC_DEFAULTS["displacement_strength"] = 0.5

CLAMP_Z_LIMIT: float = 5.0


@dataclass
class FeatureBundle:
    x: np.ndarray
    schema: dict[str, Any]
    feature_names: list[str]


@dataclass
class PayloadDiagnostics:
    missing_features: list[str] = field(default_factory=list)
    missing_critical_features: list[str] = field(default_factory=list)
    unknown_categories: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    feature_row: dict[str, Any] = field(default_factory=dict)
    completeness: float = 1.0
    schema_match: bool = True
    z_min: float = 0.0
    z_max: float = 0.0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, float) and math.isnan(value):
            return default
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            return float(stripped)
        return float(value)
    except Exception:
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _normalise_bias_strength(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            if math.isfinite(number):
                return max(0.0, min(1.0, number)) if number <= 1.0 else number
        except Exception:
            pass
    text = str(value or "").strip().lower()
    mapping = {
        "very_strong": 1.0,
        "strong": 0.85,
        "moderate": 0.55,
        "medium": 0.55,
        "weak": 0.25,
        "neutral": 0.0,
    }
    if text in mapping:
        return mapping[text]
    return _as_float(value, NUMERIC_DEFAULTS["bias_strength"])


def _clean_category(value: Any) -> str:
    if value is None:
        return UNKNOWN_TOKEN
    text = str(value).strip().lower()
    return text or UNKNOWN_TOKEN


def _outcome_class(row: dict[str, Any]) -> str:
    result = str(row.get("result") or row.get("final_result") or "").upper()
    if result == "STRONG_WIN" or _bool(row.get("tp3_hit")):
        return "STRONG_WIN"
    if result in {"BREAKEVEN_WIN", "EXPIRED_AFTER_TP1"}:
        return "BREAKEVEN_WIN"
    if result in {"LOSS", "SL_HIT", "STOP_LOSS_HIT"}:
        return "SL_HIT"
    if result.startswith("EXPIRED"):
        return "EXPIRED"
    if _bool(row.get("tp1_hit")):
        return "TP1_OR_BETTER"
    return "EXPIRED"


def _distance_to_psych(entry: float) -> float:
    if not entry:
        return 0.0
    return abs(entry - round(entry / 50.0) * 50.0)


def build_feature_row_from_trade_review(
    review: dict[str, Any],
    confirmation: dict[str, Any] | None = None,
    scenario: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical training feature row built from a stored analyst trade review."""

    confirmation = confirmation or {}
    scenario = scenario or {}

    entry = _as_float(review.get("entry"))
    sl = _as_float(review.get("sl"))
    tp1 = _as_float(review.get("tp1"))
    tp2 = _as_float(review.get("tp2"))
    tp3 = _as_float(review.get("tp3"))
    risk = _as_float(review.get("risk_pips"), abs(entry - sl))
    reaction_level = _as_float(review.get("reaction_level"))
    invalidation = _as_float(review.get("invalidation_level")) or _as_float(scenario.get("invalidation_level"))

    direction = str(
        review.get("direction")
        or confirmation.get("direction")
        or scenario.get("direction")
        or UNKNOWN_TOKEN
    ).upper()
    h4_bias = str(review.get("h4_bias") or scenario.get("h4_bias") or "neutral").lower()
    h1_bias = str(review.get("h1_bias") or scenario.get("h1_bias") or UNKNOWN_TOKEN).lower()
    dominant_bias = str(scenario.get("dominant_bias") or review.get("dominant_bias") or h4_bias or "neutral").lower()
    aligned = (direction == "BUY" and dominant_bias == "bullish") or (
        direction == "SELL" and dominant_bias == "bearish"
    )

    confirmation_score_pct = _as_float(confirmation.get("confirmation_score"))

    row: dict[str, Any] = {
        "created_at": review.get("created_at") or confirmation.get("created_at") or "",
        "symbol": review.get("symbol") or confirmation.get("symbol") or "XAUUSD",
        "direction": direction,
        "session_name": review.get("session_name") or confirmation.get("session_name") or scenario.get("session_name") or UNKNOWN_TOKEN,
        "h4_bias": h4_bias,
        "h1_bias": h1_bias,
        "dominant_bias": dominant_bias,
        "bias_strength": scenario.get("bias_strength") or ("strong" if aligned else "weak"),
        "scenario_type": review.get("scenario_type") or confirmation.get("scenario_type") or scenario.get("scenario_type") or UNKNOWN_TOKEN,
        "confirmation_type": review.get("confirmation_type") or confirmation.get("confirmation_type") or UNKNOWN_TOKEN,
        "confirmation_grade": str(review.get("confirmation_grade") or confirmation.get("confirmation_grade") or UNKNOWN_TOKEN).upper(),
        "candidate_rank_score": _as_float(review.get("candidate_rank_score"), _as_float(confirmation.get("candidate_rank_score"))),
        "learning_score": _as_float(review.get("learning_score")),
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_pips": risk,
        "tp1_reward_pips": _as_float(review.get("tp1_reward_pips"), abs(tp1 - entry)),
        "tp2_reward_pips": _as_float(review.get("tp2_reward_pips"), abs(tp2 - entry)),
        "tp3_reward_pips": _as_float(review.get("tp3_reward_pips"), abs(tp3 - entry)),
        "tp1_rr": _as_float(review.get("tp1_rr"), abs(tp1 - entry) / risk if risk else 0.0),
        "tp2_rr": _as_float(review.get("tp2_rr"), abs(tp2 - entry) / risk if risk else 0.0),
        "tp3_rr": _as_float(review.get("tp3_rr"), abs(tp3 - entry) / risk if risk else 0.0),
        "reaction_level_present": 1.0 if reaction_level else 0.0,
        "market_condition": review.get("market_condition") or scenario.get("market_condition") or UNKNOWN_TOKEN,
        "volatility_at_entry": risk,
        "atr_m15": risk,
        "atr_h1": risk * 2.0,
        "candle_body_ratio": confirmation_score_pct / 100.0 if confirmation_score_pct else NUMERIC_DEFAULTS["candle_body_ratio"],
        "candle_wick_ratio": 0.0,
        "displacement_strength": confirmation_score_pct / 100.0 if confirmation_score_pct else NUMERIC_DEFAULTS["displacement_strength"],
        "distance_to_psych_level": _distance_to_psych(entry),
        "distance_to_watch_zone": abs(entry - reaction_level) if reaction_level else 0.0,
        "distance_to_invalidation": abs(entry - invalidation) if invalidation else abs(entry - sl),
        "recent_momentum_3": 0.0,
        "recent_momentum_5": 0.0,
        "recent_momentum_10": 0.0,
        "structure_alignment_score": 1.0 if aligned else 0.0,
    }

    tp1_hit = _bool(review.get("tp1_hit"))
    outcome = _outcome_class(review)
    sl_hit = outcome == "SL_HIT"
    target_tp1_before_sl = tp1_hit and not sl_hit

    row["tp1_hit"] = int(tp1_hit)
    row["sl_hit"] = int(sl_hit)
    row["final_result"] = review.get("result") or review.get("final_result") or ""
    row["pips_result"] = _as_float(review.get("pips_result"))
    row["outcome_class"] = outcome
    row["target_tp1_before_sl"] = int(target_tp1_before_sl)
    row["protected_after_tp1"] = int(_bool(review.get("protected_after_tp1")))
    return row


def build_feature_row_from_live_setup(
    market_plan: Any,
    confirmation: Any,
    learning_score: Any,
    gate_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical live feature row built from in-memory plan/confirmation objects.

    Mirrors training-time semantics field-by-field so the live distribution
    matches what the model saw during training. Critically:
      * h4_bias = dominant bias from market plan (training stored ``h4_bias``
        as ``context.dominant_bias`` for analyst review rows).
      * h1_bias = ``h1_state`` from gate_context (training stored ``h1_bias``
        as ``context.h1_state``), NOT ``market_plan.market_structure_state``.
    """

    gate_context = gate_context or {}
    entry = _as_float(getattr(confirmation, "suggested_entry", 0.0))
    sl = _as_float(getattr(confirmation, "suggested_sl", 0.0))
    tps = getattr(confirmation, "suggested_tps", {}) or {}
    tp1 = _as_float(tps.get("tp1"))
    tp2 = _as_float(tps.get("tp2"))
    tp3 = _as_float(tps.get("tp3"))
    risk = _as_float(getattr(confirmation, "risk_pips", abs(entry - sl)), abs(entry - sl))
    reaction_level = _as_float(getattr(confirmation, "reaction_level", 0.0))
    invalidation = _as_float(getattr(confirmation, "invalidation_level", 0.0))

    direction = str(getattr(confirmation, "direction", UNKNOWN_TOKEN) or UNKNOWN_TOKEN).upper()
    dominant_bias = str(getattr(market_plan, "dominant_bias", "neutral") or "neutral").lower()
    h4_bias = dominant_bias
    h1_bias = str(
        gate_context.get("h1_state")
        or gate_context.get("h1_bias")
        or getattr(market_plan, "market_structure_state", UNKNOWN_TOKEN)
        or UNKNOWN_TOKEN
    ).lower()
    bias_strength_label = str(getattr(market_plan, "bias_strength", "weak") or "weak").lower()
    aligned = (direction == "BUY" and dominant_bias == "bullish") or (
        direction == "SELL" and dominant_bias == "bearish"
    )
    score_pct = _as_float(getattr(confirmation, "score", 0.0))

    rank_score = _as_float(gate_context.get("candidate_rank_score"))
    learning_score_value = _as_float(getattr(learning_score, "final_score", 0.0))

    return {
        "symbol": str(gate_context.get("symbol", "XAUUSD")),
        "direction": direction,
        "session_name": str(gate_context.get("session_name", UNKNOWN_TOKEN)),
        "h4_bias": h4_bias,
        "h1_bias": h1_bias,
        "dominant_bias": dominant_bias,
        "bias_strength": bias_strength_label,
        "scenario_type": str(getattr(confirmation, "scenario", "primary") or "primary"),
        "confirmation_type": str(getattr(confirmation, "confirmation_type", UNKNOWN_TOKEN) or UNKNOWN_TOKEN),
        "confirmation_grade": str(
            getattr(confirmation, "confirmation_grade", getattr(confirmation, "grade", UNKNOWN_TOKEN)) or UNKNOWN_TOKEN
        ).upper(),
        "candidate_rank_score": rank_score,
        "learning_score": learning_score_value,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_pips": risk,
        "tp1_reward_pips": _as_float(getattr(confirmation, "tp1_reward_pips", abs(tp1 - entry)), abs(tp1 - entry)),
        "tp2_reward_pips": _as_float(getattr(confirmation, "tp2_reward_pips", abs(tp2 - entry)), abs(tp2 - entry)),
        "tp3_reward_pips": _as_float(getattr(confirmation, "tp3_reward_pips", abs(tp3 - entry)), abs(tp3 - entry)),
        "tp1_rr": _as_float(
            getattr(confirmation, "tp1_rr", abs(tp1 - entry) / risk if risk else 0.0),
            abs(tp1 - entry) / risk if risk else 0.0,
        ),
        "tp2_rr": _as_float(
            getattr(confirmation, "tp2_rr", abs(tp2 - entry) / risk if risk else 0.0),
            abs(tp2 - entry) / risk if risk else 0.0,
        ),
        "tp3_rr": _as_float(
            getattr(confirmation, "tp3_rr", abs(tp3 - entry) / risk if risk else 0.0),
            abs(tp3 - entry) / risk if risk else 0.0,
        ),
        "reaction_level_present": 1.0 if reaction_level else 0.0,
        "market_condition": str(gate_context.get("market_condition", UNKNOWN_TOKEN) or UNKNOWN_TOKEN),
        "volatility_at_entry": risk,
        "atr_m15": _as_float(gate_context.get("atr_m15"), risk),
        "atr_h1": _as_float(gate_context.get("atr_h1"), risk * 2.0),
        "candle_body_ratio": score_pct / 100.0 if score_pct else NUMERIC_DEFAULTS["candle_body_ratio"],
        "candle_wick_ratio": 0.0,
        "displacement_strength": score_pct / 100.0 if score_pct else NUMERIC_DEFAULTS["displacement_strength"],
        "distance_to_psych_level": _distance_to_psych(entry),
        "distance_to_watch_zone": abs(entry - reaction_level) if reaction_level else 0.0,
        "distance_to_invalidation": abs(entry - invalidation) if invalidation else abs(entry - sl),
        "recent_momentum_3": _as_float(gate_context.get("recent_momentum_3")),
        "recent_momentum_5": _as_float(gate_context.get("recent_momentum_5")),
        "recent_momentum_10": _as_float(gate_context.get("recent_momentum_10")),
        "structure_alignment_score": 1.0 if aligned else 0.0,
    }


def build_feature_schema(df: pd.DataFrame) -> dict[str, Any]:
    validate_no_leakage_features([*CATEGORICAL_FEATURES, *NUMERIC_FEATURES])
    schema: dict[str, Any] = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "categorical_features": list(CATEGORICAL_FEATURES),
        "numeric_features": list(NUMERIC_FEATURES),
        "critical_features": sorted(CRITICAL_FEATURES),
        "categories": {},
        "numeric_stats": {},
        "feature_columns": [],
        "missing_defaults": {
            "categorical": UNKNOWN_TOKEN,
            "numeric": dict(NUMERIC_DEFAULTS),
        },
    }

    for col in CATEGORICAL_FEATURES:
        series = df.get(col, pd.Series(dtype=object))
        values = sorted({_clean_category(v) for v in series.fillna(UNKNOWN_TOKEN).tolist()})
        if UNKNOWN_TOKEN not in values:
            values.insert(0, UNKNOWN_TOKEN)
        schema["categories"][col] = values
        schema["feature_columns"].extend([f"{col}__{value}" for value in values])

    for col in NUMERIC_FEATURES:
        series = df.get(col, pd.Series(dtype=float))
        if col == "bias_strength":
            numeric_series = series.map(_normalise_bias_strength)
        else:
            numeric_series = series.map(_as_float)
        if len(numeric_series):
            mean = float(numeric_series.mean())
            std = float(numeric_series.std(ddof=0))
            min_val = float(numeric_series.min())
            max_val = float(numeric_series.max())
        else:
            mean, std, min_val, max_val = 0.0, 1.0, 0.0, 1.0
        if not math.isfinite(std) or std < 1e-9:
            std = 1.0
        if not math.isfinite(mean):
            mean = 0.0
        if not math.isfinite(min_val):
            min_val = 0.0
        if not math.isfinite(max_val):
            max_val = 1.0
        schema["numeric_stats"][col] = {
            "mean": mean,
            "std": std,
            "min": min_val,
            "max": max_val,
            "missing_default": NUMERIC_DEFAULTS.get(col, 0.0),
        }
        schema["feature_columns"].append(col)
    return schema


def validate_no_leakage_features(feature_columns: Iterable[str]) -> None:
    leaked: list[str] = []
    for feature in feature_columns:
        base = str(feature).split("__", 1)[0]
        if base in LEAKAGE_FEATURES:
            leaked.append(str(feature))
    if leaked:
        raise ValueError(f"LEAKAGE FEATURE DETECTED: {', '.join(sorted(set(leaked)))}")


def validate_feature_schema(schema: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems with the schema (empty == OK)."""
    issues: list[str] = []
    if not isinstance(schema, dict):
        return ["schema is not a dict"]
    try:
        validate_no_leakage_features(schema.get("feature_columns", []))
        validate_no_leakage_features(
            [*schema.get("categorical_features", []), *schema.get("numeric_features", [])]
        )
    except ValueError as exc:
        issues.append(str(exc))
    if schema.get("schema_version") != FEATURE_SCHEMA_VERSION:
        issues.append(
            f"schema_version mismatch (file={schema.get('schema_version')!r}, code={FEATURE_SCHEMA_VERSION!r})"
        )
    for col in CATEGORICAL_FEATURES:
        if col not in schema.get("categories", {}):
            issues.append(f"missing categorical column: {col}")
        elif UNKNOWN_TOKEN not in schema["categories"][col]:
            issues.append(f"category map for {col} missing '{UNKNOWN_TOKEN}' fallback")
    for col in NUMERIC_FEATURES:
        if col not in schema.get("numeric_stats", {}):
            issues.append(f"missing numeric stats for column: {col}")
    return issues


def save_feature_schema(
    schema: dict[str, Any],
    path: Path = FEATURE_SCHEMA_PATH,
    *,
    normalization_stats_path: Path = NORMALIZATION_STATS_PATH,
    category_maps_path: Path = CATEGORY_MAPS_PATH,
) -> None:
    ensure_ml_dirs()
    validate_no_leakage_features(schema.get("feature_columns", []))
    validate_no_leakage_features([*schema.get("categorical_features", []), *schema.get("numeric_features", [])])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    normalization_stats_path.parent.mkdir(parents=True, exist_ok=True)
    normalization_stats_path.write_text(
        json.dumps(
            {
                "schema_version": schema.get("schema_version"),
                "numeric_stats": schema.get("numeric_stats", {}),
                "missing_defaults": schema.get("missing_defaults", {}).get("numeric", {}),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    category_maps_path.parent.mkdir(parents=True, exist_ok=True)
    category_maps_path.write_text(
        json.dumps(
            {
                "schema_version": schema.get("schema_version"),
                "categories": schema.get("categories", {}),
                "unknown_token": UNKNOWN_TOKEN,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def load_feature_schema(path: Path = FEATURE_SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_value(value: float, stats: dict[str, Any], *, clamp: bool = True) -> float:
    mean = float(stats.get("mean", 0.0))
    std = float(stats.get("std", 1.0)) or 1.0
    z = (value - mean) / max(std, 1e-9)
    if clamp:
        z = max(-CLAMP_Z_LIMIT, min(CLAMP_Z_LIMIT, z))
    return z


def normalize_features(values: dict[str, float], schema: dict[str, Any], *, clamp: bool = True) -> dict[str, float]:
    out: dict[str, float] = {}
    for col, raw in values.items():
        stats = schema.get("numeric_stats", {}).get(col)
        if stats is None:
            out[col] = raw
        else:
            out[col] = _normalize_value(raw, stats, clamp=clamp)
    return out


def encode_features(
    df: pd.DataFrame,
    schema: dict[str, Any] | None = None,
    *,
    fit: bool = False,
    clamp: bool = True,
) -> FeatureBundle:
    if schema is None:
        if fit:
            schema = build_feature_schema(df)
        else:
            schema = load_feature_schema()
    validate_no_leakage_features(schema.get("feature_columns", []))
    validate_no_leakage_features([*schema.get("categorical_features", []), *schema.get("numeric_features", [])])
    rows: list[list[float]] = []
    for _, row in df.iterrows():
        encoded: list[float] = []
        for col in schema["categorical_features"]:
            actual = _clean_category(row.get(col, UNKNOWN_TOKEN))
            categories = schema["categories"].get(col, [UNKNOWN_TOKEN])
            if actual not in categories:
                actual = UNKNOWN_TOKEN
            encoded.extend([1.0 if actual == category else 0.0 for category in categories])
        for col in schema["numeric_features"]:
            default = schema.get("missing_defaults", {}).get("numeric", {}).get(col, NUMERIC_DEFAULTS.get(col, 0.0))
            raw_value = row.get(col)
            if raw_value is None or (isinstance(raw_value, float) and math.isnan(raw_value)):
                raw = float(default)
            elif col == "bias_strength":
                raw = _normalise_bias_strength(raw_value)
            else:
                raw = _as_float(raw_value, float(default))
            stats = schema["numeric_stats"].get(col, {"mean": 0.0, "std": 1.0})
            encoded.append(_normalize_value(raw, stats, clamp=clamp))
        rows.append(encoded)
    return FeatureBundle(
        x=np.asarray(rows, dtype=np.float32),
        schema=schema,
        feature_names=list(schema["feature_columns"]),
    )


def evaluate_payload_quality(payload: dict[str, Any], schema: dict[str, Any]) -> PayloadDiagnostics:
    """Inspect a single live payload before encoding for OOD diagnostics."""
    diag = PayloadDiagnostics(raw_payload=dict(payload))
    expected = list(schema.get("categorical_features", [])) + list(schema.get("numeric_features", []))
    feature_row: dict[str, Any] = {}
    missing: list[str] = []
    missing_critical: list[str] = []
    unknown: list[str] = []
    for col in expected:
        value = payload.get(col)
        if value is None or (isinstance(value, float) and math.isnan(value)) or (isinstance(value, str) and not value.strip()):
            missing.append(col)
            if col in CRITICAL_FEATURES:
                missing_critical.append(col)
            if col in schema.get("categorical_features", []):
                feature_row[col] = UNKNOWN_TOKEN
            else:
                feature_row[col] = float(
                    schema.get("missing_defaults", {}).get("numeric", {}).get(col, NUMERIC_DEFAULTS.get(col, 0.0))
                )
            continue
        if col in schema.get("categorical_features", []):
            cleaned = _clean_category(value)
            categories = schema.get("categories", {}).get(col, [UNKNOWN_TOKEN])
            if cleaned not in categories:
                unknown.append(f"{col}={cleaned}")
                cleaned = UNKNOWN_TOKEN
            feature_row[col] = cleaned
        else:
            if col == "bias_strength":
                feature_row[col] = _normalise_bias_strength(value)
            else:
                feature_row[col] = _as_float(value, NUMERIC_DEFAULTS.get(col, 0.0))
    diag.missing_features = missing
    diag.missing_critical_features = missing_critical
    diag.unknown_categories = unknown
    diag.feature_row = feature_row
    diag.completeness = 1.0 - (len(missing) / max(len(expected), 1))
    diag.schema_match = (schema.get("schema_version") == FEATURE_SCHEMA_VERSION)
    return diag


def encode_single_payload(
    payload: dict[str, Any],
    schema: dict[str, Any],
    *,
    return_diagnostics: bool = False,
    clamp: bool = True,
) -> np.ndarray | tuple[np.ndarray, PayloadDiagnostics]:
    diag = evaluate_payload_quality(payload, schema)
    df = pd.DataFrame([diag.feature_row])
    bundle = encode_features(df, schema=schema, clamp=clamp)
    if bundle.x.size:
        numeric_slice = bundle.x[0, -len(schema.get("numeric_features", [])) :]
        if len(numeric_slice):
            diag.z_min = float(numeric_slice.min())
            diag.z_max = float(numeric_slice.max())
    if return_diagnostics:
        return bundle.x, diag
    return bundle.x


def critical_completeness(diag: PayloadDiagnostics) -> float:
    total = len(CRITICAL_FEATURES)
    if total == 0:
        return 1.0
    return 1.0 - (len(diag.missing_critical_features) / total)


def feature_iterables() -> Iterable[str]:
    """Helper for tests/debug — full list of feature names in canonical order."""
    yield from CATEGORICAL_FEATURES
    yield from NUMERIC_FEATURES
