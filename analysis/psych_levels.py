from __future__ import annotations

from dataclasses import dataclass
from typing import List

from utils.logger import get_logger

logger = get_logger(__name__)

# Only include psychological levels within this many points of current price.
# At 300 pips on XAUUSD this covers ±$300 — more than enough nearby structure.
PSYCH_LEVEL_WINDOW_PIPS: float = 300.0


@dataclass
class PsychologicalLevel:
    level: float
    kind: str
    strength: str


def generate_psychological_levels(
    current_price: float,
    window_pips: float = PSYCH_LEVEL_WINDOW_PIPS,
) -> List[PsychologicalLevel]:
    """
    Generate Gold psychological levels within a window around current price.

    Major: every 100 pts
    Mid:   every 50 pts
    Minor: every 20 pts

    All levels outside current_price ± window_pips are excluded.
    """
    if current_price <= 0:
        return []

    lo = current_price - window_pips
    hi = current_price + window_pips

    # Anchors for iteration
    base_major = int(current_price // 100) * 100

    levels: dict[float, PsychologicalLevel] = {}

    def add(level: float, kind: str, strength: str) -> None:
        rounded = round(level, 2)
        if lo <= rounded <= hi and rounded not in levels:
            levels[rounded] = PsychologicalLevel(level=rounded, kind=kind, strength=strength)

    # Major every 100 — span enough to cover the window
    steps = int(window_pips // 100) + 2
    for step in range(-steps, steps + 1):
        add(base_major + step * 100, "major", "high")

    # Mid every 50 (skip if already major)
    for step in range(-steps * 2, steps * 2 + 1):
        candidate = base_major + step * 50
        if candidate % 100 != 0:
            add(candidate, "mid", "medium")

    # Minor every 20 (skip majors and mids)
    minor_base = int(current_price // 20) * 20
    minor_steps = int(window_pips // 20) + 2
    for step in range(-minor_steps, minor_steps + 1):
        candidate = minor_base + step * 20
        if candidate % 50 != 0 and candidate % 100 != 0:
            add(candidate, "minor", "light")

    result = sorted(levels.values(), key=lambda item: item.level)
    logger.info(
        "PSYCH LEVELS GENERATED: current=%.2f window=%.0f levels=%s",
        current_price,
        window_pips,
        [lvl.level for lvl in result],
    )
    return result


def nearest_psychological_levels(
    current_price: float,
    window_pips: float = PSYCH_LEVEL_WINDOW_PIPS,
    n_each_side: int = 6,
) -> dict[str, list[float]]:
    """Return nearby psychological levels split into below/above/all."""
    levels = generate_psychological_levels(current_price, window_pips=window_pips)
    below = [lvl.level for lvl in levels if lvl.level <= current_price]
    above = [lvl.level for lvl in levels if lvl.level >= current_price]
    return {
        "below": below[-n_each_side:],
        "above": above[:n_each_side],
        "all": [lvl.level for lvl in levels],
    }


def select_actionable_psych_levels(
    current_price: float,
    levels: list[float],
    key_supports: list[float],
    key_resistances: list[float],
    max_count: int = 12,
) -> list[float]:
    """
    Select the nearby, actionable psychological levels for Gold plan display.
    """
    if not levels:
        return []

    sorted_levels = sorted(set(round(float(v), 2) for v in levels))
    below = [lvl for lvl in sorted_levels if lvl < current_price]
    above = [lvl for lvl in sorted_levels if lvl > current_price]

    selected: set[float] = set()
    priority: dict[float, int] = {}

    def mark(level: float, score: int) -> None:
        rounded = round(float(level), 2)
        selected.add(rounded)
        priority[rounded] = max(priority.get(rounded, 0), score)

    # nearest actionable ladder around price
    for lvl in below[-4:]:
        mark(lvl, 2)
    for lvl in above[:4]:
        mark(lvl, 2)

    # always keep nearest major / mid anchor around current price
    anchors = []
    major_floor = int(current_price // 100) * 100
    anchors.extend([major_floor, major_floor + 50, major_floor + 100])
    anchors.extend([major_floor + 20, major_floor + 40, major_floor + 80])
    for anchor in anchors:
        nearest = min(sorted_levels, key=lambda lvl: abs(lvl - anchor))
        mark(nearest, 4 if nearest % 50 == 0 else 3)

    # include psych levels nearest to key supports/resistances
    for reference in list(key_supports or []) + list(key_resistances or []):
        nearest = min(sorted_levels, key=lambda lvl: abs(lvl - float(reference)))
        mark(nearest, 5 if nearest % 50 == 0 else 4)

    final_levels = sorted(selected)
    if len(final_levels) > max_count:
        final_levels = sorted(
            final_levels,
            key=lambda lvl: (
                -priority.get(lvl, 0),
                0 if lvl % 50 == 0 else 1,
                abs(lvl - current_price),
                lvl,
            ),
        )
        final_levels = sorted(final_levels[:max_count])

    logger.info(
        "ACTIONABLE PSYCH LEVELS SELECTED: current=%.2f levels=%s",
        current_price,
        final_levels,
    )
    return final_levels
