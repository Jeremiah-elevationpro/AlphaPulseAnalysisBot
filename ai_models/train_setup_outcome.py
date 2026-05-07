from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from ai_models.features import (
    LABEL_COLUMNS,
    LEAKAGE_FEATURES,
    PRE_ENTRY_FEATURES,
    build_feature_schema,
    encode_features,
    save_feature_schema,
    validate_feature_schema,
    validate_no_leakage_features,
)
from ai_models.model_registry import (
    APPROVED_6M_CATEGORY_MAPS_PATH,
    APPROVED_6M_FEATURE_SCHEMA_PATH,
    APPROVED_6M_MODEL_NAME,
    APPROVED_6M_NORMALIZATION_STATS_PATH,
    APPROVED_CATEGORY_MAPS_PATH,
    APPROVED_FEATURE_SCHEMA_PATH,
    APPROVED_MODEL_NAME,
    APPROVED_NORMALIZATION_STATS_PATH,
    CATEGORY_MAPS_PATH,
    DEFAULT_MODEL_PATH,
    FEATURE_SCHEMA_PATH,
    FEATURE_SCHEMA_VERSION,
    MODEL_VERSION,
    NORMALIZATION_STATS_PATH,
    ensure_ml_dirs,
    model_path_for_name,
)
from ai_models.setup_outcome_model import SpencerSetupOutcomeModel


def chronological_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("created_at").reset_index(drop=True)
    n = len(ordered)
    train_end = max(1, int(n * 0.70))
    val_end = max(train_end + 1, int(n * 0.85)) if n >= 3 else train_end
    return ordered.iloc[:train_end], ordered.iloc[train_end:val_end], ordered.iloc[val_end:]


APPROVED_CONFIRMATION_TYPES = {
    "break_retest_close_confirmation",
    "sweep_reclaim_confirmation",
    "engulfing_level_confirmation",
    "failed_retest_confirmation",
}
GOOD_RESULTS = {"WIN", "STRONG_WIN", "BREAKEVEN_WIN", "EXPIRED_AFTER_TP1"}
BAD_RESULTS = {"LOSS", "SL_HIT", "STOP_LOSS_HIT"}
NEUTRAL_RESULTS = {"EXPIRED_BEFORE_TP", "EXPIRED", "NO_TRIGGER", "MISSED_MOVE", "EXPIRED_INVALIDATED"}


def _targets(df: pd.DataFrame, *, approved_target: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    target_col = "target_good_trade" if approved_target and "target_good_trade" in df.columns else "target_tp1_before_sl"
    tp1 = df[target_col].astype(float).to_numpy(dtype=np.float32)
    if approved_target:
        sl = (1.0 - tp1).astype(np.float32)
    else:
        sl = df["sl_hit"].astype(float).to_numpy(dtype=np.float32)
    pips = df["pips_result"].astype(float).to_numpy(dtype=np.float32)
    return tp1, sl, pips


def _is_good_trade(row: pd.Series) -> bool:
    result = str(row.get("final_result") or row.get("result") or "").upper()
    return bool(row.get("tp1_hit")) or result in GOOD_RESULTS


def _is_bad_trade(row: pd.Series) -> bool:
    result = str(row.get("final_result") or row.get("result") or "").upper()
    return bool(row.get("sl_hit")) or result in BAD_RESULTS


def _is_neutral_trade(row: pd.Series) -> bool:
    result = str(row.get("final_result") or row.get("result") or "").upper()
    if result in NEUTRAL_RESULTS:
        return True
    return not _is_good_trade(row) and not _is_bad_trade(row)


def add_approved_target(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["target_good_trade"] = out.apply(lambda row: 1.0 if _is_good_trade(row) else 0.0, axis=1)
    return out


def apply_approved_only_filter(
    df: pd.DataFrame,
    *,
    exclude_neutral_outcomes: bool = False,
    log: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    before = len(df)
    mask = pd.Series(True, index=df.index)
    if "candidate_rank_score" in df.columns:
        rank_values = pd.to_numeric(df["candidate_rank_score"], errors="coerce").fillna(0.0)
        if float(rank_values.max()) > 0.0:
            mask &= rank_values >= 70.0
    if "confirmation_grade" in df.columns:
        mask &= df["confirmation_grade"].astype(str).str.upper().isin({"A", "A+"})
    if "confirmation_type" in df.columns:
        mask &= df["confirmation_type"].astype(str).isin(APPROVED_CONFIRMATION_TYPES)
    if "sl_source" in df.columns:
        mask &= df["sl_source"].astype(str).eq("structure_sl_engine")
    if "tp_source" in df.columns:
        mask &= df["tp_source"].astype(str).eq("structure_tp_engine")
    if "risk_pips" in df.columns:
        mask &= pd.to_numeric(df["risk_pips"], errors="coerce").fillna(0.0) > 0.0
    if "tp1_rr" in df.columns:
        mask &= pd.to_numeric(df["tp1_rr"], errors="coerce").fillna(0.0) > 0.0
    if "setup_quality_label" in df.columns:
        mask &= ~df["setup_quality_label"].astype(str).str.upper().eq("WATCHLIST ONLY")

    filtered = df.loc[mask].copy()
    filtered = add_approved_target(filtered)
    neutral_mask = filtered.apply(_is_neutral_trade, axis=1) if len(filtered) else pd.Series(dtype=bool)
    neutral_count = int(neutral_mask.sum()) if len(filtered) else 0
    if exclude_neutral_outcomes and len(filtered):
        filtered = filtered.loc[~neutral_mask].copy()

    positives = int(filtered["target_good_trade"].sum()) if len(filtered) else 0
    negatives = int(len(filtered) - positives)
    losses = int(filtered.apply(_is_bad_trade, axis=1).sum()) if len(filtered) else 0
    summary = {
        "before_rows": before,
        "after_rows": len(filtered),
        "positive_count": positives,
        "negative_count": negatives,
        "neutral_excluded_count": neutral_count if exclude_neutral_outcomes else 0,
        "positive_rate": round(float(positives / max(len(filtered), 1)), 4),
        "loss_rate": round(float(losses / max(len(filtered), 1)), 4),
    }
    if log:
        print("APPROVED-ONLY DATASET FILTER:")
        print(
            {
                "before_rows": summary["before_rows"],
                "after_rows": summary["after_rows"],
                "positive_rate": summary["positive_rate"],
                "loss_rate": summary["loss_rate"],
            }
        )
        if exclude_neutral_outcomes:
            print("NEUTRAL OUTCOMES EXCLUDED:")
            print({"count": neutral_count})
    return filtered, summary


def _loader(x: np.ndarray, tp1: np.ndarray, sl: np.ndarray, pips: np.ndarray, batch_size: int, shuffle: bool) -> DataLoader:
    ds = TensorDataset(
        torch.tensor(x, dtype=torch.float32),
        torch.tensor(tp1, dtype=torch.float32),
        torch.tensor(sl, dtype=torch.float32),
        torch.tensor(pips, dtype=torch.float32),
    )
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle)


def _positive_weight(y: np.ndarray) -> torch.Tensor:
    positives = float(y.sum())
    negatives = float(len(y) - positives)
    if positives <= 0.0 or negatives <= 0.0:
        return torch.tensor(1.0, dtype=torch.float32)
    return torch.tensor(negatives / positives, dtype=torch.float32)


def _binary_metrics(y_true: np.ndarray, probs: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
    pred = (probs >= threshold).astype(int)
    y = y_true.astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    return {
        "accuracy": round((tp + tn) / max(len(y), 1), 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "confusion_matrix": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
    }


def _roc_auc(y_true: np.ndarray, probs: np.ndarray) -> float | None:
    y = y_true.astype(int)
    pos = int(y.sum())
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return None
    order = np.argsort(probs)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(probs) + 1)
    auc = (ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)
    return round(float(auc), 4)


def _confidence_buckets(y_true: np.ndarray, probs: np.ndarray, pips: np.ndarray) -> dict[str, Any]:
    buckets = {"80%+": (0.80, 1.01), "70-79%": (0.70, 0.80), "60-69%": (0.60, 0.70), "below 60%": (0.0, 0.60)}
    out: dict[str, Any] = {}
    for name, (lo, hi) in buckets.items():
        mask = (probs >= lo) & (probs < hi)
        count = int(mask.sum())
        out[name] = {
            "count": count,
            "tp1_rate": round(float(y_true[mask].mean() * 100.0), 2) if count else 0.0,
            "net_pips": round(float(pips[mask].sum()), 2) if count else 0.0,
            "avg_probability": round(float(probs[mask].mean()), 4) if count else 0.0,
        }
    return out


@torch.no_grad()
def evaluate_model(model: SpencerSetupOutcomeModel, x: np.ndarray, tp1: np.ndarray, sl: np.ndarray, pips: np.ndarray) -> dict[str, Any]:
    model.eval()
    outputs = model(torch.tensor(x, dtype=torch.float32))
    tp1_probs = torch.sigmoid(outputs["tp1_logit"]).cpu().numpy()
    sl_probs = torch.sigmoid(outputs["sl_logit"]).cpu().numpy()
    pred_pips = outputs["expected_pips"].cpu().numpy()
    metrics = _binary_metrics(tp1, tp1_probs)
    metrics["roc_auc"] = _roc_auc(tp1, tp1_probs)
    metrics["sl_accuracy"] = _binary_metrics(sl, sl_probs)["accuracy"]
    metrics["sl_probability_stats"] = {
        "mean": round(float(sl_probs.mean()), 4) if len(sl_probs) else 0.0,
        "std": round(float(sl_probs.std()), 4) if len(sl_probs) else 0.0,
        "saturated_high_pct": round(float((sl_probs >= 0.95).mean() * 100.0), 2) if len(sl_probs) else 0.0,
    }
    metrics["tp1_probability_stats"] = {
        "mean": round(float(tp1_probs.mean()), 4) if len(tp1_probs) else 0.0,
        "std": round(float(tp1_probs.std()), 4) if len(tp1_probs) else 0.0,
        "above_70_pct": round(float((tp1_probs >= 0.70).mean() * 100.0), 2) if len(tp1_probs) else 0.0,
    }
    metrics["expected_pips_mae"] = round(float(np.abs(pred_pips - pips).mean()), 4) if len(pips) else 0.0
    metrics["tp1_probability_calibration"] = round(float(np.abs(tp1_probs - tp1).mean()), 4) if len(tp1) else 0.0
    metrics["confidence_buckets"] = _confidence_buckets(tp1, tp1_probs, pips)
    return metrics


def _artifact_paths(model_name: str, output: Path | None = None) -> tuple[Path, Path, Path, Path]:
    model_path = output or model_path_for_name(model_name)
    if model_name == APPROVED_6M_MODEL_NAME or "approved_outcome_model_6m" in model_name:
        return model_path, APPROVED_6M_FEATURE_SCHEMA_PATH, APPROVED_6M_NORMALIZATION_STATS_PATH, APPROVED_6M_CATEGORY_MAPS_PATH
    if model_name == APPROVED_MODEL_NAME or "approved" in model_name:
        return model_path, APPROVED_FEATURE_SCHEMA_PATH, APPROVED_NORMALIZATION_STATS_PATH, APPROVED_CATEGORY_MAPS_PATH
    return model_path, FEATURE_SCHEMA_PATH, NORMALIZATION_STATS_PATH, CATEGORY_MAPS_PATH


def train(
    dataset: Path,
    epochs: int,
    batch_size: int,
    output: Path,
    *,
    approved_only: bool = False,
    exclude_neutral_outcomes: bool = False,
    model_name: str = MODEL_VERSION,
) -> dict[str, Any]:
    ensure_ml_dirs()
    df = pd.read_csv(dataset)
    if len(df) < 5:
        raise ValueError("Need at least 5 labeled setup rows to train Spencer Setup Outcome Model v1.")
    missing_labels = [col for col in ("target_tp1_before_sl", "sl_hit", "pips_result") if col not in df.columns]
    if missing_labels:
        raise ValueError(f"Missing label columns: {missing_labels}")
    validate_no_leakage_features(PRE_ENTRY_FEATURES)
    leakage_removed = sorted(LEAKAGE_FEATURES.intersection(set(df.columns)) - set(PRE_ENTRY_FEATURES))
    print(f"ML INPUT FEATURES: {len(PRE_ENTRY_FEATURES)}")
    print(f"ML LABEL COLUMNS: {len(LABEL_COLUMNS)}")
    print(f"LEAKAGE FEATURES REMOVED: {', '.join(leakage_removed)}")
    filter_summary: dict[str, Any] | None = None
    if approved_only:
        df, filter_summary = apply_approved_only_filter(df, exclude_neutral_outcomes=exclude_neutral_outcomes)
        if len(df) < 5:
            raise ValueError("Need at least 5 approved-style labeled setup rows after filtering.")
    train_df, val_df, test_df = chronological_split(df)
    schema = build_feature_schema(train_df)
    schema_issues = validate_feature_schema(schema)
    if schema_issues:
        print({"warning": "schema_issues", "details": schema_issues})
    model_path, schema_path, stats_path, maps_path = _artifact_paths(model_name, output)
    save_feature_schema(schema, path=schema_path, normalization_stats_path=stats_path, category_maps_path=maps_path)
    print(
        {
            "feature_schema_saved": str(schema_path),
            "normalization_stats_saved": str(stats_path),
            "category_maps_saved": str(maps_path),
            "schema_version": schema.get("schema_version"),
            "categorical_features": len(schema.get("categorical_features", [])),
            "numeric_features": len(schema.get("numeric_features", [])),
            "feature_columns": len(schema.get("feature_columns", [])),
        }
    )
    train_bundle = encode_features(train_df, schema=schema)
    val_bundle = encode_features(val_df, schema=schema)
    test_bundle = encode_features(test_df if len(test_df) else val_df, schema=schema)
    train_tp1, train_sl, train_pips = _targets(train_df, approved_target=approved_only)
    val_tp1, val_sl, val_pips = _targets(val_df, approved_target=approved_only)
    test_tp1, test_sl, test_pips = _targets(test_df if len(test_df) else val_df, approved_target=approved_only)

    print(
        {
            "train_rows": len(train_df),
            "val_rows": len(val_df),
            "test_rows": len(test_df),
            "train_tensor_shape": list(train_bundle.x.shape),
            "tp1_positive_rate": round(float(train_tp1.mean()), 4) if len(train_tp1) else 0.0,
            "sl_positive_rate": round(float(train_sl.mean()), 4) if len(train_sl) else 0.0,
        }
    )

    model = SpencerSetupOutcomeModel(input_dim=train_bundle.x.shape[1])
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    tp1_pos_weight = _positive_weight(train_tp1)
    sl_pos_weight = _positive_weight(train_sl)
    bce_tp1 = nn.BCEWithLogitsLoss(pos_weight=tp1_pos_weight)
    bce_sl = nn.BCEWithLogitsLoss(pos_weight=sl_pos_weight)
    huber = nn.HuberLoss()
    print(
        {
            "loss_balancing": "enabled",
            "tp1_pos_weight": round(float(tp1_pos_weight.item()), 4),
            "sl_pos_weight": round(float(sl_pos_weight.item()), 4),
        }
    )
    best_val = math.inf
    best_payload: dict[str, Any] | None = None
    train_loader = _loader(train_bundle.x, train_tp1, train_sl, train_pips, batch_size=batch_size, shuffle=True)

    for epoch in range(1, epochs + 1):
        model.train()
        losses: list[float] = []
        for xb, y_tp1, y_sl, y_pips in train_loader:
            opt.zero_grad()
            out = model(xb)
            loss = (
                bce_tp1(out["tp1_logit"], y_tp1)
                + bce_sl(out["sl_logit"], y_sl)
                + 0.02 * huber(out["expected_pips"], y_pips)
            )
            loss.backward()
            opt.step()
            losses.append(float(loss.item()))
        val_metrics = evaluate_model(model, val_bundle.x, val_tp1, val_sl, val_pips) if len(val_df) else {}
        val_loss = float(val_metrics.get("expected_pips_mae", 0.0)) + (1.0 - float(val_metrics.get("f1", 0.0)))
        if val_loss < best_val:
            best_val = val_loss
            best_payload = {
                "model_version": MODEL_VERSION,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "input_dim": train_bundle.x.shape[1],
                "state_dict": model.state_dict(),
                "schema": schema,
                "train_rows": len(train_df),
                "validation_rows": len(val_df),
                "test_rows": len(test_df),
            }
        if epoch == 1 or epoch == epochs or epoch % 10 == 0:
            print({"epoch": epoch, "train_loss": round(sum(losses) / max(len(losses), 1), 4), "validation": val_metrics})

    if best_payload is None:
        raise RuntimeError("Training did not produce a checkpoint.")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    best_payload["model_version"] = model_name
    best_payload["approved_only"] = approved_only
    best_payload["exclude_neutral_outcomes"] = exclude_neutral_outcomes
    best_payload["filter_summary"] = filter_summary or {}
    torch.save(best_payload, model_path)
    model.load_state_dict(best_payload["state_dict"])
    test_metrics = evaluate_model(model, test_bundle.x, test_tp1, test_sl, test_pips)
    return {
        "model": str(model_path),
        "rows": len(df),
        "approved_only": approved_only,
        "filter_summary": filter_summary or {},
        "test_metrics": test_metrics,
        "feature_count": train_bundle.x.shape[1],
        "feature_schema_path": str(schema_path),
        "normalization_stats_path": str(stats_path),
        "category_maps_path": str(maps_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Spencer Setup Outcome Model v1.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--exclude-neutral-outcomes", action="store_true")
    parser.add_argument("--model-name", default=MODEL_VERSION)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    output = Path(args.output) if args.output else model_path_for_name(args.model_name)
    print(
        train(
            Path(args.dataset),
            args.epochs,
            args.batch_size,
            output,
            approved_only=args.approved_only,
            exclude_neutral_outcomes=args.exclude_neutral_outcomes,
            model_name=args.model_name,
        )
    )


if __name__ == "__main__":
    main()
