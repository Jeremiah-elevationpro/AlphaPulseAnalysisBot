"""
AlphaPulse - Multi-Timeframe Analysis Engine
==============================================
Orchestrates the full detection pipeline across all timeframe pairs:
  H4 → H1   (major structure only — trend context)
  H1 → M30  (major + recent leg + previous leg)
  M30 → M15 (major + recent leg + previous leg — priority execution pair)

For each pair the pipeline is:
  1. Detect MAJOR levels on the higher timeframe
  2. Detect RECENT LEG levels on the lower timeframe
  3. Detect PREVIOUS LEG levels on the lower timeframe (fallback)
  4. Generate standalone PSYCHOLOGICAL levels from current price
  5. Compute QM flags (break episode count) for all levels
  6. Mark psychological confluence on all levels
  7. Run confirmation candle checks on every level set
  8. Apply market-context filters (session, trend, volatility, news, sweep)
  9. Assign setup_type and confidence adjustments
 10. Return sorted SetupResult list (priority: QM > psych > recent > previous > major)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import pandas as pd

from strategies.level_detector import LevelDetector, LevelInfo
from strategies.confirmation import ConfirmationEngine, ConfirmationResult, ConfirmationRejection
from strategies.filters import MarketContextEngine, MarketContext, StructureQualityFilter
from strategies.level_selector import EliteLevelSelector
from strategies.micro_confirmation import MicroConfirmationEngine
from strategies.execution_filters import ExecutionFilterEngine
from config.settings import (
    TIMEFRAME_PAIRS,
    DISABLED_TIMEFRAME_PAIRS,
    MIN_CONFIDENCE, MAX_CONFIDENCE,
    PIP_SIZE,
    SESSION_CONFIDENCE_BONUS,
    BIAS_STRONG_BLOCK_COUNTER_TREND,
    BIAS_MODERATE_COUNTER_TREND_CONFIDENCE_PENALTY,
    BIAS_MODERATE_COUNTER_TREND_MIN_CONFIDENCE,
    BIAS_BLOCK_WEAK_DOMINANT,
    CONFIRMATION_SCORE_BONUS,
    CONFIRMATION_SCORE_BONUS_CAP,
    FINAL_SETUP_SCORE_MIN,
    MAX_ACTIVE_CANDIDATES_PER_SCAN,
    DIRECTIONAL_COUNTER_SCORE_PREMIUM,
    SESSION_PROFILE,
    SESSION_FINAL_SCORE_ADJUSTMENT,
    SESSION_MIN_CONFIRMATION_BONUS,
    MICRO_CONFIRMATION_ENABLED,
    MICRO_CONFIRMATION_USE_M1_FALLBACK,
    MICRO_CONFIRMATION_LOOKBACK,
    MICRO_CONFIDENCE_SCALE,
    MICRO_DOUBLE_PATTERN_MIN_QUALITY,
    MICRO_LIQUIDITY_SWEEP_EXTRA_BONUS,
    MICRO_PRIORITY_WEIGHTS,
    MICRO_QUALITY_SCORE_SCALE,
    MICRO_STRONG_SCORE,
    MICRO_SESSION_MIN_SCORE,
    MICRO_SESSION_REQUIRED,
    MICRO_LONDON_NO_MICRO_PENALTY,
    MICRO_NY_NO_MICRO_PENALTY,
    ACTIVE_STRATEGY_LEVEL_TYPES,
    ACTIVE_STRATEGY_REQUIRE_MICRO,
    ACTIVE_STRATEGY_ALLOWED_MICRO_TYPES,
    ENABLE_AV_DIVERSITY,
    AV_MIN_SCORE_THRESHOLD,
    AV_REPLACE_MARGIN,
    AV_MICRO_CONFIRMATION_BONUS,
    LEVEL_MIN_PRICE_DISTANCE_PIPS,
    LEVEL_PAIR_MAX_DISTANCE_PIPS,
    MIN_TP_CLEARANCE_PIPS,
    TP_PIPS,
    TP1_BONUS_MIN_PIPS,
    TP1_QUALITY_SCORE_BONUS,
    HIGH_QUALITY_TRADE_SCORE,
    ENGULFING_BOOST_SCORE,
    ENGULFING_SWEEP_COMBO_BOOST,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Timeframe pairs where recent/previous leg analysis is applied
_LEG_PAIRS = {("H1", "M30"), ("M30", "M15")}
_ACTIVE_LEVEL_TYPES = set(ACTIVE_STRATEGY_LEVEL_TYPES)
_ACTIVE_MICRO_TYPES = set(ACTIVE_STRATEGY_ALLOWED_MICRO_TYPES)
_ACTIVE_USES_AV = bool(_ACTIVE_LEVEL_TYPES & {"A", "V"})


# ─────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────

@dataclass
class TimeframeLevels:
    higher_tf: str
    lower_tf: str
    levels: List[LevelInfo] = field(default_factory=list)           # major
    recent_levels: List[LevelInfo] = field(default_factory=list)    # recent leg
    previous_levels: List[LevelInfo] = field(default_factory=list)  # previous leg
    psych_levels: List[LevelInfo] = field(default_factory=list)     # psychological

    def resistances(self) -> List[LevelInfo]:
        return [
            l for l in self.levels
            if l.level_type == "A"
            or (l.level_type == "Gap" and getattr(l, "trade_direction", "") == "SELL")
        ]

    def supports(self) -> List[LevelInfo]:
        return [
            l for l in self.levels
            if l.level_type == "V"
            or (l.level_type == "Gap" and getattr(l, "trade_direction", "") == "BUY")
        ]

    def recent_resistances(self) -> List[LevelInfo]:
        return [
            l for l in self.recent_levels
            if l.level_type == "A"
            or (l.level_type == "Gap" and getattr(l, "trade_direction", "") == "SELL")
        ]

    def recent_supports(self) -> List[LevelInfo]:
        return [
            l for l in self.recent_levels
            if l.level_type == "V"
            or (l.level_type == "Gap" and getattr(l, "trade_direction", "") == "BUY")
        ]

    def all_levels(self) -> List[LevelInfo]:
        return self.levels + self.recent_levels + self.previous_levels + self.psych_levels


@dataclass
class MarketOutlook:
    """Snapshot of all detected levels — sent to Telegram before confirmation."""
    pair: str
    timeframe_levels: List[TimeframeLevels]
    timestamp: pd.Timestamp
    context: Optional[MarketContext] = None

    def format_telegram(self) -> str:
        lines = ["📊 *AlphaPulse Market Outlook (XAUUSD)*\n"]

        if self.context:
            sess = self.context.session_name.title() if self.context.session_name else "Off-session"
            bias_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(
                self.context.h4_bias, "➡️")
            lines.append(
                f"_{sess} | H4 {bias_emoji} {self.context.h4_bias.title()}_\n"
            )

        for tfl in self.timeframe_levels:
            lines.append(f"*{tfl.higher_tf}→{tfl.lower_tf}:*")
            for lvl in tfl.resistances()[:2]:
                tag = "🔴 Res" if lvl.level_type == "A" else "🔴 Gap"
                qm  = " ⚡QM" if lvl.is_qm else ""
                psy = " 🔮" if lvl.is_psychological else ""
                lines.append(f"  {tag}: `{lvl.price:.2f}`{qm}{psy}")
            for lvl in tfl.supports()[:2]:
                tag = "🟢 Sup" if lvl.level_type == "V" else "🟢 Gap"
                qm  = " ⚡QM" if lvl.is_qm else ""
                psy = " 🔮" if lvl.is_psychological else ""
                lines.append(f"  {tag}: `{lvl.price:.2f}`{qm}{psy}")
            for lvl in (tfl.recent_resistances() + tfl.recent_supports())[:2]:
                tag = "↳ RL" if lvl.scope == "recent" else "↳ PL"
                lines.append(f"  {tag}: `{lvl.price:.2f}` _(leg)_")
            if not tfl.all_levels():
                lines.append("  • No levels")
            lines.append("")

        lines.append("_Waiting for confirmation candle..._")
        return "\n".join(lines)


@dataclass
class SetupResult:
    """A confirmed trade setup ready for signal generation."""
    pair: str
    direction: str           # "BUY" | "SELL"
    higher_tf: str
    lower_tf: str
    level: LevelInfo
    confirmation: ConfirmationResult
    confidence: float = 0.5

    # Classification
    setup_type: str = "recent_leg"
    # "recent_leg" | "previous_leg" | "major" | "qm_level" |
    # "imbalance_confluence" | "psychological_confluence"

    # Context flags (all influence confidence and signal formatting)
    is_qm: bool = False
    is_psychological: bool = False
    is_liquidity_sweep: bool = False
    session_name: str = ""       # "london" | "new_york" | ""
    h4_bias: str = "neutral"
    bias_strength: str = "weak"
    trend_aligned: bool = True
    final_score: float = 0.0
    micro_confirmation_type: str = "none"
    micro_confirmation_score: float = 0.0
    micro_layer_decision: str = "neutral"
    h1_liquidity_sweep: bool = False
    h1_sweep_direction: str = "none"
    h1_reclaim_confirmed: bool = False
    pd_location: str = "unknown"
    pd_filter_score: float = 0.0
    bias_gate_result: str = "not_checked"
    tp1_room_pips: float = 0.0
    tp1_quality_bonus: float = 0.0
    engulfing_bonus: float = 0.0
    high_quality_trade: bool = False
    micro_strength: str = "normal"
    strategy_type: str = "gap_sweep"
    source: str = "live_bot"
    dominant_bias: str = "neutral"
    quality_rejection_count: int = 0
    structure_break_count: int = 0
    confirmation_score: float = 0.0
    confirmation_path: str = ""
    revisit_time: Optional[datetime] = None
    confirmation_time: Optional[datetime] = None
    confirmation_candles_used: int = 0
    confluence_with: List[str] = field(default_factory=list)

    # Opposing structural levels — used by signal_gen for TP clearance check (Filter 5)
    opposing_levels: List[LevelInfo] = field(default_factory=list)


# ─────────────────────────────────────────────────────────
# CONFIDENCE ADJUSTMENT
# ─────────────────────────────────────────────────────────

def _adjust_confidence(base: float, setup: SetupResult) -> float:
    """Apply all additive/subtractive adjustments to the base confidence score."""
    score = base

    # Scope bonus
    if setup.setup_type == "recent_leg":
        score += 0.08
    elif setup.setup_type == "previous_leg":
        score += 0.04
    elif setup.setup_type == "imbalance_confluence":
        score += 0.02
    elif setup.setup_type == "major":
        score -= 0.03

    # QM boost
    if setup.is_qm:
        score += 0.15

    # Psychological confluence
    if setup.is_psychological:
        psych_bonus = {"major": 0.12, "medium": 0.08, "minor": 0.04}
        score += psych_bonus.get(setup.level.psych_strength, 0.05)

    confirmation_bonus = _confirmation_score_bonus(setup.confirmation.confirmation_type)
    if confirmation_bonus:
        score += confirmation_bonus / 100.0

    micro_score = float(getattr(setup, "micro_confirmation_score", 0.0) or 0.0)
    if micro_score:
        score += micro_score / (MICRO_CONFIDENCE_SCALE or 100.0)
    pd_score = float(getattr(setup, "pd_filter_score", 0.0) or 0.0)
    if pd_score:
        score += pd_score / 100.0

    # Session
    score += SESSION_CONFIDENCE_BONUS.get(setup.session_name or "off_session", 0.0)

    # Trend alignment — counter-trend setups are blocked before reaching here;
    # this bonus rewards confirmed trend-aligned setups.
    if setup.trend_aligned:
        score += 0.05
    elif setup.bias_strength == "moderate":
        score -= BIAS_MODERATE_COUNTER_TREND_CONFIDENCE_PENALTY

    return round(float(min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, score))), 3)


def _confirmation_score_bonus(confirmation_type: str) -> float:
    raw = CONFIRMATION_SCORE_BONUS.get(confirmation_type or "rejection", 0)
    if raw > 0:
        return min(float(raw), CONFIRMATION_SCORE_BONUS_CAP)
    return float(raw)


def _is_tradeable_bias(dominant_bias: str, bias_strength: str) -> bool:
    if not BIAS_BLOCK_WEAK_DOMINANT:
        return True
    return dominant_bias in ("bullish", "bearish") and bias_strength in ("moderate", "strong")


def _compute_final_setup_score(setup: SetupResult) -> float:
    selection = float(getattr(setup.level, "selection_score", getattr(setup.level, "quality_score", 0.0)) or 0.0)
    confidence_score = setup.confidence * 100.0
    confirmation_bonus = _confirmation_score_bonus(setup.confirmation.confirmation_type)
    micro_bonus = float(getattr(setup, "micro_confirmation_score", 0.0) or 0.0)
    pd_bonus = float(getattr(setup, "pd_filter_score", 0.0) or 0.0)
    tp1_bonus = float(getattr(setup, "tp1_quality_bonus", 0.0) or 0.0)
    engulfing_bonus = float(getattr(setup, "engulfing_bonus", 0.0) or 0.0)
    # Extra reward for A/V setups that earned micro confirmation — compensates for their
    # stricter scoring path relative to Gap levels.
    micro_type = getattr(setup, "micro_confirmation_type", "none") or "none"
    if (
        getattr(setup.level, "level_type", "") in ("A", "V")
        and micro_type not in ("none", "micro_contradiction")
        and micro_bonus > 0
    ):
        micro_bonus += AV_MICRO_CONFIRMATION_BONUS
        logger.debug(
            "A/V MICRO BONUS: %s %.2f | type=%s micro_base=%.0f +%d",
            setup.level.level_type, setup.level.price, micro_type,
            micro_bonus - AV_MICRO_CONFIRMATION_BONUS, AV_MICRO_CONFIRMATION_BONUS,
        )
    alignment_bonus = 6.0 if setup.trend_aligned else -DIRECTIONAL_COUNTER_SCORE_PREMIUM
    return round(
        selection * 0.65
        + confidence_score * 0.35
        + confirmation_bonus
        + micro_bonus
        + pd_bonus
        + tp1_bonus
        + engulfing_bonus
        + alignment_bonus,
        1,
    )


def _final_score_threshold(setup: SetupResult) -> float:
    if (getattr(setup, "micro_confirmation_type", "") or "") == "liquidity_sweep_reclaim":
        return 60.0
    threshold = FINAL_SETUP_SCORE_MIN + SESSION_FINAL_SCORE_ADJUSTMENT.get(setup.session_name or "off_session", 0)
    if not setup.trend_aligned:
        threshold += DIRECTIONAL_COUNTER_SCORE_PREMIUM
    return threshold


def _session_min_confirmation_bonus(session_name: str) -> float:
    return float(SESSION_MIN_CONFIRMATION_BONUS.get(session_name or "off_session", 0))


def _session_min_micro_score(session_name: str) -> float:
    return float(MICRO_SESSION_MIN_SCORE.get(session_name or "off_session", 0))


def _session_requires_micro(session_name: str) -> bool:
    return bool(MICRO_SESSION_REQUIRED.get(session_name or "off_session", False))


def _weighted_micro_score(micro_type: str, micro_score: float) -> float:
    weight = float(MICRO_PRIORITY_WEIGHTS.get(micro_type or "none", 1.0))
    weighted = float(micro_score or 0.0) * weight
    if micro_type == "liquidity_sweep_reclaim":
        weighted += MICRO_LIQUIDITY_SWEEP_EXTRA_BONUS
    return round(weighted, 1)


def _micro_quality_score(micro_score: float) -> float:
    return round(max(0.0, float(micro_score or 0.0) * MICRO_QUALITY_SCORE_SCALE), 1)


def _estimate_tp1_room_pips(setup: SetupResult) -> float:
    """Estimate path to TP1 before SignalGenerator performs final TP adjustment."""
    sign = 1 if setup.direction == "BUY" else -1
    entry = float(getattr(setup.confirmation, "entry_price", setup.level.price))
    default_tp1_pips = float(TP_PIPS[0]) if TP_PIPS else 0.0
    default_tp1 = entry + sign * default_tp1_pips * PIP_SIZE
    blockers = []
    for level in getattr(setup, "opposing_levels", []) or []:
        price = float(getattr(level, "price", 0.0) or 0.0)
        if setup.direction == "BUY" and entry < price < default_tp1:
            blockers.append(price)
        elif setup.direction == "SELL" and default_tp1 < price < entry:
            blockers.append(price)
    if blockers:
        nearest = min(blockers) if setup.direction == "BUY" else max(blockers)
        return round(abs(nearest - entry) / (PIP_SIZE or 1.0), 1)
    return round(default_tp1_pips, 1)


def _detect_engulfing_boost(micro_confirmator: MicroConfirmationEngine, data: Dict[str, pd.DataFrame], setup: SetupResult) -> float:
    """Use engulfing only as a sidecar boost, never as a standalone entry trigger."""
    if (getattr(setup, "micro_confirmation_type", "") or "") != "liquidity_sweep_reclaim":
        return 0.0
    for timeframe in ("M5", "M1"):
        df = data.get(timeframe)
        if df is None or len(df) < 5:
            continue
        window = df.iloc[-MICRO_CONFIRMATION_LOOKBACK:].reset_index(drop=True)
        if len(window) < 5:
            continue
        details = micro_confirmator._detect_engulfing_reversal(window, setup.direction, float(setup.level.price))
        if details:
            bonus = ENGULFING_SWEEP_COMBO_BOOST
            logger.info(
                "ENGULFING BOOST: %+g | micro=%s | %s | %s XAUUSD | Level %s %.2f | [%s->%s]",
                bonus,
                setup.micro_confirmation_type,
                details,
                setup.direction,
                setup.level.level_type,
                setup.level.price,
                setup.higher_tf,
                setup.lower_tf,
            )
            return float(bonus)
    return 0.0


def _av_has_tp_room(candidate: LevelInfo, structural: List[LevelInfo]) -> bool:
    """
    Simplified TP-room gate for the diversity rule.
    The nearest opposing structural level must be >= MIN_TP_CLEARANCE_PIPS away.
    Returns True when room is sufficient or cannot be determined.
    """
    trade_dir = candidate.trade_direction
    if not trade_dir:
        return True
    opp_dir = "SELL" if trade_dir == "BUY" else "BUY"
    opposing = [
        l for l in structural
        if getattr(l, "trade_direction", "") == opp_dir
        and (
            (trade_dir == "BUY" and l.price > candidate.price)
            or (trade_dir == "SELL" and l.price < candidate.price)
        )
    ]
    if not opposing:
        return True
    if trade_dir == "BUY":
        nearest = min(l.price for l in opposing)
        return (nearest - candidate.price) / PIP_SIZE >= MIN_TP_CLEARANCE_PIPS
    nearest = max(l.price for l in opposing)
    return (candidate.price - nearest) / PIP_SIZE >= MIN_TP_CLEARANCE_PIPS


def _log_av_shortlist(levels: List[LevelInfo], higher_tf: str, lower_tf: str) -> None:
    pair = f"{higher_tf}->{lower_tf}"
    a_count = sum(1 for l in levels if l.level_type == "A")
    v_count = sum(1 for l in levels if l.level_type == "V")
    g_count = sum(1 for l in levels if l.level_type == "Gap")
    logger.info(
        "LEVEL SHORTLIST [%s]: A=%d V=%d Gap=%d total=%d (after selector+cap+diversity)",
        pair, a_count, v_count, g_count, len(levels),
    )
    logger.info(
        "PIPELINE COUNTS [%s]: shortlisted A=%d V=%d Gap=%d total=%d",
        pair, a_count, v_count, g_count, len(levels),
    )
    for lvl in levels:
        if lvl.level_type in ("A", "V"):
            logger.info(
                "A/V SHORTLISTED [%s]: %s %.2f | sel=%.0f Q=%.0f scope=%s | %s",
                pair, lvl.level_type, lvl.price,
                lvl.selection_score, lvl.quality_score, lvl.scope,
                " | ".join(lvl.accepted_reasons[:3]),
            )


def _filter_active_level_types(levels: List[LevelInfo], higher_tf: str, lower_tf: str) -> List[LevelInfo]:
    if not _ACTIVE_LEVEL_TYPES:
        return levels
    kept: List[LevelInfo] = []
    for level in levels:
        if level.level_type in _ACTIVE_LEVEL_TYPES:
            kept.append(level)
            continue
        logger.info(
            "ACTIVE STRATEGY FILTER: rejecting %s-level (disabled) | "
            "watchlist/confirmation skipped | Level %.2f | [%s->%s]",
            level.level_type,
            level.price,
            higher_tf,
            lower_tf,
        )
    return kept


def _classify_setup_type(setup: SetupResult) -> str:
    """Determine primary label for Telegram/tracking visibility."""
    if setup.is_qm:
        return "qm_level"
    if setup.level.level_type == "Gap":
        return "imbalance_confluence"
    if setup.is_psychological:
        return "psychological_confluence"
    if setup.level.scope == "recent":
        return "recent_leg"
    if setup.level.scope == "previous":
        return "previous_leg"
    if setup.level.scope == "psych":
        return "psychological_confluence"
    return "major"


# ─────────────────────────────────────────────────────────
# MULTI-TIMEFRAME ANALYZER
# ─────────────────────────────────────────────────────────

class MultiTimeframeAnalyzer:
    """
    Runs the full multi-timeframe + market-context pipeline and returns
    (MarketOutlook, sorted list of SetupResult).
    """

    def __init__(self):
        self.detector       = LevelDetector()
        self.confirmator    = ConfirmationEngine()
        self.micro_confirmator = MicroConfirmationEngine()
        self.execution_filters = ExecutionFilterEngine()
        self.ctx_engine     = MarketContextEngine()
        self.quality_filter = StructureQualityFilter()
        self.level_selector = EliteLevelSelector()
        self.learning_engine = None
        logger.info(
            "ACTIVE STRATEGY LOCKED: GAP + LIQUIDITY_SWEEP_RECLAIM ONLY"
        )
        self.last_rejections: List[ConfirmationRejection] = []
        if DISABLED_TIMEFRAME_PAIRS:
            disabled = ", ".join(f"{high}->{low}" for high, low in DISABLED_TIMEFRAME_PAIRS)
            active = ", ".join(f"{high}->{low}" for high, low in TIMEFRAME_PAIRS)
            logger.info(
                "Disabled timeframe pair(s): %s | active strategy pair(s): %s",
                disabled,
                active or "none",
            )
        if not _ACTIVE_USES_AV:
            logger.info(
                "ACTIVE STRATEGY MODE: Gap-only active path — A/V detection, scoring, "
                "shortlist diversity, and confirmation are skipped."
            )

    def analyze(
        self,
        data: Dict[str, pd.DataFrame],
        pair: str = "XAUUSD",
        current_price: Optional[float] = None,
        analysis_time: Optional[datetime] = None,
    ) -> tuple[MarketOutlook, List[SetupResult]]:
        """
        Main entry point.

        Args:
            data:          timeframe → OHLCV DataFrame
            pair:          symbol name
            current_price: live price for priority sorting and psych generation

        Returns:
            (MarketOutlook, sorted SetupResult list)
        """
        now = analysis_time or datetime.now(timezone.utc)
        ctx = self.ctx_engine.analyze(data, now)

        # Hard stop — news window
        if ctx.is_news_window:
            logger.info("News filter active — skipping all setups this cycle.")

        # Low volatility: do NOT skip — apply stricter quality gates below
        low_volatility = not ctx.is_volatile
        if low_volatility:
            logger.info(
                "Low volatility detected — applying stricter signal filtering "
                "(min confidence 0.85, QM or psychological level required)."
            )

        if not getattr(ctx, "bot_window_active", ctx.session_allowed):
            logger.info(
                "Bot operating window closed: %s setups will be blocked (%s).",
                ctx.session_name,
                ctx.session_block_reason,
            )
        else:
            logger.info(
                "SESSION PROFILE: %s %s | score_adjust=%+.0f | min_confirmation_bonus=%.0f",
                ctx.session_name,
                SESSION_PROFILE.get(ctx.session_name, "balanced"),
                SESSION_FINAL_SCORE_ADJUSTMENT.get(ctx.session_name, 0),
                _session_min_confirmation_bonus(ctx.session_name),
            )

        timeframe_level_list: List[TimeframeLevels] = []
        setups: List[SetupResult] = []
        self.last_rejections = []

        for higher_tf, lower_tf in TIMEFRAME_PAIRS:
            df_high = data.get(higher_tf)
            df_low  = data.get(lower_tf)

            if df_high is None or df_low is None:
                logger.warning("Missing data for %s or %s — skipping", higher_tf, lower_tf)
                continue

            # ── 1. Detect all level types ─────────────────────────────────
            major_levels    = self.detector.detect_all(
                df_high, higher_tf,
                current_price=current_price,
                h4_bias=ctx.h4_bias,
                include_reversals=_ACTIVE_USES_AV,
            )
            recent_levels   = []
            previous_levels = []
            psych_levels    = []

            if (higher_tf, lower_tf) in _LEG_PAIRS:
                recent_levels   = self.detector.detect_recent_legs(
                    df_low, lower_tf,
                    current_price=current_price,
                    h4_bias=ctx.h4_bias,
                    include_reversals=_ACTIVE_USES_AV,
                )
                previous_levels = self.detector.detect_previous_leg(
                    df_low, lower_tf,
                    current_price=current_price,
                    h4_bias=ctx.h4_bias,
                    include_reversals=_ACTIVE_USES_AV,
                )

                if current_price and "Psych" in _ACTIVE_LEVEL_TYPES:
                    psych_levels = self.detector.generate_psych_levels(
                        current_price, lower_tf
                    )
                elif current_price:
                    logger.info(
                        "ACTIVE GAP-ONLY: psychological standalone levels skipped for [%s->%s]",
                        higher_tf,
                        lower_tf,
                    )

            # ── 2. Compute QM flags + touch counts on all level sets ─────
            all_level_sets = [major_levels, recent_levels, previous_levels, psych_levels]
            for lvl_set in all_level_sets:
                if lvl_set:
                    self.detector.compute_qm_flags(lvl_set, df_low)
                    self.detector.compute_psych_flags(lvl_set)
                    self.detector.compute_touch_counts(lvl_set, df_low)

            all_structural = major_levels + recent_levels + previous_levels + psych_levels

            # Pipeline count — detected (before selector, after detector top-N + merge)
            _pa = sum(1 for l in all_structural if l.level_type == "A")
            _pv = sum(1 for l in all_structural if l.level_type == "V")
            _pg = sum(1 for l in all_structural if l.level_type == "Gap")
            logger.info(
                "PIPELINE COUNTS [%s->%s]: detected A=%d V=%d Gap=%d total=%d",
                higher_tf, lower_tf, _pa, _pv, _pg, _pa + _pv + _pg,
            )

            # Active strategy pruning must happen before selector crowding.
            # Otherwise disabled research levels (A/V) can suppress a valid Gap,
            # then get rejected later, leaving the pair with no tradable levels.
            selection_major = _filter_active_level_types(major_levels, higher_tf, lower_tf)
            selection_recent = _filter_active_level_types(recent_levels, higher_tf, lower_tf)
            selection_previous = _filter_active_level_types(previous_levels, higher_tf, lower_tf)
            selection_psych = _filter_active_level_types(psych_levels, higher_tf, lower_tf)
            selection_structural = (
                selection_major + selection_recent + selection_previous + selection_psych
            )
            if len(selection_structural) != len(all_structural):
                _sa = sum(1 for l in selection_structural if l.level_type == "A")
                _sv = sum(1 for l in selection_structural if l.level_type == "V")
                _sg = sum(1 for l in selection_structural if l.level_type == "Gap")
                logger.info(
                    "ACTIVE STRATEGY PRESELECT [%s->%s]: A=%d V=%d Gap=%d total=%d "
                    "(disabled level types excluded before crowding)",
                    higher_tf, lower_tf, _sa, _sv, _sg, len(selection_structural),
                )

            selected_major = self.level_selector.select_scope_levels(
                levels=selection_major,
                all_structural_levels=selection_structural,
                df_low=df_low,
                ctx=ctx,
                current_price=current_price,
                higher_tf=higher_tf,
                lower_tf=lower_tf,
            )
            selected_recent = self.level_selector.select_scope_levels(
                levels=selection_recent,
                all_structural_levels=selection_structural,
                df_low=df_low,
                ctx=ctx,
                current_price=current_price,
                higher_tf=higher_tf,
                lower_tf=lower_tf,
            )
            selected_previous = self.level_selector.select_scope_levels(
                levels=selection_previous,
                all_structural_levels=selection_structural,
                df_low=df_low,
                ctx=ctx,
                current_price=current_price,
                higher_tf=higher_tf,
                lower_tf=lower_tf,
            )
            selected_major, selected_recent, selected_previous = self.level_selector.cap_pair_levels(
                selected_major, selected_recent, selected_previous
            )

            # Structural diversity only applies when A/V are explicitly active.
            if _ACTIVE_USES_AV:
                selected_major, selected_recent, selected_previous = self._apply_av_diversity(
                    selected_major, selected_recent, selected_previous,
                    selection_structural, ctx, current_price,
                    f"{higher_tf}->{lower_tf}", higher_tf, lower_tf,
                )
            else:
                logger.info(
                    "ACTIVE GAP-ONLY: A/V diversity skipped for [%s->%s]",
                    higher_tf,
                    lower_tf,
                )

            selected_major = _filter_active_level_types(selected_major, higher_tf, lower_tf)
            selected_recent = _filter_active_level_types(selected_recent, higher_tf, lower_tf)
            selected_previous = _filter_active_level_types(selected_previous, higher_tf, lower_tf)

            all_selected = selected_major + selected_recent + selected_previous
            _log_av_shortlist(all_selected, higher_tf, lower_tf)

            _final_a = sum(1 for l in all_selected if l.level_type == "A")
            _final_v = sum(1 for l in all_selected if l.level_type == "V")
            _final_g = sum(1 for l in all_selected if l.level_type == "Gap")
            logger.info(
                "FINAL LEVEL MIX [%s->%s]: A=%d V=%d Gap=%d total=%d entering confirmation",
                higher_tf, lower_tf, _final_a, _final_v, _final_g, len(all_selected),
            )

            tfl = TimeframeLevels(
                higher_tf=higher_tf,
                lower_tf=lower_tf,
                levels=selected_major,
                recent_levels=selected_recent,
                previous_levels=selected_previous,
                psych_levels=psych_levels,
            )
            timeframe_level_list.append(tfl)

            # ── 3. Skip if news window blocks all trades ──────────────────
            if ctx.is_news_window:
                continue

            level_groups = [
                (selected_major,    "major"),
                (selected_recent,   "recent"),
                (selected_previous, "previous"),
            ]

            for lvl_set, scope_label in level_groups:
                if not lvl_set:
                    continue

                confirmations = self.confirmator.check_confirmations(
                    df=df_low, levels=lvl_set,
                    timeframe=lower_tf, lookback=20,  # last 20 closed candles (~1 day on H1)
                )
                self.last_rejections.extend(self.confirmator.last_rejections)

                # ── 5. Apply approach filters (distance + impulse) ────────
                # Filter 2 + Filter 3 — applied per ConfirmationResult
                quality_confs = []
                for conf in confirmations:
                    if conf.level.level_type in ("A", "V"):
                        logger.info(
                            "A/V CONFIRMED [%s]: %s %.2f confirmed via %s | [%s->%s]",
                            lower_tf, conf.level.level_type, conf.level.price,
                            conf.confirmation_type, higher_tf, lower_tf,
                        )
                    if not self.quality_filter.is_approach_valid(df_low, conf.level):
                        # detailed rejection already logged inside is_approach_valid()
                        logger.info(
                            "SETUP REJECTED: %s XAUUSD | Reason: failed approach filter "
                            "(distance/impulse) | Level %s %.2f | [%s→%s]",
                            conf.direction, conf.level.level_type,
                            conf.level.price, higher_tf, lower_tf,
                        )
                        if conf.level.level_type in ("A", "V"):
                            logger.info(
                                "A/V REJECTED: approach filter | %s %.2f | [%s->%s]",
                                conf.level.level_type, conf.level.price, higher_tf, lower_tf,
                            )
                        continue
                    quality_confs.append(conf)
                confirmations = quality_confs

                for conf in confirmations:
                    # ── 5. Build preliminary SetupResult ──────────────────
                    is_qm   = conf.level.is_qm
                    is_psych = conf.level.is_psychological

                    # Direction vs trend filter
                    trend_aligned = self.ctx_engine.is_direction_trend_aligned(
                        ctx, conf.direction
                    )
                    dominant_bias = getattr(ctx, "dominant_bias", ctx.h4_bias)
                    bias_strength = getattr(ctx, "bias_strength", "weak")

                    if not _is_tradeable_bias(dominant_bias, bias_strength):
                        logger.info(
                            "SETUP REJECTED: %s XAUUSD | Reason: weak bias "
                            "(%s/%s not allowed) | Level %s %.2f | [%s->%s]",
                            conf.direction,
                            dominant_bias,
                            bias_strength,
                            conf.level.level_type,
                            conf.level.price,
                            higher_tf,
                            lower_tf,
                        )
                        if conf.level.level_type in ("A", "V"):
                            logger.info(
                                "A/V REJECTED: weak bias | %s %.2f | [%s->%s]",
                                conf.level.level_type, conf.level.price, higher_tf, lower_tf,
                            )
                        continue
                    logger.info(
                        "BIAS GATE PASSED: %s %s | %s XAUUSD | [%s->%s]",
                        dominant_bias,
                        bias_strength,
                        conf.direction,
                        higher_tf,
                        lower_tf,
                    )

                    # Counter-trend gate — blocked ENTIRELY (no exceptions)
                    if (
                        not trend_aligned
                        and bias_strength == "strong"
                        and False  # counter-trend exception is evaluated after micro confirmation
                    ):
                        logger.info(
                            "SETUP REJECTED: %s XAUUSD | Reason: strong trend alignment block "
                            "(%s vs dominant %s/%s bias, H1=%s) | Level %s %.2f | [%s→%s]",
                            conf.direction, conf.direction,
                            getattr(ctx, "dominant_bias", ctx.h4_bias),
                            getattr(ctx, "bias_strength", "weak"),
                            getattr(ctx, "h1_state", "range"),
                            conf.level.level_type, conf.level.price,
                            higher_tf, lower_tf,
                        )
                        if conf.level.level_type in ("A", "V"):
                            logger.info(
                                "A/V REJECTED: counter-trend bias block | %s %.2f | [%s->%s]",
                                conf.level.level_type, conf.level.price, higher_tf, lower_tf,
                            )
                        continue

                    # Liquidity sweep match
                    is_sweep = (
                        ctx.sweep_direction == conf.direction
                        and conf.level.within_tolerance(ctx.sweep_price)
                    )

                    # Build list of opposing structural levels for TP clearance (Filter 5)
                    # For SELL: opposing = support levels; for BUY: opposing = resistance
                    if conf.direction == "SELL":
                        opp = [
                            l for l in tfl.all_levels()
                            if (
                                l.level_type == "V"
                                or (l.level_type == "Gap" and getattr(l, "trade_direction", "") == "BUY")
                            )
                            and l.price < conf.entry_price
                        ]
                    else:
                        opp = [
                            l for l in tfl.all_levels()
                            if (
                                l.level_type == "A"
                                or (l.level_type == "Gap" and getattr(l, "trade_direction", "") == "SELL")
                            )
                            and l.price > conf.entry_price
                        ]

                    setup = SetupResult(
                        pair=pair,
                        direction=conf.direction,
                        higher_tf=higher_tf,
                        lower_tf=lower_tf,
                        level=conf.level,
                        confirmation=conf,
                        confidence=0.5,   # placeholder; adjusted below
                        is_qm=is_qm,
                        is_psychological=is_psych,
                        is_liquidity_sweep=is_sweep,
                        session_name=ctx.session_name,
                        h4_bias=dominant_bias,
                        bias_strength=bias_strength,
                        dominant_bias=dominant_bias,
                        trend_aligned=trend_aligned,
                        opposing_levels=opp,
                    )
                    setup.setup_type = _classify_setup_type(setup)

                    if _ACTIVE_LEVEL_TYPES and setup.level.level_type not in _ACTIVE_LEVEL_TYPES:
                        logger.info(
                            "ACTIVE STRATEGY FILTER: rejecting %s-level (disabled) | "
                            "%s XAUUSD | Level %.2f | [%s->%s]",
                            setup.level.level_type,
                            setup.direction,
                            setup.level.price,
                            higher_tf,
                            lower_tf,
                        )
                        continue

                    if MICRO_CONFIRMATION_ENABLED:
                        micro = self.micro_confirmator.evaluate(data, setup)
                        setup.micro_confirmation_type = micro.confirmation_type
                        weighted_micro_score = _weighted_micro_score(micro.confirmation_type, micro.score)
                        setup.micro_confirmation_score = weighted_micro_score
                        setup.micro_layer_decision = micro.decision
                        micro_quality = _micro_quality_score(weighted_micro_score)
                        setup.micro_strength = "strong" if micro_quality >= MICRO_STRONG_SCORE else "normal"

                        if weighted_micro_score != micro.score:
                            logger.info(
                                "MICRO PRIORITY WEIGHTED: %s | base=%+.1f weight=%.2f extra=%+.1f weighted=%+.1f quality=%.1f | %s XAUUSD | Level %s %.2f | [%s->%s]",
                                micro.confirmation_type,
                                micro.score,
                                MICRO_PRIORITY_WEIGHTS.get(micro.confirmation_type, 1.0),
                                MICRO_LIQUIDITY_SWEEP_EXTRA_BONUS if micro.confirmation_type == "liquidity_sweep_reclaim" else 0.0,
                                weighted_micro_score,
                                micro_quality,
                                setup.direction,
                                conf.level.level_type,
                                conf.level.price,
                                higher_tf,
                                lower_tf,
                            )

                        if micro.decision == "blocked":
                            logger.info(
                                "SETUP REJECTED: %s XAUUSD | Reason: micro-confirmation contradiction "
                                "(%s %.0f) | Level %s %.2f | [%s->%s]",
                                setup.direction,
                                micro.confirmation_type,
                                micro.score,
                                conf.level.level_type,
                                conf.level.price,
                                higher_tf,
                                lower_tf,
                            )
                            continue

                        min_micro_score = _session_min_micro_score(setup.session_name)
                        if _session_requires_micro(setup.session_name) and weighted_micro_score < min_micro_score:
                            logger.info(
                                "SETUP REJECTED: %s XAUUSD | Reason: %s session requires micro confirmation "
                                "(score %.0f < %.0f) | micro=%s | Level %s %.2f | [%s->%s]",
                                setup.direction,
                                setup.session_name,
                                weighted_micro_score,
                                min_micro_score,
                                micro.confirmation_type,
                                conf.level.level_type,
                                conf.level.price,
                                higher_tf,
                                lower_tf,
                            )
                            continue

                        if micro.confirmation_type == "none" and not ACTIVE_STRATEGY_REQUIRE_MICRO:
                            if setup.session_name == "london":
                                penalty = MICRO_LONDON_NO_MICRO_PENALTY
                                setup.micro_confirmation_score = -penalty
                                logger.info(
                                    "MICRO PENALTY: london no-micro penalty (-%.0f) applied | "
                                    "%s XAUUSD | Level %s %.2f | [%s->%s]",
                                    penalty,
                                    setup.direction,
                                    conf.level.level_type,
                                    conf.level.price,
                                    higher_tf,
                                    lower_tf,
                                )
                            elif setup.session_name == "new_york":
                                penalty = MICRO_NY_NO_MICRO_PENALTY
                                setup.micro_confirmation_score = -penalty
                                logger.info(
                                    "MICRO PENALTY: new_york no-micro penalty (-%.0f) applied | "
                                    "%s XAUUSD | Level %s %.2f | [%s->%s]",
                                    penalty,
                                    setup.direction,
                                    conf.level.level_type,
                                    conf.level.price,
                                    higher_tf,
                                    lower_tf,
                                )

                    if ACTIVE_STRATEGY_REQUIRE_MICRO:
                        micro_type = setup.micro_confirmation_type or "none"
                        if micro_type != "liquidity_sweep_reclaim" or micro_type not in _ACTIVE_MICRO_TYPES:
                            logger.info(
                                "ACTIVE MICRO REJECT: non-sweep confirmation blocked | micro=%s | "
                                "%s XAUUSD | Level %s %.2f | [%s->%s]",
                                micro_type,
                                setup.direction,
                                setup.level.level_type,
                                setup.level.price,
                                higher_tf,
                                lower_tf,
                            )
                            continue
                        logger.info(
                            "ACTIVE PIPELINE PASS: GAP + SWEEP_RECLAIM | %s XAUUSD | Level %.2f | [%s->%s]",
                            setup.direction,
                            setup.level.price,
                            higher_tf,
                            lower_tf,
                        )

                    execution = self.execution_filters.evaluate(data, setup, ctx)
                    setup.h1_liquidity_sweep = execution.h1_liquidity_sweep
                    setup.h1_sweep_direction = execution.h1_sweep_direction
                    setup.h1_reclaim_confirmed = execution.h1_reclaim_confirmed
                    setup.pd_location = execution.pd_location
                    setup.pd_filter_score = execution.pd_filter_score
                    setup.bias_gate_result = execution.bias_gate_result
                    if not execution.passed:
                        logger.info(
                            "ACTIVE STRATEGY FILTER: rejecting execution context | reason=%s | "
                            "%s XAUUSD | Level %s %.2f | [%s->%s]",
                            execution.reason,
                            setup.direction,
                            setup.level.level_type,
                            setup.level.price,
                            higher_tf,
                            lower_tf,
                        )
                        continue

                    time_blocked = not getattr(ctx, "bot_window_active", ctx.session_allowed)
                    logger.info(
                        "TIME BLOCK CHECK: local_time=%s | bot_window_active=%s | session_label=%s | will_block=%s",
                        getattr(ctx, "local_time", "--:--"),
                        str(getattr(ctx, "bot_window_active", ctx.session_allowed)).lower(),
                        setup.session_name or ctx.session_name,
                        str(time_blocked).lower(),
                    )
                    if time_blocked:
                        logger.info(
                            "SETUP REJECTED: %s XAUUSD | Reason: bot window closed (%s) | "
                            "Level %s %.2f | [%s->%s]",
                            conf.direction,
                            ctx.session_block_reason,
                            conf.level.level_type,
                            conf.level.price,
                            higher_tf,
                            lower_tf,
                        )
                        continue
                    if setup.session_name == "off_session":
                        logger.info(
                            "OFF-SESSION LABEL INFO ONLY | %s XAUUSD | Level %s %.2f | [%s->%s]",
                            conf.direction,
                            conf.level.level_type,
                            conf.level.price,
                            higher_tf,
                            lower_tf,
                        )

                    # Entry distance filter — reject entries that are too close to
                    # current price (< 15 pips). These produce useless signals like
                    # "price is 4790, sell at 4797" which offer no realistic fill.
                    if current_price is not None:
                        entry_dist_pips = abs(conf.entry_price - current_price) / PIP_SIZE
                        if entry_dist_pips < 15:
                            logger.info(
                                "SETUP REJECTED: %s XAUUSD | Reason: entry too close to "
                                "current price (%.1f pips < 15 min) | Level %s %.2f | [%s→%s]",
                                conf.direction, entry_dist_pips,
                                conf.level.level_type, conf.level.price,
                                higher_tf, lower_tf,
                            )
                            continue

                    setup.engulfing_bonus = _detect_engulfing_boost(self.micro_confirmator, data, setup)

                    setup.tp1_room_pips = _estimate_tp1_room_pips(setup)
                    if setup.tp1_room_pips >= TP1_BONUS_MIN_PIPS:
                        setup.tp1_quality_bonus = TP1_QUALITY_SCORE_BONUS
                        logger.info(
                            "TP1 BONUS: +%.0f (>=%.0f pips) | actual=%.0fp | %s XAUUSD | Level %s %.2f | [%s->%s]",
                            TP1_QUALITY_SCORE_BONUS,
                            TP1_BONUS_MIN_PIPS,
                            setup.tp1_room_pips,
                            setup.direction,
                            setup.level.level_type,
                            setup.level.price,
                            higher_tf,
                            lower_tf,
                        )
                    else:
                        setup.tp1_quality_bonus = 0.0

                    setup.confidence = _adjust_confidence(0.5, setup)
                    setup.final_score = _compute_final_setup_score(setup)
                    learned_edge_bonus = 0.0
                    learned_edge_key = ""
                    if self.learning_engine is not None:
                        try:
                            learned_edge_bonus, learned_edge_key = self.learning_engine.get_learned_edge_bonus(setup)
                            if learned_edge_bonus:
                                setup.final_score = round(setup.final_score + learned_edge_bonus, 1)
                                logger.info(
                                    "LEARNED EDGE %s APPLIED: %s = %+.1f | final_score=%.1f | %s XAUUSD | Level %s %.2f | [%s->%s]",
                                    "BONUS" if learned_edge_bonus > 0 else "PENALTY",
                                    learned_edge_key,
                                    learned_edge_bonus,
                                    setup.final_score,
                                    setup.direction,
                                    setup.level.level_type,
                                    setup.level.price,
                                    higher_tf,
                                    lower_tf,
                                )
                        except Exception as exc:
                            logger.debug("Learned edge lookup failed: %s", exc)
                    setup.high_quality_trade = setup.final_score >= HIGH_QUALITY_TRADE_SCORE
                    threshold = _final_score_threshold(setup)
                    confirmation_bonus = _confirmation_score_bonus(setup.confirmation.confirmation_type)
                    min_session_confirmation = _session_min_confirmation_bonus(setup.session_name)

                    logger.info(
                        "CONFIRMATION WEIGHTED: %s XAUUSD | type=%s bonus=%+.0f | micro=%s %+g | "
                        "selection=%.0f confidence=%.0f%% final_score=%.1f",
                        setup.direction,
                        setup.confirmation.confirmation_type,
                        _confirmation_score_bonus(setup.confirmation.confirmation_type),
                        setup.micro_confirmation_type,
                        setup.micro_confirmation_score,
                        getattr(setup.level, "selection_score", 0.0),
                        setup.confidence * 100,
                        setup.final_score,
                    )
                    logger.info(
                        "SCORE COMPONENTS: %s XAUUSD | gap_score=%.0f micro_bonus=%+.0f "
                        "bias_bonus=%+.0f pd_bonus=%+.0f engulfing_bonus=%+.0f tp1_bonus=%+.0f learned_edge=%+.1f | Level %s %.2f | [%s->%s]",
                        setup.direction,
                        float(getattr(setup.level, "selection_score", getattr(setup.level, "quality_score", 0.0)) or 0.0),
                        float(getattr(setup, "micro_confirmation_score", 0.0) or 0.0),
                        float(getattr(execution, "bias_gate_score", 0.0) or 0.0),
                        float(getattr(execution, "pd_bonus", 0.0) or 0.0),
                        float(getattr(setup, "engulfing_bonus", 0.0) or 0.0),
                        float(getattr(setup, "tp1_quality_bonus", 0.0) or 0.0),
                        learned_edge_bonus,
                        setup.level.level_type,
                        setup.level.price,
                        higher_tf,
                        lower_tf,
                    )
                    logger.info(
                        "FINAL SCORE: %.1f | PASSED: %s | threshold=%.1f | high_quality=%s micro_strength=%s | %s XAUUSD | Level %s %.2f | [%s->%s]",
                        setup.final_score,
                        "yes" if setup.final_score >= threshold else "no",
                        threshold,
                        setup.high_quality_trade,
                        setup.micro_strength,
                        setup.direction,
                        setup.level.level_type,
                        setup.level.price,
                        higher_tf,
                        lower_tf,
                    )

                    if confirmation_bonus < min_session_confirmation:
                        logger.info(
                            "SETUP REJECTED: %s XAUUSD | Reason: %s session requires stronger confirmation "
                            "(bonus %.0f < %.0f) | confirmation=%s | Level %s %.2f | [%s->%s]",
                            setup.direction,
                            setup.session_name,
                            confirmation_bonus,
                            min_session_confirmation,
                            setup.confirmation.confirmation_type,
                            conf.level.level_type,
                            conf.level.price,
                            higher_tf,
                            lower_tf,
                        )
                        continue

                    if setup.final_score < threshold:
                        logger.info(
                            "SETUP REJECTED: %s XAUUSD | Reason: final score %.1f < %.1f threshold | "
                            "confirmation=%s | Level %s %.2f | [%s->%s]",
                            setup.direction,
                            setup.final_score,
                            threshold,
                            setup.confirmation.confirmation_type,
                            conf.level.level_type,
                            conf.level.price,
                            higher_tf,
                            lower_tf,
                        )
                        if conf.level.level_type in ("A", "V"):
                            logger.info(
                                "A/V REJECTED: final score %.1f < %.1f threshold | "
                                "%s %.2f sel=%.0f Q=%.0f micro=%s%+g | [%s->%s]",
                                setup.final_score, threshold,
                                conf.level.level_type, conf.level.price,
                                getattr(conf.level, "selection_score", 0.0),
                                conf.level.quality_score,
                                setup.micro_confirmation_type, setup.micro_confirmation_score,
                                higher_tf, lower_tf,
                            )
                        continue

                    if (
                        setup.micro_confirmation_type == "none"
                        and setup.session_name in ("london", "new_york")
                    ):
                        logger.info(
                            "MICRO PREFERENCE PASS: %s XAUUSD | no micro but survived penalty+threshold | "
                            "final_score=%.1f >= %.1f | Level %s %.2f | [%s->%s]",
                            setup.direction,
                            setup.final_score,
                            threshold,
                            conf.level.level_type,
                            conf.level.price,
                            higher_tf,
                            lower_tf,
                        )

                    if (
                        not setup.trend_aligned
                        and setup.bias_strength == "moderate"
                        and setup.confidence < BIAS_MODERATE_COUNTER_TREND_MIN_CONFIDENCE
                    ):
                        logger.info(
                            "SETUP REJECTED: %s XAUUSD | Reason: moderate counter-trend "
                            "confirmation too weak (conf %.0f%% < %.0f%% after %.0f%% penalty) | "
                            "dominant=%s/%s | Level %s %.2f | [%s->%s]",
                            setup.direction,
                            setup.confidence * 100,
                            BIAS_MODERATE_COUNTER_TREND_MIN_CONFIDENCE * 100,
                            BIAS_MODERATE_COUNTER_TREND_CONFIDENCE_PENALTY * 100,
                            dominant_bias,
                            bias_strength,
                            conf.level.level_type,
                            conf.level.price,
                            higher_tf,
                            lower_tf,
                        )
                        continue
                    if not setup.trend_aligned and setup.bias_strength == "moderate":
                        logger.info(
                            "SETUP ALLOWED: %s XAUUSD | moderate counter-trend survived "
                            "strict bias gate (conf %.0f%% >= %.0f%%) | dominant=%s/%s | [%s->%s]",
                            setup.direction,
                            setup.confidence * 100,
                            BIAS_MODERATE_COUNTER_TREND_MIN_CONFIDENCE * 100,
                            dominant_bias,
                            bias_strength,
                            higher_tf,
                            lower_tf,
                        )

                    setups.append(setup)

                    logger.info(
                        "[%s→%s] %s SETUP: %s %s | Level %s %.2f | "
                        "QM=%s Psych=%s Session=%s Bias=%s Sel=%.0f Conf=%.0f%% | %s",
                        higher_tf, lower_tf,
                        setup.setup_type.upper(),
                        conf.direction, pair,
                        conf.level.level_type, conf.level.price,
                        is_qm, is_psych,
                        ctx.session_name or "off",
                        ctx.h4_bias,
                        getattr(conf.level, "selection_score", 0.0),
                        setup.confidence * 100,
                        " | ".join(getattr(conf.level, "accepted_reasons", [])),
                    )

        # ── 6. Low-volatility note — no hard gate applied ────────────────────
        # Quiet market conditions exist during Asian session and overnight.
        # Setups that form in low volatility are still valid — the standard
        # confidence gate (MIN_SIGNAL_CONFIDENCE) filters quality.
        if low_volatility and setups:
            logger.info(
                "Low-volatility context: %d setup(s) found — passing to confidence gate.",
                len(setups),
            )

        # ── 7. Priority sort ──────────────────────────────────────────────
        if setups:
            setups = _priority_sort(setups, current_price)
            setups = _apply_top_n_filter(setups)

        now_ts = pd.Timestamp.utcnow()
        outlook = MarketOutlook(
            pair=pair,
            timeframe_levels=timeframe_level_list,
            timestamp=now_ts,
            context=ctx,
        )
        return outlook, setups

    def _apply_av_diversity(
        self,
        selected_major: List[LevelInfo],
        selected_recent: List[LevelInfo],
        selected_previous: List[LevelInfo],
        all_structural: List[LevelInfo],
        ctx,
        current_price: Optional[float],
        pair_label: str,
        higher_tf: str,
        lower_tf: str,
    ) -> Tuple[List[LevelInfo], List[LevelInfo], List[LevelInfo]]:
        """
        Structural diversity rule: if the post-cap selected set contains no A/V levels,
        attempt to inject the best qualifying rejected A/V candidate from the detector cache.
        Candidates must pass quality, distance, TP-room, and bias alignment gates.
        If the best A/V score >= weakest Gap score - AV_REPLACE_MARGIN, it replaces that Gap;
        otherwise it is appended as an extra candidate in the 'recent' bucket.
        Controlled by ENABLE_AV_DIVERSITY env flag.
        """
        if not ENABLE_AV_DIVERSITY:
            return selected_major, selected_recent, selected_previous

        all_selected = selected_major + selected_recent + selected_previous
        has_av = any(l.level_type in ("A", "V") for l in all_selected)
        if has_av:
            return selected_major, selected_recent, selected_previous

        candidates = getattr(self.detector, "_rejected_av_candidates", [])
        if not candidates:
            logger.info(
                "A/V DIVERSITY [%s]: no A/V in shortlist and no rejected candidates cached",
                pair_label,
            )
            return selected_major, selected_recent, selected_previous

        dominant_bias = getattr(ctx, "dominant_bias", ctx.h4_bias)

        def _bias_ok(level: LevelInfo) -> bool:
            td = getattr(level, "trade_direction", None)
            if not td or not dominant_bias:
                return True
            bias_up = dominant_bias in ("bullish", "strong_bullish")
            bias_dn = dominant_bias in ("bearish", "strong_bearish")
            if bias_up and td == "BUY":
                return True
            if bias_dn and td == "SELL":
                return True
            if dominant_bias == "ranging":
                return True
            return False

        def _distance_ok(level: LevelInfo) -> bool:
            if current_price is None:
                return True
            dist_pips = abs(level.price - current_price) / PIP_SIZE
            if dist_pips < LEVEL_MIN_PRICE_DISTANCE_PIPS:
                return False
            max_distance = LEVEL_PAIR_MAX_DISTANCE_PIPS.get(pair_label, 0)
            if max_distance and dist_pips > max_distance:
                return False
            return True

        eligible = [
            c for c in candidates
            if c.quality_score >= AV_MIN_SCORE_THRESHOLD
            and _bias_ok(c)
            and _distance_ok(c)
            and _av_has_tp_room(c, all_structural)
        ]

        if not eligible:
            logger.info(
                "A/V DIVERSITY [%s]: %d rejected candidates found, none passed quality/bias/distance/TP-room gates "
                "(threshold=%.0f, bias=%s)",
                pair_label, len(candidates), AV_MIN_SCORE_THRESHOLD, dominant_bias,
            )
            return selected_major, selected_recent, selected_previous

        best = max(eligible, key=lambda l: (l.quality_score, l.selection_score or 0))

        gaps_in_selection = [l for l in all_selected if l.level_type == "Gap"]
        if gaps_in_selection:
            weakest_gap = min(gaps_in_selection, key=lambda l: l.quality_score)
            if best.quality_score >= weakest_gap.quality_score - AV_REPLACE_MARGIN:
                replaced = False
                for bucket, name in (
                    (selected_major, "major"),
                    (selected_recent, "recent"),
                    (selected_previous, "previous"),
                ):
                    if weakest_gap in bucket:
                        bucket.remove(weakest_gap)
                        bucket.append(best)
                        logger.info(
                            "A/V FORCED INCLUSION [%s]: %s %.2f Q=%.0f replaces weakest Gap %.2f Q=%.0f in %s bucket | "
                            "bias=%s tp_room=OK",
                            pair_label, best.level_type, best.price, best.quality_score,
                            weakest_gap.price, weakest_gap.quality_score, name, dominant_bias,
                        )
                        replaced = True
                        break
                if not replaced:
                    selected_recent.append(best)
                    logger.info(
                        "A/V ADDED [%s]: %s %.2f Q=%.0f injected into recent bucket (Gap not found in known buckets) | "
                        "bias=%s",
                        pair_label, best.level_type, best.price, best.quality_score, dominant_bias,
                    )
            else:
                selected_recent.append(best)
                logger.info(
                    "A/V ADDED [%s]: %s %.2f Q=%.0f appended to recent bucket (Q below weakest Gap %.2f Q=%.0f - margin %.0f) | "
                    "bias=%s",
                    pair_label, best.level_type, best.price, best.quality_score,
                    weakest_gap.price, weakest_gap.quality_score, AV_REPLACE_MARGIN, dominant_bias,
                )
        else:
            selected_recent.append(best)
            logger.info(
                "A/V ADDED [%s]: %s %.2f Q=%.0f injected into recent bucket (no Gaps in selection) | bias=%s",
                pair_label, best.level_type, best.price, best.quality_score, dominant_bias,
            )

        return selected_major, selected_recent, selected_previous

    def get_required_timeframes(self) -> List[str]:
        # D1/H4/H1 are required for directional bias even when their
        # timeframe pairs are disabled from active intraday trading.
        tfs = {"D1", "H4", "H1"}
        for higher_tf, lower_tf in TIMEFRAME_PAIRS:
            tfs.add(higher_tf)
            tfs.add(lower_tf)
        if MICRO_CONFIRMATION_ENABLED:
            tfs.add("M5")
            if MICRO_CONFIRMATION_USE_M1_FALLBACK:
                tfs.add("M1")
        return list(tfs)


# ─────────────────────────────────────────────────────────
# PRIORITY SORT
# ─────────────────────────────────────────────────────────

_INTRADAY_TYPE_RANK = {
    "recent_leg":                0,
    "previous_leg":              1,
    "qm_level":                  2,
    "imbalance_confluence":      3,
    "psychological_confluence":  4,
    "major":                     5,
}

_SWING_TYPE_RANK = {
    "qm_level":                  0,
    "recent_leg":                1,
    "previous_leg":              2,
    "imbalance_confluence":      3,
    "psychological_confluence":  4,
    "major":                     5,
}


def _priority_sort(
    setups: List[SetupResult],
    current_price: Optional[float],
) -> List[SetupResult]:
    """
    Sort confirmed setups by trading horizon.

    Intraday pairs prioritise recent/previous structure first, then QM/broken
    structure, then imbalance confluence. Swing keeps QM first.
    """
    def key(s: SetupResult):
        tf_pair = f"{s.higher_tf}->{s.lower_tf}"
        ranks = _INTRADAY_TYPE_RANK if tf_pair in {"H1->M30", "M30->M15"} else _SWING_TYPE_RANK
        rank = ranks.get(s.setup_type, 9)
        dist = abs(s.confirmation.entry_price - current_price) if current_price else 0
        sl = abs(s.confirmation.entry_price - s.confirmation.sl_price)
        selection = getattr(s.level, "selection_score", getattr(s.level, "quality_score", 0.0))
        origin_idx = getattr(s.level, "origin_index", -1)
        return (rank, dist, sl, -selection, -origin_idx)

    return sorted(setups, key=key)


def _apply_top_n_filter(setups: List[SetupResult]) -> List[SetupResult]:
    if MAX_ACTIVE_CANDIDATES_PER_SCAN <= 0 or len(setups) <= MAX_ACTIVE_CANDIDATES_PER_SCAN:
        return sorted(setups, key=lambda s: (s.final_score, s.confidence), reverse=True)

    ranked = sorted(setups, key=lambda s: (s.final_score, s.confidence), reverse=True)
    kept = ranked[:MAX_ACTIVE_CANDIDATES_PER_SCAN]
    for rank, setup in enumerate(ranked[MAX_ACTIVE_CANDIDATES_PER_SCAN:], start=MAX_ACTIVE_CANDIDATES_PER_SCAN + 1):
        logger.info(
            "SETUP DROPPED: %s XAUUSD | ranked %d, outside top %d | "
            "final_score=%.1f confirmation=%s | Level %s %.2f | [%s->%s]",
            setup.direction,
            rank,
            MAX_ACTIVE_CANDIDATES_PER_SCAN,
            setup.final_score,
            setup.confirmation.confirmation_type,
            setup.level.level_type,
            setup.level.price,
            setup.higher_tf,
            setup.lower_tf,
        )
    return kept
