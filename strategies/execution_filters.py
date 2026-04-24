"""
AlphaPulse - Institutional Execution Filters
============================================
Weighted filter layer for the active M30->M15 Gap strategy:
  - H1 premium/discount location
  - D1/H4 directional bias alignment
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd

from config.settings import (
    EXECUTION_FILTERS_ENABLED,
    HTF_SWEEP_LOOKBACK,
    HTF_SWEEP_RECENT_BARS,
    HTF_SWEEP_MIN_PIPS,
    PD_EQUILIBRIUM_BAND_PIPS,
    PD_EQUILIBRIUM_SCORE,
    PD_FAVORABLE_SCORE,
    PD_FILTER_ENABLED,
    PD_OPPOSITE_PENALTY,
    PD_RANGE_LOOKBACK,
    PIP_SIZE,
    BIAS_MIXED_PENALTY,
    BIAS_MODERATE_ALIGNED_SCORE,
    BIAS_STRONG_ALIGNED_SCORE,
    STRONG_BIAS_GATE_ENABLED,
    STRONG_BIAS_GATE_REQUIRE_STRONG,
)
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExecutionFilterResult:
    passed: bool = True
    reason: str = ""
    h1_liquidity_sweep: bool = False
    h1_sweep_direction: str = "none"  # bullish | bearish | none
    h1_reclaim_confirmed: bool = False
    pd_location: str = "unknown"      # premium | discount | equilibrium | unknown
    pd_filter_score: float = 0.0
    pd_bonus: float = 0.0
    bias_gate_result: str = "not_checked"
    bias_gate_score: float = 0.0
    sweep_filter_score: float = 0.0


class ExecutionFilterEngine:
    """Applies H1 sweep/reclaim, premium/discount, and strong bias gates."""

    def evaluate(self, data: Dict[str, pd.DataFrame], setup, ctx) -> ExecutionFilterResult:
        result = ExecutionFilterResult()
        if not EXECUTION_FILTERS_ENABLED:
            result.reason = "execution filters disabled"
            result.bias_gate_result = "disabled"
            return result

        if STRONG_BIAS_GATE_ENABLED:
            bias_ok, bias_result, bias_score = self._bias_gate(setup, ctx)
            result.bias_gate_result = bias_result
            result.bias_gate_score = bias_score
            result.pd_filter_score += bias_score
            if not bias_ok:
                result.passed = False
                result.reason = bias_result
                return result

        h1 = data.get("H1")
        result.sweep_filter_score = 0.0

        if PD_FILTER_ENABLED:
            pd_location, pd_score = self._premium_discount(h1, setup)
            result.pd_location = pd_location
            result.pd_bonus = pd_score
            result.pd_filter_score += pd_score
            favorable = (
                (setup.direction == "BUY" and pd_location == "discount")
                or (setup.direction == "SELL" and pd_location == "premium")
            )
            neutral = pd_location == "equilibrium"
            if favorable:
                logger.info(
                    "EXECUTION FILTER BONUS: PD favorable | %s in %s | +%.0f",
                    setup.direction, pd_location, pd_score,
                )
            elif neutral:
                logger.info(
                    "EXECUTION FILTER PENALTY: PD equilibrium | %s near midpoint | %.0f",
                    setup.direction, pd_score,
                )
            else:
                logger.info(
                    "EXECUTION FILTER PENALTY: PD opposite | %s in %s | %.0f",
                    setup.direction, pd_location, pd_score,
                )

        return result

    @staticmethod
    def _bias_gate(setup, ctx) -> tuple[bool, str, float]:
        dominant = getattr(ctx, "dominant_bias", "neutral")
        strength = getattr(ctx, "bias_strength", "weak")
        d1 = getattr(ctx, "d1_bias", "neutral")
        h4 = getattr(ctx, "h4_bias", "neutral")
        expected = "bullish" if setup.direction == "BUY" else "bearish"

        if dominant in ("mixed", "neutral") or strength == "weak":
            logger.info(
                "EXECUTION FILTER REJECT: directional bias not tradeable | %s/%s requires moderate or strong",
                dominant, strength,
            )
            return False, f"rejected_{dominant}_{strength}", 0.0
        if STRONG_BIAS_GATE_REQUIRE_STRONG and strength != "strong":
            logger.info(
                "EXECUTION FILTER PENALTY: %s %s below strong threshold | %.0f",
                dominant, strength, -BIAS_MIXED_PENALTY,
            )
            return True, f"penalized_{dominant}_{strength}", -BIAS_MIXED_PENALTY
        if dominant != expected:
            micro_type = getattr(setup, "micro_confirmation_type", "none") or "none"
            if micro_type == "liquidity_sweep_reclaim":
                logger.info(
                    "EXECUTION FILTER PASS: counter-trend allowed by liquidity_sweep_reclaim | %s setup vs %s %s",
                    setup.direction,
                    dominant,
                    strength,
                )
                return True, f"passed_counter_{dominant}_{strength}_liquidity_sweep_reclaim", 0.0
            logger.info(
                "EXECUTION FILTER REJECT: counter-trend requires liquidity_sweep_reclaim | %s setup vs %s %s | micro=%s",
                setup.direction,
                dominant,
                strength,
                micro_type,
            )
            return False, f"rejected_counter_{dominant}_{strength}", 0.0
        if d1 in ("bullish", "bearish") and h4 in ("bullish", "bearish") and d1 != h4:
            logger.info(
                "EXECUTION FILTER PENALTY: D1/H4 mismatch | %s/%s | %.0f (not a hard reject)",
                d1, h4, -BIAS_MIXED_PENALTY,
            )
            return True, "penalized_d1_h4_mismatch", -BIAS_MIXED_PENALTY

        score = BIAS_STRONG_ALIGNED_SCORE if strength == "strong" else BIAS_MODERATE_ALIGNED_SCORE
        logger.info(
            "EXECUTION FILTER BONUS: bias aligned | %s %s | +%.0f",
            dominant, strength, score,
        )
        return True, f"passed_{dominant}_{strength}", score

    @staticmethod
    def _h1_sweep_reclaim(df_h1: pd.DataFrame | None) -> Dict:
        empty = {"passed": False, "direction": "none", "reclaim": False}
        if df_h1 is None or len(df_h1) < HTF_SWEEP_LOOKBACK + HTF_SWEEP_RECENT_BARS + 2:
            return empty

        recent_count = max(1, HTF_SWEEP_RECENT_BARS)
        recent = df_h1.iloc[-recent_count:]
        prior = df_h1.iloc[-(HTF_SWEEP_LOOKBACK + recent_count):-recent_count]
        if prior.empty or recent.empty:
            return empty

        prior_low = float(prior["low"].min())
        prior_high = float(prior["high"].max())
        min_sweep = HTF_SWEEP_MIN_PIPS * PIP_SIZE

        bullish_sweeps = recent[
            (recent["low"].astype(float) <= prior_low - min_sweep)
            & (recent["close"].astype(float) > prior_low)
        ]
        if not bullish_sweeps.empty:
            return {"passed": True, "direction": "bullish", "reclaim": True}

        bearish_sweeps = recent[
            (recent["high"].astype(float) >= prior_high + min_sweep)
            & (recent["close"].astype(float) < prior_high)
        ]
        if not bearish_sweeps.empty:
            return {"passed": True, "direction": "bearish", "reclaim": True}

        return empty

    @staticmethod
    def _premium_discount(df_h1: pd.DataFrame | None, setup) -> tuple[str, float]:
        if df_h1 is None or len(df_h1) < max(10, PD_RANGE_LOOKBACK):
            return "unknown", 0.0

        window = df_h1.iloc[-PD_RANGE_LOOKBACK:]
        high = float(window["high"].max())
        low = float(window["low"].min())
        if high <= low:
            return "unknown", 0.0

        midpoint = (high + low) / 2.0
        band = PD_EQUILIBRIUM_BAND_PIPS * PIP_SIZE
        price = float(getattr(setup.confirmation, "entry_price", setup.level.price))

        if abs(price - midpoint) <= band:
            return "equilibrium", PD_EQUILIBRIUM_SCORE
        if price < midpoint:
            location = "discount"
        else:
            location = "premium"

        favorable = (
            (setup.direction == "BUY" and location == "discount")
            or (setup.direction == "SELL" and location == "premium")
        )
        return location, PD_FAVORABLE_SCORE if favorable else -PD_OPPOSITE_PENALTY
