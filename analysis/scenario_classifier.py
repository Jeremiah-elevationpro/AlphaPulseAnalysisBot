"""Spencer scenario change classifier.

Classifies the difference between the previously-sent primary scenario and the
current primary scenario into one of:

    no_material_change          - same direction + same rounded zone, suppress
    scenario_refined            - same direction, zone shifted by a few pips
    scenario_played_out         - TP1 reached or zone consumed (sent once)
    scenario_invalidated        - price closed beyond invalidation level
    scenario_flipped            - primary direction flipped (BUY <-> SELL)
    bias_changed                - dominant bias flipped or strength flipped opposite
    structure_breakout          - price closed below previous active support
    structure_reclaim           - price closed above previous active resistance

The classifier never imports trading-strategy modules; it operates purely on
the dictionaries produced by the market-plan layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from config.settings import (
    SCENARIO_DEDUPE_ROUND_PIPS,
    SCENARIO_NO_CHANGE_ZONE_TOLERANCE_PIPS,
    SCENARIO_REFINEMENT_COOLDOWN_MINUTES,
    SCENARIO_REFINEMENT_ZONE_TOLERANCE_PIPS,
    SCENARIO_RECLAIM_BUFFER_PIPS,
    SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES,
)
from analysis.level_intelligence import validate_invalidation_level


VALID_CHANGE_TYPES = {
    "no_material_change",
    "scenario_refined",
    "scenario_played_out",
    "scenario_invalidated",
    "scenario_flipped",
    "bias_changed",
    "structure_breakout",
    "structure_reclaim",
}


@dataclass
class ScenarioChange:
    change_type: str = "no_material_change"
    should_send: bool = False
    cooldown_minutes: int = SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES
    severity: str = "low"
    title: str = ""
    message: str = ""
    dedupe_key: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.change_type,
            "should_send": self.should_send,
            "cooldown_minutes": self.cooldown_minutes,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "dedupe_key": self.dedupe_key,
            "reason": self.reason,
            "details": self.details,
        }


def _round_pips(value: float, step: int = SCENARIO_DEDUPE_ROUND_PIPS) -> float:
    if not step or step <= 0:
        return float(value or 0.0)
    try:
        return round(float(value or 0.0) / step) * step
    except Exception:
        return 0.0


def _zone_bounds(scenario: dict | None) -> tuple[float, float]:
    if not scenario:
        return 0.0, 0.0
    low = _safe_float(scenario.get("watch_low") or scenario.get("zone_low"))
    high = _safe_float(scenario.get("watch_high") or scenario.get("zone_high"))
    if low and high:
        return min(low, high), max(low, high)
    raw = str(scenario.get("watch_zone") or scenario.get("zone") or "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw)]
    if len(nums) >= 2:
        return min(nums[0], nums[1]), max(nums[0], nums[1])
    if nums:
        return nums[0], nums[0]
    return 0.0, 0.0


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, str):
            nums = re.findall(r"-?\d+(?:\.\d+)?", value)
            if len(nums) != 1:
                return 0.0
            return float(nums[0])
        return float(value)
    except Exception:
        return 0.0


def _zone_label(low: float, high: float) -> str:
    if not low and not high:
        return "n/a"
    if abs(high - low) < 1e-3:
        return f"{low:.2f}"
    return f"{low:.2f}-{high:.2f}"


def _direction(scenario: dict | None) -> str:
    if not scenario:
        return ""
    return str(scenario.get("direction") or "").upper()


def _build_dedupe_key(symbol: str, change_type: str, primary: dict | None, secondary: dict | None) -> str:
    p_dir = _direction(primary)
    s_dir = _direction(secondary)
    p_low, p_high = _zone_bounds(primary)
    s_low, s_high = _zone_bounds(secondary)
    return (
        f"{symbol}|{change_type}|{p_dir}|{_round_pips(p_low):.0f}|{_round_pips(p_high):.0f}|"
        f"{s_dir}|{_round_pips(s_low):.0f}|{_round_pips(s_high):.0f}"
    )


def _zone_overlap(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    if not a_high or not b_high:
        return False
    return not (a_high < b_low or b_high < a_low)


def _max_zone_shift(a_low: float, a_high: float, b_low: float, b_high: float) -> float:
    if not (a_low or a_high) or not (b_low or b_high):
        return 0.0
    return max(abs(a_low - b_low), abs(a_high - b_high))


def _bias_strength_value(label: str) -> int:
    mapping = {"very_strong": 4, "strong": 3, "moderate": 2, "medium": 2, "weak": 1, "neutral": 0}
    return mapping.get(str(label or "").lower(), 0)


def _bias_polarity(bias: str) -> str:
    text = str(bias or "").lower()
    if text in {"bullish", "buy", "long", "up"}:
        return "bullish"
    if text in {"bearish", "sell", "short", "down"}:
        return "bearish"
    return "neutral"


def _invalidation_breached(direction: str, invalidation: float, current_price: float) -> bool:
    if not invalidation or not current_price:
        return False
    if direction == "SELL":
        return current_price > invalidation + SCENARIO_RECLAIM_BUFFER_PIPS
    if direction == "BUY":
        return current_price < invalidation - SCENARIO_RECLAIM_BUFFER_PIPS
    return False


def _validated_invalidation_breached(direction: str, invalidation_raw: Any, current_price: float, zone_low: float, zone_high: float) -> tuple[bool, float, str]:
    fallback_entry = zone_high if direction == "BUY" else zone_low
    check = validate_invalidation_level(
        direction,
        fallback_entry,
        zone_low,
        zone_high,
        invalidation_raw,
        current_price,
        close_price=current_price,
    )
    if not check.valid:
        logger_msg = f"SCENARIO INVALIDATION SUPPRESSED: reason={check.reason}"
        try:
            from utils.logger import get_logger
            get_logger(__name__).info(logger_msg)
        except Exception:
            pass
        return False, check.invalidation, check.reason
    return check.invalidated, check.invalidation, check.reason


def _zone_breached_above(zone_high: float, current_price: float) -> bool:
    if not zone_high or not current_price:
        return False
    return current_price > zone_high + SCENARIO_RECLAIM_BUFFER_PIPS


def _zone_breached_below(zone_low: float, current_price: float) -> bool:
    if not zone_low or not current_price:
        return False
    return current_price < zone_low - SCENARIO_RECLAIM_BUFFER_PIPS


def classify_scenario_change(
    *,
    symbol: str,
    previous_primary: dict | None,
    current_primary: dict | None,
    previous_secondary: dict | None = None,
    current_secondary: dict | None = None,
    previous_bias: str = "",
    current_bias: str = "",
    previous_bias_strength: str = "",
    current_bias_strength: str = "",
    current_price: float = 0.0,
    primary_played_out: bool = False,
) -> ScenarioChange:
    """Classify the change between two consecutive primary scenarios.

    Returns a ScenarioChange. ``should_send=False`` means the bot should
    suppress the alert (no material change). ``change_type=scenario_refined``
    means a low-priority refinement update (longer cooldown).
    """
    change = ScenarioChange()

    if not current_primary:
        change.change_type = "no_material_change"
        change.should_send = False
        change.reason = "no_current_primary"
        change.dedupe_key = _build_dedupe_key(symbol, "no_material_change", current_primary, current_secondary)
        return change

    prev_direction = _direction(previous_primary)
    cur_direction = _direction(current_primary)
    prev_low, prev_high = _zone_bounds(previous_primary)
    cur_low, cur_high = _zone_bounds(current_primary)
    prev_invalidation_raw = (previous_primary or {}).get("invalidation_level", (previous_primary or {}).get("invalidation"))
    prev_invalidation = _safe_float(prev_invalidation_raw)
    prev_bias_polarity = _bias_polarity(previous_bias)
    cur_bias_polarity = _bias_polarity(current_bias)
    cur_zone_label = _zone_label(cur_low, cur_high)
    prev_zone_label = _zone_label(prev_low, prev_high)
    waiting = ", ".join((current_primary or {}).get("trigger_conditions", [])[:3]) or "fresh confirmation"

    change.details.update(
        {
            "previous_direction": prev_direction,
            "current_direction": cur_direction,
            "previous_zone": prev_zone_label,
            "current_zone": cur_zone_label,
            "previous_invalidation": prev_invalidation,
            "current_price": current_price,
        }
    )

    if previous_primary is None:
        change.change_type = "no_material_change"
        change.should_send = False
        change.reason = "no_previous_primary"
        change.dedupe_key = _build_dedupe_key(symbol, "no_material_change", current_primary, current_secondary)
        return change

    # 1. scenario_played_out — TP1 reached / zone consumed.
    if primary_played_out:
        change.change_type = "scenario_played_out"
        change.severity = "medium"
        change.cooldown_minutes = SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES
        change.title = "SPENCER SCENARIO PLAYED OUT - %s" % symbol
        change.message = (
            f"{prev_direction or '?'} scenario at {prev_zone_label} reached its target/played out.\n"
            f"Updating to next plan."
        )
        change.should_send = True
        change.reason = "scenario_played_out"
        change.dedupe_key = _build_dedupe_key(symbol, change.change_type, previous_primary, previous_secondary)
        return change

    # 2. scenario_flipped — primary direction reversed.
    if prev_direction and cur_direction and prev_direction != cur_direction:
        change.change_type = "scenario_flipped"
        change.severity = "high"
        change.cooldown_minutes = SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES
        change.title = "SPENCER SCENARIO FLIPPED - %s" % symbol
        change.message = (
            f"Primary direction flipped from {prev_direction} {prev_zone_label} to "
            f"{cur_direction} {cur_zone_label}.\n"
            f"Reason: structure flipped on the higher timeframes.\n"
            f"Waiting for: {waiting}."
        )
        change.should_send = True
        change.reason = "primary_direction_changed"
        change.dedupe_key = _build_dedupe_key(symbol, change.change_type, current_primary, current_secondary)
        return change

    # 3. structure_reclaim — current price closed above previous SELL zone, or
    #    structure_breakout — current price closed below previous BUY zone.
    if prev_direction == "SELL" and _zone_breached_above(prev_high, current_price):
        change.change_type = "structure_reclaim"
        change.severity = "high"
        change.cooldown_minutes = SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES
        change.title = "SPENCER RECLAIM WATCH - %s" % symbol
        change.message = (
            f"Price has closed above previous resistance at {prev_zone_label}.\n"
            f"Previous sell zone is no longer fresh resistance.\n"
            f"Waiting for retest as support before any BUY confirmation."
        )
        change.should_send = True
        change.reason = "current_price_above_previous_resistance"
        change.details["broken_resistance"] = prev_zone_label
        change.dedupe_key = _build_dedupe_key(symbol, change.change_type, previous_primary, current_secondary)
        return change

    if prev_direction == "BUY" and _zone_breached_below(prev_low, current_price):
        change.change_type = "structure_breakout"
        change.severity = "high"
        change.cooldown_minutes = SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES
        change.title = "SPENCER BREAKOUT WATCH - %s" % symbol
        change.message = (
            f"Price has closed below previous support at {prev_zone_label}.\n"
            f"Previous buy zone is no longer fresh support.\n"
            f"Waiting for retest as resistance before any SELL confirmation."
        )
        change.should_send = True
        change.reason = "current_price_below_previous_support"
        change.details["broken_support"] = prev_zone_label
        change.dedupe_key = _build_dedupe_key(symbol, change.change_type, previous_primary, current_secondary)
        return change

    # 4. scenario_invalidated — invalidation level breached and direction unchanged.
    invalidation_breached, checked_invalidation, invalidation_reason = _validated_invalidation_breached(
        prev_direction,
        prev_invalidation_raw,
        current_price,
        prev_low,
        prev_high,
    )
    change.details["invalidation_check_reason"] = invalidation_reason
    if invalidation_breached:
        change.change_type = "scenario_invalidated"
        change.severity = "high"
        change.cooldown_minutes = SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES
        change.title = "SPENCER SCENARIO INVALIDATED - %s" % symbol
        change.message = (
            f"{prev_direction or '?'} scenario at {prev_zone_label} invalidated.\n"
            f"Reason: invalidation level {checked_invalidation:.2f} breached at price {current_price:.2f}.\n"
            f"Waiting for new structure."
        )
        change.should_send = True
        change.reason = "invalidation_level_breached"
        change.dedupe_key = _build_dedupe_key(symbol, change.change_type, current_primary, current_secondary)
        return change

    # 5. bias_changed — dominant bias polarity flipped.
    if prev_bias_polarity and cur_bias_polarity and prev_bias_polarity != cur_bias_polarity:
        change.change_type = "bias_changed"
        change.severity = "medium"
        change.cooldown_minutes = SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES
        change.title = "SPENCER BIAS UPDATE - %s" % symbol
        change.message = (
            f"Dominant bias flipped from {prev_bias_polarity} ({previous_bias_strength or 'unknown'}) "
            f"to {cur_bias_polarity} ({current_bias_strength or 'unknown'}).\n"
            f"Re-evaluate scenarios; previous primary {prev_direction or '?'} {prev_zone_label} now lower priority."
        )
        change.should_send = True
        change.reason = "bias_polarity_changed"
        change.dedupe_key = _build_dedupe_key(symbol, change.change_type, current_primary, current_secondary)
        return change

    # 6. Same direction — assess zone delta.
    zone_shift = _max_zone_shift(prev_low, prev_high, cur_low, cur_high)
    change.details["zone_shift_pips"] = round(zone_shift, 2)

    if zone_shift <= SCENARIO_NO_CHANGE_ZONE_TOLERANCE_PIPS:
        change.change_type = "no_material_change"
        change.should_send = False
        change.severity = "low"
        change.reason = "zone_shift_within_tolerance"
        change.dedupe_key = _build_dedupe_key(symbol, change.change_type, current_primary, current_secondary)
        return change

    if zone_shift <= SCENARIO_REFINEMENT_ZONE_TOLERANCE_PIPS:
        change.change_type = "scenario_refined"
        change.should_send = True
        change.severity = "low"
        change.cooldown_minutes = SCENARIO_REFINEMENT_COOLDOWN_MINUTES
        change.title = "SPENCER SCENARIO REFINED - %s" % symbol
        change.message = (
            f"Primary {cur_direction.lower() or 'scenario'} zone refined from {prev_zone_label} to {cur_zone_label}.\n"
            f"Reason: structure cluster expanded but the {prev_direction.lower() or 'underlying'} "
            f"scenario remains the same.\n"
            f"Waiting for fresh confirmation."
        )
        change.reason = "zone_refined_within_refinement_band"
        change.dedupe_key = _build_dedupe_key(symbol, change.change_type, current_primary, current_secondary)
        return change

    # Larger zone shift but same direction & invalidation — treat as a
    # structure-level reset of the primary watch zone, not a flip.
    change.change_type = "scenario_invalidated"
    change.severity = "medium"
    change.cooldown_minutes = SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES
    change.title = "SPENCER SCENARIO RESET - %s" % symbol
    change.message = (
        f"{prev_direction or '?'} primary watch zone moved from {prev_zone_label} to {cur_zone_label} "
        f"(>{int(SCENARIO_REFINEMENT_ZONE_TOLERANCE_PIPS)} pips).\n"
        f"Treating previous zone as superseded.\n"
        f"Waiting for new confirmation."
    )
    change.should_send = True
    change.reason = "zone_shift_exceeds_refinement_band"
    change.dedupe_key = _build_dedupe_key(symbol, change.change_type, current_primary, current_secondary)
    return change


def directional_targets(
    level_scores: Iterable[dict],
    *,
    current_price: float,
    primary_direction: str = "",
    max_per_side: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Split scored levels into directional buckets for the market plan alert.

    - Levels strictly below current price → downside_valid_targets
    - Levels strictly above current price → upside_reclaim_targets
    - Levels within MIN_MAIN_TARGET_DISTANCE_PIPS → reaction_levels
    """
    from config.settings import MIN_MAIN_TARGET_DISTANCE_PIPS

    downside: list[dict[str, Any]] = []
    upside: list[dict[str, Any]] = []
    reaction: list[dict[str, Any]] = []
    seen_levels: set[float] = set()
    for raw in level_scores or []:
        if not isinstance(raw, dict):
            continue
        level = _safe_float(raw.get("level"))
        if not level:
            continue
        if level in seen_levels:
            continue
        seen_levels.add(level)
        if str(raw.get("state", "")).lower() == "consumed":
            continue
        recommended = str(raw.get("recommended_use", ""))
        if recommended == "ignore":
            continue
        distance = abs(level - float(current_price or level))
        if distance <= MIN_MAIN_TARGET_DISTANCE_PIPS:
            reaction.append(raw)
            continue
        if level < current_price:
            downside.append(raw)
        elif level > current_price:
            upside.append(raw)

    downside.sort(key=lambda row: (-_safe_float(row.get("score")), abs(_safe_float(row.get("level")) - current_price)))
    upside.sort(key=lambda row: (-_safe_float(row.get("score")), abs(_safe_float(row.get("level")) - current_price)))
    reaction.sort(key=lambda row: (-_safe_float(row.get("score")), abs(_safe_float(row.get("level")) - current_price)))

    return {
        "downside_valid_targets": downside[:max_per_side],
        "upside_reclaim_targets": upside[:max_per_side],
        "reaction_micro_levels": reaction[:max_per_side],
        "primary_direction": str(primary_direction or "").upper(),
    }
