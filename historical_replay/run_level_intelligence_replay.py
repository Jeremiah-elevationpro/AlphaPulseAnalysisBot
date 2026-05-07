from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from analysis.level_intelligence import LevelIntelligenceEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay Spencer Level Intelligence over historical rows.")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--output-dir", default="data/level_intelligence/replays")
    args = parser.parse_args()

    engine = LevelIntelligenceEngine()
    memory = list((engine.memory or {}).values())
    symbol_rows = [row for row in memory if str(row.get("symbol", args.symbol)) == args.symbol]
    buckets: dict[str, dict[str, Any]] = {}
    for row in symbol_rows:
        label = str(row.get("level_type", "unknown"))
        bucket = buckets.setdefault(label, {"count": 0, "avg_score": 0.0, "tp1_hits": 0, "sl_hits": 0, "net_pips": 0.0})
        bucket["count"] += 1
        bucket["avg_score"] += float(row.get("score", 0.0) or 0.0)
        bucket["tp1_hits"] += int(row.get("tp1_hits_from_level", 0) or 0)
        bucket["sl_hits"] += int(row.get("sl_hits_from_level", 0) or 0)
        bucket["net_pips"] += float(row.get("net_pips_from_level", 0.0) or 0.0)
    for bucket in buckets.values():
        if bucket["count"]:
            bucket["avg_score"] = round(bucket["avg_score"] / bucket["count"], 2)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    result = {
        "run_id": run_id,
        "symbol": args.symbol,
        "months": args.months,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "levels_analyzed": len(symbol_rows),
        "level_type_performance": buckets,
        "top_performing_levels": sorted(symbol_rows, key=lambda r: float(r.get("score", 0.0) or 0.0), reverse=True)[:20],
        "worst_levels": sorted(symbol_rows, key=lambda r: float(r.get("score", 0.0) or 0.0))[:20],
        "consumed_levels": [row for row in symbol_rows if row.get("level_state") == "consumed"][:30],
    }
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    (out_dir / "latest.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"LEVEL INTELLIGENCE REPLAY COMPLETE: run_id={run_id} levels={len(symbol_rows)}")
    print(f"Output: {out_dir / f'{run_id}.json'}")


if __name__ == "__main__":
    main()
