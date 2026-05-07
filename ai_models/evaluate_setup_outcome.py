from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from ai_models.features import encode_features, load_feature_schema, validate_feature_schema
from ai_models.setup_outcome_model import SpencerSetupOutcomeModel
from ai_models.train_setup_outcome import _binary_metrics, _confidence_buckets, apply_approved_only_filter, chronological_split


PRICE_BUCKETS = [
    ("3900-4100", 3900.0, 4100.0),
    ("4100-4300", 4100.0, 4300.0),
    ("4300-4500", 4300.0, 4500.0),
    ("4500-4700", 4500.0, 4700.0),
    ("4700-4900", 4700.0, 4900.0),
    ("4900+", 4900.0, float("inf")),
]


def _best_threshold(y_true: np.ndarray, probs: np.ndarray) -> dict:
    best = {"threshold": 0.5, "f1": -1.0}
    for threshold in np.arange(0.35, 0.86, 0.01):
        metrics = _binary_metrics(y_true, probs, float(threshold))
        if metrics["f1"] > best["f1"]:
            best = {"threshold": round(float(threshold), 2), "f1": metrics["f1"], "metrics": metrics}
    return best


def _price_bucket(value: object) -> str:
    try:
        price = float(value)
    except Exception:
        return "unknown"
    for name, lo, hi in PRICE_BUCKETS:
        if lo <= price < hi:
            return name
    return "below_3900"


def _period(df: pd.DataFrame) -> dict:
    if df.empty or "created_at" not in df.columns:
        return {"start": None, "end": None}
    return {"start": str(df["created_at"].iloc[0]), "end": str(df["created_at"].iloc[-1])}


def _slice_report(df: pd.DataFrame, y: np.ndarray, probs: np.ndarray, pips: np.ndarray, *, key_values: pd.Series) -> dict:
    report: dict[str, dict] = {}
    for key in sorted(str(v) for v in key_values.dropna().unique()):
        mask = key_values.astype(str).to_numpy() == key
        count = int(mask.sum())
        report[key] = {
            "count": count,
            "tp1_rate": round(float(y[mask].mean() * 100.0), 2) if count else 0.0,
            "net_pips": round(float(pips[mask].sum()), 2) if count else 0.0,
            "avg_probability": round(float(probs[mask].mean()), 4) if count else 0.0,
            "probability_buckets": _confidence_buckets(y[mask], probs[mask], pips[mask]) if count else {},
        }
    return report


def evaluate(
    model_path: Path,
    dataset_path: Path,
    *,
    approved_only: bool = False,
    exclude_neutral_outcomes: bool = False,
    walk_forward: bool = False,
) -> dict:
    payload = torch.load(model_path, map_location="cpu")
    schema = payload.get("schema") or load_feature_schema()
    schema_issues = validate_feature_schema(schema)
    if schema_issues:
        print({"warning": "schema_issues", "details": schema_issues})
    df = pd.read_csv(dataset_path).sort_values("created_at").reset_index(drop=True)
    filter_summary = None
    if approved_only:
        df, filter_summary = apply_approved_only_filter(
            df,
            exclude_neutral_outcomes=exclude_neutral_outcomes,
            log=True,
        )
        df = df.sort_values("created_at").reset_index(drop=True)
    bundle = encode_features(df, schema=schema)
    model = SpencerSetupOutcomeModel(input_dim=int(payload["input_dim"]))
    model.load_state_dict(payload["state_dict"])
    model.eval()
    with torch.no_grad():
        out = model(torch.tensor(bundle.x, dtype=torch.float32))
        tp1_probs = torch.sigmoid(out["tp1_logit"]).numpy()
        sl_probs = torch.sigmoid(out["sl_logit"]).numpy()
        pips_pred = out["expected_pips"].numpy()
    target_col = "target_good_trade" if approved_only and "target_good_trade" in df.columns else "target_tp1_before_sl"
    y_tp1 = df[target_col].astype(float).to_numpy()
    y_sl = df["sl_hit"].astype(float).to_numpy()
    pips = df["pips_result"].astype(float).to_numpy()
    high = tp1_probs >= 0.70
    low = ~high
    sl_saturated_pct = round(float((sl_probs >= 0.95).mean() * 100.0), 2) if len(sl_probs) else 0.0
    result = {
        "model_version": payload.get("model_version"),
        "feature_schema_version": payload.get("feature_schema_version"),
        "rows": len(df),
        "approved_only": approved_only,
        "exclude_neutral_outcomes": exclude_neutral_outcomes,
        "filter_summary": filter_summary or payload.get("filter_summary", {}),
        "positive_rate": round(float(y_tp1.mean()), 4) if len(y_tp1) else 0.0,
        "feature_count": int(bundle.x.shape[1]),
        "tp1_prediction": _binary_metrics(y_tp1, tp1_probs),
        "sl_prediction": _binary_metrics(y_sl, sl_probs),
        "expected_pips_mae": round(float(np.abs(pips_pred - pips).mean()), 4) if len(df) else 0.0,
        "sl_probability_distribution": {
            "mean": round(float(sl_probs.mean()), 4) if len(sl_probs) else 0.0,
            "std": round(float(sl_probs.std()), 4) if len(sl_probs) else 0.0,
            "min": round(float(sl_probs.min()), 4) if len(sl_probs) else 0.0,
            "max": round(float(sl_probs.max()), 4) if len(sl_probs) else 0.0,
            "saturated_high_pct": sl_saturated_pct,
        },
        "tp1_probability_distribution": {
            "mean": round(float(tp1_probs.mean()), 4) if len(tp1_probs) else 0.0,
            "std": round(float(tp1_probs.std()), 4) if len(tp1_probs) else 0.0,
            "min": round(float(tp1_probs.min()), 4) if len(tp1_probs) else 0.0,
            "max": round(float(tp1_probs.max()), 4) if len(tp1_probs) else 0.0,
        },
        "best_threshold_for_live_use": _best_threshold(y_tp1, tp1_probs),
        "win_rate_by_predicted_probability_bucket": _confidence_buckets(y_tp1, tp1_probs, pips),
        "net_pips_by_predicted_probability_bucket": {k: v["net_pips"] for k, v in _confidence_buckets(y_tp1, tp1_probs, pips).items()},
        "tp1_probability_70_plus_comparison": {
            "count_70_plus": int(high.sum()),
            "tp1_rate_70_plus": round(float(y_tp1[high].mean() * 100.0), 2) if high.any() else 0.0,
            "net_pips_70_plus": round(float(pips[high].sum()), 2) if high.any() else 0.0,
            "count_below_70": int(low.sum()),
            "tp1_rate_below_70": round(float(y_tp1[low].mean() * 100.0), 2) if low.any() else 0.0,
            "net_pips_below_70": round(float(pips[low].sum()), 2) if low.any() else 0.0,
        },
    }
    if walk_forward:
        train_df, val_df, test_df = chronological_split(df)
        split_reports: dict[str, dict] = {}
        for split_name, split_df in (("train", train_df), ("validation", val_df), ("test", test_df)):
            if split_df.empty:
                split_reports[split_name] = {"rows": 0, "period": _period(split_df)}
                continue
            split_bundle = encode_features(split_df, schema=schema)
            with torch.no_grad():
                split_out = model(torch.tensor(split_bundle.x, dtype=torch.float32))
                split_probs = torch.sigmoid(split_out["tp1_logit"]).numpy()
            split_y = split_df[target_col].astype(float).to_numpy()
            split_pips = split_df["pips_result"].astype(float).to_numpy()
            price_regimes = split_df.get("entry", pd.Series(dtype=float)).apply(_price_bucket)
            months = pd.to_datetime(split_df.get("created_at"), errors="coerce", utc=True).dt.strftime("%Y-%m")
            split_reports[split_name] = {
                "rows": len(split_df),
                "period": _period(split_df),
                "positive_rate": round(float(split_y.mean()), 4) if len(split_y) else 0.0,
                "metrics": _binary_metrics(split_y, split_probs),
                "probability_buckets": _confidence_buckets(split_y, split_probs, split_pips),
                "performance_by_price_regime": _slice_report(split_df, split_y, split_probs, split_pips, key_values=price_regimes),
                "performance_by_month": _slice_report(split_df, split_y, split_probs, split_pips, key_values=months),
            }
        result["walk_forward"] = {
            "split_method": "chronological_70_15_15",
            "train_period": split_reports.get("train", {}).get("period"),
            "validation_period": split_reports.get("validation", {}).get("period"),
            "test_period": split_reports.get("test", {}).get("period"),
            "splits": split_reports,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Spencer Setup Outcome Model v1.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--exclude-neutral-outcomes", action="store_true")
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args()
    print(
        evaluate(
            Path(args.model),
            Path(args.dataset),
            approved_only=args.approved_only,
            exclude_neutral_outcomes=args.exclude_neutral_outcomes,
            walk_forward=args.walk_forward,
        )
    )


if __name__ == "__main__":
    main()
