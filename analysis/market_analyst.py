from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from analysis.psych_levels import generate_psychological_levels, select_actionable_psych_levels
from analysis.level_intelligence import LevelIntelligenceEngine
from analysis.session_liquidity import SessionLiquidityEngine
from analysis.tp_engine import (
    filter_market_plan_targets,
    MIN_TP1_DISTANCE_PIPS,
    PREFERRED_SCENARIO_TP_DISTANCE_PIPS,
    PREFERRED_TP1_DISTANCE_PIPS,
)
from config.settings import (
    ACTIVE_SCENARIO_IDEAL_MAX_DISTANCE_PIPS,
    ACTIVE_SCENARIO_IDEAL_MIN_DISTANCE_PIPS,
    ACTIVE_SCENARIO_MAX_DISTANCE_PIPS,
    DEEP_CONTEXT_DISTANCE_PIPS,
    MIN_TP_LEVEL_SCORE,
)
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Scenario:
    direction: str
    reason: str
    watch_zone: str
    watch_low: float
    watch_high: float
    trigger_conditions: list[str]
    invalidation: str
    targets: list[float]
    market_plan_targets: list[float]
    confidence: str
    matching_confirmation_types: list[str]
    scenario_status: str = "fresh"
    played_out_note: str = ""
    scenario_type: str = "primary"
    invalidation_level: float = 0.0
    active_distance_pips: float = 0.0
    role: str = "active_intraday"
    confluence: list[str] = field(default_factory=list)


@dataclass
class AnalystMarketPlan:
    symbol: str
    current_price: float
    timestamp: str
    h4_context: str
    h1_context: str
    m15_context: str
    dominant_bias: str
    bias_strength: str
    market_structure: str
    market_structure_state: str
    key_supports: list[float]
    key_resistances: list[float]
    actionable_psych_levels: list[float]
    psychological_levels: list[float]
    liquidity_zones: list[float]
    broken_levels: list[float]
    retest_zones: list[dict[str, Any]]
    active_watch_zones: list[dict[str, Any]]
    primary_scenario: dict[str, Any]
    secondary_scenario: dict[str, Any]
    invalidation_levels: list[float]
    confirmation_triggers: list[str]
    structure_targets: list[float]
    recommended_strategy_focus: list[str]
    confirmation_waiting_for: list[str]
    confidence_score: float
    market_plan_signature: str
    plan_status: str
    market_plan_text: str
    level_intelligence: dict[str, Any]
    deep_context_levels: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    activation_pipeline: dict[str, Any] = field(default_factory=dict)
    session_liquidity: dict[str, Any] = field(default_factory=dict)
    targets_source: str = "structure_tp_engine"
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MarketPlan = AnalystMarketPlan


class MarketAnalyst:
    def analyze(self, symbol: str, data: dict[str, pd.DataFrame], current_price: float, context=None) -> AnalystMarketPlan:
        now = datetime.now(timezone.utc).isoformat()
        h4 = data.get("H4")
        h1 = data.get("H1")
        m15 = data.get("M15")
        if h4 is None or h1 is None or m15 is None or h4.empty or h1.empty or m15.empty:
            raise ValueError("Missing H4/H1/M15 data for market analysis")

        psych = generate_psychological_levels(current_price)
        psych_prices = [lvl.level for lvl in psych]

        h4_state = self._structure_bias(h4)
        h1_state = self._structure_bias(h1)
        m15_state = self._execution_state(m15)

        dominant_bias, bias_strength = self._combine_bias(h4_state["bias"], h1_state["bias"], context)
        supports = self._key_levels(h1, kind="support", current_price=current_price, psych=psych_prices)
        resistances = self._key_levels(h1, kind="resistance", current_price=current_price, psych=psych_prices)
        level_engine = LevelIntelligenceEngine()
        session_name = str(getattr(context, "session_name", "") if context else "")
        level_scores = level_engine.score_levels(
            symbol,
            list(dict.fromkeys(supports + resistances + psych_prices)),
            current_price=current_price,
            data=data,
            supports=supports,
            resistances=resistances,
            session_name=session_name,
        )
        level_intelligence = level_engine.build_market_plan_summary(level_scores, current_price)
        try:
            level_engine.persist_scores(symbol, level_scores, session_name=session_name, timeframe="M15")
        except Exception as exc:
            logger.debug("LEVEL MEMORY PERSIST SKIPPED: %s", exc)
        supports = sorted([
            float(item["level"]) for item in level_intelligence.get("level_scores", [])
            if float(item.get("level", 0.0)) < current_price
            and float(item.get("score", 0.0)) >= 60
            and item.get("state") != "consumed"
            and item.get("recommended_use") != "ignore"
        ] or supports)
        resistances = sorted([
            float(item["level"]) for item in level_intelligence.get("level_scores", [])
            if float(item.get("level", 0.0)) > current_price
            and float(item.get("score", 0.0)) >= 60
            and item.get("state") != "consumed"
            and item.get("recommended_use") != "ignore"
        ] or resistances)
        broken_levels = self._broken_levels(current_price, psych_prices, h1)
        liquidity_zones = sorted(set(supports[:2] + resistances[:2] + broken_levels[:2]))
        deep_context_levels = self._classify_deep_context(level_intelligence, current_price)

        session_liquidity_summary: dict[str, Any] = {}
        try:
            session_engine = SessionLiquidityEngine()
            session_liquidity_summary = session_engine.build_market_plan_summary(
                m15,
                current_price=current_price,
                h1_supports=supports,
                h1_resistances=resistances,
                psych_levels=psych_prices,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("SESSION LIQUIDITY SKIPPED: %s", exc)

        primary = self._build_primary_scenario(current_price, dominant_bias, supports, resistances, psych_prices, h4_state, h1_state, level_scores=level_intelligence, session_liquidity=session_liquidity_summary)
        secondary = self._build_secondary_scenario(current_price, dominant_bias, supports, resistances, psych_prices, h4_state, h1_state, primary=primary, level_scores=level_intelligence)
        activation_pipeline = self._build_activation_pipeline(
            primary,
            level_intelligence=level_intelligence,
            session_liquidity=session_liquidity_summary,
            deep_context_levels=deep_context_levels,
        )
        # Re-split valid targets by direction now that the primary scenario is
        # known. The telegram formatter falls back to splitting client-side if
        # this enrichment is missing, but doing it here makes the structured
        # API response carry the directional buckets too.
        try:
            from analysis.scenario_classifier import directional_targets

            split = directional_targets(
                level_intelligence.get("next_valid_targets") or [],
                current_price=current_price,
                primary_direction=str(primary.direction or ""),
            )
            level_intelligence["downside_valid_targets"] = split.get("downside_valid_targets", [])
            level_intelligence["upside_reclaim_targets"] = split.get("upside_reclaim_targets", [])
            level_intelligence["reaction_micro_levels"] = split.get("reaction_micro_levels", [])
            level_intelligence["primary_direction"] = split.get("primary_direction", "")
        except Exception as exc:
            logger.debug("DIRECTIONAL TARGET SPLIT SKIPPED: %s", exc)
        actionable_psych = select_actionable_psych_levels(current_price, psych_prices, supports, resistances, max_count=12)

        primary_wz = self._watch_zone_from_scenario(symbol, "primary", primary, current_price) or {}
        secondary_wz = self._watch_zone_from_scenario(symbol, "secondary", secondary, current_price) or {}
        watch_zones = [wz for wz in [primary_wz, secondary_wz] if wz]

        h4_context = self._format_h4_context(current_price, h4_state, resistances, supports, psych_prices)
        h1_context = self._format_h1_context(current_price, h1_state, resistances, supports)
        m15_context = self._format_m15_context(current_price, m15_state, primary)

        market_plan_text = self._format_market_plan_text(
            symbol=symbol,
            current_price=current_price,
            h4_context=h4_context,
            h1_context=h1_context,
            m15_context=m15_context,
            dominant_bias=dominant_bias,
            primary=primary,
            secondary=secondary,
            supports=supports,
            resistances=resistances,
            psych_prices=actionable_psych,
            primary_zone_status=primary_wz.get("status", "fresh"),
            secondary_zone_status=secondary_wz.get("status", "fresh"),
            primary_played_out_note=primary_wz.get("played_out_note", ""),
            secondary_played_out_note=secondary_wz.get("played_out_note", ""),
            deep_context_levels=deep_context_levels,
            session_liquidity=session_liquidity_summary,
            activation_pipeline=activation_pipeline,
        )

        confidence = 82.0 if bias_strength == "strong" else 71.0 if bias_strength == "moderate" else 58.0

        logger.info(
            "MARKET PLAN GENERATED: bias=%s primary=%s secondary=%s",
            dominant_bias,
            primary.direction,
            secondary.direction,
        )
        logger.info("MARKET PLAN SIGNATURE: pending")
        logger.info("WATCH ZONES GENERATED: count=%d", len(watch_zones))

        plan_status = self._derive_plan_status(watch_zones)
        market_structure_state = f"H4 {h4_state['bias']} / H1 {h1_state['bias']} / M15 {m15_state['bias']}"

        # Session Liquidity Intelligence — additive advisory layer. Failure
        # here must never break the market plan, so the call is wrapped.
        session_liquidity_summary: dict[str, Any] = {}
        try:
            session_engine = SessionLiquidityEngine()
            session_liquidity_summary = session_engine.build_market_plan_summary(
                m15,
                current_price=current_price,
                h1_supports=supports,
                h1_resistances=resistances,
                psych_levels=psych_prices,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("SESSION LIQUIDITY SKIPPED: %s", exc)
        market_plan_signature = self._signature_payload(
            symbol,
            dominant_bias,
            primary.watch_zone,
            secondary.watch_zone,
            supports[:4],
            resistances[:4],
        )
        logger.info("MARKET PLAN SIGNATURE: %s", market_plan_signature)
        logger.info("MARKET PLAN STATUS: %s", plan_status)

        return AnalystMarketPlan(
            symbol=symbol,
            current_price=round(current_price, 2),
            timestamp=now,
            h4_context=h4_context,
            h1_context=h1_context,
            m15_context=m15_context,
            dominant_bias=dominant_bias,
            bias_strength=bias_strength,
            market_structure=f"H4 {h4_state['bias']} / H1 {h1_state['bias']}",
            market_structure_state=market_structure_state,
            key_supports=supports[:5],
            key_resistances=resistances[:5],
            actionable_psych_levels=actionable_psych,
            psychological_levels=actionable_psych,
            liquidity_zones=liquidity_zones[:6],
            broken_levels=broken_levels[:6],
            retest_zones=[
                {"type": "primary", "watch_zone": primary.watch_zone, "direction": primary.direction},
                {"type": "secondary", "watch_zone": secondary.watch_zone, "direction": secondary.direction},
            ],
            active_watch_zones=watch_zones,
            primary_scenario=asdict(primary),
            secondary_scenario=asdict(secondary),
            invalidation_levels=[lvl for lvl in dict.fromkeys([primary.invalidation_level, secondary.invalidation_level]) if lvl],
            confirmation_triggers=list(dict.fromkeys(primary.matching_confirmation_types + secondary.matching_confirmation_types)),
            structure_targets=list(dict.fromkeys(primary.market_plan_targets + secondary.market_plan_targets)),
            recommended_strategy_focus=[
                "sweep_reclaim_confirmation",
                "break_retest_close_confirmation",
                "failed_retest_confirmation",
                "engulfing_level_confirmation",
                "displacement_confirmation",
                "structure_shift_confirmation",
            ],
            confirmation_waiting_for=primary.matching_confirmation_types,
            confidence_score=confidence,
            market_plan_signature=market_plan_signature,
            plan_status=plan_status,
            market_plan_text=market_plan_text,
            level_intelligence=level_intelligence,
            deep_context_levels=deep_context_levels,
            activation_pipeline=activation_pipeline,
            session_liquidity=session_liquidity_summary,
        )

    @staticmethod
    def _zone_distance(current_price: float, low: float, high: float) -> float:
        if low <= current_price <= high:
            return 0.0
        return min(abs(current_price - low), abs(current_price - high))

    @staticmethod
    def _level_score_map(level_intelligence: dict[str, Any] | None) -> dict[float, dict[str, Any]]:
        out: dict[float, dict[str, Any]] = {}
        for row in (level_intelligence or {}).get("level_scores", []) or []:
            try:
                out[round(float(row.get("level")), 2)] = dict(row)
            except Exception:
                continue
        return out

    def _classify_deep_context(self, level_intelligence: dict[str, Any], current_price: float) -> dict[str, list[dict[str, Any]]]:
        supports: list[dict[str, Any]] = []
        resistances: list[dict[str, Any]] = []
        for row in level_intelligence.get("level_scores", []) or []:
            try:
                level = float(row.get("level"))
            except Exception:
                continue
            if abs(level - current_price) < DEEP_CONTEXT_DISTANCE_PIPS:
                continue
            shaped = dict(row)
            shaped["distance_pips"] = round(abs(level - current_price), 1)
            shaped["role"] = "deep_context_support" if level < current_price else "deep_context_resistance"
            if level < current_price:
                supports.append(shaped)
            else:
                resistances.append(shaped)
        return {
            "deep_context_supports": sorted(supports, key=lambda r: abs(float(r.get("level", 0.0)) - current_price))[:6],
            "deep_context_resistances": sorted(resistances, key=lambda r: abs(float(r.get("level", 0.0)) - current_price))[:6],
        }

    def _build_activation_pipeline(self, primary: Scenario, *, level_intelligence: dict[str, Any], session_liquidity: dict[str, Any], deep_context_levels: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        active_count = 0 if primary.scenario_status == "no_active_intraday" else 1
        liquidity_candidates = len(session_liquidity.get("setups", []) or [])
        continuation_candidates = len([
            row for row in (level_intelligence.get("level_scores", []) or [])
            if float(row.get("score", 0.0) or 0.0) >= MIN_TP_LEVEL_SCORE
            and row.get("state") != "consumed"
        ])
        deep_count = len(deep_context_levels.get("deep_context_supports", [])) + len(deep_context_levels.get("deep_context_resistances", []))
        reason = primary.reason if active_count else "no near-price scenario met active distance and quality rules"
        pipeline = {
            "active_scenario_candidates": active_count,
            "watchlist_candidates": 0,
            "liquidity_candidates": liquidity_candidates,
            "continuation_candidates": continuation_candidates,
            "deep_context_candidates": deep_count,
            "selected_active_scenario": primary.scenario_type if active_count else "",
            "reason": reason,
            "nearest_valid_candidate": primary.watch_zone if active_count else "",
            "waiting_for": "; ".join(primary.trigger_conditions[:3]),
        }
        logger.info(
            "ACTIVATION PIPELINE: active_scenario_candidates=%d watchlist_candidates=%d liquidity_candidates=%d continuation_candidates=%d deep_context_candidates=%d selected_active_scenario=%s reason=%s",
            pipeline["active_scenario_candidates"],
            pipeline["watchlist_candidates"],
            pipeline["liquidity_candidates"],
            pipeline["continuation_candidates"],
            pipeline["deep_context_candidates"],
            pipeline["selected_active_scenario"],
            pipeline["reason"],
        )
        if not active_count:
            logger.info(
                "NO ACTIVE TRADE: reason=%s nearest_valid_candidate=%s waiting_for=%s",
                reason,
                pipeline["nearest_valid_candidate"],
                pipeline["waiting_for"],
            )
        return pipeline

    @staticmethod
    def _derive_plan_status(watch_zones: list[dict[str, Any]]) -> str:
        statuses = {str(zone.get("status", "fresh")) for zone in watch_zones}
        if "active" in statuses:
            return "active"
        if "fresh" in statuses:
            return "fresh"
        if "played_out" in statuses:
            return "played_out"
        if "invalidated" in statuses:
            return "invalidated"
        if "stale" in statuses:
            return "stale"
        return "fresh"

    @staticmethod
    def _signature_payload(symbol: str, dominant_bias: str, primary_zone: str, secondary_zone: str, supports: list[float], resistances: list[float]) -> str:
        raw = f"{symbol}|{dominant_bias}|{primary_zone}|{secondary_zone}|{supports}|{resistances}"
        import hashlib
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _structure_bias(self, df: pd.DataFrame) -> dict[str, Any]:
        recent = df.tail(40).reset_index(drop=True)
        highs = recent["high"].astype(float)
        lows = recent["low"].astype(float)
        close = recent["close"].astype(float)
        last_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-5]) if len(close) >= 5 else last_close
        recent_high = float(highs.tail(12).max())
        recent_low = float(lows.tail(12).min())
        slope = last_close - prev_close
        range_mid = (recent_high + recent_low) / 2.0

        if slope < 0 and last_close < range_mid:
            bias = "bearish"
        elif slope > 0 and last_close > range_mid:
            bias = "bullish"
        else:
            bias = "neutral"

        displacement = abs(float(close.iloc[-1]) - float(close.iloc[-2]))
        avg_body = float((recent["close"] - recent["open"]).abs().tail(10).mean() or 0.01)
        return {
            "bias": bias,
            "recent_high": round(recent_high, 2),
            "recent_low": round(recent_low, 2),
            "last_close": round(last_close, 2),
            "displacement": displacement > avg_body * 1.6,
        }

    def _execution_state(self, df: pd.DataFrame) -> dict[str, Any]:
        recent = df.tail(12).reset_index(drop=True)
        last = recent.iloc[-1]
        prev = recent.iloc[-2]
        avg_body = float((recent["close"] - recent["open"]).abs().tail(8).mean() or 0.01)
        body = abs(float(last["close"]) - float(last["open"]))
        direction = "bullish" if float(last["close"]) > float(last["open"]) else "bearish"
        return {
            "bias": direction,
            "displacement": body > avg_body * 1.6,
            "engulfing": (
                min(float(last["open"]), float(last["close"])) <= min(float(prev["open"]), float(prev["close"]))
                and max(float(last["open"]), float(last["close"])) >= max(float(prev["open"]), float(prev["close"]))
            ),
            "last_close": round(float(last["close"]), 2),
        }

    def _combine_bias(self, h4_bias: str, h1_bias: str, context=None) -> tuple[str, str]:
        ctx_bias = getattr(context, "dominant_bias", None) if context else None
        ctx_strength = getattr(context, "bias_strength", None) if context else None
        if ctx_bias in {"bullish", "bearish"}:
            return ctx_bias, ctx_strength or "moderate"
        if h4_bias == h1_bias and h4_bias in {"bullish", "bearish"}:
            return h4_bias, "strong"
        if h4_bias in {"bullish", "bearish"}:
            return h4_bias, "moderate"
        if h1_bias in {"bullish", "bearish"}:
            return h1_bias, "moderate"
        return "neutral", "weak"

    def _key_levels(self, df: pd.DataFrame, kind: str, current_price: float, psych: list[float]) -> list[float]:
        recent = df.tail(80).reset_index(drop=True)
        if kind == "support":
            raw = sorted(set(round(float(v), 2) for v in recent["low"].tail(20).nsmallest(6).tolist() + [p for p in psych if p <= current_price][-4:]))
        else:
            raw = sorted(set(round(float(v), 2) for v in recent["high"].tail(20).nlargest(6).tolist() + [p for p in psych if p >= current_price][:4]))
        return raw

    def _broken_levels(self, current_price: float, psych: list[float], h1: pd.DataFrame) -> list[float]:
        last = float(h1["close"].iloc[-1])
        prev = float(h1["close"].iloc[-4]) if len(h1) >= 4 else last
        crossed = []
        for level in psych:
            if (prev >= level > last) or (prev <= level < last):
                crossed.append(round(level, 2))
        return crossed

    def _best_active_level(self, levels: list[float], current_price: float, level_scores: dict[str, Any] | None, *, below: bool) -> float | None:
        score_map = self._level_score_map(level_scores)
        candidates: list[tuple[float, float, float]] = []
        for level in levels:
            level = round(float(level), 2)
            distance = abs(current_price - level)
            if distance > ACTIVE_SCENARIO_MAX_DISTANCE_PIPS:
                continue
            if below and level > current_price:
                continue
            if not below and level < current_price:
                continue
            row = score_map.get(level, {})
            if score_map and not row:
                continue
            score = float(row.get("score", 0.0) or 0.0)
            if score < MIN_TP_LEVEL_SCORE or row.get("state") == "consumed":
                continue
            recommended_use = str(row.get("recommended_use") or "").lower()
            if recommended_use in {"ignore", "manual_watch_only"}:
                continue
            evidence = str(row.get("evidence_summary") or "").lower()
            active_entry_evidence = (
                score >= 80.0
                or recommended_use == "entry_zone"
                or "reclaimed resistance acting as support" in evidence
                or "broken support acting as resistance" in evidence
                or "break/retest" in evidence
            )
            if not active_entry_evidence:
                continue
            ideal_bonus = 15.0 if ACTIVE_SCENARIO_IDEAL_MIN_DISTANCE_PIPS <= distance <= ACTIVE_SCENARIO_IDEAL_MAX_DISTANCE_PIPS else 0.0
            candidates.append((score + ideal_bonus - distance * 0.2, level, distance))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _no_active_scenario(self, current_price: float, dominant_bias: str, psych: list[float], supports: list[float], resistances: list[float]) -> Scenario:
        direction = "BUY" if dominant_bias == "bullish" else "SELL" if dominant_bias == "bearish" else "WAIT"
        targets = filter_market_plan_targets(
            "BUY" if direction != "SELL" else "SELL",
            current_price,
            current_price,
            (resistances if direction != "SELL" else supports) + psych,
            zone_low=current_price,
            zone_high=current_price,
            preferred_levels=resistances if direction != "SELL" else supports,
            max_count=5,
        )
        return Scenario(
            direction=direction,
            reason="No active intraday scenario yet; nearest high-quality level is outside active distance.",
            watch_zone="No active intraday scenario",
            watch_low=current_price,
            watch_high=current_price,
            trigger_conditions=["wait for near-price reclaim/retest", "wait for liquidity sweep/reversal", "do not force deep-context trade"],
            invalidation="n/a",
            targets=targets,
            market_plan_targets=targets,
            confidence="none",
            matching_confirmation_types=[],
            scenario_status="no_active_intraday",
            played_out_note="Deep levels remain context only until price approaches them.",
            scenario_type="no_active_intraday",
            invalidation_level=0.0,
            active_distance_pips=0.0,
            role="no_active_trade",
        )

    def _build_primary_scenario(self, current_price: float, dominant_bias: str, supports: list[float], resistances: list[float], psych: list[float], h4_state: dict[str, Any], h1_state: dict[str, Any], level_scores: dict[str, Any] | None = None, session_liquidity: dict[str, Any] | None = None) -> Scenario:
        if dominant_bias == "bearish":
            active_level = self._best_active_level(resistances, current_price, level_scores, below=False)
            if active_level is None:
                return self._no_active_scenario(current_price, dominant_bias, psych, supports, resistances)
            resist = [lvl for lvl in resistances if lvl >= current_price and abs(lvl - active_level) <= ACTIVE_SCENARIO_MAX_DISTANCE_PIPS]
            zone_low = resist[0] if resist else round(current_price + 10, 2)
            zone_low = active_level
            zone_high = resist[1] if len(resist) > 1 and resist[1] - zone_low <= 30 else round(zone_low + 20, 2)
            distance = self._zone_distance(current_price, zone_low, zone_high)
            min_tp_ceiling = zone_low - MIN_TP1_DISTANCE_PIPS
            execution_targets = [lvl for lvl in sorted(set(supports + [p for p in psych if p < current_price]), reverse=True) if lvl <= min_tp_ceiling][:4]
            if not execution_targets:
                execution_targets = [round(zone_low - PREFERRED_TP1_DISTANCE_PIPS, 2), round(zone_low - PREFERRED_TP1_DISTANCE_PIPS * 2, 2), round(zone_low - PREFERRED_TP1_DISTANCE_PIPS * 3, 2)]
            market_plan_targets = filter_market_plan_targets(
                "SELL",
                zone_low,
                current_price,
                supports + psych,
                zone_low=zone_low,
                zone_high=zone_high,
                preferred_levels=supports,
                max_count=5,
                level_scores=level_scores,
            )
            return Scenario(
                direction="SELL",
                reason="Active bearish continuation/retest: nearest broken support acting as resistance within live distance.",
                watch_zone=f"{zone_low:.2f}-{zone_high:.2f}",
                watch_low=zone_low,
                watch_high=zone_high,
                trigger_conditions=[
                    f"sweep above {zone_high:.2f} and close back below",
                    f"failed retest of {zone_low:.2f}",
                    "bearish displacement from zone",
                    "bearish engulfing at key level",
                    f"break/retest close below {zone_low:.2f}",
                ],
                invalidation=f"close above {zone_high:.2f}",
                targets=execution_targets[:4],
                market_plan_targets=market_plan_targets,
                confidence="high",
                matching_confirmation_types=[
                    "sweep_reclaim_confirmation",
                    "failed_retest_confirmation",
                    "break_retest_close_confirmation",
                    "displacement_confirmation",
                    "engulfing_level_confirmation",
                ],
                scenario_type="active_continuation_retest",
                invalidation_level=round(zone_high, 2),
                active_distance_pips=round(distance, 1),
            )
        active_level = self._best_active_level(supports, current_price, level_scores, below=True)
        if active_level is None:
            return self._no_active_scenario(current_price, dominant_bias, psych, supports, resistances)
        supports_above = [lvl for lvl in supports if lvl <= current_price and abs(lvl - active_level) <= ACTIVE_SCENARIO_MAX_DISTANCE_PIPS]
        zone_high = supports_above[-1] if supports_above else round(current_price - 10, 2)
        zone_high = active_level
        zone_low = supports_above[-2] if len(supports_above) > 1 and zone_high - supports_above[-2] <= 30 else round(zone_high - 20, 2)
        distance = self._zone_distance(current_price, zone_low, zone_high)
        min_tp_floor = zone_high + MIN_TP1_DISTANCE_PIPS
        execution_targets = [lvl for lvl in sorted(set(resistances + [p for p in psych if p > current_price])) if lvl >= min_tp_floor][:4]
        if not execution_targets:
            execution_targets = [round(zone_high + PREFERRED_TP1_DISTANCE_PIPS, 2), round(zone_high + PREFERRED_TP1_DISTANCE_PIPS * 2, 2), round(zone_high + PREFERRED_TP1_DISTANCE_PIPS * 3, 2)]
        market_plan_targets = filter_market_plan_targets(
            "BUY",
            zone_high,
            current_price,
            resistances + psych,
            zone_low=zone_low,
            zone_high=zone_high,
            preferred_levels=resistances,
            max_count=5,
            level_scores=level_scores,
        )
        return Scenario(
            direction="BUY",
            reason="Active bullish continuation/retest: nearest reclaimed resistance acting as support within live distance.",
            watch_zone=f"{zone_low:.2f}-{zone_high:.2f}",
            watch_low=zone_low,
            watch_high=zone_high,
            trigger_conditions=[
                f"sweep below {zone_low:.2f} and close back above",
                f"failed retest of {zone_high:.2f}",
                "bullish displacement from zone",
                "bullish engulfing at key level",
                f"break/retest close above {zone_high:.2f}",
            ],
            invalidation=f"close below {zone_low:.2f}",
            targets=execution_targets[:4],
            market_plan_targets=market_plan_targets,
            confidence="high",
            matching_confirmation_types=[
                "sweep_reclaim_confirmation",
                "failed_retest_confirmation",
                "break_retest_close_confirmation",
                "displacement_confirmation",
                "engulfing_level_confirmation",
            ],
            scenario_type="active_continuation_retest",
            invalidation_level=round(zone_low, 2),
            active_distance_pips=round(distance, 1),
        )

    def _build_secondary_scenario(self, current_price: float, dominant_bias: str, supports: list[float], resistances: list[float], psych: list[float], h4_state: dict[str, Any], h1_state: dict[str, Any], primary: "Scenario | None" = None, level_scores: dict[str, Any] | None = None) -> "Scenario":
        primary_bearish = dominant_bias == "bearish"
        if primary_bearish:
            # BUY recovery requires reclaim of the SELL zone HIGH, not just any resistance.
            if primary is not None and primary.direction == "SELL":
                zone_low = primary.watch_high  # e.g. 4600 — must close above this to flip
            else:
                buy_levels = [lvl for lvl in sorted(set(resistances + [p for p in psych if p > current_price])) if lvl > current_price]
                zone_low = buy_levels[0] if buy_levels else round(current_price + 20, 2)
            zone_high = next(
                (lvl for lvl in sorted(set(resistances + [p for p in psych if p > zone_low])) if lvl > zone_low),
                round(zone_low + 15, 2),
            )
            execution_targets = [lvl for lvl in sorted(set(resistances + [p for p in psych if p > zone_high])) if lvl > zone_high][:4]
            if not execution_targets:
                execution_targets = [round(zone_low + PREFERRED_TP1_DISTANCE_PIPS, 2), round(zone_low + PREFERRED_TP1_DISTANCE_PIPS * 2.5, 2), round(zone_low + PREFERRED_TP1_DISTANCE_PIPS * 4, 2)]
            market_plan_targets = filter_market_plan_targets(
                "BUY",
                zone_low,
                current_price,
                resistances + psych,
                zone_low=zone_low,
                zone_high=zone_high,
                preferred_levels=resistances,
                max_count=4,
                level_scores=level_scores,
            )
            return Scenario(
                direction="BUY",
                reason=f"Bullish recovery watch only — requires a confirmed close and hold above {zone_low:.2f} (sell-zone reclaim). Not confirmed until that level breaks.",
                watch_zone=f"{zone_low:.2f}-{zone_high:.2f}",
                watch_low=zone_low,
                watch_high=zone_high,
                trigger_conditions=[
                    f"close and hold above {zone_low:.2f} (sell-zone reclaim)",
                    f"retest {zone_low:.2f} as support after reclaim",
                    "bullish displacement above zone",
                    "bullish structure shift on H1",
                ],
                invalidation=f"close back below {max(0.0, zone_low - 20):.2f}",
                targets=execution_targets[:4],
                market_plan_targets=market_plan_targets,
                confidence="low",
                matching_confirmation_types=[
                    "break_retest_close_confirmation",
                    "structure_shift_confirmation",
                    "displacement_confirmation",
                    "engulfing_level_confirmation",
                ],
                scenario_type="active_liquidity_sweep_reversal",
                invalidation_level=round(max(0.0, zone_low - 20), 2),
                active_distance_pips=round(self._zone_distance(current_price, zone_low, zone_high), 1),
            )
        sell_levels = [lvl for lvl in sorted(set(supports + [p for p in psych if p < current_price]), reverse=True) if lvl < current_price]
        zone_high = sell_levels[0] if sell_levels else round(current_price - 15, 2)
        zone_low = sell_levels[1] if len(sell_levels) > 1 and zone_high - sell_levels[1] <= 30 else round(zone_high - 10, 2)
        execution_targets = sell_levels[1:5] or [round(current_price - 20, 2), round(current_price - 50, 2), round(current_price - 80, 2)]
        market_plan_targets = filter_market_plan_targets(
            "SELL",
            zone_high,
            current_price,
            supports + psych,
            zone_low=zone_low,
            zone_high=zone_high,
            preferred_levels=supports,
            max_count=5,
            level_scores=level_scores,
        )
        return Scenario(
            direction="SELL",
            reason="Downside continuation only if price loses reclaimed support again.",
            watch_zone=f"{zone_low:.2f}-{zone_high:.2f}",
            watch_low=zone_low,
            watch_high=zone_high,
            trigger_conditions=[
                f"close below {zone_high:.2f}",
                f"failed retest of {zone_high:.2f}",
                "bearish displacement",
                "bearish structure shift",
            ],
            invalidation=f"close back above {zone_high + 20:.2f}",
            targets=execution_targets[:4],
            market_plan_targets=market_plan_targets,
            confidence="medium",
            matching_confirmation_types=[
                "break_retest_close_confirmation",
                "structure_shift_confirmation",
                "displacement_confirmation",
                "engulfing_level_confirmation",
            ],
            scenario_type="active_liquidity_sweep_reversal",
            invalidation_level=round(zone_high + 20, 2),
            active_distance_pips=round(self._zone_distance(current_price, zone_low, zone_high), 1),
        )

    def _watch_zone_from_scenario(self, symbol: str, scenario_type: str, scenario: Scenario, current_price: float) -> Optional[dict[str, Any]]:
        if scenario.scenario_status == "no_active_intraday":
            return None
        low = min(scenario.watch_low, scenario.watch_high)
        high = max(scenario.watch_low, scenario.watch_high)
        zone_mid = (low + high) / 2.0
        distance = abs(current_price - zone_mid)
        status = "fresh"
        played_out_note = ""
        tp1 = scenario.targets[0] if scenario.targets else None

        if low <= current_price <= high:
            status = "active"
        elif scenario.direction == "SELL" and tp1 is not None and current_price <= tp1:
            status = "played_out"
            played_out_note = (
                f"Previous sell scenario from {scenario.watch_zone} played out and TP1 was reached. "
                f"Spencer will not chase this move. {scenario.watch_zone} remains reusable resistance only if price pulls back and gives fresh confirmation."
            )
        elif scenario.direction == "BUY" and tp1 is not None and current_price >= tp1:
            status = "played_out"
            played_out_note = (
                f"Previous buy scenario from {scenario.watch_zone} played out and TP1 was reached. "
                f"Spencer will not chase this move. {scenario.watch_zone} remains reusable support only if price pulls back and gives fresh confirmation."
            )
        elif distance > ACTIVE_SCENARIO_MAX_DISTANCE_PIPS:
            status = "deep_context"

        return {
            "zone_id": f"{symbol}:{scenario_type}:{scenario.direction}:{low:.2f}:{high:.2f}",
            "symbol": symbol,
            "direction": scenario.direction,
            "level_low": round(low, 2),
            "level_high": round(high, 2),
            "scenario_type": scenario_type,
            "status": status,
            "confirmations_seen": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "distance_from_current_pips": round(distance, 1),
            "played_out_note": played_out_note,
            "invalidation_level": scenario.invalidation_level or (high if scenario.direction == "SELL" else low),
        }

    def _format_h4_context(self, current_price: float, h4_state: dict[str, Any], resistances: list[float], supports: list[float], psych: list[float]) -> str:
        nearest_res = next((lvl for lvl in resistances if lvl >= current_price), None)
        nearest_sup = max([lvl for lvl in supports if lvl <= current_price], default=None)
        res_label = f"{(nearest_res if nearest_res is not None else current_price):.2f}"
        sup_label = f"{(nearest_sup if nearest_sup is not None else current_price):.2f}"
        range_low = f"{(nearest_sup if nearest_sup is not None else current_price - 20):.2f}"
        range_high = f"{(nearest_res if nearest_res is not None else current_price + 20):.2f}"
        bias = h4_state["bias"]
        if bias == "bearish":
            return (
                f"H4 is bearish. Price is trading below {res_label} "
                f"after a strong selloff. Sellers remain in control while price stays below resistance."
            )
        if bias == "bullish":
            return (
                f"H4 is bullish. Price is holding above {sup_label} "
                f"after reclaiming support. Buyers remain in control while price holds above support."
            )
        return (
            f"H4 is ranging between {range_low} and {range_high}. A clean break is needed."
        )

    def _format_h1_context(self, current_price: float, h1_state: dict[str, Any], resistances: list[float], supports: list[float]) -> str:
        nearest_res = next((lvl for lvl in resistances if lvl >= current_price), None)
        nearest_sup = max([lvl for lvl in supports if lvl <= current_price], default=None)
        res_label = f"{(nearest_res if nearest_res is not None else current_price):.2f}"
        sup_label = f"{(nearest_sup if nearest_sup is not None else current_price):.2f}"
        if h1_state["bias"] == "bearish":
            return (
                f"H1 remains bearish. Price is trading below {res_label}. "
                f"The main sell retest zone is around resistance."
            )
        if h1_state["bias"] == "bullish":
            return (
                f"H1 remains bullish. Price is holding above {sup_label}. "
                f"The main buy retest zone is around support."
            )
        return "H1 is mixed. Spencer is waiting for a cleaner intraday break or reclaim."

    def _format_m15_context(self, current_price: float, m15_state: dict[str, Any], primary: Scenario) -> str:
        if primary.direction == "SELL":
            return (
                f"M15 is below the preferred sell zone. If price pulls back into {primary.watch_zone} and rejects, "
                f"Spencer will watch for sell confirmation."
            )
        return (
            f"M15 is holding above the preferred buy zone. If price pulls back into {primary.watch_zone} and holds, "
            f"Spencer will watch for buy confirmation."
        )

    def _format_market_plan_text(
        self,
        symbol: str,
        current_price: float,
        h4_context: str,
        h1_context: str,
        m15_context: str,
        dominant_bias: str,
        primary: Scenario,
        secondary: Scenario,
        supports: list[float],
        resistances: list[float],
        psych_prices: list[float],
        primary_zone_status: str = "fresh",
        secondary_zone_status: str = "fresh",
        primary_played_out_note: str = "",
        secondary_played_out_note: str = "",
        deep_context_levels: dict[str, list[dict[str, Any]]] | None = None,
        session_liquidity: dict[str, Any] | None = None,
        activation_pipeline: dict[str, Any] | None = None,
    ) -> str:
        _STATUS_LABELS = {
            "active": " [ACTIVE — price in zone]",
            "played_out": " [PLAYED OUT]",
            "reusable_on_fresh_retest": " [REUSABLE — watch for fresh retest]",
            "invalidated": " [INVALIDATED]",
            "stale": " [STALE — zone far from price]",
        }

        def status_tag(status: str, note: str) -> str:
            label = _STATUS_LABELS.get(status, "")
            if status == "played_out" and note:
                label = f" [PLAYED OUT — {note}]"
            return label

        is_counter_bias_recovery = (
            (dominant_bias == "bearish" and secondary.direction == "BUY")
            or (dominant_bias == "bullish" and secondary.direction == "SELL")
        )
        secondary_header = (
            "Secondary Scenario (Recovery Watch — unconfirmed)"
            if is_counter_bias_recovery
            else "Secondary Scenario"
        )
        deep_context_levels = deep_context_levels or {}
        session_liquidity = session_liquidity or {}
        activation_pipeline = activation_pipeline or {}
        deep_supports = deep_context_levels.get("deep_context_supports", [])
        deep_resistances = deep_context_levels.get("deep_context_resistances", [])
        deep_support_text = " / ".join(f"{float(row.get('level') or 0.0):.2f}" for row in deep_supports[:4]) or "none"
        deep_resistance_text = " / ".join(f"{float(row.get('level') or 0.0):.2f}" for row in deep_resistances[:4]) or "none"
        buy_liq = float(session_liquidity.get("nearest_buy_side_liquidity") or 0.0)
        sell_liq = float(session_liquidity.get("nearest_sell_side_liquidity") or 0.0)
        active_header = "Active Intraday Scenario" if primary.scenario_status != "no_active_intraday" else "Active Intraday Scenario: No active intraday scenario yet"
        active_line = (
            f"{primary.direction} continuation retest of {primary.watch_zone}"
            if primary.scenario_status != "no_active_intraday"
            else "No active intraday scenario yet. Spencer is not forcing a deep-context setup."
        )
        confluence_line = " + ".join(primary.confluence[:6]) if getattr(primary, "confluence", None) else ""

        return (
            f"SPENCER MARKET PLAN — {symbol}\n\n"
            f"Current Price: {current_price:.2f}\n\n"
            f"H4 Context:\n{h4_context}\n\n"
            f"H1 Context:\n{h1_context}\n\n"
            f"M15 Execution Context:\n{m15_context}\n\n"
            f"{active_header}{status_tag(primary_zone_status, primary_played_out_note)}:\n"
            f"{active_line}\n"
            f"{('Confluence: ' + confluence_line + chr(10)) if confluence_line else ''}"
            f"Type: {primary.scenario_type}\n"
            f"Reason: {primary.reason}\n"
            f"Trigger: {'; '.join(primary.trigger_conditions)}\n"
            f"Targets: {' / '.join(f'{target:.2f}' for target in primary.market_plan_targets[:5])}\n"
            f"Invalidation: {primary.invalidation}\n\n"
            f"Deep Context Levels:\n"
            f"Deep Context Support: {deep_support_text}\n"
            f"Deep Context Resistance: {deep_resistance_text}\n\n"
            f"Session Liquidity Plan:\n"
            f"Nearest Buy-side Liquidity: {buy_liq:.2f}\n"
            f"Nearest Sell-side Liquidity: {sell_liq:.2f}\n"
            f"Liquidity Bias: {session_liquidity.get('liquidity_bias', 'balanced')}\n"
            f"Expected Play: {session_liquidity.get('expected_play', 'watch nearest liquidity reaction')}\n\n"
            f"{secondary_header}{status_tag(secondary_zone_status, secondary_played_out_note)}:\n"
            f"{secondary.direction} only if {secondary.watch_zone} activates.\n"
            f"Trigger: {'; '.join(secondary.trigger_conditions)}\n"
            f"Targets: {' / '.join(f'{target:.2f}' for target in secondary.market_plan_targets[:5])}\n"
            f"Invalidation: {secondary.invalidation}\n\n"
            f"Psychological Levels:\n"
            f"{', '.join(f'{target:.2f}' for target in psych_prices[:12])}\n\n"
            f"Watching:\n"
            f"Psychological levels, structure retests, liquidity sweeps, break/retest, displacement, engulfing at key levels.\n\n"
            f"Activation Pipeline:\n"
            f"active={activation_pipeline.get('active_scenario_candidates', 0)} "
            f"watchlist={activation_pipeline.get('watchlist_candidates', 0)} "
            f"liquidity={activation_pipeline.get('liquidity_candidates', 0)} "
            f"continuation={activation_pipeline.get('continuation_candidates', 0)} "
            f"deep_context={activation_pipeline.get('deep_context_candidates', 0)}\n"
            f"Selected: {activation_pipeline.get('selected_active_scenario', '') or 'none'} - {activation_pipeline.get('reason', '')}"
        )
