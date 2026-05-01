from __future__ import annotations

from config.settings import (
    XAUUSD_SL_BUFFER_PIPS_DEFAULT,
    XAUUSD_SL_BUFFER_PIPS_MAX,
    XAUUSD_SL_BUFFER_PIPS_MIN,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def validate_sl_direction(direction: str, entry: float, sl: float) -> tuple[bool, str]:
    direction = direction.upper()
    if direction == "BUY" and sl >= entry:
        logger.warning("SL VALIDATION FAILED: BUY sl_above_entry")
        return False, "invalid_sl_direction"
    if direction == "SELL" and sl <= entry:
        logger.warning("SL VALIDATION FAILED: SELL sl_below_entry")
        return False, "invalid_sl_direction"
    return True, ""


def build_structure_sl(
    direction: str,
    zone_low: float,
    zone_high: float,
    invalidation_level: float,
    *,
    entry: float | None = None,
    structural_level: float | None = None,
    invalidation_reason: str = "",
    volatility_buffer: float | None = None,
) -> dict[str, object]:
    direction = direction.upper()
    buffer_pips = float(volatility_buffer if volatility_buffer is not None else XAUUSD_SL_BUFFER_PIPS_DEFAULT)
    buffer_pips = max(XAUUSD_SL_BUFFER_PIPS_MIN, min(XAUUSD_SL_BUFFER_PIPS_MAX, buffer_pips))

    if direction == "SELL":
        anchor = max(float(zone_high), float(invalidation_level), float(structural_level or zone_high))
        sl = round(anchor + buffer_pips, 2)
        if entry is not None and sl <= float(entry):
            sl = round(max(anchor, float(entry)) + buffer_pips, 2)
        source = "watch_zone_high/invalidation"
        rationale = (
            f"SL placed above {anchor:.2f} resistance/invalidation with {buffer_pips:.0f}-pip buffer."
        )
        reason = invalidation_reason or f"Reclaim above {anchor:.2f} invalidates the sell idea."
    else:
        anchor = min(float(zone_low), float(invalidation_level), float(structural_level or zone_low))
        sl = round(anchor - buffer_pips, 2)
        if entry is not None and sl >= float(entry):
            sl = round(min(anchor, float(entry)) - buffer_pips, 2)
        source = "watch_zone_low/invalidation"
        rationale = (
            f"SL placed below {anchor:.2f} support/invalidation with {buffer_pips:.0f}-pip buffer."
        )
        reason = invalidation_reason or f"Close below {anchor:.2f} invalidates the buy idea."

    valid, sl_reason = validate_sl_direction(direction, float(entry or 0.0), sl) if entry is not None else (True, "")
    if not valid and entry is not None:
        if direction == "SELL":
            sl = round(float(entry) + buffer_pips, 2)
        else:
            sl = round(float(entry) - buffer_pips, 2)
        valid, sl_reason = validate_sl_direction(direction, float(entry), sl)
    return {
        "sl": round(sl, 2),
        "sl_source": "structure_sl_engine",
        "sl_anchor": round(anchor, 2),
        "invalidation_level": round(anchor, 2),
        "invalidation_reason": reason,
        "sl_rationale": rationale,
        "sl_valid": valid,
        "sl_validation_reason": sl_reason,
        "sl_source_detail": source,
    }
