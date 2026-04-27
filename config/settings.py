"""
AlphaPulse - Central Configuration
All system-wide constants, environment loading, and typed settings.
"""

import os
from typing import List
from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────
# SYMBOL & TRADING CONSTANTS
# ─────────────────────────────────────────────
SYMBOL = "XAUUSD"

# MT5 / price conventions for XAUUSD (gold)
# Price is quoted to 2 decimal places, e.g. 3200.50
# 1 point  = 0.01  (smallest MT5 price increment)
# 1 pip    = 1.00  (standard trading convention — matches TradingView)
# So "30 pip SL" means price must not move more than $30 from entry.
POINT_VALUE = 0.01          # smallest MT5 price move
PIP_SIZE    = 1.00          # 1 pip = $1.00 for XAUUSD

MAX_SL_PIPS  = 30           # max allowed SL = $30 from entry
MIN_SL_PIPS  = 15           # minimum SL = $15 — tight SLs not valid for Gold

# Fixed TP pip targets (measured from entry price).
# TP1=40, TP2=60, TP3=80, TP4=100, TP5=150 pips.
# These are MANDATORY — no dynamic multipliers. Disciplined, fixed targets.
TP_PIPS = [40, 60, 80, 100, 150]

# Minimum Risk:Reward ratio for TP1 (40 pip TP / 30 pip max SL = 1.33R min).
# With fixed TPs this is mostly a safety guard against edge cases.
MIN_RR_RATIO = 1.3

# Pending-order retest fill tolerance after first rejection confirmation.
# 1.0 means price may revisit within $1 of the exact XAUUSD level.
PENDING_ORDER_FILL_TOLERANCE_PIPS = 1.0

# Confirmation candle quality filters
MIN_WICK_BODY_RATIO  = 2.0  # rejection wick must be at least 2× the candle body (strict)
MIN_CANDLE_BODY_PIPS = 3    # body must be at least $3 — rejects doji / spinning tops

# TP_MULTIPLIERS kept for backward-compatibility with old code paths — not used in signals
TF_TP_MULTIPLIERS = {
    "H1":  [3.0, 5.0, 7.0, 9.0, 12.0],
    "M30": [2.0, 3.5, 5.0, 7.0,  9.0],
    "M15": [1.5, 2.5, 3.5, 5.0,  7.0],
}
TP_MULTIPLIERS = [2.0, 3.0, 4.5, 6.0, 8.0]

LEVEL_TOLERANCE_PIPS = 3   # How close price must be to a level to "touch" it

# ─────────────────────────────────────────────
# STRUCTURE QUALITY FILTERS
# ─────────────────────────────────────────────

# Filter 1 — Clean Level
MAX_LEVEL_TOUCHES = 6          # reject level if touched more than 6 times (unless QM)
MAX_LEVEL_BREAKS  = 3          # reject level if broken more than 3 times (dead level)

# ─────────────────────────────────────────────
# LEVEL QUALITY ENGINE (v2)
# ─────────────────────────────────────────────

# Structural (A/V) level quality bonus — balances the Gap +4 imbalance bonus so that
# high-quality swing highs/lows compete fairly against Gap entries.
LEVEL_AV_QUALITY_BONUS = int(os.getenv("LEVEL_AV_QUALITY_BONUS", "3"))
LEVEL_AV_QUALITY_THRESHOLD = int(os.getenv("LEVEL_AV_QUALITY_THRESHOLD", "50"))
LEVEL_AV_TRACE_ENABLED = os.getenv("LEVEL_AV_TRACE_ENABLED", "true").lower() == "true"
LEVEL_AV_MIN_BODY_PIPS = float(os.getenv("LEVEL_AV_MIN_BODY_PIPS", "3"))
LEVEL_AV_WICK_RATIO = float(os.getenv("LEVEL_AV_WICK_RATIO", "1.4"))
LEVEL_AV_ORIGIN_MIN_WICK_RATIO = float(os.getenv("LEVEL_AV_ORIGIN_MIN_WICK_RATIO", "0.0"))
LEVEL_AV_ORIGIN_SCORE_BONUS = int(os.getenv("LEVEL_AV_ORIGIN_SCORE_BONUS", "8"))

# Structural diversity rule.
# After normal top-N + cap selection, if no A/V level survived, the best rejected
# A/V candidate is given one more chance to enter the monitored set.
# AV_MIN_SCORE_THRESHOLD — minimum raw quality_score (0-100) for the candidate to qualify.
# AV_REPLACE_MARGIN — gap (quality pts) by which the candidate may be weaker than the
#   weakest selected Gap before it switches from "replace" to "add" mode.
ENABLE_AV_DIVERSITY = os.getenv("ENABLE_AV_DIVERSITY", "true").lower() == "true"
AV_MIN_SCORE_THRESHOLD = float(os.getenv("AV_MIN_SCORE_THRESHOLD", "60"))
AV_REPLACE_MARGIN = float(os.getenv("AV_REPLACE_MARGIN", "5"))

# A/V filter relaxation — these override the global thresholds for A/V detection only.
# Gap detection and Gap selection thresholds are not affected.
AV_MIN_DISPLACEMENT_PIPS = float(os.getenv("AV_MIN_DISPLACEMENT_PIPS", "30"))
AV_MIN_DISTANCE_FROM_PRICE_PIPS = float(os.getenv("AV_MIN_DISTANCE_FROM_PRICE_PIPS", "15"))
AV_MAX_BREAK_COUNT = int(os.getenv("AV_MAX_BREAK_COUNT", "6"))
AV_MID_RANGE_SCORE_PENALTY = float(os.getenv("AV_MID_RANGE_SCORE_PENALTY", "12"))

# Broken-level: keep in pool with score penalty instead of hard rejection
AV_BROKEN_LEVEL_PENALTY = float(os.getenv("AV_BROKEN_LEVEL_PENALTY", "12"))

# Body size: hard reject only genuine doji; thin bodies get a soft penalty
AV_BODY_HARD_REJECT_PIPS = float(os.getenv("AV_BODY_HARD_REJECT_PIPS", "2.0"))
AV_BODY_THIN_PENALTY = float(os.getenv("AV_BODY_THIN_PENALTY", "5"))

# Break/touch count: penalty per unit above soft floor; hard reject only at ceiling
AV_BREAK_COUNT_SOFT_THRESHOLD = int(os.getenv("AV_BREAK_COUNT_SOFT_THRESHOLD", "10"))
AV_BREAK_COUNT_PENALTY_PER = float(os.getenv("AV_BREAK_COUNT_PENALTY_PER", "3"))
AV_TOUCH_COUNT_SOFT_THRESHOLD = int(os.getenv("AV_TOUCH_COUNT_SOFT_THRESHOLD", "12"))
AV_TOUCH_COUNT_PENALTY_PER = float(os.getenv("AV_TOUCH_COUNT_PENALTY_PER", "2"))

# Origin + strong displacement bonus (applied inside quality scorer)
AV_ORIGIN_DISPLACEMENT_STRONG_PIPS = float(os.getenv("AV_ORIGIN_DISPLACEMENT_STRONG_PIPS", "50"))
AV_ORIGIN_DISPLACEMENT_BONUS = int(os.getenv("AV_ORIGIN_DISPLACEMENT_BONUS", "8"))

# Gap / engulf-zone historical rejection quality
GAP_REJECTION_WICK_TO_BODY_MIN = float(os.getenv("GAP_REJECTION_WICK_TO_BODY_MIN", "1.2"))
GAP_REJECTION_WICK_RANGE_PCT_MIN = float(os.getenv("GAP_REJECTION_WICK_RANGE_PCT_MIN", "0.35"))
GAP_REJECTION_MIN_BODY_PIPS = float(os.getenv("GAP_REJECTION_MIN_BODY_PIPS", "1.5"))
GAP_REJECTION_PUSH_AWAY_PIPS = {
    "H1": float(os.getenv("GAP_REJECTION_PUSH_H1", "30")),
    "M30": float(os.getenv("GAP_REJECTION_PUSH_M30", "20")),
    "M15": float(os.getenv("GAP_REJECTION_PUSH_M15", "15")),
}
DEBUG_REJECTION_TRACE = os.getenv("DEBUG_REJECTION_TRACE", "false").lower() == "true"
DEBUG_ENGULF_TRACE = os.getenv("DEBUG_ENGULF_TRACE", "false").lower() == "true"

# Engulfing research-only replay tuning
ENGULF_MIN_QUALITY_SCORE = float(os.getenv("ENGULF_MIN_QUALITY_SCORE", "68"))
ENGULF_H1_RELAXED_QUALITY_SCORE = float(os.getenv("ENGULF_H1_RELAXED_QUALITY_SCORE", "64"))
ENGULF_MIN_QUALITY_REJECTIONS = int(os.getenv("ENGULF_MIN_QUALITY_REJECTIONS", "3"))
BEARISH_ENGULF_BIAS_BONUS = int(os.getenv("BEARISH_ENGULF_BIAS_BONUS", "8"))
ENGULF_BULLISH_DIRECTION_BONUS = int(os.getenv("ENGULF_BULLISH_DIRECTION_BONUS", "4"))
ENGULF_MAX_PER_TIMEFRAME_DIRECTION_SESSION = int(os.getenv("ENGULF_MAX_PER_TIMEFRAME_DIRECTION_SESSION", "2"))
ENGULF_MAX_ACTIVE_CANDIDATES_PER_SYMBOL = int(os.getenv("ENGULF_MAX_ACTIVE_CANDIDATES_PER_SYMBOL", "6"))
ENGULF_ALLOWED_RESEARCH_TIMEFRAMES = tuple(
    part.strip() for part in os.getenv("ENGULF_ALLOWED_RESEARCH_TIMEFRAMES", "H1,M30").split(",") if part.strip()
)
ENGULF_STRONG_BIAS_BONUS = int(os.getenv("ENGULF_STRONG_BIAS_BONUS", "8"))
ENGULF_MODERATE_BIAS_BONUS = int(os.getenv("ENGULF_MODERATE_BIAS_BONUS", "3"))

# Live strategy registry / forward-testing controls
# ── PRODUCTION LOCK ──────────────────────────────────────────────────────────
# Only gap_sweep (Gap + liquidity_sweep_reclaim) is approved for live forward
# testing. All other strategies are research/replay only until verified.
# Override via env: LIVE_ENABLED_STRATEGIES=gap_sweep
LIVE_ENABLED_STRATEGIES = [
    part.strip()
    for part in os.getenv("LIVE_ENABLED_STRATEGIES", "gap_sweep").split(",")
    if part.strip()
]

# Strategies that are scanning/research only — must never produce live Telegram
# alerts or live trade tracking entries. Changing this list requires explicit
# replay verification first.
RESEARCH_ONLY_STRATEGIES: List[str] = [
    part.strip()
    for part in os.getenv(
        "RESEARCH_ONLY_STRATEGIES",
        "engulfing_rejection,standard_break_retest,failed_engulf_break_retest,failed_gap_break_retest",
    ).split(",")
    if part.strip()
]
ENGULF_ALLOWED_LIVE_TIMEFRAMES = tuple(
    part.strip()
    for part in os.getenv("ENGULF_ALLOWED_LIVE_TIMEFRAMES", "H1,M30").split(",")
    if part.strip()
)
ENGULF_LIVE_SETUP_TYPE = os.getenv("ENGULF_LIVE_SETUP_TYPE", "engulfing_rejection")
ENGULF_LIVE_CONFIRMATION_TYPE = os.getenv("ENGULF_LIVE_CONFIRMATION_TYPE", "engulfing_reversal")
ENGULF_LIVE_ALERT_ONLY = os.getenv("ENGULF_LIVE_ALERT_ONLY", "true").lower() == "true"
ENGULF_LIVE_MIN_QUALITY_SCORE = float(os.getenv("ENGULF_LIVE_MIN_QUALITY_SCORE", str(ENGULF_MIN_QUALITY_SCORE)))
ENGULF_LIVE_H1_RELAXED_QUALITY_SCORE = float(
    os.getenv("ENGULF_LIVE_H1_RELAXED_QUALITY_SCORE", str(ENGULF_H1_RELAXED_QUALITY_SCORE))
)
ENGULF_LIVE_MIN_QUALITY_REJECTIONS = int(
    os.getenv("ENGULF_LIVE_MIN_QUALITY_REJECTIONS", str(ENGULF_MIN_QUALITY_REJECTIONS))
)
ENGULF_LIVE_TIMEFRAME_SCORE = {
    "H1": int(os.getenv("ENGULF_LIVE_H1_SCORE", "8")),
    "M30": int(os.getenv("ENGULF_LIVE_M30_SCORE", "6")),
    "M15": int(os.getenv("ENGULF_LIVE_M15_SCORE", "-99")),
}
ENGULF_LIVE_SESSION_SCORE = {
    "london": int(os.getenv("ENGULF_LIVE_SESSION_LONDON", "10")),
    "asia": int(os.getenv("ENGULF_LIVE_SESSION_ASIA", "4")),
    "new_york": int(os.getenv("ENGULF_LIVE_SESSION_NEW_YORK", "-8")),
    "off_session": int(os.getenv("ENGULF_LIVE_SESSION_OFF", "2")),
    "overlap": int(os.getenv("ENGULF_LIVE_SESSION_OVERLAP", "6")),
}
ENGULF_LIVE_NEW_YORK_STRONG_QUALITY = float(os.getenv("ENGULF_LIVE_NEW_YORK_STRONG_QUALITY", "78"))
ENGULF_LIVE_MAX_PER_TIMEFRAME_DIRECTION_SESSION = int(
    os.getenv("ENGULF_LIVE_MAX_PER_TIMEFRAME_DIRECTION_SESSION", "2")
)
ENGULF_LIVE_MAX_CANDIDATES_PER_SCAN = int(os.getenv("ENGULF_LIVE_MAX_CANDIDATES_PER_SCAN", "4"))

# A/V quality scoring bonuses (selector + final scoring layers only)
AV_STRONG_ORIGIN_BONUS = int(os.getenv("AV_STRONG_ORIGIN_BONUS", "8"))
AV_DISPLACEMENT_BONUS = int(os.getenv("AV_DISPLACEMENT_BONUS", "6"))
AV_QM_CONTEXT_BONUS = int(os.getenv("AV_QM_CONTEXT_BONUS", "5"))
AV_MICRO_CONFIRMATION_BONUS = int(os.getenv("AV_MICRO_CONFIRMATION_BONUS", "10"))

# Minimum 100-pt quality score to pass per scope
LEVEL_MIN_QUALITY_MAJOR    = 45   # major structure: stricter — needs real confluence
LEVEL_MIN_QUALITY_RECENT   = 38   # recent leg: slightly looser — fresher context
LEVEL_MIN_QUALITY_PREVIOUS = 32   # previous leg: loosest — used as fallback only

# Maximum levels returned per scope per timeframe pair
LEVEL_MAX_PER_MAJOR    = 2   # top 2 major levels only
LEVEL_MAX_PER_RECENT   = 2   # top 2 recent-leg levels
LEVEL_MAX_PER_PREVIOUS = 1   # top 1 previous-leg level (fallback)
LEVEL_MAX_MONITORED_PER_PAIR = 4   # final cap after pair-level prioritisation

# Crowding: suppress a weaker level if a stronger one exists within this distance
LEVEL_CROWDING_PIPS = 15     # pips — prevents clustering of near-duplicate levels

# Range-extreme filter: level must be in the outer N% of the recent trading range
# 0.30 = outer 30% at top or bottom; levels in the middle 40% are rejected
LEVEL_RANGE_EXTREME_PCT = 0.30   # 0.0–0.5; higher = stricter mid-range rejection

# Number of bars to compute the range extreme reference window
LEVEL_RANGE_LOOKBACK = 50    # candles (same TF as the level)

# Additional elite-level selection gates
LEVEL_MIN_PRICE_DISTANCE_PIPS = 12   # reject levels already too close to spot
LEVEL_CHOP_LOOKBACK = 24             # lower-TF candles used to measure chop near a level
LEVEL_CHOP_MAX_FLIPS = 14            # too many body-direction flips = noisy chop

# Pair-aware level anchoring.
# These gates affect which structural levels are monitored for watchlist +
# confirmation. H4->H1 remains flexible for swing structure, while intraday
# pairs are anchored to current price and the active psychological zone.
# Reject only truly deep intraday levels. The softer 4700 anchor below is a
# scoring penalty, not an automatic rejection.
LEVEL_PAIR_DEEP_REJECT_FLOOR = {
    "H4->H1": None,
    "H1->M30": 4600.0,
    "M30->M15": 4600.0,
}
LEVEL_PAIR_SOFT_FLOOR = {
    "H4->H1": None,
    "H1->M30": 4700.0,
    "M30->M15": 4700.0,
}
LEVEL_PAIR_SOFT_FLOOR_PENALTY_PER_PIP = {
    "H4->H1": 0.0,
    "H1->M30": 0.18,
    "M30->M15": 0.28,
}
LEVEL_PAIR_MAX_DISTANCE_PIPS = {
    "H4->H1": 0,       # 0 disables max-distance rejection for swing structures
    "H1->M30": 320,
    "M30->M15": 300,
}
LEVEL_PAIR_NEAR_DISTANCE_PIPS = {
    "H4->H1": 180,
    "H1->M30": 75,
    "M30->M15": 40,
}
LEVEL_PAIR_SOFT_DISTANCE_PIPS = {
    "H4->H1": 320,
    "H1->M30": 130,
    "M30->M15": 70,
}
LEVEL_PAIR_DISTANCE_PENALTY_PER_PIP = {
    "H4->H1": 0.02,
    "H1->M30": 0.12,
    "M30->M15": 0.25,
}
LEVEL_PSYCH_REGION_TRIGGER_PRICE = 4800.0
LEVEL_PSYCH_REGION_RADIUS_PIPS = 75
LEVEL_PSYCH_REGION_BONUS = {
    "H4->H1": 2,
    "H1->M30": 10,
    "M30->M15": 14,
}
LEVEL_PSYCH_REGION_PENALTY = {
    "H4->H1": 0,
    "H1->M30": 7,
    "M30->M15": 12,
}
LEVEL_PAIR_SCOPE_BONUS = {
    "H4->H1": {"major": 6, "recent": 4, "previous": 0},
    "H1->M30": {"recent": 12, "previous": 5, "major": -6},
    "M30->M15": {"recent": 16, "previous": 4, "major": -12},
}
LEVEL_TP_ROOM_TOLERANCE_PIPS = 8
LEVEL_TP_ROOM_PENALTY_PER_PIP = 1.5
LEVEL_TP_ROOM_HARD_FLOOR_PIPS = float(os.getenv("LEVEL_TP_ROOM_HARD_FLOOR_PIPS", "45"))
LEVEL_TP_ROOM_MARGINAL_PIPS = float(os.getenv("LEVEL_TP_ROOM_MARGINAL_PIPS", "55"))
SESSION_TP1_MIN_PIPS = {
    "asia": float(os.getenv("SESSION_TP1_MIN_ASIA", "35")),
    "london": float(os.getenv("SESSION_TP1_MIN_LONDON", "30")),
    "new_york": float(os.getenv("SESSION_TP1_MIN_NEW_YORK", "35")),
    "overlap": float(os.getenv("SESSION_TP1_MIN_OVERLAP", "35")),
    "off_session": float(os.getenv("SESSION_TP1_MIN_OFF_SESSION", "40")),
}
SESSION_TP1_MARGINAL_BUFFER_PIPS = float(os.getenv("SESSION_TP1_MARGINAL_BUFFER_PIPS", "8"))

# Filter 2 — Approach Distance
# Candle BEFORE the confirmation candle must have been at least this far from the level.
# Prevents "price grinding sideways into the level" false signals.
MIN_APPROACH_DISTANCE_PIPS = 10   # pip distance the approach candle must start from

# Filter 3 — Impulse
# Approach candles (before the confirmation candle) must show directional momentum.
IMPULSE_LOOKBACK    = 5    # candles to evaluate before the confirmation candle
IMPULSE_MIN_CANDLES = 3    # at least N candles in approach direction
IMPULSE_BODY_RATIO  = 1.5  # OR any approach candle body ≥ 1.5× recent average body

# Watch Level — pre-confirmation approach alert
# When current price is within this distance of a level, send a "watch" alert.
# Must be wider than LEVEL_TOLERANCE_PIPS to give a useful early warning.
WATCH_DISTANCE_PIPS = 20    # pips from level → send "approaching" alert

# Watchlist alert selection
# These filters only control early Telegram watchlist alerts. They do not
# remove levels from confirmation monitoring.
WATCHLIST_MAX_DISTANCE_PIPS = {
    "H4->H1": 450,    # swing structures can be far from spot
    "H1->M30": 260,   # intraday can monitor penalised but relevant pullbacks
    "M30->M15": 180,  # fast intraday still prefers near price, but no hard 95p wall
}
WATCHLIST_SOFT_DISTANCE_PIPS = {
    "H4->H1": 180,
    "H1->M30": 75,
    "M30->M15": 35,
}
WATCHLIST_MIN_ADJUSTED_SCORE = {
    "H4->H1": 50,
    "H1->M30": 55,
    "M30->M15": 58,
}
WATCHLIST_MAX_ALERTS_BY_HORIZON = {
    "fast_intraday": 2,
    "intraday": 3,
    "swing": 2,
}

# Entry-ready alerts
# Signals are already deduped by fingerprint; this cap prevents one scan from
# dumping too many confirmed pending-order ideas at once.
ENTRY_READY_MAX_ALERTS_PER_SCAN = 3

# Historical replay defaults. Replay is separate from the live bot and uses
# these limits when run from historical_replay.run.
REPLAY_DEFAULT_MONTHS = 2
REPLAY_WARMUP_DAYS = 45
REPLAY_STEP_TIMEFRAME = "M15"

# Filter 5 — TP Clearance
# The path to TP1 must not have a structural level blocking it.
# If a blocking level is closer than this, TP1 is adjusted to that level.
MIN_TP_CLEARANCE_PIPS = 40  # minimum clear path to TP1 (pips)

# ─────────────────────────────────────────────
# ADVANCED STRATEGY PARAMETERS
# ─────────────────────────────────────────────

# QM (Quasimodo) detection
QM_BREAK_THRESHOLD = 2          # break episodes through a level before it becomes QM

# Leg lookback windows (candles)
RECENT_LEG_LOOKBACK  = 50       # candles for recent leg
PREV_LEG_START       = 50       # previous leg: from N candles ago ...
PREV_LEG_END         = 150      # ... to M candles ago

# Volatility filter — minimum average candle body in price units (raw $, not pips)
# $2.00 = 2 pip minimum body — filters near-flat, non-tradeable conditions
VOLATILITY_MIN_BODY  = 2.00

# Psychological level increments
PSYCH_MAJOR_STEP = 100          # very strong round number (3200, 3300 ...)
PSYCH_MEDIUM_STEP = 50          # medium (3250, 3350 ...)
PSYCH_MINOR_STEP  = 10          # minor (3210, 3220 ...)

# Session windows in UTC hours  (CAT = UTC+2)
# Asia: 00:00-07:00 UTC = 02:00-09:00 CAT
# London: 07:00-10:00 UTC  = 09:00-12:00 CAT
# New York: 13:00-16:00 UTC = 15:00-18:00 CAT
SESSION_ASIA_UTC      = (0, 7)
SESSION_LONDON_UTC    = (7, 10)
SESSION_NEW_YORK_UTC  = (13, 16)
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Africa/Johannesburg")
BOT_ACTIVE_START_HOUR = int(os.getenv("BOT_ACTIVE_START_HOUR", "7"))
BOT_ACTIVE_END_HOUR = int(os.getenv("BOT_ACTIVE_END_HOUR", "19"))
ALLOWED_SESSIONS = [
    item.strip().lower()
    for item in os.getenv("ALLOWED_SESSIONS", "asia,london,new_york,overlap").split(",")
    if item.strip()
]
BLOCK_OFF_SESSION = os.getenv("BLOCK_OFF_SESSION", "true").lower() == "true"
SESSION_CONFIDENCE_BONUS = {
    "overlap": 0.10,
    "asia": 0.00,
    "london": 0.05,
    "new_york": 0.07,
    "off_session": -0.10,
}
SESSION_PROFILE = {
    "asia": "strict",
    "london": "balanced",
    "new_york": "aggressive",
    "overlap": "priority",
    "off_session": "blocked",
}
SESSION_FINAL_SCORE_ADJUSTMENT = {
    "asia": 8,
    "london": 0,
    "new_york": -3,
    "overlap": 2,
    "off_session": 0,
}
SESSION_MIN_CONFIRMATION_BONUS = {
    "asia": 8,
    "london": 0,
    "new_york": 0,
    "overlap": 8,
    "off_session": 0,
}

# News filter — list of UTC times "HH:MM" for high-impact USD events
# Extend this list with upcoming NFP/FOMC/CPI times
USD_NEWS_TIMES: List[str] = [
    # Example recurring patterns (update with actual scheduled times):
    # "13:30",   # NFP / CPI / PPI (US data releases)
    # "18:00",   # FOMC statement
]
NEWS_FILTER_MINUTES = 15        # minutes to block before and after each news event

# Trend filter
H4_EMA_PERIOD = 20              # EMA period on H4 for trend bias
BIAS_EMA_PERIOD = 20            # EMA period used by D1/H4/H1 directional bias engine
BIAS_RECENT_LOOKBACK = 8        # recent closes used to detect impulse/pullback state
BIAS_CONTINUATION_BONUS = {
    "strong": 18,
    "moderate": 12,
    "weak": 6,
}
BIAS_COUNTER_TREND_PENALTY = {
    "strong": 35,
    "moderate": 24,
    "weak": 12,
}
BIAS_PULLBACK_CONTINUATION_BONUS = 8
BIAS_STRONG_BLOCK_COUNTER_TREND = os.getenv("BIAS_STRONG_BLOCK_COUNTER_TREND", "true").lower() == "true"
BIAS_MODERATE_COUNTER_TREND_CONFIDENCE_PENALTY = float(
    os.getenv("BIAS_MODERATE_COUNTER_TREND_CONFIDENCE_PENALTY", "0.12")
)
BIAS_MODERATE_COUNTER_TREND_MIN_CONFIDENCE = float(
    os.getenv("BIAS_MODERATE_COUNTER_TREND_MIN_CONFIDENCE", "0.75")
)
BIAS_BLOCK_WEAK_DOMINANT = os.getenv("BIAS_BLOCK_WEAK_DOMINANT", "true").lower() == "true"

# Final setup quality gate. The score is interpretable and replay/live share it.
FINAL_SETUP_SCORE_MIN = float(os.getenv("FINAL_SETUP_SCORE_MIN", "65"))
MAX_ACTIVE_CANDIDATES_PER_SCAN = int(os.getenv("MAX_ACTIVE_CANDIDATES_PER_SCAN", "3"))
DIRECTIONAL_COUNTER_SCORE_PREMIUM = float(os.getenv("DIRECTIONAL_COUNTER_SCORE_PREMIUM", "8"))
CONFIRMATION_SCORE_BONUS = {
    "rejection": 0,
    "first_rejection": 0,
    "liquidity_sweep_reclaim": 12,
    "double_pattern": 8,
    "engulfing_reversal": 10,
    "weak_rejection": -10,
}
CONFIRMATION_SCORE_BONUS_CAP = float(os.getenv("CONFIRMATION_SCORE_BONUS_CAP", "16"))

# Lower-timeframe micro-confirmation is an enhancement layer only. M15 remains
# the primary confirmation timeframe for the active M30->M15 strategy.
MICRO_CONFIRMATION_ENABLED = os.getenv("MICRO_CONFIRMATION_ENABLED", "true").lower() == "true"
MICRO_CONFIRMATION_USE_M1_FALLBACK = os.getenv("MICRO_CONFIRMATION_USE_M1_FALLBACK", "true").lower() == "true"
MICRO_CONFIRMATION_LOOKBACK = int(os.getenv("MICRO_CONFIRMATION_LOOKBACK", "14"))
MICRO_CONFIRMATION_AOI_PIPS = float(os.getenv("MICRO_CONFIRMATION_AOI_PIPS", "6"))
MICRO_CONFIRMATION_SCORE = {
    "liquidity_sweep_reclaim": float(os.getenv("MICRO_SCORE_LIQUIDITY_SWEEP_RECLAIM", "12")),
    "double_pattern": float(os.getenv("MICRO_SCORE_DOUBLE_PATTERN", "8")),
    "engulfing_reversal": float(os.getenv("MICRO_SCORE_ENGULFING_REVERSAL", "10")),
    "wick_follow_through": float(os.getenv("MICRO_SCORE_WICK_FOLLOW_THROUGH", "6")),
    "micro_contradiction": float(os.getenv("MICRO_SCORE_CONTRADICTION", "-12")),
}
MICRO_CONFIRMATION_SCORE_CAP = float(os.getenv("MICRO_CONFIRMATION_SCORE_CAP", "16"))
MICRO_CONFIDENCE_SCALE = float(os.getenv("MICRO_CONFIDENCE_SCALE", "100"))
MICRO_PRIORITY_WEIGHTS = {
    "liquidity_sweep_reclaim": float(os.getenv("MICRO_PRIORITY_LIQUIDITY_SWEEP_RECLAIM", "1.25")),
    "double_pattern": float(os.getenv("MICRO_PRIORITY_DOUBLE_PATTERN", "1.0")),
}
MICRO_LIQUIDITY_SWEEP_EXTRA_BONUS = float(os.getenv("MICRO_LIQUIDITY_SWEEP_EXTRA_BONUS", "4"))
ENGULFING_BOOST_SCORE = float(os.getenv("ENGULFING_BOOST_SCORE", "2"))
ENGULFING_SWEEP_COMBO_BOOST = float(os.getenv("ENGULFING_SWEEP_COMBO_BOOST", "4"))
MICRO_DOUBLE_PATTERN_MIN_QUALITY = float(os.getenv("MICRO_DOUBLE_PATTERN_MIN_QUALITY", "60"))
MICRO_QUALITY_SCORE_SCALE = float(os.getenv("MICRO_QUALITY_SCORE_SCALE", "8"))
MICRO_SESSION_MIN_SCORE = {
    "asia": float(os.getenv("MICRO_SESSION_MIN_ASIA", "8")),
    "london": float(os.getenv("MICRO_SESSION_MIN_LONDON", "0")),
    "new_york": float(os.getenv("MICRO_SESSION_MIN_NEW_YORK", "0")),
    "overlap": float(os.getenv("MICRO_SESSION_MIN_OVERLAP", "8")),
    "off_session": float(os.getenv("MICRO_SESSION_MIN_OFF_SESSION", "0")),
}
MICRO_SESSION_REQUIRED = {
    "asia": os.getenv("MICRO_SESSION_REQUIRED_ASIA", "true").lower() == "true",
    "london": os.getenv("MICRO_SESSION_REQUIRED_LONDON", "false").lower() == "true",
    "new_york": os.getenv("MICRO_SESSION_REQUIRED_NEW_YORK", "false").lower() == "true",
    "overlap": os.getenv("MICRO_SESSION_REQUIRED_OVERLAP", "false").lower() == "true",
    "off_session": os.getenv("MICRO_SESSION_REQUIRED_OFF_SESSION", "false").lower() == "true",
}
# Score penalty applied to the final_score when London/NY setups have no micro confirmation.
# London is stricter than NY because Asia liquidity makes London reversals more predictable.
MICRO_LONDON_NO_MICRO_PENALTY = float(os.getenv("MICRO_LONDON_NO_MICRO_PENALTY", "8"))
MICRO_NY_NO_MICRO_PENALTY = float(os.getenv("MICRO_NY_NO_MICRO_PENALTY", "5"))

# Active strategy gate. Research detectors may still create A/V and other
# micro-patterns, but live/replay trade generation only uses proven components.
ACTIVE_STRATEGY_LEVEL_TYPES = [
    item.strip()
    for item in os.getenv("ACTIVE_STRATEGY_LEVEL_TYPES", "Gap").split(",")
    if item.strip()
]
ACTIVE_STRATEGY_REQUIRE_MICRO = os.getenv("ACTIVE_STRATEGY_REQUIRE_MICRO", "true").lower() == "true"
# Final production lock: active strategy uses sweep-reclaim only.
# Other micro patterns may remain in the codebase for research/debug, but they
# are not allowed to participate in the active trading path.
ACTIVE_STRATEGY_ALLOWED_MICRO_TYPES = ["liquidity_sweep_reclaim"]

# Institutional-style execution filters layered on top of the active Gap +
# approved-micro strategy.
EXECUTION_FILTERS_ENABLED = os.getenv("EXECUTION_FILTERS_ENABLED", "true").lower() == "true"
HTF_SWEEP_FILTER_ENABLED = os.getenv("HTF_SWEEP_FILTER_ENABLED", "false").lower() == "true"
HTF_SWEEP_LOOKBACK = int(os.getenv("HTF_SWEEP_LOOKBACK", "24"))
HTF_SWEEP_RECENT_BARS = int(os.getenv("HTF_SWEEP_RECENT_BARS", "4"))
HTF_SWEEP_MIN_PIPS = float(os.getenv("HTF_SWEEP_MIN_PIPS", "6"))
PD_FILTER_ENABLED = os.getenv("PD_FILTER_ENABLED", "true").lower() == "true"
PD_RANGE_LOOKBACK = int(os.getenv("PD_RANGE_LOOKBACK", "48"))
PD_EQUILIBRIUM_BAND_PIPS = float(os.getenv("PD_EQUILIBRIUM_BAND_PIPS", "12"))
PD_FAVORABLE_SCORE = float(os.getenv("PD_FAVORABLE_SCORE", "5"))
PD_EQUILIBRIUM_SCORE = float(os.getenv("PD_EQUILIBRIUM_SCORE", "0"))
PD_OPPOSITE_REJECT = os.getenv("PD_OPPOSITE_REJECT", "false").lower() == "true"
PD_OPPOSITE_PENALTY = float(os.getenv("PD_OPPOSITE_PENALTY", "3"))
STRONG_BIAS_GATE_ENABLED = os.getenv("STRONG_BIAS_GATE_ENABLED", "true").lower() == "true"
STRONG_BIAS_GATE_REQUIRE_STRONG = os.getenv("STRONG_BIAS_GATE_REQUIRE_STRONG", "false").lower() == "true"
HTF_SWEEP_ALIGNED_SCORE = float(os.getenv("HTF_SWEEP_ALIGNED_SCORE", "5"))
HTF_SWEEP_ABSENT_PENALTY = float(os.getenv("HTF_SWEEP_ABSENT_PENALTY", "0"))
HTF_SWEEP_OPPOSITE_PENALTY = float(os.getenv("HTF_SWEEP_OPPOSITE_PENALTY", "0"))
HTF_SWEEP_OPPOSITE_REJECT = os.getenv("HTF_SWEEP_OPPOSITE_REJECT", "false").lower() == "true"
BIAS_MIXED_PENALTY = float(os.getenv("BIAS_MIXED_PENALTY", "10"))
BIAS_MODERATE_ALIGNED_SCORE = float(os.getenv("BIAS_MODERATE_ALIGNED_SCORE", "6"))
BIAS_STRONG_ALIGNED_SCORE = float(os.getenv("BIAS_STRONG_ALIGNED_SCORE", "10"))
TP1_BONUS_MIN_PIPS = float(os.getenv("TP1_BONUS_MIN_PIPS", "40"))
TP1_QUALITY_SCORE_BONUS = float(os.getenv("TP1_QUALITY_SCORE_BONUS", "3"))
HIGH_QUALITY_TRADE_SCORE = float(os.getenv("HIGH_QUALITY_TRADE_SCORE", "80"))
MICRO_STRONG_SCORE = float(os.getenv("MICRO_STRONG_SCORE", "70"))

# ─────────────────────────────────────────────
# TIMEFRAME PAIRS  (higher → lower)
# ─────────────────────────────────────────────
ENABLE_H4_H1 = os.getenv("ENABLE_H4_H1", "false").lower() == "true"
ENABLE_H1_M30 = os.getenv("ENABLE_H1_M30", "false").lower() == "true"
ENABLE_M30_M15 = os.getenv("ENABLE_M30_M15", "true").lower() == "true"

ALL_TIMEFRAME_PAIRS = [
    ("H4", "H1"),
    ("H1", "M30"),
    ("M30", "M15"),
]

TIMEFRAME_PAIR_FLAGS = {
    ("H4", "H1"): ENABLE_H4_H1,
    ("H1", "M30"): ENABLE_H1_M30,
    ("M30", "M15"): ENABLE_M30_M15,
}

TIMEFRAME_PAIRS = [
    pair for pair in ALL_TIMEFRAME_PAIRS
    if TIMEFRAME_PAIR_FLAGS.get(pair, False)
]

DISABLED_TIMEFRAME_PAIRS = [
    pair for pair in ALL_TIMEFRAME_PAIRS
    if not TIMEFRAME_PAIR_FLAGS.get(pair, False)
]

ACTIVE_TIMEFRAME_PAIR_LABELS = {
    f"{high}->{low}" for high, low in TIMEFRAME_PAIRS
} | {
    f"{high}-{low}" for high, low in TIMEFRAME_PAIRS
}

DISABLED_TIMEFRAME_PAIR_LABELS = {
    f"{high}->{low}" for high, low in DISABLED_TIMEFRAME_PAIRS
} | {
    f"{high}-{low}" for high, low in DISABLED_TIMEFRAME_PAIRS
}

# MT5 timeframe string → integer mapping (filled by mt5_client at runtime)
TF_MAP = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "M30": 30,
    "H1":  60,
    "H4":  240,
    "D1":  1440,
}

# Number of candles to fetch per timeframe
CANDLE_COUNT = {
    "D1":  220,
    "H4":  200,
    "H1":  300,
    "M30": 400,
    "M15": 500,
    "M5":  500,
    "M1":  300,
}

# ─────────────────────────────────────────────
# LEVEL DETECTION PARAMETERS
# ─────────────────────────────────────────────
MIN_GAP_CANDLES = 2          # Minimum consecutive same-direction candles for a gap
GAP_BODY_MULTIPLIER = 1.5    # Gap candle body must be X× average body size

# Lookback window for detecting swing highs/lows
SWING_LOOKBACK = 5

# ─────────────────────────────────────────────
# LEARNING SYSTEM
# ─────────────────────────────────────────────
MIN_TRADES_FOR_LEARNING = 10  # Minimum trades before adjusting confidence
BASE_CONFIDENCE = 0.5         # Default confidence score
CONFIDENCE_DECAY = 0.05       # How much confidence drops per loss
CONFIDENCE_BOOST = 0.05       # How much confidence rises per win
MAX_CONFIDENCE = 0.95
MIN_CONFIDENCE = 0.10

# Learned setup-combination ranking layer. This is bounded so learning can
# guide priority without overriding risk/session/TP controls.
LEARNED_EDGE_ENABLED = os.getenv("LEARNED_EDGE_ENABLED", "true").lower() == "true"
LEARNED_EDGE_MIN_TRADES = int(os.getenv("LEARNED_EDGE_MIN_TRADES", "2"))
LEARNED_EDGE_MAX_BONUS = float(os.getenv("LEARNED_EDGE_MAX_BONUS", "8"))
LEARNED_EDGE_MAX_PENALTY = float(os.getenv("LEARNED_EDGE_MAX_PENALTY", "6"))
LEARNED_EDGE_REWARD_SCALE = float(os.getenv("LEARNED_EDGE_REWARD_SCALE", "2.0"))
LEARNED_EDGE_LIQUIDITY_MULTIPLIER = float(os.getenv("LEARNED_EDGE_LIQUIDITY_MULTIPLIER", "1.20"))
LEARNED_EDGE_DOUBLE_PATTERN_MULTIPLIER = float(os.getenv("LEARNED_EDGE_DOUBLE_PATTERN_MULTIPLIER", "0.90"))

# Minimum confidence to generate a signal and send a Telegram alert.
# Setups below this threshold are logged and a skip alert is sent to Telegram.
# 0.65 requires solid confluence (QM, psych level, or session alignment).
# With no trade history the learning engine does not scale confidence down,
# so raw setup scores of 0.65+ will dispatch without penalty.
MIN_SIGNAL_CONFIDENCE = 0.65

# ─────────────────────────────────────────────
# TRADE LIMITS
# ─────────────────────────────────────────────
MAX_CONCURRENT_TRADES = 3        # hard cap: no new signals when 3 trades active
MAX_TRADES_PER_TF_PAIR = 1       # only 1 active trade per timeframe pair (e.g. H1→M30)

# ─────────────────────────────────────────────
# LIQUIDITY SWEEP + DISPLACEMENT (LSD) STRATEGY
# ─────────────────────────────────────────────

# Equal-level detection — highs/lows within this distance are "equal"
LSD_EQUAL_LEVEL_TOLERANCE_PIPS = 5

# How many candles to look back when searching for equal highs/lows
LSD_SWEEP_LOOKBACK = 10

# Displacement candle: body must be >= this multiple of the lookback average body
LSD_DISPLACEMENT_RATIO = 1.5

# Displacement candle: close must be in the top/bottom this fraction of the candle range
# e.g. 0.3 means close must be in the top 30% (BUY) or bottom 30% (SELL) of the candle
LSD_DISPLACEMENT_CLOSE_PCT = 0.3

# BOS detection: how many candles back to look for the most recent swing high/low
LSD_BOS_LOOKBACK = 10

# Swing mode (H4 → H1 → M15): max stop-loss in pips
LSD_SWING_MAX_SL_PIPS = 80

# Scalp mode (M15 → M5 → M1): SL range in pips
LSD_SCALP_MIN_SL_PIPS = 15
LSD_SCALP_MAX_SL_PIPS = 30

# SL buffer above/below sweep wick in pips
LSD_SWING_SL_BUFFER_PIPS = 7
LSD_SCALP_SL_BUFFER_PIPS = 4

# Minimum Risk:Reward ratio for LSD signals
LSD_MIN_RR = 2.0

# Asian session range: 00:00 – this UTC hour (H1 bars used for range calculation)
LSD_ASIAN_SESSION_END = 6

# ─────────────────────────────────────────────
# SCAN ENGINE
# ─────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", 60))

# ─────────────────────────────────────────────
# MetaTrader 5
# ─────────────────────────────────────────────
MT5_LOGIN = int(os.getenv("MT5_LOGIN", 435409123))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "Jeremiah26$")
MT5_SERVER = os.getenv("MT5_SERVER", "Exness-MT5Trial9")
MT5_PATH = os.getenv("MT5_PATH", "C:\\Program Files\\MetaTrader 5\\terminal64.exe")

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8423015469:AAETTIiz9ydz83aMOECVFAwpTFVbTYhfrE8")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1003937713982")

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
USE_SUPABASE = os.getenv("USE_SUPABASE", "false").lower() == "true"

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "alphapulse")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "AlphaPulse26$$777")

# SUPABASE_URL = os.getenv("SUPABASE_URL", "")
# SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "logs/alphapulse.log"

# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", 8501))

# ─────────────────────────────────────────────
# NO-SETUP STATUS ALERTS
# ─────────────────────────────────────────────
# When true, Spencer sends a periodic Telegram message when no setups are
# found over the configured interval (useful to confirm the bot is alive).
SEND_NO_SETUP_STATUS_ALERT = os.getenv("SEND_NO_SETUP_STATUS_ALERT", "false").lower() == "true"
NO_SETUP_STATUS_INTERVAL_MINUTES = int(os.getenv("NO_SETUP_STATUS_INTERVAL_MINUTES", "30"))
