from __future__ import annotations

from typing import Optional

STRATEGY_ALIASES = {
    "gap_sweep": "gap_liquidity_sweep_reclaim",
    "gap_liquidity_sweep_reclaim": "gap_liquidity_sweep_reclaim",
    "engulfing": "engulfing_rejection",
    "engulfing_rejection": "engulfing_rejection",
    "break_retest": "standard_break_retest",
    "standard_break_retest": "standard_break_retest",
    "failed_engulf": "failed_engulf_break_retest",
    "failed_engulf_break_retest": "failed_engulf_break_retest",
}

STRATEGY_DISPLAY_NAMES = {
    "gap_liquidity_sweep_reclaim": "Gap Sweep",
    "engulfing_rejection": "Engulfing Rejection",
    "standard_break_retest": "Break + Retest",
    "failed_engulf_break_retest": "Failed Engulf Break + Retest",
}


def canonical_strategy_type(value: Optional[str], default: str = "gap_liquidity_sweep_reclaim") -> str:
    raw = (value or "").strip()
    if not raw:
        return default
    return STRATEGY_ALIASES.get(raw, raw)


def strategy_display_name(value: Optional[str], default: str = "Gap Sweep") -> str:
    canonical = canonical_strategy_type(value)
    return STRATEGY_DISPLAY_NAMES.get(canonical, canonical.replace("_", " ").title() if canonical else default)
