from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from ai_models.features import (
    CATEGORICAL_FEATURES,
    LABEL_COLUMNS,
    LEAKAGE_FEATURES,
    NUMERIC_FEATURES,
    PRE_ENTRY_FEATURES,
    build_feature_row_from_trade_review,
    validate_no_leakage_features,
)
from ai_models.model_registry import DEFAULT_DATASET_PATH, ensure_ml_dirs
from config.settings import SUPABASE_AUTH_KEY, SUPABASE_URL
from db.database import Database


DATASET_COLUMNS = [
    "created_at",
    *CATEGORICAL_FEATURES,
    *NUMERIC_FEATURES,
    *LABEL_COLUMNS,
]
PRICE_BUCKETS = [
    ("3900-4100", 3900.0, 4100.0),
    ("4100-4300", 4100.0, 4300.0),
    ("4300-4500", 4300.0, 4500.0),
    ("4500-4700", 4500.0, 4700.0),
    ("4700-4900", 4700.0, 4900.0),
    ("4900+", 4900.0, float("inf")),
]


def _validate_supabase_config() -> None:
    if not SUPABASE_URL or not SUPABASE_AUTH_KEY:
        print("Missing Supabase configuration.")
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY.")
        raise SystemExit(2)


def _price_bucket(value: Any) -> str:
    try:
        price = float(value)
    except Exception:
        return "unknown"
    for name, lo, hi in PRICE_BUCKETS:
        if lo <= price < hi:
            return name
    return "below_3900"


def _print_regime_coverage(df: pd.DataFrame) -> None:
    if df.empty:
        print("REGIME COVERAGE:")
        print({})
        return
    entry = pd.to_numeric(df.get("entry"), errors="coerce")
    created = pd.to_datetime(df.get("created_at"), errors="coerce", utc=True)
    _entry_buckets = df.get("entry", pd.Series(dtype=float)).apply(_price_bucket)
    bucket_counts = {name: int((_entry_buckets == name).sum()) for name, *_ in PRICE_BUCKETS}
    bucket_counts["below_3900"] = int((_entry_buckets == "below_3900").sum())
    bucket_counts["unknown"] = int((_entry_buckets == "unknown").sum())
    print("REGIME COVERAGE:")
    print(
        {
            "price_min": round(float(entry.min()), 2) if entry.notna().any() else None,
            "price_max": round(float(entry.max()), 2) if entry.notna().any() else None,
            "price_buckets": bucket_counts,
            "rows_per_month": created.dt.strftime("%Y-%m").value_counts().sort_index().to_dict(),
            "rows_per_confirmation_type": df.get("confirmation_type", pd.Series(dtype=str)).astype(str).value_counts().to_dict(),
            "rows_per_session": df.get("session_name", pd.Series(dtype=str)).astype(str).value_counts().to_dict(),
            "rows_per_direction": df.get("direction", pd.Series(dtype=str)).astype(str).value_counts().to_dict(),
        }
    )


def build_dataset(symbol: str = "XAUUSD", months: int = 12, db_mode: str = "supabase_rest", limit: int | None = 5000) -> pd.DataFrame:
    db_mode = (db_mode or "supabase_rest").strip().lower()
    if db_mode not in {"supabase_rest", "postgres", "auto"}:
        raise ValueError("db_mode must be one of: supabase_rest, postgres, auto")
    print(f"AI DATASET BUILDER DB MODE: {db_mode}")
    if db_mode == "supabase_rest":
        _validate_supabase_config()

    db = Database()
    db.init(mode=db_mode)
    if db_mode == "supabase_rest" or getattr(db, "_sb", None) is not None:
        print("Using Supabase REST API for AI dataset export")
    reviews = db.fetch_analyst_trade_reviews_for_ml(symbol=symbol, months=months, limit=limit)
    confirmations = db.fetch_analyst_confirmations_for_ml(symbol=symbol, months=months, limit=limit)
    scenarios = db.fetch_analyst_scenarios_for_ml(symbol=symbol, months=months, limit=limit)

    confirmation_by_key = {str(row.get("confirmation_key") or ""): row for row in confirmations if row.get("confirmation_key")}
    confirmations_by_scenario: dict[str, dict[str, Any]] = {}
    for row in confirmations:
        scenario_key = str(row.get("scenario_key") or "")
        if scenario_key and scenario_key not in confirmations_by_scenario:
            confirmations_by_scenario[scenario_key] = row
    scenario_by_key = {str(row.get("scenario_key") or ""): row for row in scenarios if row.get("scenario_key")}
    rows: list[dict[str, Any]] = []
    missing_confirmation_count = 0
    missing_scenario_count = 0
    for review in reviews:
        scenario_key = str(review.get("scenario_key") or "")
        confirmation = confirmation_by_key.get(str(review.get("setup_id") or "")) or confirmations_by_scenario.get(scenario_key)
        scenario = scenario_by_key.get(scenario_key)
        if confirmation is None:
            missing_confirmation_count += 1
        if scenario is None:
            missing_scenario_count += 1
        rows.append(build_feature_row_from_trade_review(review, confirmation, scenario))
    if missing_confirmation_count or missing_scenario_count:
        print(
            {
                "warning": "dataset_enrichment_missing_rows",
                "missing_confirmation_count": missing_confirmation_count,
                "missing_scenario_count": missing_scenario_count,
            }
        )
    df = pd.DataFrame(rows, columns=DATASET_COLUMNS).sort_values("created_at")
    print("DATASET FETCH:")
    print(
        {
            "months": months,
            "limit": "none" if limit is None else limit,
            "rows_fetched": len(df),
            "oldest_created_at": str(df["created_at"].iloc[0]) if len(df) else None,
            "newest_created_at": str(df["created_at"].iloc[-1]) if len(df) else None,
        }
    )
    _print_regime_coverage(df)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Spencer setup outcome ML dataset.")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--db-mode", default="supabase_rest", choices=["supabase_rest", "postgres", "auto"])
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--no-limit", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--write-empty-header", action="store_true")
    args = parser.parse_args()
    ensure_ml_dirs()
    validate_no_leakage_features(PRE_ENTRY_FEATURES)
    removed = sorted(LEAKAGE_FEATURES.intersection(set(LABEL_COLUMNS) | {"result", "tp2_hit", "tp3_hit", "closed_at", "review_notes"}))
    print(f"ML INPUT FEATURES: {len(PRE_ENTRY_FEATURES)}")
    print(f"ML LABEL COLUMNS: {len(LABEL_COLUMNS)}")
    print(f"LEAKAGE FEATURES REMOVED: {', '.join(removed)}")
    fetch_limit = None if args.no_limit else int(args.limit)
    df = build_dataset(symbol=args.symbol, months=args.months, db_mode=args.db_mode, limit=fetch_limit)
    if df.empty:
        print(f"No analyst trade review rows found for symbol={args.symbol} months={args.months}.")
        print("Run analyst replay first:")
        print(f"python -m historical_replay.run_analyst_replay --symbol {args.symbol} --months {args.months} --export-learning")
        if not args.write_empty_header:
            return
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)
    print({"output": str(output), "rows": len(df), "columns": list(df.columns)})


if __name__ == "__main__":
    main()
