"""
AlphaPulse — Break + Retest Research Engine
=============================================
Research-only replay module for two strategies:

  standard_break_retest
    Standard structural level break + retest setup.
    Level breaks cleanly, price retests from the opposite side, confirmation fires.

  failed_engulf_break_retest
    Gap/engulf zone breaks in the "wrong" direction for its expected rejection,
    then retests the zone as continuation. Uses Gap levels only.

DO NOT activate live. No Telegram alerts. Research + learning only.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from config.settings import (
    PIP_SIZE, TP_PIPS, MIN_SL_PIPS, MAX_SL_PIPS,
    LEVEL_TOLERANCE_PIPS, SYMBOL,
)
from data.mt5_client import MT5Client
from db.database import Database
from strategies.strategy_manager import StrategyManager
from utils.logger import get_logger

logger = get_logger("alphapulse.break_retest_research")

# ─────────────────────────────────────────────────────────────────────────────
# Strategy identifiers
# ─────────────────────────────────────────────────────────────────────────────

STRATEGY_STANDARD      = "standard_break_retest"
STRATEGY_FAILED_ENGULF = "failed_engulf_break_retest"

# ─────────────────────────────────────────────────────────────────────────────
# Break validation parameters
# ─────────────────────────────────────────────────────────────────────────────

# Minimum close distance beyond level for a valid break (pip)
MIN_BREAK_DISTANCE_PIPS: Dict[str, float] = {
    "M15": 10.0,
    "M30": 15.0,
    "H1":  20.0,
}

# Candle body must be ≥ this fraction of total range
MIN_BODY_RANGE_RATIO = 0.50
MIN_BODY_PIPS        = 5.0   # absolute minimum body pips

# Retest zone tolerance around broken level (pips)
RETEST_ZONE_TOLERANCE_PIPS = 4.0

# Bars to wait for retest before expiring candidate
MAX_BARS_TO_RETEST: Dict[str, int] = {
    "M15": 32,   # ≈ 8 hours on M15
    "M30": 16,   # ≈ 8 hours on M30
    "H1":  8,    # ≈ 8 hours on H1
}

# Bars after first retest touch to see confirmation
MAX_BARS_FOR_CONFIRMATION = 5

# Confirmation scoring
CONFIRMATION_WICK_SCORE     = 25
CONFIRMATION_CLOSE_SCORE    = 20
CONFIRMATION_MOMENTUM_SCORE = 15
CONFIRMATION_BIAS_BONUS     = 5
CONFIRMATION_THRESHOLD      = 25   # minimum score to activate

# Timeframes to scan
SCAN_TIMEFRAMES_STANDARD      = ["H1", "M30", "M15"]
SCAN_TIMEFRAMES_FAILED_ENGULF = ["H1", "M30"]       # M15 disabled per spec

# Allowed bias strengths
ALLOWED_BIAS_STRENGTHS = {"moderate", "strong"}

# Minimum bars needed in snapshot before scanning
SNAPSHOT_MIN_BARS = {
    "D1": 30, "H4": 50, "H1": 80, "M30": 100, "M15": 100,
}

# Max live break candidates per direction/timeframe to cap memory
MAX_CANDIDATES_PER_BUCKET = 8


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BreakCandidate:
    """Tracks a structural level that was cleanly broken and awaits retest."""
    candidate_key: str
    strategy_type: str
    symbol: str
    direction: str        # BUY = broke above resistance | SELL = broke below support
    timeframe: str

    # Level identity
    break_level: float
    source_level_type: str        # A | V | Gap
    source_strategy_type: str     # gap_sweep | engulfing_rejection
    zone_high: float
    zone_low: float

    # Break candle
    break_time: datetime
    break_close: float
    break_distance_pips: float
    break_body_pips: float
    break_body_ratio: float

    # Context at break time
    dominant_bias: str
    bias_strength: str
    session_name: str

    # Failed-engulf extras (None for standard_break_retest)
    original_engulf_high:              Optional[float]    = None
    original_engulf_low:               Optional[float]    = None
    original_engulf_mid:               Optional[float]    = None
    original_engulf_direction:         Optional[str]      = None
    original_engulf_time:              Optional[datetime] = None
    original_quality_rejection_count:  Optional[int]      = None
    original_structure_break_count:    Optional[int]      = None
    original_quality_score:            Optional[float]    = None

    # Retest tracking
    retest_touched:            bool               = False
    retest_time:               Optional[datetime] = None
    retest_level:              Optional[float]    = None
    retest_confirmation_type:  Optional[str]      = None
    retest_confirmation_score: float              = 0.0
    confirmation_bars:         int                = 0

    # State machine
    state: str       = "broken"   # broken | retest_zone | activated | expired | failed
    bars_since_break: int = 0
    reject_reason:   str = ""

    # Trade details (set on activation)
    entry: float = 0.0
    sl:    float = 0.0
    tp1:   float = 0.0
    tp2:   float = 0.0
    tp3:   float = 0.0


@dataclass
class BreakRetestTrade:
    """Fully activated break+retest trade being tracked through replay."""
    candidate_key:   str
    strategy_type:   str
    symbol:          str
    direction:       str
    timeframe:       str
    session_name:    str
    dominant_bias:   str
    bias_strength:   str

    break_level:         float
    source_level_type:   str
    source_strategy_type: str

    break_time:             datetime
    break_close:            float
    break_distance_pips:    float

    retest_time:                datetime
    retest_level:               float
    retest_confirmation_type:   str
    retest_confirmation_score:  float

    entry: float
    sl:    float
    tp1:   float
    tp2:   float
    tp3:   float

    # Failed-engulf extras
    original_engulf_high:             Optional[float]    = None
    original_engulf_low:              Optional[float]    = None
    original_engulf_mid:              Optional[float]    = None
    original_engulf_direction:        Optional[str]      = None
    original_engulf_time:             Optional[datetime] = None
    original_quality_rejection_count: Optional[int]      = None
    original_structure_break_count:   Optional[int]      = None
    original_quality_score:           Optional[float]    = None

    # Live tracking
    activated_at:          Optional[datetime]  = None
    tp_hit:                List[bool]          = field(default_factory=lambda: [False, False, False])
    sl_price_current:      float               = 0.0
    protected_after_tp1:   bool                = False
    closed:                bool                = False
    closed_at:             Optional[datetime]  = None
    final_result:          Optional[str]       = None
    final_pips:            float               = 0.0
    reward_score:          float               = 0.0
    tp_progress:           int                 = 0
    max_favorable_excursion: float             = 0.0
    max_adverse_excursion:   float             = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

class BreakRetestResearchEngine:
    """
    Research-only candle-by-candle replay engine for standard_break_retest
    and failed_engulf_break_retest strategies.

    Never sends Telegram alerts. Stores results to strategy_research_* tables.
    """

    def __init__(self, strategy_type: str = STRATEGY_STANDARD):
        if strategy_type not in (STRATEGY_STANDARD, STRATEGY_FAILED_ENGULF):
            raise ValueError(f"Unknown strategy_type: {strategy_type!r}")
        self.strategy_type = strategy_type
        self.db = Database()
        self.mt5 = MT5Client()
        self.strategy_manager = StrategyManager(learning_engine=None)
        logger.info("BreakRetestResearchEngine: strategy=%s", strategy_type)

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry points
    # ─────────────────────────────────────────────────────────────────────────

    def run_last_months(
        self,
        months: int = 4,
        symbol: str = SYMBOL,
        show_trades: int = 20,
    ) -> dict:
        warmup_days = 90
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=months * 30 + warmup_days)
        replay_start = end - timedelta(days=months * 30)
        return self.run(
            start=start,
            end=end,
            symbol=symbol,
            show_trades=show_trades,
            replay_start=replay_start,
        )

    def run(
        self,
        start: datetime,
        end: datetime,
        symbol: str = SYMBOL,
        show_trades: int = 20,
        replay_start: Optional[datetime] = None,
    ) -> dict:
        os.environ["ALPHAPULSE_REPLAY_MODE"] = "1"
        if replay_start is None:
            replay_start = start

        self.db.init()
        self.mt5.connect()

        run_id = self.db.create_strategy_research_run({
            "symbol":        symbol,
            "strategy_type": self.strategy_type,
            "status":        "running",
            "replay_start":  replay_start.isoformat(),
            "replay_end":    end.isoformat(),
            "started_at":    datetime.now(timezone.utc).isoformat(),
        })
        if not run_id:
            logger.error("Failed to create strategy research run row")
            return {}

        logger.info(
            "%s run %d: %s → %s",
            self.strategy_type, run_id,
            replay_start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )

        try:
            history = self._load_history(start, end, symbol)
            result = self._replay(run_id, history, replay_start, end, symbol)
            self._store_result(run_id, result)
            self.db.update_strategy_research_run(run_id, {
                "status":      "completed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            return result
        except Exception as exc:
            logger.error("Run %d failed: %s", run_id, exc, exc_info=True)
            self.db.update_strategy_research_run(run_id, {
                "status": "failed",
                "notes":  str(exc)[:500],
            })
            return {}
        finally:
            self.mt5.disconnect()
            self.db.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Data loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_history(
        self, start: datetime, end: datetime, symbol: str
    ) -> Dict[str, pd.DataFrame]:
        required = ["D1", "H4", "H1", "M30", "M15"]
        history: Dict[str, pd.DataFrame] = {}
        for tf in required:
            try:
                df = self.mt5.get_ohlcv(tf)
                if df is not None and len(df) > 0:
                    history[tf] = df
                    logger.info("[%s] Loaded %d bars", tf, len(df))
                else:
                    logger.warning("[%s] No data returned", tf)
            except Exception as exc:
                logger.warning("[%s] Load failed: %s", tf, exc)
        return history

    def _snapshot(
        self, history: Dict[str, pd.DataFrame], current_time: datetime
    ) -> Dict[str, pd.DataFrame]:
        snap: Dict[str, pd.DataFrame] = {}
        for tf, df in history.items():
            if "time" not in df.columns:
                snap[tf] = df
                continue
            mask = pd.to_datetime(df["time"], utc=True) <= current_time
            snap[tf] = df[mask].copy()
        return snap

    def _snapshot_ready(self, snap: Dict[str, pd.DataFrame]) -> bool:
        for tf, min_bars in SNAPSHOT_MIN_BARS.items():
            if tf in snap and len(snap[tf]) < min_bars:
                return False
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Main replay loop
    # ─────────────────────────────────────────────────────────────────────────

    def _replay(
        self,
        run_id: int,
        history: Dict[str, pd.DataFrame],
        replay_start: datetime,
        end: datetime,
        symbol: str,
    ) -> dict:
        # Step on M15 (finest grain); fall back to M30/H1
        step_df = history.get("M15") or history.get("M30") or history.get("H1")
        if step_df is None or len(step_df) == 0:
            logger.error("No step-timeframe data available")
            return {}

        # Trim to replay window
        step_times = pd.to_datetime(step_df["time"], utc=True)
        step_df = step_df[step_times >= replay_start].copy()

        candidates: Dict[str, BreakCandidate] = {}
        active_trades: List[BreakRetestTrade] = []
        closed_trades: List[BreakRetestTrade] = []

        # ── Funnel counters ───────────────────────────────────────────────────
        counters: Dict[str, int] = {
            "raw_levels_detected":       0,
            "break_candidates":          0,
            "valid_breaks":              0,
            "fake_breaks_rejected":      0,
            "retest_candidates":         0,
            "valid_retests":             0,
            "confirmation_passed":       0,
            "activated_trades":          0,
            "expired_candidates":        0,
            "failed_engulf_candidates":  0,
        }
        reject_reasons: Dict[str, int] = {}

        def _inc(reason: str) -> None:
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

        seen_break_keys: set = set()
        scan_tfs = (
            SCAN_TIMEFRAMES_STANDARD
            if self.strategy_type == STRATEGY_STANDARD
            else SCAN_TIMEFRAMES_FAILED_ENGULF
        )

        for _, bar_row in step_df.iterrows():
            bar_time  = pd.to_datetime(bar_row["time"], utc=True).to_pydatetime()
            bar_high  = float(bar_row["high"])
            bar_low   = float(bar_row["low"])
            bar_close = float(bar_row["close"])
            bar_open  = float(bar_row.get("open", bar_close))

            snap = self._snapshot(history, bar_time)
            if not self._snapshot_ready(snap):
                continue

            # ── Get market context (bias / session) ───────────────────────────
            ctx = self._get_context(snap, bar_close)

            # ── Detect new break candidates ───────────────────────────────────
            new_candidates = self._detect_breaks(
                snap, bar_row, bar_time, ctx, symbol, seen_break_keys, scan_tfs
            )
            for c in new_candidates:
                counters["raw_levels_detected"] += 1
                if c.strategy_type == STRATEGY_FAILED_ENGULF:
                    counters["failed_engulf_candidates"] += 1

                # Bias gates
                if not self._bias_strength_ok(c):
                    _inc("weak_bias")
                    continue
                if not self._bias_direction_aligned(c):
                    _inc("counter_bias")
                    continue

                # Break quality
                ok, reason = self._validate_break_quality(c)
                if not ok:
                    if reason == "wick_only_break":
                        counters["fake_breaks_rejected"] += 1
                    _inc(reason)
                    continue

                counters["break_candidates"] += 1
                counters["valid_breaks"] += 1
                seen_break_keys.add(c.candidate_key)
                candidates[c.candidate_key] = c

            # ── Expire stale candidates ───────────────────────────────────────
            expired_keys = [
                k for k, c in candidates.items()
                if not c.retest_touched
                and c.bars_since_break >= MAX_BARS_TO_RETEST.get(c.timeframe, 16)
            ]
            for k in expired_keys:
                counters["expired_candidates"] += 1
                _inc("expired")
                del candidates[k]

            for c in candidates.values():
                c.bars_since_break += 1

            # ── Retest check + confirmation ───────────────────────────────────
            activated_keys: List[str] = []
            failed_keys:    List[str] = []

            zone_tol = RETEST_ZONE_TOLERANCE_PIPS * PIP_SIZE

            for key, c in candidates.items():
                if c.state not in ("broken", "retest_zone"):
                    continue

                in_zone = (bar_low <= c.break_level + zone_tol
                           and bar_high >= c.break_level - zone_tol)

                if not c.retest_touched and in_zone:
                    c.retest_touched = True
                    c.state = "retest_zone"
                    c.retest_time = bar_time
                    c.retest_level = c.break_level
                    counters["retest_candidates"] += 1

                if c.retest_touched:
                    c.confirmation_bars += 1

                    # Fail if price closed deeply through zone in wrong direction
                    if c.direction == "BUY" and bar_close < c.break_level - zone_tol * 2:
                        c.state = "failed"
                        c.reject_reason = "retest_failed"
                        failed_keys.append(key)
                        _inc("retest_failed")
                        continue
                    if c.direction == "SELL" and bar_close > c.break_level + zone_tol * 2:
                        c.state = "failed"
                        c.reject_reason = "retest_failed"
                        failed_keys.append(key)
                        _inc("retest_failed")
                        continue

                    # Score retest bar
                    conf_type, conf_score = self._evaluate_confirmation(
                        c, bar_high, bar_low, bar_close, bar_open, ctx
                    )

                    if conf_score >= CONFIRMATION_THRESHOLD:
                        counters["valid_retests"] += 1
                        counters["confirmation_passed"] += 1

                        entry, sl = self._compute_entry_sl(
                            c, bar_high, bar_low, bar_close
                        )
                        if sl == 0.0:
                            _inc("distance_too_far")
                            failed_keys.append(key)
                            continue

                        tps = self._compute_tps(entry, sl, c.direction)

                        trade = BreakRetestTrade(
                            candidate_key=key,
                            strategy_type=c.strategy_type,
                            symbol=symbol,
                            direction=c.direction,
                            timeframe=c.timeframe,
                            session_name=c.session_name,
                            dominant_bias=c.dominant_bias,
                            bias_strength=c.bias_strength,
                            break_level=c.break_level,
                            source_level_type=c.source_level_type,
                            source_strategy_type=c.source_strategy_type,
                            break_time=c.break_time,
                            break_close=c.break_close,
                            break_distance_pips=c.break_distance_pips,
                            retest_time=c.retest_time,
                            retest_level=c.retest_level,
                            retest_confirmation_type=conf_type,
                            retest_confirmation_score=conf_score,
                            entry=entry,
                            sl=sl,
                            tp1=tps[0],
                            tp2=tps[1],
                            tp3=tps[2],
                            sl_price_current=sl,
                            activated_at=bar_time,
                            # failed-engulf extras
                            original_engulf_high=c.original_engulf_high,
                            original_engulf_low=c.original_engulf_low,
                            original_engulf_mid=c.original_engulf_mid,
                            original_engulf_direction=c.original_engulf_direction,
                            original_engulf_time=c.original_engulf_time,
                            original_quality_rejection_count=c.original_quality_rejection_count,
                            original_structure_break_count=c.original_structure_break_count,
                            original_quality_score=c.original_quality_score,
                        )
                        c.state = "activated"
                        active_trades.append(trade)
                        counters["activated_trades"] += 1
                        activated_keys.append(key)
                        logger.debug(
                            "ACTIVATED: %s %s %s @ %.2f | conf=%s(%.0f)",
                            c.strategy_type, c.direction, c.timeframe,
                            entry, conf_type, conf_score,
                        )

                    elif c.confirmation_bars >= MAX_BARS_FOR_CONFIRMATION:
                        c.state = "failed"
                        c.reject_reason = "confirmation_failed"
                        failed_keys.append(key)
                        _inc("confirmation_failed")

            for k in activated_keys + failed_keys:
                candidates.pop(k, None)

            # ── Update active trades (SL / TP state machine) ──────────────────
            still_active: List[BreakRetestTrade] = []
            for trade in active_trades:
                trade = self._update_trade(trade, bar_high, bar_low, bar_time)
                if trade.closed:
                    closed_trades.append(trade)
                    try:
                        self.db.insert_strategy_research_trade(
                            self._trade_to_db_payload(trade, run_id)
                        )
                    except Exception as exc:
                        logger.warning("Trade DB insert failed: %s", exc)
                else:
                    still_active.append(trade)
            active_trades = still_active

        # ── Close remaining open trades at end of replay ──────────────────────
        for trade in active_trades:
            trade.final_result = self._classify_result(trade)
            trade.final_pips   = self._final_pips(trade)
            trade.reward_score = self._reward_score(trade)
            trade.closed   = True
            trade.closed_at = end
            closed_trades.append(trade)
            try:
                self.db.insert_strategy_research_trade(
                    self._trade_to_db_payload(trade, run_id)
                )
            except Exception as exc:
                logger.warning("Trade DB insert failed: %s", exc)

        return self._build_result(run_id, closed_trades, counters, reject_reasons)

    # ─────────────────────────────────────────────────────────────────────────
    # Context extraction
    # ─────────────────────────────────────────────────────────────────────────

    def _get_context(self, snap: Dict[str, pd.DataFrame], current_price: float) -> dict:
        ctx = {"dominant_bias": "neutral", "bias_strength": "weak", "session_name": "off"}
        try:
            result = self.strategy_manager.run(snap, current_price=current_price)
            c = getattr(result.outlook, "context", None)
            if c:
                ctx["dominant_bias"] = (
                    getattr(c, "dominant_bias", "") or getattr(c, "h4_bias", "neutral") or "neutral"
                )
                ctx["bias_strength"] = getattr(c, "bias_strength", "weak") or "weak"
                ctx["session_name"]  = getattr(c, "session_name", "off") or "off"
        except Exception:
            pass
        return ctx

    # ─────────────────────────────────────────────────────────────────────────
    # Break detection
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_breaks(
        self,
        snap: Dict[str, pd.DataFrame],
        bar_row,
        bar_time: datetime,
        ctx: dict,
        symbol: str,
        seen_keys: set,
        scan_tfs: List[str],
    ) -> List[BreakCandidate]:
        candidates: List[BreakCandidate] = []
        bar_high  = float(bar_row["high"])
        bar_low   = float(bar_row["low"])
        bar_close = float(bar_row["close"])
        bar_open  = float(bar_row.get("open", bar_close))

        try:
            result  = self.strategy_manager.run(snap, current_price=bar_close)
            outlook = result.outlook
        except Exception:
            return candidates

        for tfl in outlook.timeframe_levels:
            tf = tfl.lower_tf
            if tf not in scan_tfs:
                continue

            all_levels = tfl.levels + tfl.recent_levels + tfl.previous_levels
            for level in all_levels:
                lp = level.price
                lt = level.level_type

                # failed_engulf only uses Gap levels (engulfing zones)
                if self.strategy_type == STRATEGY_FAILED_ENGULF and lt != "Gap":
                    continue

                zone_high = getattr(level, "zone_high", lp + LEVEL_TOLERANCE_PIPS * PIP_SIZE)
                zone_low  = getattr(level, "zone_low",  lp - LEVEL_TOLERANCE_PIPS * PIP_SIZE)

                # Determine break direction
                direction: Optional[str] = None
                if lt in ("A", "Gap") and bar_close > zone_high:
                    # Broke ABOVE resistance → BUY break+retest
                    direction = "BUY"
                elif lt in ("V", "Gap") and bar_close < zone_low:
                    # Broke BELOW support → SELL break+retest
                    direction = "SELL"

                if direction is None:
                    continue

                dist_pips = (
                    (bar_close - zone_high) / PIP_SIZE
                    if direction == "BUY"
                    else (zone_low - bar_close) / PIP_SIZE
                )

                body       = abs(bar_close - bar_open)
                rng        = bar_high - bar_low
                body_ratio = body / rng if rng > 0 else 0.0
                body_pips  = body / PIP_SIZE

                # Deduplicate within the same hour to avoid re-firing every M15 bar
                key_hour = bar_time.strftime("%Y%m%d%H")
                ckey = f"{symbol}_{tf}_{direction}_{round(lp, 2)}_{key_hour}"
                if ckey in seen_keys:
                    continue

                # Determine source labels for failed_engulf
                if self.strategy_type == STRATEGY_FAILED_ENGULF:
                    src_strategy = "engulfing_rejection"
                    orig_engulf_dir = "SELL" if direction == "BUY" else "BUY"
                else:
                    src_strategy    = "gap_sweep"
                    orig_engulf_dir = None

                c = BreakCandidate(
                    candidate_key=ckey,
                    strategy_type=self.strategy_type,
                    symbol=symbol,
                    direction=direction,
                    timeframe=tf,
                    break_level=lp,
                    source_level_type=lt,
                    source_strategy_type=src_strategy,
                    zone_high=zone_high,
                    zone_low=zone_low,
                    break_time=bar_time,
                    break_close=bar_close,
                    break_distance_pips=dist_pips,
                    break_body_pips=body_pips,
                    break_body_ratio=body_ratio,
                    dominant_bias=ctx.get("dominant_bias", "neutral"),
                    bias_strength=ctx.get("bias_strength", "weak"),
                    session_name=ctx.get("session_name", "off"),
                    # Failed-engulf extras
                    original_engulf_high=(zone_high if self.strategy_type == STRATEGY_FAILED_ENGULF else None),
                    original_engulf_low=(zone_low if self.strategy_type == STRATEGY_FAILED_ENGULF else None),
                    original_engulf_mid=(lp if self.strategy_type == STRATEGY_FAILED_ENGULF else None),
                    original_engulf_direction=orig_engulf_dir,
                    original_engulf_time=(bar_time if self.strategy_type == STRATEGY_FAILED_ENGULF else None),
                    original_quality_rejection_count=getattr(level, "quality_rejection_count", None),
                    original_structure_break_count=(
                        getattr(level, "structure_break_count", None)
                        if self.strategy_type == STRATEGY_FAILED_ENGULF else None
                    ),
                    original_quality_score=(
                        getattr(level, "quality_score", None)
                        if self.strategy_type == STRATEGY_FAILED_ENGULF else None
                    ),
                )
                candidates.append(c)

        return candidates

    # ─────────────────────────────────────────────────────────────────────────
    # Break validation
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_break_quality(self, c: BreakCandidate) -> Tuple[bool, str]:
        min_dist = MIN_BREAK_DISTANCE_PIPS.get(c.timeframe, 15.0)
        if c.break_distance_pips < min_dist:
            return False, "no_valid_break"
        if c.break_body_ratio < MIN_BODY_RANGE_RATIO:
            return False, "wick_only_break"
        if c.break_body_pips < MIN_BODY_PIPS:
            return False, "no_valid_break"
        return True, ""

    def _bias_strength_ok(self, c: BreakCandidate) -> bool:
        return c.bias_strength in ALLOWED_BIAS_STRENGTHS

    def _bias_direction_aligned(self, c: BreakCandidate) -> bool:
        b = c.dominant_bias.lower()
        if b == "neutral":
            return True
        return (c.direction == "BUY" and b == "bullish") or (c.direction == "SELL" and b == "bearish")

    # ─────────────────────────────────────────────────────────────────────────
    # Retest confirmation scoring
    # ─────────────────────────────────────────────────────────────────────────

    def _evaluate_confirmation(
        self,
        c: BreakCandidate,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bar_open: float,
        ctx: dict,
    ) -> Tuple[str, float]:
        score     = 0.0
        conf_type = "none"
        zone_tol  = RETEST_ZONE_TOLERANCE_PIPS * PIP_SIZE
        body      = abs(bar_close - bar_open)

        if c.direction == "BUY":
            lower_wick = min(bar_open, bar_close) - bar_low
            if body > 0 and lower_wick > body * 1.5 and bar_close > c.break_level:
                score    += CONFIRMATION_WICK_SCORE
                conf_type = "rejection_wick"
            elif bar_close > c.break_level + zone_tol * 0.5:
                score    += CONFIRMATION_CLOSE_SCORE
                conf_type = "close_confirmation"

            momentum_threshold = MIN_BREAK_DISTANCE_PIPS.get(c.timeframe, 15.0) * PIP_SIZE * 0.3
            if bar_close > c.break_level + momentum_threshold:
                score += CONFIRMATION_MOMENTUM_SCORE
                if conf_type == "none":
                    conf_type = "momentum"
        else:
            upper_wick = bar_high - max(bar_open, bar_close)
            if body > 0 and upper_wick > body * 1.5 and bar_close < c.break_level:
                score    += CONFIRMATION_WICK_SCORE
                conf_type = "rejection_wick"
            elif bar_close < c.break_level - zone_tol * 0.5:
                score    += CONFIRMATION_CLOSE_SCORE
                conf_type = "close_confirmation"

            momentum_threshold = MIN_BREAK_DISTANCE_PIPS.get(c.timeframe, 15.0) * PIP_SIZE * 0.3
            if bar_close < c.break_level - momentum_threshold:
                score += CONFIRMATION_MOMENTUM_SCORE
                if conf_type == "none":
                    conf_type = "momentum"

        # Bias alignment bonus
        b = ctx.get("dominant_bias", "neutral").lower()
        if (c.direction == "BUY" and b == "bullish") or (c.direction == "SELL" and b == "bearish"):
            score += CONFIRMATION_BIAS_BONUS

        return conf_type, score

    # ─────────────────────────────────────────────────────────────────────────
    # Trade construction
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_entry_sl(
        self,
        c: BreakCandidate,
        bar_high: float,
        bar_low: float,
        bar_close: float,
    ) -> Tuple[float, float]:
        zone_tol = RETEST_ZONE_TOLERANCE_PIPS * PIP_SIZE
        if c.direction == "BUY":
            entry    = bar_close
            sl       = min(bar_low, c.break_level - zone_tol)
            sl_pips  = (entry - sl) / PIP_SIZE
            if sl_pips < MIN_SL_PIPS:
                sl = entry - MIN_SL_PIPS * PIP_SIZE
            if (entry - sl) / PIP_SIZE > MAX_SL_PIPS:
                return 0.0, 0.0
        else:
            entry    = bar_close
            sl       = max(bar_high, c.break_level + zone_tol)
            sl_pips  = (sl - entry) / PIP_SIZE
            if sl_pips < MIN_SL_PIPS:
                sl = entry + MIN_SL_PIPS * PIP_SIZE
            if (sl - entry) / PIP_SIZE > MAX_SL_PIPS:
                return 0.0, 0.0
        return entry, sl

    def _compute_tps(self, entry: float, sl: float, direction: str) -> List[float]:
        tps = []
        for pip_target in TP_PIPS[:3]:
            if direction == "BUY":
                tps.append(round(entry + pip_target * PIP_SIZE, 3))
            else:
                tps.append(round(entry - pip_target * PIP_SIZE, 3))
        return tps

    # ─────────────────────────────────────────────────────────────────────────
    # Trade state machine
    # ─────────────────────────────────────────────────────────────────────────

    def _update_trade(
        self,
        trade: BreakRetestTrade,
        bar_high: float,
        bar_low: float,
        bar_time: datetime,
    ) -> BreakRetestTrade:
        if trade.closed:
            return trade

        # Excursion tracking
        if trade.direction == "BUY":
            trade.max_favorable_excursion = max(
                trade.max_favorable_excursion, bar_high - trade.entry
            )
            trade.max_adverse_excursion = max(
                trade.max_adverse_excursion, trade.entry - bar_low
            )
        else:
            trade.max_favorable_excursion = max(
                trade.max_favorable_excursion, trade.entry - bar_low
            )
            trade.max_adverse_excursion = max(
                trade.max_adverse_excursion, bar_high - trade.entry
            )

        # SL check
        if self._sl_hit(trade, bar_high, bar_low):
            trade.final_result = self._classify_result(trade)
            trade.final_pips   = self._final_pips(trade)
            trade.reward_score = self._reward_score(trade)
            trade.closed   = True
            trade.closed_at = bar_time
            return trade

        # TP progression
        for i, tp in enumerate([trade.tp1, trade.tp2, trade.tp3]):
            if trade.tp_hit[i]:
                continue
            if self._tp_hit(trade, tp, bar_high, bar_low):
                trade.tp_hit[i]  = True
                trade.tp_progress = sum(trade.tp_hit)
                if i == 0 and not trade.protected_after_tp1:
                    trade.sl_price_current = trade.entry  # move SL to BE
                    trade.protected_after_tp1 = True

        if all(trade.tp_hit):
            trade.final_result = "STRONG_WIN"
            trade.final_pips   = sum(
                abs(tp - trade.entry) / PIP_SIZE
                for tp in [trade.tp1, trade.tp2, trade.tp3]
            ) / 3.0
            trade.reward_score = 1.0
            trade.closed   = True
            trade.closed_at = bar_time

        return trade

    def _sl_hit(
        self, trade: BreakRetestTrade, bar_high: float, bar_low: float
    ) -> bool:
        sl = trade.sl_price_current
        return bar_low <= sl if trade.direction == "BUY" else bar_high >= sl

    def _tp_hit(
        self,
        trade: BreakRetestTrade,
        tp: float,
        bar_high: float,
        bar_low: float,
    ) -> bool:
        return bar_high >= tp if trade.direction == "BUY" else bar_low <= tp

    def _classify_result(self, trade: BreakRetestTrade) -> str:
        tps_hit = sum(trade.tp_hit)
        if tps_hit == 0:
            return "LOSS"
        if tps_hit >= 3:
            return "STRONG_WIN"
        if trade.protected_after_tp1:
            return "WIN" if tps_hit == 2 else "BREAKEVEN_WIN"
        return "PARTIAL_WIN"

    def _final_pips(self, trade: BreakRetestTrade) -> float:
        tps   = [trade.tp1, trade.tp2, trade.tp3]
        hit   = [tp for tp, h in zip(tps, trade.tp_hit) if h]
        if not hit:
            return -((abs(trade.entry - trade.sl)) / PIP_SIZE)
        return sum(abs(tp - trade.entry) for tp in hit) / len(hit) / PIP_SIZE

    def _reward_score(self, trade: BreakRetestTrade) -> float:
        return {
            "STRONG_WIN":    1.00,
            "WIN":           0.65,
            "BREAKEVEN_WIN": 0.15,
            "PARTIAL_WIN":   0.10,
            "LOSS":         -0.80,
        }.get(trade.final_result or "LOSS", 0.0)

    # ─────────────────────────────────────────────────────────────────────────
    # DB payload serialisation
    # ─────────────────────────────────────────────────────────────────────────

    def _trade_to_db_payload(self, trade: BreakRetestTrade, run_id: int) -> dict:
        def _iso(dt: Optional[datetime]) -> Optional[str]:
            return dt.isoformat() if dt else None

        return {
            # Required
            "symbol":       trade.symbol,
            "strategy_type": trade.strategy_type,
            "direction":    trade.direction,
            "entry":        trade.entry,
            "final_result": trade.final_result,
            # Run link
            "run_id":       run_id,
            "source":       trade.strategy_type,
            # Context
            "timeframe":          trade.timeframe,
            "session_name":       trade.session_name,
            "dominant_bias":      trade.dominant_bias,
            "bias_strength":      trade.bias_strength,
            # Break info
            "break_level":            trade.break_level,
            "break_time":             _iso(trade.break_time),
            "break_close":            trade.break_close,
            "break_distance_pips":    trade.break_distance_pips,
            "source_level_type":      trade.source_level_type,
            "source_strategy_type":   trade.source_strategy_type,
            # Retest
            "retest_level":               trade.retest_level,
            "retest_time":                _iso(trade.retest_time),
            "retest_confirmation_type":   trade.retest_confirmation_type,
            "retest_confirmation_score":  trade.retest_confirmation_score,
            "confirmation_score":         trade.retest_confirmation_score,
            "confirmation_path":          trade.retest_confirmation_type,
            # Trade prices
            "sl":  trade.sl,
            "tp1": trade.tp1,
            "tp2": trade.tp2,
            "tp3": trade.tp3,
            # Outcome
            "final_pips":          trade.final_pips,
            "reward_score":        trade.reward_score,
            "activated_at":        _iso(trade.activated_at),
            "closed_at":           _iso(trade.closed_at),
            # Failed-engulf extras
            "original_engulf_high":            trade.original_engulf_high,
            "original_engulf_low":             trade.original_engulf_low,
            "original_engulf_mid":             trade.original_engulf_mid,
            "original_engulf_direction":       trade.original_engulf_direction,
            "original_engulf_time":            _iso(trade.original_engulf_time),
            "original_quality_rejection_count": trade.original_quality_rejection_count,
            "original_structure_break_count":  trade.original_structure_break_count,
            "original_quality_score":          trade.original_quality_score,
            # Quality fields (reuse existing column names)
            "quality_score":             trade.original_quality_score,
            "quality_rejection_count":   trade.original_quality_rejection_count,
            "structure_break_count":     trade.original_structure_break_count,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Result aggregation
    # ─────────────────────────────────────────────────────────────────────────

    def _build_result(
        self,
        run_id: int,
        trades: List[BreakRetestTrade],
        counters: dict,
        reject_reasons: dict,
    ) -> dict:
        activated = [
            t for t in trades
            if t.final_result in ("LOSS", "BREAKEVEN_WIN", "PARTIAL_WIN", "WIN", "STRONG_WIN")
        ]
        total     = len(activated)
        wins      = sum(1 for t in activated if t.final_result != "LOSS")
        losses    = sum(1 for t in activated if t.final_result == "LOSS")
        win_rate  = (wins / total * 100) if total else 0.0
        tp1_hit   = sum(1 for t in activated if t.tp_hit[0]) if activated else 0
        tp2_hit   = sum(1 for t in activated if t.tp_hit[1]) if activated else 0
        tp3_hit   = sum(1 for t in activated if t.tp_hit[2]) if activated else 0
        net_pips  = sum(t.final_pips for t in activated)
        avg_pips  = (net_pips / total) if total else 0.0

        logger.info(
            "%s run %d done: %d trades | WR=%.1f%% | TP1=%.0f%% | Net=%.1f pips",
            self.strategy_type, run_id, total, win_rate,
            (tp1_hit / total * 100) if total else 0.0, net_pips,
        )

        return {
            "run_id":        run_id,
            "strategy_type": self.strategy_type,
            "total_trades":  total,
            "wins":          wins,
            "losses":        losses,
            "win_rate":      round(win_rate, 2),
            "tp1_rate":      round((tp1_hit / total * 100) if total else 0.0, 2),
            "tp2_rate":      round((tp2_hit / total * 100) if total else 0.0, 2),
            "tp3_rate":      round((tp3_hit / total * 100) if total else 0.0, 2),
            "net_pips":      round(net_pips, 2),
            "avg_pips":      round(avg_pips, 2),
            "funnel":        counters,
            "reject_reasons": reject_reasons,
            "by_timeframe":         self._group(activated, "timeframe"),
            "by_session":           self._group(activated, "session_name"),
            "by_bias":              self._group(activated, "dominant_bias"),
            "by_direction":         self._group(activated, "direction"),
            "by_confirmation_type": self._group(activated, "retest_confirmation_type"),
        }

    def _group(self, trades: List[BreakRetestTrade], attr: str) -> dict:
        groups: Dict[str, list] = {}
        for t in trades:
            key = str(getattr(t, attr, "unknown") or "unknown")
            groups.setdefault(key, []).append(t)
        out = {}
        for key, grp in groups.items():
            n    = len(grp)
            w    = sum(1 for t in grp if t.final_result != "LOSS")
            net  = sum(t.final_pips for t in grp)
            out[key] = {
                "trades":   n,
                "wins":     w,
                "losses":   n - w,
                "win_rate": round(w / n * 100, 1) if n else 0.0,
                "net_pips": round(net, 2),
                "avg_pips": round(net / n, 2) if n else 0.0,
            }
        return out

    def _store_result(self, run_id: int, result: dict) -> None:
        """Persist funnel + summary stats rows for evaluate_strategy to read."""
        try:
            self.db.insert_strategy_research_stats({
                "run_id":       run_id,
                "strategy_type": self.strategy_type,
                "symbol":       SYMBOL,
                "stats_key":    "summary",
                "payload": {
                    "total_trades":  result.get("total_trades", 0),
                    "wins":          result.get("wins", 0),
                    "losses":        result.get("losses", 0),
                    "win_rate":      result.get("win_rate", 0.0),
                    "tp1_rate":      result.get("tp1_rate", 0.0),
                    "tp2_rate":      result.get("tp2_rate", 0.0),
                    "tp3_rate":      result.get("tp3_rate", 0.0),
                    "net_pips":      result.get("net_pips", 0.0),
                    "avg_pips":      result.get("avg_pips", 0.0),
                    "funnel_summary":  result.get("funnel", {}),
                    "reject_summary":  result.get("reject_reasons", {}),
                },
            })
        except Exception as exc:
            logger.warning("Stats insert failed: %s", exc)

        # Also patch funnel/reject_summary onto the run row itself for evaluate_strategy
        try:
            self.db.update_strategy_research_run(run_id, {
                "funnel_summary":  json.dumps(result.get("funnel", {})),
                "reject_summary":  json.dumps(result.get("reject_reasons", {})),
            })
        except Exception as exc:
            logger.warning("Run funnel patch failed: %s", exc)
