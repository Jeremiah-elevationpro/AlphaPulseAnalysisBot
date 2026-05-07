from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import (
    LEVEL_INTELLIGENCE_BLOCKING_MODE,
    LEVEL_INTELLIGENCE_ENABLED,
    MIN_MAIN_TARGET_DISTANCE_PIPS,
    MIN_TP_LEVEL_SCORE,
)
from utils.logger import get_logger

logger = get_logger(__name__)

LEVEL_MEMORY_PATH = Path("data/level_intelligence/spencer_level_memory.json")
MANUAL_SETUPS_PATH = Path("data/level_intelligence/manual_setups.json")

LEVEL_TYPES = {
    "psychological",
    "structure_support",
    "structure_resistance",
    "swing_high",
    "swing_low",
    "gap_level",
    "imbalance_level",
    "reaction_level",
    "previous_day_high",
    "previous_day_low",
    "session_high",
    "session_low",
    "manual_level",
    "discovered_level",
}

LEVEL_STATES = {
    "fresh",
    "active",
    "respected",
    "consumed",
    "broken",
    "reclaimed",
    "weak",
    "invalidated",
}

RECOMMENDED_USES = {
    "entry_zone",
    "target",
    "reaction_level",
    "invalidation",
    "context_only",
    "ignore",
    "manual_watch_only",
}


@dataclass
class LevelEvidence:
    structural: list[str] = field(default_factory=list)
    reaction: list[str] = field(default_factory=list)
    freshness: list[str] = field(default_factory=list)
    historical: list[str] = field(default_factory=list)
    live_session: list[str] = field(default_factory=list)
    ai: list[str] = field(default_factory=list)
    negative: list[str] = field(default_factory=list)

    def summary(self) -> str:
        positives = self.structural + self.reaction + self.freshness + self.historical + self.live_session + self.ai
        if positives:
            return " + ".join(positives[:5])
        if self.negative:
            return " / ".join(self.negative[:3])
        return "No strong level evidence yet"


@dataclass
class LevelScore:
    level: float
    level_type: str
    direction_relevance: str
    score: float
    confidence: float
    state: str
    recommended_use: str
    quality_label: str
    evidence_summary: str
    positive_factors: list[str] = field(default_factory=list)
    negative_factors: list[str] = field(default_factory=list)
    historical_tp1_rate: float = 0.0
    historical_sl_rate: float = 0.0
    ai_average_tp1_probability: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LevelMemoryRecord:
    id: str
    symbol: str
    level: float
    level_type: str
    level_state: str
    score: float
    confidence: float
    direction_relevance: str
    recommended_use: str
    evidence_summary: str
    positive_factors: list[str] = field(default_factory=list)
    negative_factors: list[str] = field(default_factory=list)
    last_reaction_at: str = ""
    last_consumed_at: str = ""
    times_touched: int = 0
    times_rejected: int = 0
    times_broken: int = 0
    times_reclaimed: int = 0
    tp1_hits_from_level: int = 0
    sl_hits_from_level: int = 0
    historical_win_rate: float = 0.0
    historical_tp1_rate: float = 0.0
    historical_sl_rate: float = 0.0
    net_pips_from_level: float = 0.0
    ai_boost_count: int = 0
    ai_caution_count: int = 0
    ai_would_block_count: int = 0
    session_name: str = ""
    timeframe: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioComplianceResult:
    compliant: bool
    reason: str
    severity: str
    corrected_status: str
    actionable: bool
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InvalidationCheck:
    valid: bool
    invalidated: bool
    reason: str
    invalidation: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_invalidation_level(
    direction: str,
    entry: float,
    zone_low: float,
    zone_high: float,
    invalidation: Any,
    current_price: float,
    *,
    close_price: float | None = None,
    swing_mode: bool = False,
) -> InvalidationCheck:
    try:
        if isinstance(invalidation, str):
            import re
            nums = re.findall(r"-?\d+(?:\.\d+)?", invalidation)
            if len(nums) != 1:
                logger.warning("INVALID INVALIDATION LEVEL: raw=%s reason=ambiguous_or_concatenated_digits", invalidation)
                return InvalidationCheck(False, False, "ambiguous_or_concatenated_digits")
            value = float(nums[0])
        else:
            value = float(invalidation)
    except Exception:
        logger.warning("INVALID INVALIDATION LEVEL: raw=%s reason=not_numeric", invalidation)
        return InvalidationCheck(False, False, "not_numeric")

    direction = str(direction or "").upper()
    entry = float(entry or 0.0)
    zone_low = float(zone_low or 0.0)
    zone_high = float(zone_high or 0.0)
    current_price = float(current_price or 0.0)
    close = float(close_price if close_price is not None else current_price)

    if value < 1000 or value > 10000:
        logger.warning("INVALID INVALIDATION LEVEL: invalidation=%.2f reason=outside_xauusd_range", value)
        return InvalidationCheck(False, False, "outside_xauusd_range", value)
    if not swing_mode and current_price and abs(value - current_price) > 500:
        logger.warning("INVALID INVALIDATION LEVEL: invalidation=%.2f current_price=%.2f reason=too_far_from_current_price", value, current_price)
        return InvalidationCheck(False, False, "too_far_from_current_price", value)
    if direction == "BUY" and not (value <= entry or value <= zone_low):
        logger.warning("INVALID INVALIDATION LEVEL: direction=BUY invalidation=%.2f entry=%.2f zone_low=%.2f reason=wrong_side", value, entry, zone_low)
        return InvalidationCheck(False, False, "buy_invalidation_not_below_entry_or_zone", value)
    if direction == "SELL" and not (value >= entry or value >= zone_high):
        logger.warning("INVALID INVALIDATION LEVEL: direction=SELL invalidation=%.2f entry=%.2f zone_high=%.2f reason=wrong_side", value, entry, zone_high)
        return InvalidationCheck(False, False, "sell_invalidation_not_above_entry_or_zone", value)

    invalidated = close < value if direction == "BUY" else close > value if direction == "SELL" else False
    logger.info(
        "INVALIDATION CHECK: direction=%s current_price=%.2f invalidation=%.2f valid=true invalidated=%s reason=%s",
        direction,
        current_price,
        value,
        str(invalidated).lower(),
        "close_breached" if invalidated else "direction_check_passed_not_breached",
    )
    return InvalidationCheck(True, invalidated, "valid", value)


class LevelIntelligenceEngine:
    def __init__(self, memory_path: str | Path = LEVEL_MEMORY_PATH, manual_path: str | Path = MANUAL_SETUPS_PATH):
        self.memory_path = Path(memory_path)
        self.manual_path = Path(manual_path)
        self.memory: dict[str, dict[str, Any]] = self._load_json(self.memory_path, {})
        self.manual_setups: list[dict[str, Any]] = self._load_json(self.manual_path, [])

    @staticmethod
    def _load_json(path: Path, fallback):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("LEVEL MEMORY LOAD FAILED: path=%s error=%s", path, exc)
        return fallback

    @staticmethod
    def _save_json(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")

    @staticmethod
    def quality_label(score: float, state: str = "") -> str:
        if state == "consumed":
            return "CONSUMED LEVEL"
        if score >= 90:
            return "A+ KEY LEVEL"
        if score >= 80:
            return "A KEY LEVEL"
        if score >= 70:
            return "VALID LEVEL"
        if score >= 60:
            return "WEAK LEVEL / CONTEXT ONLY"
        return "IGNORE LEVEL"

    @staticmethod
    def _near(level: float, values: list[float], tolerance: float = 5.0) -> bool:
        return any(abs(float(level) - float(v)) <= tolerance for v in values if v is not None)

    @staticmethod
    def _touch_stats(level: float, df: pd.DataFrame | None, tolerance: float = 4.0) -> tuple[int, int, int]:
        if df is None or df.empty:
            return 0, 0, 0
        recent = df.tail(80).copy()
        touches = 0
        rejections = 0
        breaks = 0
        previous_side = ""
        for _, row in recent.iterrows():
            high = float(row.get("high", 0.0))
            low = float(row.get("low", 0.0))
            close = float(row.get("close", 0.0))
            open_ = float(row.get("open", close))
            if low - tolerance <= level <= high + tolerance:
                touches += 1
                candle_range = max(high - low, 0.01)
                body = abs(close - open_)
                upper_wick = high - max(open_, close)
                lower_wick = min(open_, close) - low
                if max(upper_wick, lower_wick) > body and max(upper_wick, lower_wick) > candle_range * 0.35:
                    rejections += 1
            side = "above" if close > level else "below"
            if previous_side and side != previous_side:
                breaks += 1
            previous_side = side
        return touches, rejections, breaks

    def detect_consumed(self, level: float, *, current_price: float, direction: str = "", tp1_reached: bool = False, touches: int = 0, breaks: int = 0, fresh_confirmation: bool = False, last_setup: str = "") -> tuple[bool, str]:
        if fresh_confirmation:
            return False, "fresh_confirmation"
        if tp1_reached:
            reason = "tp1_reached"
        elif breaks >= 3:
            reason = "chopped_through_repeatedly"
        elif abs(float(level) - float(current_price)) < MIN_MAIN_TARGET_DISTANCE_PIPS:
            reason = "too_close_without_fresh_confirmation"
        elif touches >= 5 and breaks >= 2:
            reason = "failed_to_displace_after_retests"
        else:
            return False, ""
        logger.info(
            "LEVEL CONSUMED: level=%.2f reason=%s tp1_reached=%s last_setup=%s",
            level,
            reason,
            tp1_reached,
            last_setup,
        )
        return True, reason

    def score_level(
        self,
        symbol: str,
        level: float,
        *,
        level_type: str = "psychological",
        direction_relevance: str = "both",
        current_price: float = 0.0,
        data: dict[str, pd.DataFrame] | None = None,
        session_name: str = "",
        ai_prediction: dict[str, Any] | None = None,
        tp1_reached: bool = False,
        fresh_confirmation: bool = False,
    ) -> LevelScore:
        if not LEVEL_INTELLIGENCE_ENABLED:
            return LevelScore(float(level), level_type, direction_relevance, 70.0, 0.5, "active", "context_only", "VALID LEVEL", "Level Intelligence disabled")

        data = data or {}
        level = round(float(level), 2)
        evidence = LevelEvidence()
        score = 35.0

        h4 = data.get("H4")
        h1 = data.get("H1")
        m15 = data.get("M15")
        h4_lows = [float(v) for v in (h4.tail(30)["low"].tolist() if h4 is not None and not h4.empty else [])]
        h4_highs = [float(v) for v in (h4.tail(30)["high"].tolist() if h4 is not None and not h4.empty else [])]
        h1_lows = [float(v) for v in (h1.tail(60)["low"].tolist() if h1 is not None and not h1.empty else [])]
        h1_highs = [float(v) for v in (h1.tail(60)["high"].tolist() if h1 is not None and not h1.empty else [])]
        if self._near(level, h4_lows + h4_highs, 6.0):
            score += 18
            evidence.structural.append("H4 swing alignment")
        if self._near(level, h1_lows + h1_highs, 5.0):
            score += 14
            evidence.structural.append("H1 swing alignment")
        if level_type in {"structure_support", "structure_resistance", "swing_high", "swing_low"}:
            score += 10
            evidence.structural.append(level_type)
        if current_price and level_type in {"structure_resistance", "swing_high"} and level < float(current_price):
            score += 12
            direction_relevance = "BUY"
            evidence.structural.append("reclaimed resistance acting as support")
            level_type = "structure_support"
        if current_price and level_type in {"structure_support", "swing_low"} and level > float(current_price):
            score += 12
            direction_relevance = "SELL"
            evidence.structural.append("broken support acting as resistance")
            level_type = "structure_resistance"
        if level_type == "psychological":
            score += 6
            evidence.structural.append("psychological level")

        touches, rejections, breaks = self._touch_stats(level, m15)
        if rejections >= 3:
            score += 20
            evidence.reaction.append(f"{rejections} M15 rejections")
        elif rejections >= 1:
            score += 8
            evidence.reaction.append(f"{rejections} M15 rejection")
        if touches:
            evidence.live_session.append(f"{touches} recent touches")
        if breaks >= 3:
            score -= 22
            evidence.negative.append("chopped through repeatedly")

        if current_price:
            distance = abs(level - float(current_price))
            if distance < MIN_MAIN_TARGET_DISTANCE_PIPS:
                score -= 14
                evidence.negative.append("too close for main TP")
            elif distance >= 20:
                score += 5
                evidence.freshness.append("clean distance from current price")

        ai_prediction = ai_prediction or {}
        ai_tp1 = float(ai_prediction.get("tp1_probability", 0.0) or 0.0)
        if ai_tp1 >= 0.70:
            score += 6
            evidence.ai.append("AI boost near level")
        elif ai_prediction and ai_tp1 < 0.55:
            score -= 5
            evidence.ai.append("AI caution near level")

        consumed, consumed_reason = self.detect_consumed(
            level,
            current_price=current_price or level,
            direction=direction_relevance,
            tp1_reached=tp1_reached,
            touches=touches,
            breaks=breaks,
            fresh_confirmation=fresh_confirmation,
        )
        state = "consumed" if consumed else "active" if score >= 70 else "weak" if score >= 60 else "fresh"
        if fresh_confirmation and consumed:
            state = "reclaimed"
            score += 10
            evidence.freshness.append("fresh reclaim/retest reactivated")
            logger.info("LEVEL REACTIVATED: level=%.2f reason=fresh_reclaim/fresh_retest/new_displacement", level)

        recommended_use = "ignore"
        if state == "consumed":
            recommended_use = "context_only"
        elif score >= 80:
            recommended_use = "entry_zone" if direction_relevance in {"BUY", "SELL"} else "target"
        elif score >= MIN_TP_LEVEL_SCORE:
            recommended_use = "target"
        elif score >= 60:
            recommended_use = "context_only"

        if abs(level - float(current_price or level)) < MIN_MAIN_TARGET_DISTANCE_PIPS:
            recommended_use = "reaction_level"

        score = max(0.0, min(100.0, score))
        positives = evidence.structural + evidence.reaction + evidence.freshness + evidence.historical + evidence.live_session + evidence.ai
        label = self.quality_label(score, state)
        return LevelScore(
            level=level,
            level_type=level_type if level_type in LEVEL_TYPES else "discovered_level",
            direction_relevance=direction_relevance,
            score=round(score, 1),
            confidence=round(min(0.95, 0.35 + (len(positives) * 0.1)), 2),
            state=state,
            recommended_use=recommended_use if recommended_use in RECOMMENDED_USES else "context_only",
            quality_label=label,
            evidence_summary=evidence.summary() if not consumed_reason else f"{evidence.summary()} / consumed: {consumed_reason}",
            positive_factors=positives,
            negative_factors=evidence.negative,
            ai_average_tp1_probability=round(ai_tp1, 3),
        )

    def score_levels(
        self,
        symbol: str,
        levels: list[float],
        *,
        current_price: float,
        data: dict[str, pd.DataFrame] | None = None,
        supports: list[float] | None = None,
        resistances: list[float] | None = None,
        session_name: str = "",
    ) -> list[LevelScore]:
        supports_set = {round(float(v), 2) for v in supports or []}
        resistances_set = {round(float(v), 2) for v in resistances or []}
        scored: list[LevelScore] = []
        for raw in sorted({round(float(v), 2) for v in levels if v is not None}):
            if raw in supports_set:
                level_type = "structure_support"
                relevance = "BUY"
            elif raw in resistances_set:
                level_type = "structure_resistance"
                relevance = "SELL"
            else:
                level_type = "psychological"
                relevance = "both"
            scored.append(
                self.score_level(
                    symbol,
                    raw,
                    level_type=level_type,
                    direction_relevance=relevance,
                    current_price=current_price,
                    data=data,
                    session_name=session_name,
                )
            )
        return scored

    def build_market_plan_summary(
        self,
        scores: list[LevelScore],
        current_price: float,
        *,
        primary_direction: str = "",
    ) -> dict[str, Any]:
        active = [s for s in scores if s.state != "consumed" and s.score >= 60 and s.recommended_use != "ignore"]
        supports = sorted([s for s in active if s.level < current_price], key=lambda s: s.score, reverse=True)
        resistances = sorted([s for s in active if s.level > current_price], key=lambda s: s.score, reverse=True)
        consumed = [s for s in scores if s.state == "consumed"]
        targets = sorted([s for s in active if s.recommended_use in {"target", "entry_zone"}], key=lambda s: (-s.score, abs(s.level - current_price)))

        # Split valid targets by direction so the alert never mixes a level
        # below current price (downside target) with a level above it (upside
        # reclaim/resistance). Reaction/micro levels — those within
        # MIN_MAIN_TARGET_DISTANCE_PIPS — are surfaced separately.
        downside: list[LevelScore] = []
        upside: list[LevelScore] = []
        reaction: list[LevelScore] = []
        for s in targets:
            distance = abs(float(s.level) - float(current_price or s.level))
            if distance <= MIN_MAIN_TARGET_DISTANCE_PIPS:
                reaction.append(s)
                continue
            if s.level < current_price:
                downside.append(s)
            elif s.level > current_price:
                upside.append(s)

        downside.sort(key=lambda s: (-s.score, abs(s.level - current_price)))
        upside.sort(key=lambda s: (-s.score, abs(s.level - current_price)))
        reaction.sort(key=lambda s: (-s.score, abs(s.level - current_price)))

        return {
            "enabled": bool(LEVEL_INTELLIGENCE_ENABLED),
            "blocking_mode": bool(LEVEL_INTELLIGENCE_BLOCKING_MODE),
            "strongest_support": supports[0].to_dict() if supports else None,
            "strongest_resistance": resistances[0].to_dict() if resistances else None,
            "consumed_levels": [s.to_dict() for s in consumed[:6]],
            # Back-compat field — kept for any caller still reading it.
            "next_valid_targets": [s.to_dict() for s in targets[:8]],
            "downside_valid_targets": [s.to_dict() for s in downside[:5]],
            "upside_reclaim_targets": [s.to_dict() for s in upside[:5]],
            "reaction_micro_levels": [s.to_dict() for s in reaction[:5]],
            "primary_direction": str(primary_direction or "").upper(),
            "manual_levels_watched": len(self.manual_setups),
            "level_scores": [s.to_dict() for s in sorted(scores, key=lambda x: x.score, reverse=True)[:20]],
        }

    def persist_scores(self, symbol: str, scores: list[LevelScore], *, session_name: str = "", timeframe: str = "M15") -> None:
        now = datetime.now(timezone.utc).isoformat()
        for score in scores:
            key = f"{symbol}:{score.level:.2f}:{score.level_type}"
            existing = dict(self.memory.get(key) or {})
            created_at = existing.get("created_at") or now
            record = LevelMemoryRecord(
                id=key,
                symbol=symbol,
                level=score.level,
                level_type=score.level_type,
                level_state=score.state,
                score=score.score,
                confidence=score.confidence,
                direction_relevance=score.direction_relevance,
                recommended_use=score.recommended_use,
                evidence_summary=score.evidence_summary,
                positive_factors=score.positive_factors,
                negative_factors=score.negative_factors,
                historical_tp1_rate=score.historical_tp1_rate,
                historical_sl_rate=score.historical_sl_rate,
                ai_boost_count=int(existing.get("ai_boost_count", 0) or 0),
                ai_caution_count=int(existing.get("ai_caution_count", 0) or 0),
                ai_would_block_count=int(existing.get("ai_would_block_count", 0) or 0),
                session_name=session_name,
                timeframe=timeframe,
                created_at=created_at,
                updated_at=now,
            )
            self.memory[key] = record.to_dict()
        self._save_json(self.memory_path, self.memory)

    def create_manual_setup(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        level = float(payload.get("level") or payload.get("zone_low") or 0.0)
        score = self.score_level(
            str(payload.get("symbol", "XAUUSD")),
            level,
            level_type="manual_level",
            direction_relevance=str(payload.get("direction", "both")).upper(),
            current_price=float(payload.get("current_price") or level),
        )
        setup = {
            "id": str(payload.get("id") or f"manual:{payload.get('symbol', 'XAUUSD')}:{level:.2f}:{now}"),
            "symbol": str(payload.get("symbol", "XAUUSD")),
            "direction": str(payload.get("direction", "")).upper(),
            "zone_low": float(payload.get("zone_low") or level),
            "zone_high": float(payload.get("zone_high") or level),
            "level": level,
            "user_reason": str(payload.get("user_reason", "")),
            "timeframe": str(payload.get("timeframe", "M15")),
            "invalidation_level": float(payload.get("invalidation_level") or 0.0),
            "preferred_confirmation": str(payload.get("preferred_confirmation", "")),
            "status": str(payload.get("status", "pending")),
            "level_score": score.to_dict(),
            "evidence_summary": score.evidence_summary,
            "created_at": now,
            "updated_at": now,
            "resolved_at": "",
        }
        self.manual_setups.append(setup)
        self._save_json(self.manual_path, self.manual_setups)
        return setup

    def track_manual_setups(self, current_price: float) -> list[dict[str, Any]]:
        changed: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()
        for setup in self.manual_setups:
            if setup.get("status") in {"confirmed", "invalidated", "played_out"}:
                continue
            low = float(setup.get("zone_low") or setup.get("level") or 0.0)
            high = float(setup.get("zone_high") or setup.get("level") or 0.0)
            old = setup.get("status", "pending")
            if low <= current_price <= high:
                setup["status"] = "touched"
            elif abs(current_price - low) <= 10 or abs(current_price - high) <= 10:
                setup["status"] = "approaching"
            if setup.get("status") != old:
                setup["updated_at"] = now
                changed.append(dict(setup))
        if changed:
            self._save_json(self.manual_path, self.manual_setups)
        return changed


def _scenario_dict(market_plan, scenario_type: str) -> dict[str, Any]:
    plan = market_plan.to_dict() if hasattr(market_plan, "to_dict") else dict(market_plan or {})
    return dict(plan.get(f"{scenario_type}_scenario", {}) or {})


def _zone_bounds(scenario: dict[str, Any]) -> tuple[float, float]:
    low = float(scenario.get("watch_low") or scenario.get("zone_low") or 0.0)
    high = float(scenario.get("watch_high") or scenario.get("zone_high") or 0.0)
    if not low or not high:
        raw = str(scenario.get("watch_zone", ""))
        import re
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw)]
        if len(nums) >= 2:
            low, high = nums[0], nums[1]
    return min(low, high), max(low, high)


def validate_scenario_compliance(setup, market_plan, *, ai_label: str = "", current_price: float | None = None) -> ScenarioComplianceResult:
    plan = market_plan.to_dict() if hasattr(market_plan, "to_dict") else dict(market_plan or {})
    direction = str(getattr(setup, "direction", "") or (setup.get("direction") if isinstance(setup, dict) else "")).upper()
    scenario_type = str(getattr(setup, "scenario", "") or getattr(setup, "scenario_type", "") or (setup.get("scenario", "") if isinstance(setup, dict) else "") or "primary").lower()
    entry = float(getattr(setup, "suggested_entry", 0.0) or getattr(setup, "entry", 0.0) or (setup.get("entry", 0.0) if isinstance(setup, dict) else 0.0) or 0.0)
    confirmation_type = str(getattr(setup, "confirmation_type", "") or (setup.get("confirmation_type", "") if isinstance(setup, dict) else ""))
    ai_label = ai_label or str((setup.get("ai_label", "") if isinstance(setup, dict) else "") or "AI-ALLOWED SETUP")
    dominant_bias = str(plan.get("dominant_bias", "neutral")).lower()
    bias_strength = str(plan.get("bias_strength", "weak")).lower()
    scenario = _scenario_dict(market_plan, scenario_type)
    primary = _scenario_dict(market_plan, "primary")
    zone_low, zone_high = _zone_bounds(scenario)
    primary_direction = str(primary.get("direction", "")).upper()

    def block(reason: str, **details) -> ScenarioComplianceResult:
        logger.info("SCENARIO COMPLIANCE BLOCK: setup_id=%s reason=%s entry=%.2f", getattr(setup, "confirmation_signature", ""), reason, entry)
        return ScenarioComplianceResult(False, reason, "block_actionable", "manual_review_only", False, details)

    if scenario_type == "primary":
        if primary_direction and direction != primary_direction:
            return block("primary_direction_mismatch", primary_direction=primary_direction, direction=direction)
        if zone_low and zone_high:
            tolerance = 12.0
            if direction == "BUY" and entry < zone_low - tolerance:
                return block("primary_buy_entry_too_far_below_zone", watch_zone=f"{zone_low:.2f}-{zone_high:.2f}")
            if direction == "SELL" and entry > zone_high + tolerance:
                return block("primary_sell_entry_too_far_above_zone", watch_zone=f"{zone_low:.2f}-{zone_high:.2f}")
        logger.info("SCENARIO COMPLIANCE PASS: setup_id=%s reason=primary_matches_plan", getattr(setup, "confirmation_signature", ""))
        return ScenarioComplianceResult(True, "primary_matches_plan", "pass", "actionable", True)

    counter_bias_buy = dominant_bias == "bearish" and direction == "BUY"
    counter_bias_sell = dominant_bias == "bullish" and direction == "SELL"
    ai_ok = ai_label in {"AI-CONFIRMED SETUP", "AI-ALLOWED SETUP"}

    if counter_bias_buy:
        if entry < zone_low:
            return block("secondary_buy_entry_below_reclaim_zone", reclaim_zone=f"{zone_low:.2f}-{zone_high:.2f}", dominant_bias=dominant_bias)
        if confirmation_type == "structure_shift_confirmation" and not ai_ok:
            return block("structure_shift_counter_bias_requires_reclaim_confirmation", confirmation_type=confirmation_type, ai_label=ai_label)
        allowed_buy_confirmations = {"break_retest_close_confirmation", "displacement_confirmation", "sweep_reclaim_confirmation", "engulfing_level_confirmation"}
        if ai_ok:
            allowed_buy_confirmations.add("structure_shift_confirmation")
        if confirmation_type not in allowed_buy_confirmations:
            return block("secondary_buy_missing_reclaim_confirmation", confirmation_type=confirmation_type)
        if not ai_ok:
            return block("secondary_buy_ai_not_confirmed_or_allowed", ai_label=ai_label)

    if counter_bias_sell:
        if entry > zone_high:
            return block("secondary_sell_entry_above_breakdown_zone", breakdown_zone=f"{zone_low:.2f}-{zone_high:.2f}", dominant_bias=dominant_bias)
        if confirmation_type == "structure_shift_confirmation" and not ai_ok:
            return block("structure_shift_counter_bias_requires_breakdown_confirmation", confirmation_type=confirmation_type, ai_label=ai_label)
        allowed_sell_confirmations = {"break_retest_close_confirmation", "displacement_confirmation", "sweep_reclaim_confirmation", "engulfing_level_confirmation"}
        if ai_ok:
            allowed_sell_confirmations.add("structure_shift_confirmation")
        if confirmation_type not in allowed_sell_confirmations:
            return block("secondary_sell_missing_breakdown_confirmation", confirmation_type=confirmation_type)
        if not ai_ok:
            return block("secondary_sell_ai_not_confirmed_or_allowed", ai_label=ai_label)

    if bias_strength == "strong" and confirmation_type == "structure_shift_confirmation" and (counter_bias_buy or counter_bias_sell):
        return block("structure_shift_confirmation_counter_bias_not_enough", confirmation_type=confirmation_type)

    logger.info("SCENARIO COMPLIANCE PASS: setup_id=%s reason=scenario_conditions_met", getattr(setup, "confirmation_signature", ""))
    return ScenarioComplianceResult(True, "scenario_conditions_met", "pass", "actionable", True)
