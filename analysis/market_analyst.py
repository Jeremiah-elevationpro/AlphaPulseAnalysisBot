from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from analysis.psych_levels import generate_psychological_levels, select_actionable_psych_levels
from analysis.tp_engine import (
    filter_market_plan_targets,
    MIN_TP1_DISTANCE_PIPS,
    PREFERRED_SCENARIO_TP_DISTANCE_PIPS,
    PREFERRED_TP1_DISTANCE_PIPS,
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
        broken_levels = self._broken_levels(current_price, psych_prices, h1)
        liquidity_zones = sorted(set(supports[:2] + resistances[:2] + broken_levels[:2]))

        primary = self._build_primary_scenario(current_price, dominant_bias, supports, resistances, psych_prices, h4_state, h1_state)
        secondary = self._build_secondary_scenario(current_price, dominant_bias, supports, resistances, psych_prices, h4_state, h1_state, primary=primary)
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
            invalidation_levels=list(dict.fromkeys(primary.market_plan_targets[:1] + secondary.market_plan_targets[:1] + [primary.watch_high, secondary.watch_low])),
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
        )

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

    def _build_primary_scenario(self, current_price: float, dominant_bias: str, supports: list[float], resistances: list[float], psych: list[float], h4_state: dict[str, Any], h1_state: dict[str, Any]) -> Scenario:
        if dominant_bias == "bearish":
            resist = [lvl for lvl in resistances if lvl >= current_price]
            zone_low = resist[0] if resist else round(current_price + 10, 2)
            zone_high = resist[1] if len(resist) > 1 and resist[1] - zone_low <= 30 else round(zone_low + 20, 2)
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
            )
            return Scenario(
                direction="SELL",
                reason="H4/H1 bearish structure remains in control below nearby resistance.",
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
                invalidation=f"strong M15/H1 close above {zone_high:.2f}",
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
            )
        supports_above = [lvl for lvl in supports if lvl <= current_price]
        zone_high = supports_above[-1] if supports_above else round(current_price - 10, 2)
        zone_low = supports_above[-2] if len(supports_above) > 1 and zone_high - supports_above[-2] <= 30 else round(zone_high - 20, 2)
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
        )
        return Scenario(
            direction="BUY",
            reason="H4/H1 bullish structure remains in control above nearby support.",
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
            invalidation=f"strong M15/H1 close below {zone_low:.2f}",
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
        )

    def _build_secondary_scenario(self, current_price: float, dominant_bias: str, supports: list[float], resistances: list[float], psych: list[float], h4_state: dict[str, Any], h1_state: dict[str, Any], primary: "Scenario | None" = None) -> "Scenario":
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
        )

    def _watch_zone_from_scenario(self, symbol: str, scenario_type: str, scenario: Scenario, current_price: float) -> Optional[dict[str, Any]]:
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
        elif distance > 80:
            status = "stale"

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
            "invalidation_level": high if scenario.direction == "SELL" else low,
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

        return (
            f"SPENCER MARKET PLAN — {symbol}\n\n"
            f"Current Price: {current_price:.2f}\n\n"
            f"H4 Context:\n{h4_context}\n\n"
            f"H1 Context:\n{h1_context}\n\n"
            f"M15 Execution Context:\n{m15_context}\n\n"
            f"Primary Scenario{status_tag(primary_zone_status, primary_played_out_note)}:\n"
            f"{primary.direction} fresh retest of {primary.watch_zone} only\n"
            f"Trigger: {'; '.join(primary.trigger_conditions)}\n"
            f"Targets: {' / '.join(f'{target:.2f}' for target in primary.market_plan_targets[:5])}\n"
            f"Invalidation: {primary.invalidation}\n\n"
            f"{secondary_header}{status_tag(secondary_zone_status, secondary_played_out_note)}:\n"
            f"{secondary.direction} only if {secondary.watch_zone} activates.\n"
            f"Trigger: {'; '.join(secondary.trigger_conditions)}\n"
            f"Targets: {' / '.join(f'{target:.2f}' for target in secondary.market_plan_targets[:5])}\n"
            f"Invalidation: {secondary.invalidation}\n\n"
            f"Psychological Levels:\n"
            f"{', '.join(f'{target:.2f}' for target in psych_prices[:12])}\n\n"
            f"Watching:\n"
            f"Psychological levels, structure retests, liquidity sweeps, break/retest, displacement, engulfing at key levels."
        )
