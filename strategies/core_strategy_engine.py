"""Spencer Core Strategy Engine.

Replaces the legacy multi-strategy_manager with a clean, focused engine that
emits actionable setups from exactly three strategies:

  * supply_demand_retest
  * session_liquidity_sweep_reversal
  * break_retest_continuation

Everything else (Level Intelligence, Session Liquidity, AI Advisory, Scenario
Compliance, TP/SL engine, Risk/Trade Management) remains as supporting evidence
only — those engines may score, validate, reject, or improve setups but never
generate trades directly.

Public API
==========
    engine = CoreStrategyEngine(level_intel_engine, session_liquidity_engine)
    result = engine.run(data, current_price=price, ctx=ctx, plan=market_plan)
    # result.primary, result.alternative, result.candidates, result.rejected,
    # result.market_condition

The orchestrator is stateless across scans; persistent dedupe / cooldown lives
in main.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import pandas as pd

from config.settings import (
    CORE_ALLOWED_STRATEGY_TYPES,
    CORE_MIN_LEVEL_INTEL_SCORE,
    CORE_MIN_LIQUIDITY_SCORE,
    CORE_MIN_RISK_PIPS,
    CORE_MIN_TP1_RR,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StrategySetup:
    """A normalized, actionable setup from one of the three core strategies.

    Every field here is mandatory at the time the setup is emitted to the
    main loop. The engine enforces this shape; downstream code (Telegram,
    trade management, dashboard) reads it directly.
    """

    strategy_type: str
    symbol: str
    direction: str  # "BUY" | "SELL"
    entry_zone_low: float
    entry_zone_high: float
    trigger_level: float
    confirmation_required: list[str]
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    invalidation: float
    reason: str
    confidence_internal: float
    status: str = "watching"
    # supporting evidence (filled in by core engine after strategy emits)
    level_intel_score: float = 0.0
    liquidity_score: float = 0.0
    ai_label: str = ""
    scenario_compliance: str = ""
    session_name: str = ""
    higher_tf: str = ""
    lower_tf: str = ""
    confirmation_candle_time: Optional[datetime] = None

    def fingerprint(self) -> str:
        """Stable dedupe key — unique per setup across scans."""
        return (
            f"core:{self.strategy_type}:{self.direction}:"
            f"{self.symbol}:{self.entry:.2f}:{self.sl:.2f}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_type":        self.strategy_type,
            "symbol":               self.symbol,
            "direction":            self.direction,
            "entry_zone_low":       self.entry_zone_low,
            "entry_zone_high":      self.entry_zone_high,
            "trigger_level":        self.trigger_level,
            "confirmation_required": list(self.confirmation_required),
            "entry":                self.entry,
            "sl":                   self.sl,
            "tp1":                  self.tp1,
            "tp2":                  self.tp2,
            "tp3":                  self.tp3,
            "invalidation":         self.invalidation,
            "reason":               self.reason,
            "confidence_internal":  self.confidence_internal,
            "status":               self.status,
            "level_intel_score":    self.level_intel_score,
            "liquidity_score":      self.liquidity_score,
            "ai_label":             self.ai_label,
            "scenario_compliance":  self.scenario_compliance,
            "session_name":         self.session_name,
            "higher_tf":            self.higher_tf,
            "lower_tf":             self.lower_tf,
            "confirmation_candle_time": (
                self.confirmation_candle_time.isoformat()
                if self.confirmation_candle_time else None
            ),
        }


@dataclass
class RejectedCandidate:
    """A candidate that didn't make it to actionable — for diagnostics."""
    strategy_type: str
    direction: str
    reason: str
    level: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_type": self.strategy_type,
            "direction":     self.direction,
            "reason":        self.reason,
            "level":         self.level,
            "detail":        self.detail,
        }


@dataclass
class StrategyResult:
    """Complete output of one CoreStrategyEngine.run() call."""

    market_condition: str
    primary: Optional[StrategySetup] = None
    alternative: Optional[StrategySetup] = None
    candidates: list[StrategySetup] = field(default_factory=list)
    rejected: list[RejectedCandidate] = field(default_factory=list)
    scan_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_condition": self.market_condition,
            "primary":          self.primary.to_dict() if self.primary else None,
            "alternative":      self.alternative.to_dict() if self.alternative else None,
            "candidates_count": len(self.candidates),
            "rejected_count":   len(self.rejected),
            "rejected":         [r.to_dict() for r in self.rejected[:20]],
            "scan_summary":     dict(self.scan_summary),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Allowed confirmation types
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_BUY_CONFIRMATIONS = {
    "bullish_rejection",
    "bullish_engulfing",
    "bullish_displacement_close",
    "break_retest_close_above",
    "sweep_below_close_back_above",
}
ALLOWED_SELL_CONFIRMATIONS = {
    "bearish_rejection",
    "bearish_engulfing",
    "bearish_displacement_close",
    "break_retest_close_below",
    "sweep_above_close_back_below",
}

# Confirmations explicitly disabled as independent triggers (confluence only).
DISABLED_STANDALONE_CONFIRMATIONS = {
    "structure_shift_confirmation",
    "raw_gap_confirmation",
    "raw_qm_confirmation",
    "psychological_level_touch",
}


# ─────────────────────────────────────────────────────────────────────────────
# Risk validation
# ─────────────────────────────────────────────────────────────────────────────

PIP_SIZE = 0.1  # XAUUSD


def _pip_distance(a: float, b: float) -> float:
    return abs(a - b) / PIP_SIZE


def validate_setup_risk(setup: StrategySetup) -> tuple[bool, str]:
    """Spec section: risk validation before a setup becomes Primary/Entry.

    BUY: SL below entry/zone; TP1/TP2/TP3 above entry; min risk > CORE_MIN_RISK_PIPS;
         TP1 RR >= CORE_MIN_TP1_RR.
    SELL: mirror.
    """
    direction = setup.direction.upper()
    entry = setup.entry
    sl = setup.sl
    tps = [setup.tp1, setup.tp2, setup.tp3]

    if entry == 0 or sl == 0:
        return False, "invalid_entry_or_sl"
    risk_pips = _pip_distance(entry, sl)
    if risk_pips < CORE_MIN_RISK_PIPS:
        return False, f"risk_too_small ({risk_pips:.1f}p < {CORE_MIN_RISK_PIPS}p)"

    if direction == "BUY":
        if sl >= entry:
            return False, "sl_not_below_entry"
        for i, tp in enumerate(tps, start=1):
            if tp <= entry:
                return False, f"tp{i}_not_above_entry"
    elif direction == "SELL":
        if sl <= entry:
            return False, "sl_not_above_entry"
        for i, tp in enumerate(tps, start=1):
            if tp >= entry:
                return False, f"tp{i}_not_below_entry"
    else:
        return False, f"unknown_direction:{direction}"

    reward_pips = _pip_distance(setup.tp1, entry)
    rr = reward_pips / risk_pips if risk_pips > 0 else 0.0
    if rr < CORE_MIN_TP1_RR:
        return False, f"tp1_rr_too_low ({rr:.2f}R < {CORE_MIN_TP1_RR}R)"

    return True, "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class CoreStrategyEngine:
    """Stateless orchestrator over the three core strategies.

    Wires in the supporting engines so each strategy can ask the same shared
    Level Intelligence / Session Liquidity instance for evidence rather than
    rebuilding scores per scan.
    """

    def __init__(
        self,
        level_intel_engine: Any = None,
        session_liquidity_engine: Any = None,
        ai_advisor: Any = None,
        scenario_compliance_validator: Any = None,
    ) -> None:
        # Lazy import to avoid circular import at module load
        from strategies.supply_demand_retest import SupplyDemandRetestStrategy
        from strategies.session_liquidity_sweep_reversal import (
            SessionLiquiditySweepReversalStrategy,
        )
        from strategies.break_retest_continuation import (
            BreakRetestContinuationStrategy,
        )

        self.level_intel = level_intel_engine
        self.session_liquidity = session_liquidity_engine
        self.ai_advisor = ai_advisor
        self.scenario_compliance = scenario_compliance_validator
        self.strategies = {
            "supply_demand_retest": SupplyDemandRetestStrategy(level_intel_engine),
            "session_liquidity_sweep_reversal": SessionLiquiditySweepReversalStrategy(
                session_liquidity_engine, level_intel_engine
            ),
            "break_retest_continuation": BreakRetestContinuationStrategy(
                level_intel_engine
            ),
        }
        logger.info(
            "CoreStrategyEngine initialised — strategies=%s",
            ", ".join(sorted(self.strategies.keys())),
        )

    # ──────────────────────────────────────────────────────────────
    # Market condition classifier
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def detect_market_condition(
        data: dict[str, pd.DataFrame],
        current_price: float | None,
    ) -> str:
        """Returns one of: trending | ranging | liquidity_sweep | breakout_retest.

        Heuristic only — used to bias which strategy's candidates rank higher.
        Cheap by design: looks at last 20 M15 candles.
        """
        m15 = data.get("M15") if data else None
        if m15 is None or len(m15) < 20 or current_price is None:
            return "unknown"
        recent = m15.tail(20)
        try:
            highs = recent["high"].astype(float)
            lows = recent["low"].astype(float)
            closes = recent["close"].astype(float)
            rng_high = float(highs.max())
            rng_low = float(lows.min())
            rng_pips = (rng_high - rng_low) / PIP_SIZE
            net_move = float(closes.iloc[-1] - closes.iloc[0])
            net_pips = abs(net_move) / PIP_SIZE
            wick_top = float((highs - closes.combine(recent["open"].astype(float), max)).clip(lower=0).sum())
            wick_bot = float((closes.combine(recent["open"].astype(float), min) - lows).clip(lower=0).sum())
        except Exception:
            return "unknown"

        if rng_pips <= 60:
            return "ranging"
        if net_pips > rng_pips * 0.55:
            return "trending"
        # If recent bars have very long wicks at extremes, treat as sweep-favourable
        if (wick_top + wick_bot) / max(1.0, rng_high - rng_low) > 4.0:
            return "liquidity_sweep"
        return "breakout_retest"

    # ──────────────────────────────────────────────────────────────
    # Main scan
    # ──────────────────────────────────────────────────────────────

    def run(
        self,
        data: dict[str, pd.DataFrame],
        *,
        current_price: float | None,
        ctx: Any = None,
        plan: Any = None,
        symbol: str = "XAUUSD",
    ) -> StrategyResult:
        """Run all three strategies; rank candidates; pick primary + alternative."""
        market_condition = self.detect_market_condition(data, current_price)
        all_candidates: list[StrategySetup] = []
        rejected: list[RejectedCandidate] = []
        per_strategy_counts: dict[str, dict[str, int]] = {}

        for name, strategy in self.strategies.items():
            try:
                candidates, strategy_rejected = strategy.scan(
                    data,
                    current_price=current_price,
                    ctx=ctx,
                    plan=plan,
                    symbol=symbol,
                )
            except Exception as exc:
                logger.error(
                    "CORE STRATEGY ERROR: strategy=%s err=%s", name, exc, exc_info=True
                )
                rejected.append(
                    RejectedCandidate(
                        strategy_type=name,
                        direction="?",
                        reason="strategy_exception",
                        detail=str(exc)[:120],
                    )
                )
                per_strategy_counts[name] = {"candidates": 0, "rejected": 1}
                continue

            # Each strategy MAY return both raw candidates and pre-rejected ones.
            # Apply final risk validation here as the last gate.
            validated: list[StrategySetup] = []
            for setup in candidates:
                if setup.strategy_type not in CORE_ALLOWED_STRATEGY_TYPES:
                    rejected.append(
                        RejectedCandidate(
                            strategy_type=setup.strategy_type,
                            direction=setup.direction,
                            reason="disallowed_strategy_type",
                            level=setup.entry,
                            detail=setup.strategy_type,
                        )
                    )
                    continue
                ok, why = validate_setup_risk(setup)
                if not ok:
                    rejected.append(
                        RejectedCandidate(
                            strategy_type=setup.strategy_type,
                            direction=setup.direction,
                            reason=why,
                            level=setup.entry,
                            detail=f"sl={setup.sl:.2f} tp1={setup.tp1:.2f}",
                        )
                    )
                    continue
                validated.append(setup)
            rejected.extend(strategy_rejected)
            per_strategy_counts[name] = {
                "candidates": len(validated),
                "rejected": len(strategy_rejected)
                + (len(candidates) - len(validated)),
            }
            all_candidates.extend(validated)

        # Score each candidate
        for setup in all_candidates:
            setup.confidence_internal = self._score_candidate(setup, market_condition)

        # Pick primary + alternative
        primary, alternative = self._pick_primary_alternative(all_candidates)

        scan_summary = {
            "market_condition": market_condition,
            "candidates_total": len(all_candidates),
            "rejected_total":   len(rejected),
            "by_strategy":      per_strategy_counts,
            "scanned_at":       datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "CORE STRATEGY SCAN: condition=%s candidates=%d rejected=%d primary=%s",
            market_condition,
            len(all_candidates),
            len(rejected),
            f"{primary.strategy_type}:{primary.direction}@{primary.entry:.2f}"
            if primary
            else "none",
        )

        return StrategyResult(
            market_condition=market_condition,
            primary=primary,
            alternative=alternative,
            candidates=all_candidates,
            rejected=rejected,
            scan_summary=scan_summary,
        )

    # ──────────────────────────────────────────────────────────────
    # Scoring + ranking
    # ──────────────────────────────────────────────────────────────

    def _score_candidate(self, setup: StrategySetup, market_condition: str) -> float:
        """Combine evidence into a single 0-100 confidence score.

        Components (weights sum to 100):
          * Level Intelligence score   (35 pts)
          * Liquidity / sweep score    (25 pts)
          * TP/SL geometry quality     (20 pts)
          * Scenario compliance bonus  (10 pts)
          * Market condition fit       (10 pts)
        """
        # Level Intelligence (already 0-100)
        li_norm = max(0.0, min(setup.level_intel_score / 100.0, 1.0))
        # Liquidity (0-100)
        liq_norm = max(0.0, min(setup.liquidity_score / 100.0, 1.0))

        # TP/SL geometry: prefer TP1 RR >= 1.5 capped at 3.0
        risk_pips = max(_pip_distance(setup.entry, setup.sl), 0.001)
        rr1 = _pip_distance(setup.tp1, setup.entry) / risk_pips
        rr_norm = max(0.0, min((rr1 - 0.5) / 2.5, 1.0))

        # Scenario compliance bonus (pass = +1, manual_review = 0.5, fail = 0)
        sc = (setup.scenario_compliance or "").lower()
        sc_norm = 1.0 if sc in {"pass", "ok", "actionable"} else 0.5 if "review" in sc else 0.0

        # Market condition fit
        fit = {
            "supply_demand_retest": {"ranging": 1.0, "trending": 0.7, "liquidity_sweep": 0.4, "breakout_retest": 0.6},
            "session_liquidity_sweep_reversal": {"liquidity_sweep": 1.0, "ranging": 0.7, "trending": 0.4, "breakout_retest": 0.5},
            "break_retest_continuation": {"breakout_retest": 1.0, "trending": 0.9, "ranging": 0.3, "liquidity_sweep": 0.5},
        }
        cond_score = fit.get(setup.strategy_type, {}).get(market_condition, 0.5)

        score = (
            35.0 * li_norm
            + 25.0 * liq_norm
            + 20.0 * rr_norm
            + 10.0 * sc_norm
            + 10.0 * cond_score
        )
        return round(score, 1)

    def _pick_primary_alternative(
        self, candidates: list[StrategySetup]
    ) -> tuple[Optional[StrategySetup], Optional[StrategySetup]]:
        """Primary = highest score.
        Alternative = highest score among opposite-direction candidates, or
        second-best same-direction if no opposite exists.
        """
        if not candidates:
            return None, None
        ranked = sorted(candidates, key=lambda s: s.confidence_internal, reverse=True)
        primary = ranked[0]
        opposite = [c for c in ranked[1:] if c.direction.upper() != primary.direction.upper()]
        alternative = opposite[0] if opposite else (ranked[1] if len(ranked) > 1 else None)
        return primary, alternative


# ─────────────────────────────────────────────────────────────────────────────
# Helpers exposed to strategies
# ─────────────────────────────────────────────────────────────────────────────


def attach_level_intel_evidence(
    setup: StrategySetup, level_intel_engine: Any, *, current_price: float | None
) -> None:
    """Look up the level_intel score for the setup's trigger level and attach
    it to the candidate. Safe no-op if the engine isn't available.
    """
    if level_intel_engine is None or current_price is None:
        return
    try:
        score = level_intel_engine.score_level(
            level=float(setup.trigger_level),
            current_price=float(current_price),
            direction=setup.direction,
        )
        if hasattr(score, "score"):
            setup.level_intel_score = float(getattr(score, "score", 0.0))
        elif isinstance(score, dict):
            setup.level_intel_score = float(score.get("score", 0.0))
    except Exception:
        # supporting evidence is best-effort, never raise
        return


def candidates_filter_min_level_intel(
    candidates: Iterable[StrategySetup],
) -> tuple[list[StrategySetup], list[RejectedCandidate]]:
    """Apply CORE_MIN_LEVEL_INTEL_SCORE gate. Returns (kept, rejected)."""
    kept: list[StrategySetup] = []
    rejected: list[RejectedCandidate] = []
    for s in candidates:
        if s.level_intel_score >= CORE_MIN_LEVEL_INTEL_SCORE:
            kept.append(s)
        else:
            rejected.append(
                RejectedCandidate(
                    strategy_type=s.strategy_type,
                    direction=s.direction,
                    reason="level_intel_score_below_min",
                    level=s.trigger_level,
                    detail=f"score={s.level_intel_score:.1f} < {CORE_MIN_LEVEL_INTEL_SCORE}",
                )
            )
    return kept, rejected


def log_legacy_disabled(strategy_name: str) -> None:
    """Spec-required log marker when a legacy strategy entry source is blocked."""
    logger.info("LEGACY STRATEGY DISABLED: strategy_name=%s", strategy_name)
