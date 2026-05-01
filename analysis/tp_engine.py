from __future__ import annotations

import os
from typing import Iterable

from config.settings import (
    ALLOW_MICRO_TP_IN_LIVE,
    ALLOW_MICRO_TP_IN_REPLAY,
    TP_MIN_SPACING_PIPS,
    TP_MIN_USEFUL_DISTANCE_PIPS,
    TP_PREFERRED_SPACING_PIPS,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def _replay_verbose_logs() -> bool:
    return os.getenv("ALPHAPULSE_ANALYST_REPLAY_VERBOSE", "0").strip() in {"1", "true", "TRUE", "yes", "YES"}


def _in_replay() -> bool:
    return os.getenv("ALPHAPULSE_ANALYST_REPLAY", "0").strip() in {"1", "true", "TRUE", "yes", "YES"}

# ─────────────────────────────────────────────────────────────────────────────
# TP Ladder Modes
# ─────────────────────────────────────────────────────────────────────────────

TP_LADDER_MODE: str = os.getenv("TP_LADDER_MODE", "INTRADAY_STRUCTURE")

MIN_TP_SPACING_PIPS_SCALP: float = 10.0
MIN_TP_SPACING_PIPS_INTRADAY: float = 20.0
MIN_TP_SPACING_PIPS_SWING: float = 40.0

_MODE_SPACING: dict[str, float] = {
    "SCALP": MIN_TP_SPACING_PIPS_SCALP,
    "INTRADAY_STRUCTURE": MIN_TP_SPACING_PIPS_INTRADAY,
    "SWING": MIN_TP_SPACING_PIPS_SWING,
}

# ─────────────────────────────────────────────────────────────────────────────
# Legacy constants — kept for callers that import them directly
# ─────────────────────────────────────────────────────────────────────────────

MIN_TP1_DISTANCE_PIPS: float = 10.0
PREFERRED_TP1_DISTANCE_PIPS: float = 20.0
MIN_SCENARIO_TP_DISTANCE_PIPS: float = 15.0
PREFERRED_SCENARIO_TP_DISTANCE_PIPS: float = 25.0


def _ladder_spacing(mode: str | None = None) -> float:
    return _MODE_SPACING.get((mode or TP_LADDER_MODE).upper(), MIN_TP_SPACING_PIPS_INTRADAY)


# ─────────────────────────────────────────────────────────────────────────────
# Target priority scoring
# ─────────────────────────────────────────────────────────────────────────────

def _level_priority(level: float) -> int:
    """
    Score a price level by structural significance.
    1 = Priority 1 — major psych (multiples of 100 or 50)
    2 = Priority 2 — tactical psych (multiples of 20 or 10)
    3 = Priority 3 — micro candle level (everything else)
    Lower number = higher quality.
    """
    frac = round(abs(level), 2) % 100
    if frac == 0.0:
        return 1  # multiple of 100
    if frac == 50.0:
        return 1  # multiple of 50
    if round(frac % 20, 2) == 0.0:
        return 2  # multiple of 20 (but not 50 or 100)
    if round(frac % 10, 2) == 0.0:
        return 2  # multiple of 10
    return 3


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _unique_sorted(values: Iterable[float], reverse: bool = False) -> list[float]:
    cleaned = []
    seen: set[float] = set()
    for value in values:
        if value is None:
            continue
        rounded = round(float(value), 2)
        if rounded in seen:
            continue
        seen.add(rounded)
        cleaned.append(rounded)
    return sorted(cleaned, reverse=reverse)


def _walk_with_spacing(
    direction: str,
    pool: list[float],
    min_spacing: float,
    max_count: int,
    seed: list[float] | None = None,
) -> list[float]:
    """
    Walk `pool` (already sorted nearest-first for the given direction) and accept
    each level only if it is at least `min_spacing` away from the previous accepted TP.
    `seed` is an already-accepted list that sets the initial `last` reference.
    """
    accepted = list(seed or [])
    last: float | None = accepted[-1] if accepted else None
    direction = direction.upper()

    for level in pool:
        if level in set(accepted):
            continue
        if last is not None:
            gap = (level - last) if direction == "BUY" else (last - level)
            if gap < min_spacing:
                logger.info(
                    "TP LEVEL SKIPPED: level=%.2f reason=spacing_too_close_to_previous_tp "
                    "previous=%.2f min_spacing=%.0f",
                    level, last, min_spacing,
                ) if _replay_verbose_logs() else logger.debug(
                    "TP LEVEL SKIPPED: level=%.2f reason=spacing_too_close_to_previous_tp "
                    "previous=%.2f min_spacing=%.0f",
                    level, last, min_spacing,
                )
                continue
        accepted.append(level)
        last = level
        if len(accepted) >= max_count:
            break

    return accepted


# ─────────────────────────────────────────────────────────────────────────────
# R:R check
# ─────────────────────────────────────────────────────────────────────────────

def check_risk_reward(
    direction: str,
    entry: float,
    sl: float,
    targets: list[float],
) -> dict:
    """
    Compute R:R for each target and log results.
    Returns dict: risk_pips, rr_values, tp1_ok, warnings.
    """
    risk_pips = abs(entry - sl)
    rewards = [abs(float(tp) - entry) for tp in targets]
    rr_values = [r / risk_pips if risk_pips > 0 else 0.0 for r in rewards]
    warnings: list[str] = []

    if rr_values and rr_values[0] < 1.0:
        warnings.append(
            f"TP1 below 1R threshold (rr={rr_values[0]:.2f}) — early partial target only"
        )

    tp_rr_str = "  ".join(f"tp{i + 1}_rr={v:.2f}" for i, v in enumerate(rr_values[:4]))
    logger.info(
        "RR CHECK: direction=%s entry=%.2f sl=%.2f risk=%.1f  %s  result=%s",
        direction.upper(), entry, sl, risk_pips, tp_rr_str,
        "OK" if not warnings else "; ".join(warnings),
    )
    return {
        "risk_pips": round(risk_pips, 1),
        "rewards": [round(r, 1) for r in rewards],
        "rr_values": [round(v, 2) for v in rr_values],
        "tp1_ok": bool(rr_values and rr_values[0] >= 1.0),
        "warnings": warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Core: select_ladder_targets
# ─────────────────────────────────────────────────────────────────────────────

def select_ladder_targets(
    direction: str,
    reference: float,
    candidates: list[float],
    *,
    mode: str | None = None,
    max_count: int = 5,
    min_distance_from_reference: float = MIN_TP1_DISTANCE_PIPS,
    structural_levels: list[float] | None = None,
    for_market_plan: bool = True,
) -> tuple[list[float], str]:
    """
    Select a clean TP ladder from raw candidate levels.

    Selection rules:
    - TP1 must be at least `min_distance_from_reference` from `reference`
    - Consecutive TPs must be spaced at least `spacing` pips apart (mode-dependent)
    - For market plan: Priority 3 (micro candle) levels skipped unless < 3 targets found
    - For entry alerts: all priority levels eligible

    Args:
        direction:                  "BUY" or "SELL"
        reference:                  zone_low (SELL) or zone_high (BUY) — the entry boundary
        candidates:                 raw list of structural + psych levels
        mode:                       ladder mode override (default: TP_LADDER_MODE env)
        max_count:                  maximum TPs to return (default 5)
        min_distance_from_reference: minimum distance from reference to TP1 (default 10)
        structural_levels:          explicitly Priority-1 levels (H1/H4 S/R)
        for_market_plan:            True = skip P3 micro levels; False = include if needed

    Returns:
        (selected_targets, rationale_string)
    """
    direction = direction.upper()
    spacing = _ladder_spacing(mode)
    struct_set = {round(float(v), 2) for v in (structural_levels or [])}

    # ── Step 1: filter to correct side and minimum distance from reference ──
    if direction == "SELL":
        valid = _unique_sorted(
            [
                v for v in candidates
                if round(float(v), 2) < reference
                and (reference - round(float(v), 2)) >= min_distance_from_reference
            ],
            reverse=True,  # nearest to reference first
        )
    else:
        valid = _unique_sorted(
            [
                v for v in candidates
                if round(float(v), 2) > reference
                and (round(float(v), 2) - reference) >= min_distance_from_reference
            ],
        )  # ascending: nearest to reference first

    # ── Step 2: pass 1 — structural + Priority 1 and 2 levels only ──
    p12_pool = [v for v in valid if _level_priority(v) <= 2 or round(v, 2) in struct_set]
    accepted = _walk_with_spacing(direction, p12_pool, spacing, max_count)

    # ── Step 3: pass 2 — include Priority 3 if not enough ──
    if len(accepted) < 3:
        if for_market_plan:
            logger.info(
                "TP LADDER: insufficient P1/P2 levels (%d) — adding micro levels for market plan",
                len(accepted),
            )
        # Re-run from scratch with all levels
        accepted = _walk_with_spacing(direction, valid, spacing, max_count)

    # ── Step 4: arithmetic fallback ──
    if len(accepted) < 3:
        anchor = accepted[-1] if accepted else reference
        step = spacing
        while len(accepted) < 3:
            fallback = (
                round(anchor - step, 2) if direction == "SELL" else round(anchor + step, 2)
            )
            if fallback not in set(accepted):
                accepted.append(fallback)
            step += spacing

    # ── Step 5: rationale ──
    p1_count = sum(1 for v in accepted if _level_priority(v) == 1 or round(v, 2) in struct_set)
    p2_count = sum(1 for v in accepted if _level_priority(v) == 2 and round(v, 2) not in struct_set)
    p3_count = len(accepted) - p1_count - p2_count

    tier_parts: list[str] = []
    if p1_count:
        tier_parts.append(f"{p1_count} major structure/psych")
    if p2_count:
        tier_parts.append(f"{p2_count} tactical")
    if p3_count:
        tier_parts.append(f"{p3_count} micro")
    tier_str = " + ".join(tier_parts) or "structure"
    path = " -> ".join(f"{t:.2f}" for t in accepted[:max_count])
    mode_label = (mode or TP_LADDER_MODE).replace("_", " ").title()

    if direction == "SELL":
        rationale = (
            f"Downside path: {path}. "
            f"Targets from {tier_str} below entry ({mode_label} ladder, {spacing:.0f}-pip spacing)."
        )
    else:
        rationale = (
            f"Upside path: {path}. "
            f"Targets from {tier_str} above entry ({mode_label} ladder, {spacing:.0f}-pip spacing)."
        )

    logger.info(
        "TP LADDER SELECTED: direction=%s mode=%s reference=%.2f spacing=%.0f targets=%s",
        direction,
        mode or TP_LADDER_MODE,
        reference,
        spacing,
        ",".join(f"{t:.2f}" for t in accepted),
    )
    return accepted[:max_count], rationale


# ─────────────────────────────────────────────────────────────────────────────
# build_structure_tps — entry alert TP selection
# ─────────────────────────────────────────────────────────────────────────────

def build_structure_tps(
    direction: str,
    entry: float,
    supports: list[float],
    resistances: list[float],
    psych_levels: list[float],
    liquidity_levels: list[float] | None = None,
    entry_zone: str = "",
    sl: float | None = None,
    trade_path=None,
) -> dict[str, object]:
    """
    Build TP1/TP2/TP3 for an entry alert using the ladder engine.
    Respects TP_LADDER_MODE spacing. Logs R:R if SL is supplied.
    """
    direction = direction.upper()
    liquidity_levels = liquidity_levels or []
    structural_levels = supports if direction == "SELL" else resistances
    risk = abs(entry - sl) if sl is not None else 0.0
    allow_micro = ALLOW_MICRO_TP_IN_REPLAY if _in_replay() else ALLOW_MICRO_TP_IN_LIVE

    raw = (supports if direction == "SELL" else resistances) + psych_levels + liquidity_levels
    if trade_path is not None:
        raw += list(getattr(trade_path, "reaction_levels", []) or [])
        raw += list(getattr(trade_path, "main_targets", []) or [])
        raw += list(getattr(trade_path, "runner_targets", []) or [])
        if getattr(trade_path, "major_liquidity_target", 0.0):
            raw.append(getattr(trade_path, "major_liquidity_target", 0.0))

    if direction == "SELL":
        candidates = _unique_sorted([v for v in raw if float(v) < entry], reverse=True)
    else:
        candidates = _unique_sorted([v for v in raw if float(v) > entry])

    filtered: list[float] = []
    level_types: dict[float, str] = {}
    for level in candidates:
        level_type = "structure" if round(level, 2) in {round(float(v), 2) for v in structural_levels} else (
            "major" if _level_priority(level) == 1 else "tactical" if _level_priority(level) == 2 else "micro"
        )
        reward = abs(level - entry)
        if level_type == "micro" and not allow_micro:
            logger.warning("MICRO TP BLOCKED: level=%.2f mode=live", level)
            continue
        if reward < TP_MIN_USEFUL_DISTANCE_PIPS:
            if _replay_verbose_logs():
                logger.info("TP LEVEL SKIPPED: level=%.2f reason=too_close_to_entry", level)
            continue
        if filtered and abs(level - filtered[-1]) < TP_MIN_SPACING_PIPS:
            if _replay_verbose_logs():
                logger.info("TP LEVEL SKIPPED: level=%.2f reason=spacing_too_close_to_previous_tp", level)
            continue
        filtered.append(level)
        level_types[round(level, 2)] = level_type

    reaction_level = 0.0
    tp_targets: list[float] = []
    target_roles: dict[str, str] = {}
    for level in filtered:
        reward = abs(level - entry)
        if not tp_targets and risk > 0 and reward < max(TP_MIN_USEFUL_DISTANCE_PIPS, 0.8 * risk):
            reaction_level = level
            logger.info(
                "TP ROLE ASSIGNED: level=%.2f role=reaction reward=%.0f risk=%.0f",
                level,
                reward,
                risk,
            )
            continue
        if not tp_targets:
            target_roles["tp1"] = f"{level_types.get(round(level, 2), 'structure')}:main_target"
        elif len(tp_targets) == 1:
            target_roles["tp2"] = f"{level_types.get(round(level, 2), 'structure')}:runner_target"
        elif len(tp_targets) == 2:
            target_roles["tp3"] = f"{level_types.get(round(level, 2), 'structure')}:extended_runner"
        tp_targets.append(level)
        if len(tp_targets) == 3:
            break

    step = TP_PREFERRED_SPACING_PIPS
    anchor = tp_targets[-1] if tp_targets else (reaction_level if reaction_level else entry)
    while len(tp_targets) < 3:
        anchor = round(anchor - step, 2) if direction == "SELL" else round(anchor + step, 2)
        tp_targets.append(anchor)
        role = f"tp{len(tp_targets)}"
        target_roles[role] = "fallback:structure_extension"

    tp1, tp2, tp3 = tp_targets[:3]
    rr = check_risk_reward(direction, entry, sl or entry, [tp1, tp2, tp3]) if sl is not None else {
        "risk_pips": 0.0,
        "rewards": [abs(tp1 - entry), abs(tp2 - entry), abs(tp3 - entry)],
        "rr_values": [0.0, 0.0, 0.0],
        "warnings": [],
    }
    logger.info(
        "RR-AWARE TP SELECTED: tp1=%.2f tp2=%.2f tp3=%.2f",
        tp1,
        tp2,
        tp3,
    )
    rationale = (
        f"{'Downside' if direction == 'SELL' else 'Upside'} path uses "
        f"{'reaction ' + f'{reaction_level:.2f}, ' if reaction_level else ''}"
        f"TP1 {tp1:.2f}, TP2 {tp2:.2f}, TP3 {tp3:.2f} from structure/psych path."
    )
    logger.info(
        "TP ENGINE SELECTED: direction=%s entry_zone=%s targets=%s rationale=%s",
        direction,
        entry_zone or f"{entry:.2f}",
        f"{tp1:.2f},{tp2:.2f},{tp3:.2f}",
        rationale,
    )
    return {
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "reaction_level": round(reaction_level, 2) if reaction_level else 0.0,
        "risk_pips": float(rr.get("risk_pips", 0.0)),
        "tp1_reward_pips": float(rr.get("rewards", [0.0, 0.0, 0.0])[0]),
        "tp2_reward_pips": float(rr.get("rewards", [0.0, 0.0, 0.0])[1]),
        "tp3_reward_pips": float(rr.get("rewards", [0.0, 0.0, 0.0])[2]),
        "tp1_rr": float(rr.get("rr_values", [0.0, 0.0, 0.0])[0]),
        "tp2_rr": float(rr.get("rr_values", [0.0, 0.0, 0.0])[1]),
        "tp3_rr": float(rr.get("rr_values", [0.0, 0.0, 0.0])[2]),
        "tp_source": "structure_tp_engine",
        "tp_rationale": rationale,
        "target_roles": target_roles,
        "tp1_source_type": level_types.get(round(tp1, 2), "fallback"),
        "micro_tp_blocked": not allow_micro,
    }


# ─────────────────────────────────────────────────────────────────────────────
# filter_market_plan_targets — market plan TP ladder (backward-compatible)
# ─────────────────────────────────────────────────────────────────────────────

def filter_market_plan_targets(
    direction: str,
    reference_entry: float,
    current_price: float,
    candidates: list[float],
    *,
    zone_low: float,
    zone_high: float,
    preferred_levels: list[float] | None = None,
    max_count: int = 5,
    mode: str | None = None,
) -> list[float]:
    """
    Market-plan TP ladder selection.

    Targets must be:
    - On the correct side of BOTH reference_entry AND current_price
    - At least MIN_TP1_DISTANCE_PIPS from reference_entry
    - Spaced at least mode-spacing pips apart
    - Priority 1/2 (structural/psych) preferred; Priority 3 (micro) only as fallback

    Args:
        direction:       "BUY" or "SELL"
        reference_entry: zone_low (SELL) or zone_high (BUY)
        current_price:   live price — targets must be beyond this
        candidates:      raw structural + psych level pool
        zone_low/high:   entry zone boundaries (targets must be outside the zone)
        preferred_levels: explicitly structural levels (boosted to Priority 1)
        max_count:       maximum TPs
        mode:            ladder mode override
    """
    direction = direction.upper()

    # Pre-filter: targets must be on the correct side of BOTH reference and current price
    if direction == "SELL":
        pre_filtered = [
            v for v in candidates
            if round(float(v), 2) < zone_low and round(float(v), 2) < current_price
        ]
    else:
        pre_filtered = [
            v for v in candidates
            if round(float(v), 2) > zone_high and round(float(v), 2) > current_price
        ]

    targets, _ = select_ladder_targets(
        direction=direction,
        reference=reference_entry,
        candidates=pre_filtered,
        mode=mode,
        max_count=max_count,
        min_distance_from_reference=MIN_TP1_DISTANCE_PIPS,
        structural_levels=preferred_levels,
        for_market_plan=True,
    )
    return targets
