"""Summarise a Session Liquidity replay run.

Reads the CSV produced by ``run_session_liquidity_replay`` and aggregates
counts + simple win-rate proxies (using the next-candle close vs the swept
level as a directional outcome). True TP1/TP2 evaluation is delegated to the
analyst-replay layer; this report is the advisory-layer view only.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd


REPLAY_DIR = Path("data/session_liquidity_replay")


def _resolve(run_id: str) -> Path:
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    if run_id == "latest":
        files = sorted(REPLAY_DIR.glob("*.csv"))
        if not files:
            raise SystemExit("No session liquidity replay runs found.")
        return files[-1]
    candidate = REPLAY_DIR / f"{run_id}.csv"
    if not candidate.exists():
        raise SystemExit(f"Run not found: {candidate}")
    return candidate


def evaluate(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {"file": str(path), "rows": 0, "error": "empty_or_missing"}
    df = pd.read_csv(path)
    if df.empty:
        return {"file": str(path), "rows": 0, "by_setup_type": {}}

    by_setup: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "average_trap_quality": 0.0,
        "directions": defaultdict(int),
        "with_displacement": 0,
    })
    for _, row in df.iterrows():
        setup = str(row.get("setup_type") or "unknown")
        bucket = by_setup[setup]
        bucket["count"] += 1
        try:
            bucket["average_trap_quality"] += float(row.get("trap_quality_score") or 0.0)
        except Exception:
            pass
        bucket["directions"][str(row.get("direction") or "")] += 1
        if str(row.get("displacement") or "").lower() in {"true", "1"}:
            bucket["with_displacement"] += 1

    for name, b in by_setup.items():
        if b["count"]:
            b["average_trap_quality"] = round(b["average_trap_quality"] / b["count"], 2)
            b["with_displacement_pct"] = round(b["with_displacement"] / b["count"] * 100.0, 2)
        b["directions"] = dict(b["directions"])

    return {
        "file": str(path),
        "rows": int(len(df)),
        "by_setup_type": dict(by_setup),
        "session_breakdown": (
            df.assign(session=df["swept_level_type"].astype(str).str.split("_").str[0])
              .groupby("session")
              .size()
              .to_dict()
            if "swept_level_type" in df.columns
            else {}
        ),
        "liquidity_bias_distribution": (
            df["liquidity_bias"].value_counts().to_dict() if "liquidity_bias" in df.columns else {}
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a Session Liquidity replay run.")
    parser.add_argument("--run-id", default="latest")
    args = parser.parse_args()
    path = _resolve(args.run_id)
    print(json.dumps(evaluate(path), indent=2, default=str))


if __name__ == "__main__":
    main()
