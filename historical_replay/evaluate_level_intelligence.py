from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(run_id: str, output_dir: str) -> dict:
    path = Path(output_dir) / ("latest.json" if run_id == "latest" else f"{run_id}.json")
    if not path.exists():
        raise SystemExit(f"Level intelligence replay not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Spencer Level Intelligence replay output.")
    parser.add_argument("--run-id", default="latest")
    parser.add_argument("--output-dir", default="data/level_intelligence/replays")
    args = parser.parse_args()
    result = _load(args.run_id, args.output_dir)

    print("SPENCER LEVEL INTELLIGENCE EVALUATION")
    print(f"Run ID: {result.get('run_id')}")
    print(f"Symbol: {result.get('symbol')}")
    print(f"Levels analyzed: {result.get('levels_analyzed')}")
    print("\nLevel Type Performance:")
    for level_type, stats in (result.get("level_type_performance") or {}).items():
        print(
            f"- {level_type}: count={stats.get('count', 0)} avg_score={stats.get('avg_score', 0)} "
            f"tp1_hits={stats.get('tp1_hits', 0)} sl_hits={stats.get('sl_hits', 0)} net_pips={stats.get('net_pips', 0):.1f}"
        )
    print("\nTop Performing Levels:")
    for row in (result.get("top_performing_levels") or [])[:10]:
        print(f"- {float(row.get('level') or 0.0):.2f} {row.get('level_type')} score={row.get('score')} state={row.get('level_state')}")
    print("\nWorst Levels:")
    for row in (result.get("worst_levels") or [])[:10]:
        print(f"- {float(row.get('level') or 0.0):.2f} {row.get('level_type')} score={row.get('score')} state={row.get('level_state')}")
    print(f"\nConsumed-level reuse sample: {len(result.get('consumed_levels') or [])} consumed levels recorded")


if __name__ == "__main__":
    main()
