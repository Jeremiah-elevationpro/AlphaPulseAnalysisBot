"""
AlphaPulse - Elite Level Selection
==================================
Final quality gate between raw level detection and lower-timeframe confirmation.

This layer keeps the existing runtime flow intact:
  LevelDetector -> EliteLevelSelector -> ConfirmationEngine -> SignalGenerator

Responsibilities:
  - score detected levels with market-context confluence
  - reject messy chop, weak room-to-target, and levels too close to spot
  - preserve interpretable acceptance/rejection reasons in logs
  - cap the number of monitored levels per timeframe pair
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd

from config.settings import (
    LEVEL_CHOP_LOOKBACK,
    LEVEL_CHOP_MAX_FLIPS,
    LEVEL_CROWDING_PIPS,
    LEVEL_MAX_MONITORED_PER_PAIR,
    LEVEL_MIN_PRICE_DISTANCE_PIPS,
    LEVEL_PAIR_DISTANCE_PENALTY_PER_PIP,
    LEVEL_PAIR_DEEP_REJECT_FLOOR,
    LEVEL_PAIR_MAX_DISTANCE_PIPS,
    LEVEL_PAIR_NEAR_DISTANCE_PIPS,
    LEVEL_PAIR_SCOPE_BONUS,
    LEVEL_PAIR_SOFT_DISTANCE_PIPS,
    LEVEL_PAIR_SOFT_FLOOR,
    LEVEL_PAIR_SOFT_FLOOR_PENALTY_PER_PIP,
    LEVEL_PSYCH_REGION_BONUS,
    LEVEL_PSYCH_REGION_PENALTY,
    LEVEL_PSYCH_REGION_RADIUS_PIPS,
    LEVEL_PSYCH_REGION_TRIGGER_PRICE,
    LEVEL_TP_ROOM_PENALTY_PER_PIP,
    LEVEL_TP_ROOM_TOLERANCE_PIPS,
    SESSION_TP1_MIN_PIPS,
    SESSION_TP1_MARGINAL_BUFFER_PIPS,
    MIN_TP_CLEARANCE_PIPS,
    PIP_SIZE,
    PSYCH_MAJOR_STEP,
    BIAS_CONTINUATION_BONUS,
    BIAS_COUNTER_TREND_PENALTY,
    BIAS_PULLBACK_CONTINUATION_BONUS,
    BIAS_STRONG_BLOCK_COUNTER_TREND,
    BIAS_BLOCK_WEAK_DOMINANT,
    LEVEL_AV_QUALITY_BONUS,
    LEVEL_AV_QUALITY_THRESHOLD,
    AV_STRONG_ORIGIN_BONUS,
    AV_DISPLACEMENT_BONUS,
    AV_QM_CONTEXT_BONUS,
)
from strategies.filters import MarketContext
from strategies.level_detector import LevelInfo
from utils.logger import get_logger

logger = get_logger(__name__)


class EliteLevelSelector:
    """
    Applies final selection rules after raw levels have been detected and scored.
    """

    def select_scope_levels(
        self,
        levels: Sequence[LevelInfo],
        all_structural_levels: Sequence[LevelInfo],
        df_low: pd.DataFrame,
        ctx: MarketContext,
        current_price: float | None,
        higher_tf: str,
        lower_tf: str,
    ) -> List[LevelInfo]:
        selected: List[LevelInfo] = []
        if not levels:
            return selected

        pair_label = f"{higher_tf}->{lower_tf}"
        for level in levels:
            accepted, score, accepted_reasons, rejected_reasons = self._evaluate_level(
                level=level,
                all_structural_levels=all_structural_levels,
                df_low=df_low,
                ctx=ctx,
                current_price=current_price,
                pair_label=pair_label,
            )
            level.selection_score = round(score, 1)
            level.accepted_reasons = accepted_reasons
            level.rejected_reasons = rejected_reasons

            if not accepted:
                logger.info(
                    "LEVEL REJECTED: %s %.2f [%s] | score=%.0f | %s",
                    level.level_type, level.price, pair_label, score,
                    " | ".join(rejected_reasons),
                )
                continue

            logger.info(
                "LEVEL ACCEPTED: %s %.2f [%s] | score=%.0f | %s",
                level.level_type, level.price, pair_label, score,
                " | ".join(accepted_reasons),
            )
            selected.append(level)

        selected.sort(key=lambda lvl: (lvl.selection_score, lvl.quality_score), reverse=True)
        return selected

    def cap_pair_levels(
        self,
        major_levels: Sequence[LevelInfo],
        recent_levels: Sequence[LevelInfo],
        previous_levels: Sequence[LevelInfo],
        max_total: int = LEVEL_MAX_MONITORED_PER_PAIR,
    ) -> Tuple[List[LevelInfo], List[LevelInfo], List[LevelInfo]]:
        combined = list(major_levels) + list(recent_levels) + list(previous_levels)
        if len(combined) <= max_total:
            return list(major_levels), list(recent_levels), list(previous_levels)

        keep_ids = {
            id(level)
            for level in sorted(
                combined,
                key=lambda lvl: (
                    lvl.selection_score,
                    lvl.quality_score,
                    getattr(lvl, "origin_index", -1),
                ),
                reverse=True,
            )[:max_total]
        }

        def _keep(scope_levels: Sequence[LevelInfo]) -> List[LevelInfo]:
            kept = [lvl for lvl in scope_levels if id(lvl) in keep_ids]
            dropped = [lvl for lvl in scope_levels if id(lvl) not in keep_ids]
            for lvl in dropped:
                lvl.rejected_reasons.append("suppressed by pair-level cap")
                logger.info(
                    "LEVEL SUPPRESSED: %s %.2f [%s] | score=%.0f | pair already has stronger monitored levels",
                    lvl.level_type, lvl.price, lvl.timeframe, lvl.selection_score,
                )
            return kept

        return _keep(major_levels), _keep(recent_levels), _keep(previous_levels)

    def _evaluate_level(
        self,
        level: LevelInfo,
        all_structural_levels: Sequence[LevelInfo],
        df_low: pd.DataFrame,
        ctx: MarketContext,
        current_price: float | None,
        pair_label: str,
    ) -> Tuple[bool, float, List[str], List[str]]:
        score = float(level.quality_score)
        accepted: List[str] = []
        rejected: List[str] = []

        if level.accepted_reasons:
            accepted.extend(level.accepted_reasons)

        if current_price is not None:
            anchor_score, anchor_accepts, anchor_rejects = self._price_anchor_score(
                level=level,
                current_price=current_price,
                pair_label=pair_label,
            )
            score += anchor_score
            accepted.extend(anchor_accepts)
            rejected.extend(anchor_rejects)

        chop_reject, chop_note = self._check_chop(level, df_low)
        if chop_reject:
            rejected.append(chop_note)
        elif chop_note:
            score += 4.0
            accepted.append(chop_note)

        room_pips = self._room_to_opposing_level(level, all_structural_levels)
        if room_pips is not None:
            session_name = getattr(ctx, "session_name", "off_session") or "off_session"
            required_tp1 = SESSION_TP1_MIN_PIPS.get(session_name, MIN_TP_CLEARANCE_PIPS)
            marginal_tp1 = max(required_tp1 + SESSION_TP1_MARGINAL_BUFFER_PIPS, required_tp1)
            tp1_epsilon = 0.01
            if room_pips + tp1_epsilon < required_tp1:
                logger.info(
                    "TP1 MIN CHECK: session=%s | required=%.0fp | actual=%.0fp -> REJECT",
                    session_name, required_tp1, room_pips,
                )
                rejected.append(
                    f"insufficient TP1 room ({room_pips:.0f}p < {required_tp1:.0f}p session minimum)"
                )
            elif room_pips < marginal_tp1:
                logger.info(
                    "TP1 MIN CHECK: session=%s | required=%.0fp | actual=%.0fp -> PASS marginal",
                    session_name, required_tp1, room_pips,
                )
                deficit = marginal_tp1 - room_pips
                penalty = deficit * LEVEL_TP_ROOM_PENALTY_PER_PIP
                score -= penalty
                accepted.append(
                    f"marginal opposing-structure clearance ({room_pips:.0f}p; -{penalty:.0f}, session min {required_tp1:.0f}p)"
                )
            elif room_pips >= 60:
                logger.info(
                    "TP1 MIN CHECK: session=%s | required=%.0fp | actual=%.0fp -> PASS",
                    session_name, required_tp1, room_pips,
                )
                score += 5.0
                accepted.append(f"clear opposing-structure room ({room_pips:.0f}p)")
            else:
                logger.info(
                    "TP1 MIN CHECK: session=%s | required=%.0fp | actual=%.0fp -> PASS",
                    session_name, required_tp1, room_pips,
                )
                accepted.append(f"tradable room ({room_pips:.0f}p)")

        crowded_by = self._find_stronger_neighbor(level, all_structural_levels)
        if crowded_by is not None:
            rejected.append(
                f"crowded by stronger nearby level {crowded_by.level_type} {crowded_by.price:.2f}"
            )

        if level.trade_direction:
            dominant_bias = getattr(ctx, "dominant_bias", ctx.h4_bias)
            bias_strength = getattr(ctx, "bias_strength", "weak")
            h1_state = getattr(ctx, "h1_state", "range")
            if (
                BIAS_BLOCK_WEAK_DOMINANT
                and not (dominant_bias in ("bullish", "bearish") and bias_strength in ("moderate", "strong"))
            ):
                rejected.append(
                    f"weak bias ({dominant_bias}/{bias_strength}) not allowed"
                )
            elif bias_strength == "weak" or dominant_bias in ("neutral", "mixed"):
                accepted.append(f"{dominant_bias} {bias_strength} bias allows both directions")
            else:
                aligned = (
                    (dominant_bias == "bullish" and level.trade_direction == "BUY")
                    or (dominant_bias == "bearish" and level.trade_direction == "SELL")
                )
                if aligned:
                    bonus = BIAS_CONTINUATION_BONUS.get(bias_strength, 6)
                    score += bonus
                    accepted.append(
                        f"{dominant_bias} {bias_strength} continuation bias (+{bonus})"
                    )
                    if h1_state == "pullback":
                        score += BIAS_PULLBACK_CONTINUATION_BONUS
                        accepted.append(
                            f"H1 pullback continuation setup (+{BIAS_PULLBACK_CONTINUATION_BONUS})"
                        )
                else:
                    if bias_strength == "strong" and BIAS_STRONG_BLOCK_COUNTER_TREND:
                        accepted.append(
                            f"strong {dominant_bias} counter-trend requires liquidity_sweep_reclaim confirmation"
                        )
                    penalty = BIAS_COUNTER_TREND_PENALTY.get(bias_strength, 12)
                    score -= penalty
                    accepted.append(
                        f"counter to {dominant_bias} {bias_strength} bias (-{penalty})"
                    )

        if level.is_qm:
            score += 6.0
            accepted.append("QM / broken-structure context")

        if level.level_type == "Gap":
            score += 4.0
            accepted.append("clean imbalance overlap")

        if level.level_type in ("A", "V") and level.quality_score >= LEVEL_AV_QUALITY_THRESHOLD:
            av_bonus = LEVEL_AV_QUALITY_BONUS + (2 if level.is_qm else 0)
            score += av_bonus
            accepted.append(f"structural {level.level_type}-level quality bonus (+{av_bonus:.0f})")
            logger.info(
                "A/V BOOST APPLIED: %s %.2f | quality=%d >= %d | +%d | QM=%s",
                level.level_type, level.price, level.quality_score,
                LEVEL_AV_QUALITY_THRESHOLD, av_bonus, level.is_qm,
            )

        if level.level_type in ("A", "V"):
            # Strong reversal-origin wick bonus
            wick_pts = level.quality_breakdown.get("wick", 0)
            if wick_pts >= 16:   # wick ratio >= 4.0 — very clean rejection
                score += AV_STRONG_ORIGIN_BONUS
                accepted.append(f"strong wick origin (+{AV_STRONG_ORIGIN_BONUS})")
                logger.info(
                    "A/V ORIGIN BONUS: %s %.2f | wick_pts=%d | +%d",
                    level.level_type, level.price, wick_pts, AV_STRONG_ORIGIN_BONUS,
                )
            elif wick_pts >= 12:  # wick ratio >= 3.0 — clean rejection
                half = AV_STRONG_ORIGIN_BONUS // 2
                score += half
                accepted.append(f"clean wick origin (+{half})")
            # Displacement bonus — rewards structural levels with real institutional impulse
            if level.displacement_pips >= 60:
                score += AV_DISPLACEMENT_BONUS
                accepted.append(f"strong displacement {level.displacement_pips:.0f}p (+{AV_DISPLACEMENT_BONUS})")
                logger.info(
                    "A/V DISPLACEMENT BONUS: %s %.2f | disp=%.0fp | +%d",
                    level.level_type, level.price, level.displacement_pips, AV_DISPLACEMENT_BONUS,
                )
            elif level.displacement_pips >= 40:
                half = AV_DISPLACEMENT_BONUS // 2
                score += half
                accepted.append(f"moderate displacement {level.displacement_pips:.0f}p (+{half})")
            # QM context bonus for A/V — additional reward on top of the general QM bonus
            if level.is_qm:
                score += AV_QM_CONTEXT_BONUS
                accepted.append(f"A/V broken-structure context (+{AV_QM_CONTEXT_BONUS})")
                logger.info(
                    "A/V QM CONTEXT BONUS: %s %.2f | +%d",
                    level.level_type, level.price, AV_QM_CONTEXT_BONUS,
                )

        if level.is_psychological:
            score += 3.0
            accepted.append(f"{level.psych_strength} psychological confluence")

        if ctx.session_name in ("asia", "london", "new_york", "overlap"):
            score += 3.0
            accepted.append(f"{ctx.session_name} session context")

        accepted = self._dedupe(accepted)
        rejected = self._dedupe(rejected)
        return len(rejected) == 0, score, accepted, rejected

    def _price_anchor_score(
        self,
        level: LevelInfo,
        current_price: float,
        pair_label: str,
    ) -> Tuple[float, List[str], List[str]]:
        """
        Anchor monitored levels to the active trading horizon.

        H4->H1 stays flexible for swing ideas. Intraday pairs receive hard
        floors, near-price bonuses, distance penalties, and psychological-zone
        preference when price trades around/above the active major round number.
        """
        score = 0.0
        accepted: List[str] = []
        rejected: List[str] = []
        distance_pips = abs(level.price - current_price) / PIP_SIZE

        deep_floor = LEVEL_PAIR_DEEP_REJECT_FLOOR.get(pair_label)
        if deep_floor is not None and level.price < deep_floor:
            rejected.append(
                f"too deep for {pair_label} intraday anchor ({level.price:.2f} < {deep_floor:.2f})"
            )
            return score, accepted, rejected

        soft_floor = LEVEL_PAIR_SOFT_FLOOR.get(pair_label)
        if soft_floor is not None and level.price < soft_floor:
            floor_deficit = (soft_floor - level.price) / PIP_SIZE
            penalty_rate = LEVEL_PAIR_SOFT_FLOOR_PENALTY_PER_PIP.get(pair_label, 0.0)
            penalty = floor_deficit * penalty_rate
            score -= penalty
            accepted.append(
                f"below soft intraday anchor {soft_floor:.0f} (-{penalty:.0f}, still monitorable)"
            )

        if distance_pips < LEVEL_MIN_PRICE_DISTANCE_PIPS:
            rejected.append(
                f"too close to current price ({distance_pips:.1f}p < {LEVEL_MIN_PRICE_DISTANCE_PIPS}p)"
            )
            return score, accepted, rejected

        max_distance = LEVEL_PAIR_MAX_DISTANCE_PIPS.get(pair_label, 0)
        if max_distance and distance_pips > max_distance:
            rejected.append(
                f"too far for {pair_label} horizon ({distance_pips:.0f}p > {max_distance}p)"
            )
            return score, accepted, rejected

        near_distance = LEVEL_PAIR_NEAR_DISTANCE_PIPS.get(pair_label, 80)
        soft_distance = LEVEL_PAIR_SOFT_DISTANCE_PIPS.get(pair_label, near_distance * 2)

        if distance_pips <= near_distance:
            score += 10.0
            accepted.append(f"near current price ({distance_pips:.0f}p)")
        elif distance_pips <= soft_distance:
            score += 4.0
            accepted.append(f"within {pair_label} working range ({distance_pips:.0f}p)")
        else:
            penalty_rate = LEVEL_PAIR_DISTANCE_PENALTY_PER_PIP.get(pair_label, 0.1)
            penalty = (distance_pips - soft_distance) * penalty_rate
            score -= penalty
            accepted.append(f"distance penalty -{penalty:.0f} ({distance_pips:.0f}p away)")

        scope_bonus = LEVEL_PAIR_SCOPE_BONUS.get(pair_label, {}).get(level.scope, 0)
        if scope_bonus:
            score += scope_bonus
            if scope_bonus > 0:
                accepted.append(f"{level.scope} scope preferred for {pair_label}")
            else:
                accepted.append(f"{level.scope} scope de-prioritised for {pair_label}")

        psych_anchor = self._current_psych_anchor(current_price)
        if psych_anchor is not None:
            psych_dist = abs(level.price - psych_anchor) / PIP_SIZE
            if psych_dist <= LEVEL_PSYCH_REGION_RADIUS_PIPS:
                bonus = LEVEL_PSYCH_REGION_BONUS.get(pair_label, 0)
                if bonus:
                    score += bonus
                    accepted.append(
                        f"current psych region {psych_anchor:.0f} confluence (+{bonus})"
                    )
            else:
                penalty = LEVEL_PSYCH_REGION_PENALTY.get(pair_label, 0)
                if penalty:
                    score -= penalty
                    accepted.append(
                        f"outside current psych region {psych_anchor:.0f} (-{penalty})"
                    )

        return score, accepted, rejected

    @staticmethod
    def _current_psych_anchor(current_price: float) -> float | None:
        if current_price < LEVEL_PSYCH_REGION_TRIGGER_PRICE:
            return None
        return round(current_price / PSYCH_MAJOR_STEP) * PSYCH_MAJOR_STEP

    @staticmethod
    def _dedupe(items: Iterable[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _check_chop(self, level: LevelInfo, df_low: pd.DataFrame) -> Tuple[bool, str]:
        if df_low is None or len(df_low) < 8:
            return False, ""

        window = df_low.iloc[-min(len(df_low), LEVEL_CHOP_LOOKBACK):]
        opens = window["open"].values.astype(float)
        closes = window["close"].values.astype(float)
        highs = window["high"].values.astype(float)
        lows = window["low"].values.astype(float)

        near_mask = (
            (highs >= level.price - LEVEL_CROWDING_PIPS * PIP_SIZE)
            & (lows <= level.price + LEVEL_CROWDING_PIPS * PIP_SIZE)
        )
        if int(np.sum(near_mask)) < 5:
            return False, "not sitting in recent chop"

        relevant = window[near_mask]
        if len(relevant) < 5:
            return False, "not sitting in recent chop"

        dirs = np.sign((relevant["close"] - relevant["open"]).values.astype(float))
        flips = int(np.sum(dirs[1:] * dirs[:-1] < 0))
        if flips > LEVEL_CHOP_MAX_FLIPS:
            return True, f"inside messy chop ({flips} body flips near level)"
        return False, f"chop controlled ({flips} flips)"

    def _room_to_opposing_level(
        self,
        level: LevelInfo,
        levels: Sequence[LevelInfo],
    ) -> float | None:
        direction = level.trade_direction or ("SELL" if level.level_type == "A" else "BUY")
        if direction == "SELL":
            opposing = [
                other for other in levels
                if other is not level
                and (other.trade_direction or ("BUY" if other.level_type == "V" else "SELL")) == "BUY"
                and other.price < level.price
            ]
            if not opposing:
                return None
            nearest = max(opposing, key=lambda other: other.price)
            return (level.price - nearest.price) / PIP_SIZE

        opposing = [
            other for other in levels
            if other is not level
            and (other.trade_direction or ("BUY" if other.level_type == "V" else "SELL")) == "SELL"
            and other.price > level.price
        ]
        if not opposing:
            return None
        nearest = min(opposing, key=lambda other: other.price)
        return (nearest.price - level.price) / PIP_SIZE

    def _find_stronger_neighbor(
        self,
        level: LevelInfo,
        levels: Sequence[LevelInfo],
    ) -> LevelInfo | None:
        tol = LEVEL_CROWDING_PIPS * PIP_SIZE
        for other in levels:
            if other is level:
                continue
            if abs(other.price - level.price) > tol:
                continue
            other_dir = other.trade_direction or ("SELL" if other.level_type == "A" else "BUY")
            level_dir = level.trade_direction or ("SELL" if level.level_type == "A" else "BUY")
            if other_dir != level_dir:
                continue
            if other.quality_score > level.quality_score + 5:
                return other
        return None
