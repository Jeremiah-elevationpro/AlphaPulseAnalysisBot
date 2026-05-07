from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Any

from config.settings import (
    ALLOW_MICRO_TP_IN_LIVE,
    ALLOW_MICRO_TP_IN_REPLAY,
    TP_MIN_SPACING_PIPS,
    TP_MIN_USEFUL_DISTANCE_PIPS,
)
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TradePathPlan:
    direction: str
    entry: float
    invalidation_level: float
    invalidation_reason: str
    reaction_levels: list[float]
    main_targets: list[float]
    runner_targets: list[float]
    major_liquidity_target: float
    opposing_levels: list[float]
    path_quality: str
    path_rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_replay() -> bool:
    return os.getenv("ALPHAPULSE_ANALYST_REPLAY", "0").strip() in {"1", "true", "TRUE"}


def _classify_level(level: float, structural_levels: set[float]) -> str:
    rounded = round(float(level), 2)
    if rounded in structural_levels:
        return "structure"
    frac = int(abs(round(rounded))) % 100
    if frac == 0:
        return "major"
    if frac == 50:
        return "mid"
    if frac % 20 == 0:
        return "tactical"
    return "micro"


def build_trade_path_plan(
    *,
    symbol: str,
    direction: str,
    entry: float,
    current_price: float,
    watch_zone: tuple[float, float],
    scenario: str,
    key_supports: list[float],
    key_resistances: list[float],
    psychological_levels: list[float],
    liquidity_zones: list[float] | list[dict[str, Any]] | None,
    invalidation_level: float,
) -> TradePathPlan:
    direction = direction.upper()
    zone_low, zone_high = watch_zone
    liquidity_zones = liquidity_zones or []
    liquidity_levels = [
        float(item.get("level", 0.0)) if isinstance(item, dict) else float(item)
        for item in liquidity_zones
    ]
    structure_levels = {
        round(float(level), 2)
        for level in (key_supports or []) + (key_resistances or []) + liquidity_levels
    }
    raw_candidates = sorted(
        {
            round(float(level), 2)
            for level in (key_supports or []) + (key_resistances or []) + (psychological_levels or []) + liquidity_levels
            if level is not None
        }
    )
    if direction == "BUY":
        path_candidates = [level for level in raw_candidates if level > entry]
        opposing_levels = sorted([level for level in raw_candidates if level < entry], reverse=True)[:5]
    else:
        path_candidates = [level for level in raw_candidates if level < entry]
        path_candidates = sorted(path_candidates, reverse=True)
        opposing_levels = sorted([level for level in raw_candidates if level > entry])[:5]

    allow_micro = ALLOW_MICRO_TP_IN_REPLAY if _is_replay() else ALLOW_MICRO_TP_IN_LIVE
    filtered: list[float] = []
    last_level: float | None = None
    for level in path_candidates:
        level_type = _classify_level(level, structure_levels)
        if level_type == "micro" and not allow_micro:
            continue
        if abs(level - entry) < TP_MIN_USEFUL_DISTANCE_PIPS:
            continue
        if last_level is not None and abs(level - last_level) < TP_MIN_SPACING_PIPS:
            continue
        filtered.append(level)
        last_level = level

    reaction_levels = filtered[:1]
    main_targets = filtered[1:3] if len(filtered) > 1 else filtered[:1]
    runner_targets = filtered[3:5] if len(filtered) > 3 else filtered[2:4]
    major_liquidity_target = 0.0
    for level in reversed(filtered):
        level_type = _classify_level(level, structure_levels)
        if level_type in {"major", "mid", "structure"}:
            major_liquidity_target = level
            break
    if major_liquidity_target == 0.0 and filtered:
        major_liquidity_target = filtered[-1]

    path_parts = filtered[:4] if filtered else []
    path_quality = "high" if len(main_targets) >= 1 and len(runner_targets) >= 1 else "moderate" if filtered else "weak"
    path_rationale = (
        f"{direction} path from {entry:.2f} through "
        f"{' -> '.join(f'{level:.2f}' for level in path_parts) if path_parts else 'limited structure'}; "
        f"{scenario} scenario, invalidation at {invalidation_level:.2f}, watch zone {zone_low:.2f}-{zone_high:.2f}."
    )
    logger.info(
        "TRADE PATH BUILT: direction=%s entry=%.2f path=%s",
        direction,
        entry,
        "->".join(f"{level:.2f}" for level in path_parts) if path_parts else "none",
    )
    return TradePathPlan(
        direction=direction,
        entry=round(entry, 2),
        invalidation_level=round(invalidation_level, 2),
        invalidation_reason=f"{'Close below' if direction == 'BUY' else 'Reclaim above'} {invalidation_level:.2f} invalidates the idea.",
        reaction_levels=reaction_levels,
        main_targets=main_targets,
        runner_targets=runner_targets,
        major_liquidity_target=round(float(major_liquidity_target), 2) if major_liquidity_target else 0.0,
        opposing_levels=opposing_levels,
        path_quality=path_quality,
        path_rationale=path_rationale,
    )


def validate_final_trade_setup(setup: Any) -> tuple[bool, str]:
    getter = (lambda key, default=None: setup.get(key, default)) if isinstance(setup, dict) else (lambda key, default=None: getattr(setup, key, default))
    direction = str(getter("direction", "")).upper()
    entry = float(getter("entry", getter("suggested_entry", 0.0)) or 0.0)
    sl = float(getter("sl", getter("suggested_sl", 0.0)) or 0.0)
    reaction = float(getter("reaction_level", 0.0) or 0.0)
    suggested_tps = getter("suggested_tps", {}) or {}
    tp1 = float(getter("tp1", suggested_tps.get("tp1", 0.0)) or 0.0)
    tp2 = float(getter("tp2", suggested_tps.get("tp2", 0.0)) or 0.0)
    tp3 = float(getter("tp3", suggested_tps.get("tp3", 0.0)) or 0.0)
    sl_source = str(getter("sl_source", ""))
    tp_source = str(getter("tp_source", ""))
    if sl_source and sl_source != "structure_sl_engine":
        logger.warning("OLD TP/SL SOURCE BLOCKED: source=%s reason=market_setup_requires_structure_sl", sl_source)
        return False, "old_tp_sl_source_blocked"
    if tp_source and tp_source != "structure_tp_engine":
        logger.warning("OLD TP/SL SOURCE BLOCKED: source=%s reason=market_setup_requires_structure_tp", tp_source)
        return False, "old_tp_sl_source_blocked"
    if direction == "BUY":
        checks = {
            "sl_below_entry": sl < entry,
            "tp1_above_entry": tp1 > entry,
            "tp2_above_tp1": tp2 > tp1,
            "tp3_above_tp2": tp3 > tp2,
        }
        logger.info(
            "BUY LADDER VALIDATION: sl=%.2f entry=%.2f reaction=%.2f tp1=%.2f tp2=%.2f tp3=%.2f checks=%s",
            sl,
            entry,
            reaction,
            tp1,
            tp2,
            tp3,
            checks,
        )
        if not checks["sl_below_entry"]:
            logger.warning("FINAL SETUP REJECTED: reason=buy_sl_not_below_entry")
            return False, "buy_sl_not_below_entry"
        if not checks["tp1_above_entry"]:
            logger.warning("FINAL SETUP REJECTED: reason=buy_tp1_not_above_entry")
            return False, "buy_tp1_not_above_entry"
        if not checks["tp2_above_tp1"]:
            logger.warning("FINAL SETUP REJECTED: reason=buy_tp2_not_above_tp1")
            return False, "buy_tp2_not_above_tp1"
        if not checks["tp3_above_tp2"]:
            logger.warning("FINAL SETUP REJECTED: reason=buy_tp3_not_above_tp2")
            return False, "buy_tp3_not_above_tp2"
    elif direction == "SELL":
        checks = {
            "sl_above_entry": sl > entry,
            "tp1_below_entry": tp1 < entry,
            "tp2_below_tp1": tp2 < tp1,
            "tp3_below_tp2": tp3 < tp2,
        }
        logger.info(
            "SELL LADDER VALIDATION: sl=%.2f entry=%.2f reaction=%.2f tp1=%.2f tp2=%.2f tp3=%.2f checks=%s",
            sl,
            entry,
            reaction,
            tp1,
            tp2,
            tp3,
            checks,
        )
        if not checks["sl_above_entry"]:
            logger.warning("FINAL SETUP REJECTED: reason=sell_sl_not_above_entry")
            return False, "sell_sl_not_above_entry"
        if not checks["tp1_below_entry"]:
            logger.warning("FINAL SETUP REJECTED: reason=sell_tp1_not_below_entry")
            return False, "sell_tp1_not_below_entry"
        if not checks["tp2_below_tp1"]:
            logger.warning("FINAL SETUP REJECTED: reason=sell_tp2_not_below_tp1")
            return False, "sell_tp2_not_below_tp1"
        if not checks["tp3_below_tp2"]:
            logger.warning("FINAL SETUP REJECTED: reason=sell_tp3_not_below_tp2")
            return False, "sell_tp3_not_below_tp2"
    risk = abs(entry - sl)
    if risk <= 0:
        logger.warning("FINAL SETUP REJECTED: reason=zero_risk")
        return False, "zero_risk"
    logger.info(
        "FINAL SETUP VALIDATED: entry=%.2f sl=%.2f tp1=%.2f rr=%.2f",
        entry,
        sl,
        tp1,
        abs(tp1 - entry) / risk if risk > 0 else 0.0,
    )
    return True, ""
