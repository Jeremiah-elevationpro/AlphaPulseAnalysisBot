from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from db.database import Database
from historical_replay.break_retest_research import STRATEGY_FAILED_ENGULF, STRATEGY_STANDARD
from utils.strategy_registry import canonical_strategy_type
from utils.logger import get_logger

GAP_SWEEP = "gap_liquidity_sweep_reclaim"
ENGULFING = "engulfing_rejection"
SUPPORTED_STRATEGIES = {
    GAP_SWEEP,
    "gap_sweep",
    ENGULFING,
    "engulfing",
    STRATEGY_STANDARD,
    "break_retest",
    STRATEGY_FAILED_ENGULF,
    "failed_engulf",
}

logger = get_logger("historical_replay.export_strategy_learning")


def export_strategy_learning(
    db: Database,
    *,
    strategy_type: str,
    run_id: Optional[int] = None,
) -> Dict[str, Any]:
    if strategy_type not in SUPPORTED_STRATEGIES:
        raise ValueError(f"Unsupported strategy_type: {strategy_type}")
    strategy_type = canonical_strategy_type(strategy_type)

    rows_exported = 0
    learning_valid = 0
    skipped = 0
    duplicates_skipped = 0
    invalid_skipped = 0
    activated_trades = 0
    errors: List[str] = []
    source = "replay" if strategy_type == GAP_SWEEP else "research"

    if strategy_type == GAP_SWEEP:
      replay_run = db.get_latest_replay_run() if run_id in (None, 0) else db.get_replay_run(int(run_id))
      if not replay_run:
          return _result(strategy_type, source, None, 0, 0, 0, 0, 0, 0)
      source_run_id = int(replay_run["id"])
      replay_trades = db.get_replay_trades(source_run_id, limit=10000)
      activated_trades = len(replay_trades)
      existing_export_keys = _existing_export_keys(db, strategy_type, source, source_run_id, errors)
      seen_export_keys = set(existing_export_keys)
      for row in replay_trades:
          payload = _normalize_gap_trade(row, source_run_id)
          if payload is None:
              skipped += 1
              invalid_skipped += 1
              continue
          export_key = str(payload.get("export_key") or "")
          if export_key and export_key in seen_export_keys:
              duplicates_skipped += 1
              logger.info("LEARNING EXPORT DEDUPE: skipped duplicate trade export_key=%s", export_key)
              continue
          try:
              db.insert_strategy_learning_trade(payload)
              rows_exported += 1
              learning_valid += 1 if payload.get("learning_valid", True) else 0
              if export_key:
                  seen_export_keys.add(export_key)
          except Exception as exc:
              errors.append(
                  f"strategy_learning_trades insert failed | keys={','.join(sorted(payload.keys()))} | error={exc}"
              )
    else:
      result = db.get_strategy_research_results(int(run_id)) if run_id not in (None, 0) else db.get_latest_strategy_research_results(strategy_type)
      if not result:
          return _result(strategy_type, source, None, 0, 0, 0, 0, 0, 0)
      source_run_id = int(result["run"]["id"])
      activated_trades = len(result["trades"])
      existing_export_keys = _existing_export_keys(db, strategy_type, source, source_run_id, errors)
      seen_export_keys = set(existing_export_keys)
      for row in result["trades"]:
          payload = _normalize_research_trade(row, strategy_type, source_run_id)
          if payload is None:
              skipped += 1
              invalid_skipped += 1
              continue
          export_key = str(payload.get("export_key") or "")
          if export_key and export_key in seen_export_keys:
              duplicates_skipped += 1
              logger.info("LEARNING EXPORT DEDUPE: skipped duplicate trade export_key=%s", export_key)
              continue
          try:
              db.insert_strategy_learning_trade(payload)
              rows_exported += 1
              learning_valid += 1 if payload.get("learning_valid", True) else 0
              if export_key:
                  seen_export_keys.add(export_key)
          except Exception as exc:
              errors.append(
                  f"strategy_learning_trades insert failed | keys={','.join(sorted(payload.keys()))} | error={exc}"
              )

    profile_result = db.rebuild_strategy_learning_profiles(strategy_type)
    return _result(
        strategy_type,
        source,
        source_run_id,
        activated_trades,
        rows_exported,
        duplicates_skipped,
        invalid_skipped,
        learning_valid,
        skipped,
        int(profile_result.get("profiles_upserted", 0)),
        errors=errors,
    )


def _normalize_gap_trade(row: Dict[str, Any], source_run_id: int) -> Optional[Dict[str, Any]]:
    result = row.get("final_result") or row.get("result")
    final_pips = _num(row.get("final_pips"), row.get("realized_pips"))
    if result is None or final_pips is None:
        return None
    strategy_type = canonical_strategy_type(row.get("strategy_type") or GAP_SWEEP)
    entry = _num(row.get("entry"), row.get("entry_price"))
    activated_at = row.get("activated_at")
    symbol = row.get("pair") or row.get("symbol") or "XAUUSD"
    return {
        "source": "replay",
        "source_run_id": source_run_id,
        "strategy_type": strategy_type,
        "setup_type": row.get("setup_type") or row.get("level_type"),
        "symbol": symbol,
        "direction": row.get("direction"),
        "timeframe": row.get("lower_tf") or row.get("timeframe"),
        "timeframe_pair": row.get("timeframe_pair") or _join_tf(row.get("higher_tf"), row.get("lower_tf")),
        "session_name": row.get("session_name"),
        "market_condition": row.get("market_condition"),
        "dominant_bias": row.get("dominant_bias") or row.get("h4_bias"),
        "bias_strength": row.get("bias_strength"),
        "confirmation_type": row.get("micro_confirmation_type") or row.get("confirmation_type"),
        "confirmation_score": _num(row.get("micro_confirmation_score"), row.get("confirmation_score")),
        "level_type": row.get("level_type"),
        "level_price": _num(row.get("level_price")),
        "level_high": _num(row.get("level_high")),
        "level_low": _num(row.get("level_low")),
        "level_mid": _num(row.get("level_mid")),
        "entry": entry,
        "sl": _num(row.get("sl_price")),
        "tp1": _num(row.get("tp1")),
        "tp2": _num(row.get("tp2")),
        "tp3": _num(row.get("tp3")),
        "final_result": result,
        "final_pips": final_pips,
        "reward_score": _num(row.get("reward_score")),
        "tp_progress": int(row.get("tp_progress") or row.get("tp_progress_reached") or 0),
        "protected_after_tp1": bool(row.get("protected_after_tp1", False)),
        "activated_at": activated_at,
        "closed_at": row.get("closed_at"),
        "quality_rejection_count": row.get("quality_rejection_count"),
        "structure_break_count": row.get("structure_break_count"),
        "pd_location": row.get("pd_location"),
        "learning_valid": True,
        "validation_warning": None,
        "export_key": f"{strategy_type}:replay:{source_run_id}:{symbol}:{row.get('direction') or ''}:{activated_at or ''}:{entry if entry is not None else ''}:{result}",
        "created_at": row.get("created_at") or datetime.now(timezone.utc).isoformat(),
    }


def _normalize_research_trade(row: Dict[str, Any], strategy_type: str, source_run_id: int) -> Optional[Dict[str, Any]]:
    result = row.get("final_result")
    final_pips = _num(row.get("final_pips"))
    if result is None or final_pips is None:
        return None
    entry = _num(row.get("entry"))
    activated_at = row.get("activated_at")
    symbol = row.get("symbol") or "XAUUSD"
    return {
        "source": "research",
        "source_run_id": source_run_id,
        "strategy_type": strategy_type,
        "setup_type": row.get("setup_type") or strategy_type,
        "symbol": symbol,
        "direction": row.get("direction"),
        "timeframe": row.get("timeframe"),
        "timeframe_pair": row.get("timeframe_pair"),
        "session_name": row.get("session_name"),
        "market_condition": row.get("market_condition"),
        "dominant_bias": row.get("dominant_bias"),
        "bias_strength": row.get("bias_strength"),
        "confirmation_type": row.get("retest_confirmation_type") or row.get("confirmation_path") or row.get("confirmation_type"),
        "confirmation_score": _num(row.get("confirmation_score")),
        "level_type": row.get("level_type") or row.get("source_level_type") or row.get("engulf_type"),
        "level_price": _num(row.get("level_price"), row.get("retest_level"), row.get("engulf_mid")),
        "level_high": _num(row.get("level_high"), row.get("engulf_high"), row.get("original_engulf_high")),
        "level_low": _num(row.get("level_low"), row.get("engulf_low"), row.get("original_engulf_low")),
        "level_mid": _num(row.get("level_mid"), row.get("engulf_mid"), row.get("original_engulf_mid")),
        "entry": entry,
        "sl": _num(row.get("sl")),
        "tp1": _num(row.get("tp1")),
        "tp2": _num(row.get("tp2")),
        "tp3": _num(row.get("tp3")),
        "final_result": result,
        "final_pips": final_pips,
        "reward_score": _num(row.get("reward_score")),
        "tp_progress": int(row.get("tp_progress") or 0),
        "protected_after_tp1": bool(row.get("protected_after_tp1", False)),
        "activated_at": activated_at,
        "closed_at": row.get("closed_at"),
        "quality_rejection_count": row.get("quality_rejection_count"),
        "structure_break_count": row.get("structure_break_count"),
        "pd_location": row.get("pd_location"),
        "break_level": _num(row.get("break_level")),
        "break_distance_pips": _num(row.get("break_distance_pips")),
        "retest_level": _num(row.get("retest_level")),
        "retest_confirmation_type": row.get("retest_confirmation_type"),
        "original_engulf_high": _num(row.get("original_engulf_high")),
        "original_engulf_low": _num(row.get("original_engulf_low")),
        "original_engulf_direction": row.get("original_engulf_direction"),
        "learning_valid": str(result).upper() != "OPEN" and final_pips is not None,
        "validation_warning": None if final_pips is not None else "missing_final_pips",
        "export_key": f"{strategy_type}:research:{source_run_id}:{symbol}:{row.get('direction') or ''}:{activated_at or ''}:{entry if entry is not None else ''}:{result}",
        "created_at": row.get("created_at") or datetime.now(timezone.utc).isoformat(),
    }


def _join_tf(higher_tf: Any, lower_tf: Any) -> Optional[str]:
    if higher_tf and lower_tf:
        return f"{higher_tf}->{lower_tf}"
    return None


def _num(*values: Any) -> Optional[float]:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _existing_export_keys(
    db: Database,
    strategy_type: str,
    source: str,
    source_run_id: int,
    errors: List[str],
) -> set[str]:
    try:
        rows = db.get_strategy_learning_trades(
            strategy_type=strategy_type,
            source=source,
            source_run_id=source_run_id,
            limit=20000,
        )
        return {str(row.get("export_key")) for row in rows if row.get("export_key")}
    except Exception as exc:
        logger.warning("LEARNING EXPORT PREFETCH FAILED: continuing without dedupe prefetch | %s", exc)
        errors.append(f"learning export prefetch failed: {exc}")
        return set()


def _result(strategy_type: str, source: str, source_run_id: Optional[int], activated_trades: int, rows_exported: int, duplicates_skipped: int, invalid_skipped: int, learning_valid: int, skipped: int, profiles_upserted: int, errors: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "strategy_type": strategy_type,
        "source": source,
        "source_run_id": source_run_id,
        "activated_trades": activated_trades,
        "rows_exported": rows_exported,
        "duplicates_skipped": duplicates_skipped,
        "invalid_skipped": invalid_skipped,
        "learning_valid": learning_valid,
        "skipped": skipped,
        "profiles_upserted": profiles_upserted,
        "errors": errors or [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export replay/research rows into strategy_learning_trades.")
    parser.add_argument("--strategy", required=True, choices=sorted(SUPPORTED_STRATEGIES))
    parser.add_argument("--run-id", default="latest")
    args = parser.parse_args()

    db = Database()
    try:
        db.init()
        run_id = None if str(args.run_id).lower() == "latest" else int(args.run_id)
        print(export_strategy_learning(db, strategy_type=args.strategy, run_id=run_id))
    finally:
        db.close()


if __name__ == "__main__":
    main()
