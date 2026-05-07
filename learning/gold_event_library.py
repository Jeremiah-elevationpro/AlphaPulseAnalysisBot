from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class GoldEventPattern:
    event_name: str
    conditions: list[str]
    bullish_example: str
    bearish_example: str
    confirmation_required: list[str]
    invalidation: str
    target_logic: str
    risk_notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


GOLD_EVENT_LIBRARY: dict[str, GoldEventPattern] = {
    "psychological_break_continuation": GoldEventPattern(
        event_name="psychological_break_continuation",
        conditions=["clean close through major psych level", "follow-through candle", "retest holds/breaks cleanly"],
        bullish_example="Gold closes above 4600, holds, then expands toward 4650/4700.",
        bearish_example="Gold closes below 4600, retests from underneath, then expands toward 4550/4500.",
        confirmation_required=["break_retest_close_confirmation", "displacement_confirmation"],
        invalidation="Fast reclaim back through the broken psychological level.",
        target_logic="Target next 20/50/100-point psychological ladder and nearby H1/H4 structure.",
        risk_notes="Avoid late chase entries after TP1 path already started.",
    ),
    "psychological_reclaim_retest": GoldEventPattern(
        event_name="psychological_reclaim_retest",
        conditions=["level reclaimed with close", "retest holds", "structure shifts with bias"],
        bullish_example="4600 reclaimed, retested as support, then push to 4640/4650.",
        bearish_example="4600 reclaimed as resistance after fakeout above, then push to 4550.",
        confirmation_required=["sweep_reclaim_confirmation", "break_retest_close_confirmation"],
        invalidation="Immediate failure back through reclaim level.",
        target_logic="Use reclaim level as pivot and map targets to next major structure.",
        risk_notes="Require close confirmation, not wick-only reclaim, in live mode.",
    ),
    "failed_retest_continuation": GoldEventPattern(
        event_name="failed_retest_continuation",
        conditions=["prior break exists", "pullback fails to close back through level", "close away confirms"],
        bullish_example="Break above resistance, retest stalls, bullish close continues higher.",
        bearish_example="Break below support, pullback stalls, bearish close continues lower.",
        confirmation_required=["failed_retest_confirmation", "displacement_confirmation"],
        invalidation="Strong close back through the retest level.",
        target_logic="TPs follow structure continuation path and next psych ladder.",
        risk_notes="Messy retests are common; wait for decisive close away.",
    ),
    "sweep_reclaim_reversal": GoldEventPattern(
        event_name="sweep_reclaim_reversal",
        conditions=["liquidity sweep through key level", "close back inside", "no immediate invalidation"],
        bullish_example="Sweep below support, reclaim, then break minor high.",
        bearish_example="Sweep above resistance, reclaim below, then break minor low.",
        confirmation_required=["sweep_reclaim_confirmation", "structure_shift_confirmation"],
        invalidation="Second close through the sweep extreme.",
        target_logic="TP1 at nearest internal structure, then major psych/HTF levels.",
        risk_notes="Best when aligned with H4/H1 bias or clear reversal structure shift.",
    ),
    "displacement_from_key_level": GoldEventPattern(
        event_name="displacement_from_key_level",
        conditions=["large body relative to recent candles", "close near extreme", "starts at key level"],
        bullish_example="Strong bullish impulse out of support/discount zone.",
        bearish_example="Strong bearish impulse out of resistance/premium zone.",
        confirmation_required=["displacement_confirmation"],
        invalidation="Impulse fully retraced back through origin zone.",
        target_logic="Project to next structure shelf / imbalance target / psych level.",
        risk_notes="Reject random mid-range displacement away from key levels.",
    ),
    "break_retest_close_continuation": GoldEventPattern(
        event_name="break_retest_close_continuation",
        conditions=["clean structural break", "retest", "confirmation close in breakout direction"],
        bullish_example="Close above resistance, hold retest, bullish close to continue.",
        bearish_example="Close below support, retest resistance, bearish close to continue.",
        confirmation_required=["break_retest_close_confirmation"],
        invalidation="Retest closes back on wrong side of level.",
        target_logic="TP ladder follows next H1/H4 structural objectives.",
        risk_notes="Approved live confirmation for standard BRT uses close confirmation only.",
    ),
    "engulfing_at_key_level": GoldEventPattern(
        event_name="engulfing_at_key_level",
        conditions=["engulf occurs at support/resistance/watch zone", "engulf close is strong", "prefer structure break"],
        bullish_example="Bullish engulf at support followed by minor structure break.",
        bearish_example="Bearish engulf at resistance followed by minor structure break.",
        confirmation_required=["engulfing_level_confirmation"],
        invalidation="Immediate close through opposite side of engulf zone.",
        target_logic="Use engulf zone invalidation and target next structure/psych levels.",
        risk_notes="Ignore engulfing in the middle of nowhere.",
    ),
    "structure_shift_reversal": GoldEventPattern(
        event_name="structure_shift_reversal",
        conditions=["previous trend broken", "key swing reclaimed/lost", "retest or strong hold confirms"],
        bullish_example="Lower high broken after reclaiming support, flipping bearish story to bullish.",
        bearish_example="Higher low broken after losing support, flipping bullish story to bearish.",
        confirmation_required=["structure_shift_confirmation", "break_retest_close_confirmation"],
        invalidation="Structure break fails and price returns through reclaimed pivot.",
        target_logic="Reset scenario and target next HTF structure path in new direction.",
        risk_notes="Use to flip scenario, not to chase an already extended move.",
    ),
}


def get_gold_event_library() -> dict[str, dict[str, Any]]:
    return {name: pattern.to_dict() for name, pattern in GOLD_EVENT_LIBRARY.items()}
