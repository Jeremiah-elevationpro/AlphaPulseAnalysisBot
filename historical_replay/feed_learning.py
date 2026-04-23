from __future__ import annotations

import argparse
import os
from collections import defaultdict
from typing import Dict, Iterable, List

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from config.settings import (
    ACTIVE_TIMEFRAME_PAIR_LABELS,
    BASE_CONFIDENCE,
    MAX_CONFIDENCE,
    MIN_CONFIDENCE,
    MIN_TRADES_FOR_LEARNING,
)
from db.database import Database
from learning.rl_engine import LearningEngine
from learning.stats_learner import StatisticalLearner
from utils.logger import get_logger


logger = get_logger(__name__)
EMA_ALPHA = 0.30
WIN_RESULTS = {"PARTIAL_WIN", "BREAKEVEN_WIN", "WIN", "STRONG_WIN"}


def main():
    parser = argparse.ArgumentParser(
        description="Feed activated historical replay trades into AlphaPulse learning tables."
    )
    parser.add_argument("--run-id", type=int, default=0, help="Replay run id. Defaults to latest run.")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = Database()
    try:
        db.init()
        run = db.get_replay_run(args.run_id) if args.run_id else db.get_latest_replay_run()
        if not run:
            raise SystemExit("No replay runs found in Supabase.")

        run_id = int(run["id"])
        trades = db.get_replay_trades_for_learning(limit=args.limit, replay_run_id=run_id)
        if not trades:
            raise SystemExit(f"No activated replay trades found for run {run_id}.")

        summary = build_learning_summary(trades)
        if args.dry_run:
            print(format_summary(run_id, summary, dry_run=True))
            return

        apply_learning_summary(db, summary)
        apply_combination_learning(db, trades)
        print(format_summary(run_id, summary, dry_run=False))
    finally:
        db.close()


def build_learning_summary(trades: Iterable[Dict]) -> Dict[str, Dict]:
    grouped: Dict[str, Dict] = defaultdict(
        lambda: {
            "level_type": "",
            "tf_pair": "",
            "wins": 0,
            "losses": 0,
            "total": 0,
            "reward_sum": 0.0,
            "ema": 0.0,
            "ema_n": 0,
        }
    )

    for trade in sorted(trades, key=lambda row: row.get("timestamp") or ""):
        result = trade.get("final_result")
        level_type = trade.get("level_type") or "unknown"
        tf_pair = _normalise_tf_pair(trade.get("timeframe_pair") or "")
        if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS:
            logger.info("Replay learning skipped disabled timeframe pair: %s", tf_pair)
            continue
        if result not in WIN_RESULTS and result != "LOSS":
            continue

        key = f"{level_type}|{tf_pair}"
        bucket = grouped[key]
        bucket["level_type"] = level_type
        bucket["tf_pair"] = tf_pair
        bucket["total"] += 1
        if result in WIN_RESULTS:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1

        reward = float(trade.get("reward_score") or _fallback_reward(result, trade.get("tp_progress")))
        bucket["reward_sum"] += reward
        if bucket["ema_n"] == 0:
            bucket["ema"] = reward
        else:
            bucket["ema"] = EMA_ALPHA * reward + (1 - EMA_ALPHA) * bucket["ema"]
        bucket["ema_n"] += 1

    for bucket in grouped.values():
        total = max(1, bucket["total"])
        win_rate = bucket["wins"] / total
        stat_score = win_rate if bucket["total"] >= MIN_TRADES_FOR_LEARNING else BASE_CONFIDENCE
        rl_score = _clamp(BASE_CONFIDENCE + bucket["ema"] * 0.18)
        bucket["win_rate"] = round(win_rate, 3)
        bucket["confidence"] = round(_clamp(0.55 * stat_score + 0.45 * rl_score), 3)

    return dict(grouped)


def apply_learning_summary(db: Database, summary: Dict[str, Dict]):
    for bucket in summary.values():
        db.upsert_performance(
            level_type=bucket["level_type"],
            tf_pair=bucket["tf_pair"],
            wins=bucket["wins"],
            losses=bucket["losses"],
            reward=round(bucket["reward_sum"], 3),
        )
        db.upsert_confidence(
            level_type=bucket["level_type"],
            tf_pair=bucket["tf_pair"],
            score=bucket["confidence"],
            reward_total=round(bucket["ema"], 3),
        )
        logger.info(
            "REPLAY LEARNING FED: %s|%s wins=%d losses=%d score=%.3f ema=%.3f",
            bucket["level_type"],
            bucket["tf_pair"],
            bucket["wins"],
            bucket["losses"],
            bucket["confidence"],
            bucket["ema"],
        )


def apply_combination_learning(db: Database, trades: Iterable[Dict]):
    """Feed activated replay trades into the learned combination ranker."""
    stats = StatisticalLearner(db)
    learning = LearningEngine(db, stats)
    count = 0
    for trade in trades:
        learning.process_replay_trade_dict(trade)
        count += 1
    logger.info("REPLAY COMBINATION LEARNING FED: %d activated trade(s)", count)


def format_summary(run_id: int, summary: Dict[str, Dict], *, dry_run: bool) -> str:
    status = "DRY RUN" if dry_run else "APPLIED"
    lines = [f"Replay learning import {status} | run_id={run_id}"]
    for key, bucket in sorted(summary.items()):
        lines.append(
            "- {key}: trades={total} wins={wins} losses={losses} "
            "win_rate={win_rate:.1%} confidence={confidence:.1%} reward_sum={reward_sum:.2f}".format(
                key=key,
                total=bucket["total"],
                wins=bucket["wins"],
                losses=bucket["losses"],
                win_rate=bucket["win_rate"],
                confidence=bucket["confidence"],
                reward_sum=bucket["reward_sum"],
            )
        )
    return "\n".join(lines)


def _normalise_tf_pair(value: str) -> str:
    return value.replace("->", "-").replace(" ", "")


def _fallback_reward(result: str, tp_progress) -> float:
    if result == "LOSS":
        return -3.0
    if result in ("PARTIAL_WIN", "BREAKEVEN_WIN"):
        return 1.0
    if result == "WIN":
        return 2.0
    if result == "STRONG_WIN":
        return 3.5 + max(0, int(tp_progress or 0) - 3) * 0.5
    return 0.0


def _clamp(value: float) -> float:
    return max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, value))


if __name__ == "__main__":
    main()
