from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import pandas as pd

from config.settings import (
    BEARISH_ENGULF_BIAS_BONUS,
    DEBUG_ENGULF_TRACE,
    ENGULF_ALLOWED_RESEARCH_TIMEFRAMES,
    ENGULF_BULLISH_DIRECTION_BONUS,
    ENGULF_H1_RELAXED_QUALITY_SCORE,
    ENGULF_MAX_ACTIVE_CANDIDATES_PER_SYMBOL,
    ENGULF_MAX_PER_TIMEFRAME_DIRECTION_SESSION,
    ENGULF_MIN_QUALITY_REJECTIONS,
    ENGULF_MIN_QUALITY_SCORE,
    ENGULF_MODERATE_BIAS_BONUS,
    ENGULF_STRONG_BIAS_BONUS,
    PIP_SIZE,
    REPLAY_WARMUP_DAYS,
    SYMBOL,
    TP_PIPS,
)
from data.mt5_client import MT5Client
from db.database import Database
from strategies.filters import MarketContextEngine, SessionFilter
from strategies.level_detector import LevelDetector, LevelInfo
from utils.logger import get_logger

logger = get_logger(__name__)

_TF_MINUTES = {
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


@dataclass
class ResearchCandidate:
    key: str
    trace_id: int
    level: LevelInfo
    timeframe: str
    detected_at: datetime
    session_name: str
    market_condition: str
    dominant_bias: str
    bias_strength: str
    rejection_count: int
    historical_rejection_count: int
    quality_rejection_count: int
    avg_rejection_wick_ratio: float
    avg_push_away_pips: float
    strongest_rejection_pips: float
    rejection_quality_score: float
    structure_break_count: int
    quality_score: float
    bearish_bias_bonus: int = 0
    structure_break_bonus: int = 0
    quality_rejection_bonus: int = 0
    session_bonus: int = 0
    shortlist_score: float = 0.0
    failed_break_logged: bool = False
    shortlisted: bool = False
    revisited: bool = False
    revisit_time: Optional[datetime] = None
    rejection_confirmed: bool = False
    activated: bool = False
    final_state: str = "pending"
    reject_reason: str = ""


@dataclass
class ResearchTrade:
    key: str
    strategy_type: str
    symbol: str
    direction: str
    timeframe: str
    session_name: str
    market_condition: str
    dominant_bias: str
    bias_strength: str
    engulf_high: float
    engulf_low: float
    engulf_mid: float
    engulf_time: datetime
    historical_rejection_count: int
    quality_rejection_count: int
    avg_rejection_wick_ratio: float
    avg_push_away_pips: float
    strongest_rejection_pips: float
    rejection_quality_score: float
    structure_break_count: int
    quality_score: float
    timeframe_pair: str
    engulf_body_pips: float
    engulf_range_pips: float
    engulf_type: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    activated_at: datetime
    final_result: str = "OPEN"
    final_pips: float = 0.0
    reward_score: float = 0.0
    failure_reason: str = ""
    tp_progress: int = 0
    protected_after_tp1: bool = False
    closed_at: Optional[datetime] = None
    confirmation_path: str = ""
    confirmation_score: float = 0.0
    revisit_time: Optional[datetime] = None
    confirmation_time: Optional[datetime] = None
    confirmation_candles_used: int = 0

    def to_payload(self, run_id: int) -> Dict:
        return {
            "research_run_id": run_id,
            "run_id": run_id,
            "source": "strategy_research",
            "strategy_type": self.strategy_type,
            "symbol": self.symbol,
            "direction": self.direction,
            "timeframe": self.timeframe,
            "timeframe_pair": self.timeframe_pair,
            "session_name": self.session_name,
            "market_condition": self.market_condition,
            "dominant_bias": self.dominant_bias,
            "bias_strength": self.bias_strength,
            "engulf_high": self.engulf_high,
            "engulf_low": self.engulf_low,
            "engulf_mid": self.engulf_mid,
            "engulf_time": self.engulf_time,
            "historical_rejection_count": self.historical_rejection_count,
            "quality_rejection_count": self.quality_rejection_count,
            "avg_rejection_wick_ratio": self.avg_rejection_wick_ratio,
            "avg_push_away_pips": self.avg_push_away_pips,
            "strongest_rejection_pips": self.strongest_rejection_pips,
            "rejection_quality_score": self.rejection_quality_score,
            "structure_break_count": self.structure_break_count,
            "quality_score": self.quality_score,
            "engulf_body_pips": self.engulf_body_pips,
            "engulf_range_pips": self.engulf_range_pips,
            "engulf_type": self.engulf_type,
            "entry": self.entry,
            "sl": self.sl,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "status": self.final_result,
            "final_result": self.final_result,
            "final_pips": self.final_pips,
            "reward_score": self.reward_score,
            "failure_reason": self.failure_reason,
            "activated_at": self.activated_at,
            "closed_at": self.closed_at,
            "completed_at": self.closed_at,
            "confirmation_path": self.confirmation_path,
            "confirmation_score": self.confirmation_score,
            "revisit_time": self.revisit_time,
            "confirmation_time": self.confirmation_time,
            "confirmation_candles_used": self.confirmation_candles_used,
            "level_high": self.engulf_high,
            "level_low": self.engulf_low,
            "level_mid": self.engulf_mid,
            "created_at": self.activated_at,
        }


class EngulfingResearchEngine:
    strategy_type = "engulfing_rejection"

    def __init__(self, *, db: Optional[Database] = None, mt5: Optional[MT5Client] = None):
        self.db = db or Database()
        self.mt5 = mt5 or MT5Client()
        self.level_detector = LevelDetector()
        self.context_engine = MarketContextEngine()
        self.session_filter = SessionFilter()
        self.research_counters: Dict[str, int] = defaultdict(int)
        self.reject_counters: Dict[str, int] = defaultdict(int)
        self.candidate_traces: List[Dict] = []
        self._trace_limit = 20
        self._candidate_seq = 0

    def _reset_diagnostics(self) -> None:
        self.research_counters = defaultdict(int)
        self.reject_counters = defaultdict(int)
        self.candidate_traces = []
        self._candidate_seq = 0

    def _bump(self, key: str, amount: int = 1) -> None:
        self.research_counters[key] += amount

    def _reject(self, reason: str, candidate: Optional[ResearchCandidate] = None) -> None:
        self.reject_counters[reason] += 1
        if candidate is not None:
            candidate.final_state = "rejected"
            candidate.reject_reason = reason

    def _trace_candidate(self, candidate: ResearchCandidate) -> None:
        if candidate.trace_id > self._trace_limit and not DEBUG_ENGULF_TRACE:
            return
        trace = {
            "candidate_id": candidate.trace_id,
            "detected": True,
            "timeframe": candidate.timeframe,
            "direction": candidate.level.trade_direction,
            "quality_score": round(candidate.quality_score, 2),
            "quality_rejections": candidate.quality_rejection_count,
            "bias": candidate.dominant_bias,
            "bias_strength": candidate.bias_strength,
            "shortlisted": candidate.shortlisted,
            "revisited": candidate.revisited,
            "revisit_time": candidate.revisit_time,
            "rejection_confirmed": candidate.rejection_confirmed,
            "activated": candidate.activated,
            "final_state": candidate.final_state,
            "reject_reason": candidate.reject_reason,
        }
        logger.info("ENGULF CANDIDATE TRACE: %s", json.dumps(trace, default=str))
        self.candidate_traces.append(trace)

    def run_last_months(self, months: int, symbol: str = SYMBOL, show_trades: int = 20) -> Dict:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=months * 30)
        return self.run(start=start, end=end, symbol=symbol, show_trades=show_trades)

    def run(self, *, start: datetime, end: datetime, symbol: str = SYMBOL, show_trades: int = 20) -> Dict:
        start = self._as_utc(start)
        end = self._as_utc(end)
        self._reset_diagnostics()
        self.db.init()
        self.mt5.connect()

        run_id = self.db.create_strategy_research_run({
            "strategy_group": "engulfing_rejection",
            "strategy_type": self.strategy_type,
            "symbol": symbol,
            "started_at": datetime.utcnow(),
            "replay_start": start,
            "replay_end": end,
            "status": "running",
            "notes": "Research replay only - engulfing rejection",
        })
        if run_id is None:
            raise RuntimeError("Strategy research run was not created")

        try:
            history = self._load_history(start, end)
            result = self._replay(run_id, history, start, end, symbol=symbol, show_trades=show_trades)
            self.db.update_strategy_research_run(
                run_id,
                {
                    "finished_at": datetime.utcnow(),
                    "status": "completed",
                    "notes": f"Activated {result['activated_trades']} | WR {result['win_rate']:.1f}%",
                    "funnel_summary": result.get("funnel_summary", {}),
                    "reject_summary": result.get("reject_summary", {}),
                },
            )
            self._store_stats(run_id, symbol, result)
            return result
        except Exception as exc:
            self.db.update_strategy_research_run(
                run_id,
                {
                    "finished_at": datetime.utcnow(),
                    "status": "failed",
                    "notes": str(exc)[:1000],
                    "funnel_summary": dict(self.research_counters),
                    "reject_summary": dict(self.reject_counters),
                },
            )
            raise
        finally:
            self.mt5.disconnect()
            self.db.close()

    def _load_history(self, start: datetime, end: datetime) -> Dict[str, pd.DataFrame]:
        warmup_start = start - timedelta(days=REPLAY_WARMUP_DAYS)
        data: Dict[str, pd.DataFrame] = {}
        for timeframe in ("D1", "H4", "H1", "M30", "M15"):
            df = self.mt5.get_ohlcv_range(timeframe, warmup_start, end)
            if df is None or df.empty:
                logger.warning("Engulfing research data missing for %s", timeframe)
                continue
            data[timeframe] = df
        if "M15" not in data:
            raise RuntimeError("No M15 data available for engulfing research replay")
        return data

    def _replay(
        self,
        run_id: int,
        history: Dict[str, pd.DataFrame],
        start: datetime,
        end: datetime,
        *,
        symbol: str,
        show_trades: int,
    ) -> Dict:
        step_df = history["M15"]
        replay_rows = step_df[(step_df["time"] >= start) & (step_df["time"] <= end)]
        candidates: Dict[str, ResearchCandidate] = {}
        active_trades: Dict[str, ResearchTrade] = {}
        closed_trades: List[ResearchTrade] = []
        failed_structures = 0
        total_candidates = 0
        shortlisted_total = 0

        for _, row in replay_rows.iterrows():
            bar_time = self._to_datetime(row["time"]) + timedelta(minutes=15)
            current_price = float(row["close"])
            snapshot = self._snapshot(history, bar_time)
            if not self._snapshot_ready(snapshot):
                continue

            session_label = self.session_filter.get_session(bar_time)
            allowed, local_time, _active_until = self.session_filter.is_bot_window_active(bar_time)
            logger.info(
                "RESEARCH SESSION CHECK: local_time=%s | bot_window=07:00-19:00 | allowed=%s | session_label=%s",
                local_time,
                str(allowed).lower(),
                session_label,
            )
            if not allowed:
                self._bump("session_blocked_if_any")
                self._reject("session_blocked")
                continue

            ctx = self.context_engine.analyze(snapshot, utc_dt=bar_time)
            total_candidates += self._refresh_candidates(
                snapshot=snapshot,
                current_price=current_price,
                bar_time=bar_time,
                ctx=ctx,
                store=candidates,
            )
            shortlisted_keys = self._shortlist_candidates(
                candidates=candidates,
                current_price=current_price,
            )
            shortlisted_total += len(shortlisted_keys)
            failed_structures += self._process_candidates(
                run_id=run_id,
                bar_time=bar_time,
                m15_row=row,
                snapshot=snapshot,
                ctx=ctx,
                candidates=candidates,
                shortlisted_keys=shortlisted_keys,
                active_trades=active_trades,
                closed_trades=closed_trades,
                symbol=symbol,
            )
            self._update_active_trades(
                run_id=run_id,
                bar_time=bar_time,
                row=row,
                active_trades=active_trades,
                closed_trades=closed_trades,
            )

        for trade in list(active_trades.values()):
            trade.closed_at = end
            trade.final_result = self._classify_result(trade)
            if trade.final_result == "OPEN":
                trade.final_result = "BREAKEVEN_WIN" if trade.protected_after_tp1 else "LOSS"
                trade.failure_reason = "replay ended before full completion"
            trade.final_pips = self._final_pips(trade)
            trade.reward_score = self._reward_score(trade.final_result)
            self.db.insert_strategy_research_trade(trade.to_payload(run_id))
            closed_trades.append(trade)
            active_trades.pop(trade.key, None)

        for candidate in list(candidates.values()):
            candidate.final_state = "expired"
            candidate.reject_reason = "expired_before_revisit"
            self._bump("expired_candidates")
            if not candidate.revisited:
                self._reject("no_revisit", candidate)
            self._reject("expired_before_revisit", candidate)
            self._trace_candidate(candidate)

        return self._build_result(
            run_id=run_id,
            symbol=symbol,
            start=start,
            end=end,
            candidates=total_candidates,
            shortlisted_total=shortlisted_total,
            failed_structures=failed_structures,
            trades=closed_trades,
            show_trades=show_trades,
        )

    def _refresh_candidates(
        self,
        *,
        snapshot: Dict[str, pd.DataFrame],
        current_price: float,
        bar_time: datetime,
        ctx,
        store: Dict[str, ResearchCandidate],
    ) -> int:
        raw_candidates: List[ResearchCandidate] = []
        for timeframe in ("H1", "M30", "M15"):
            if timeframe not in ENGULF_ALLOWED_RESEARCH_TIMEFRAMES:
                self._bump("m15_disabled_count" if timeframe == "M15" else "disabled_timeframe_count")
                if self.research_counters.get("logged_disabled_timeframe_" + timeframe, 0) == 0:
                    logger.info("ENGULF REJECTED: %s disabled for research refinement", timeframe)
                    self._bump("logged_disabled_timeframe_" + timeframe)
                continue
            df = snapshot.get(timeframe)
            if df is None or len(df) < 30:
                continue
            levels = self.level_detector._detect_gap_levels(
                df,
                timeframe,
                current_price=current_price,
                h4_bias=getattr(ctx, "h4_bias", "neutral"),
                scope="research",
                min_quality=0.0,
                distance_filter_pips=0.0,
            )
            for level in levels:
                if level.level_type != "Gap":
                    continue
                self._bump("raw_engulf_candles_detected")
                self._bump("engulf_zones_created")
                origin_time = self._to_datetime(df.iloc[level.origin_index]["time"]) if 0 <= level.origin_index < len(df) else bar_time
                key = f"{timeframe}:{level.trade_direction}:{round(level.zone_low,2)}:{round(level.zone_high,2)}:{origin_time.isoformat()}"
                if key in store:
                    self._bump("duplicate_candidates_removed")
                    self._reject("duplicate_zone")
                    continue
                self._candidate_seq += 1
                candidate = ResearchCandidate(
                    key=key,
                    trace_id=self._candidate_seq,
                    level=level,
                    timeframe=timeframe,
                    detected_at=bar_time,
                    session_name=getattr(ctx, "session_name", "off_session"),
                    market_condition="trending" if getattr(ctx, "is_volatile", True) else "ranging",
                    dominant_bias=getattr(ctx, "dominant_bias", "neutral"),
                    bias_strength=getattr(ctx, "bias_strength", "weak"),
                    rejection_count=int(getattr(level, "quality_rejection_count", 0)),
                    historical_rejection_count=int(getattr(level, "historical_rejection_count", 0)),
                    quality_rejection_count=int(getattr(level, "quality_rejection_count", 0)),
                    avg_rejection_wick_ratio=float(getattr(level, "avg_rejection_wick_ratio", 0.0)),
                    avg_push_away_pips=float(getattr(level, "avg_push_away_pips", 0.0)),
                    strongest_rejection_pips=float(getattr(level, "strongest_rejection_pips", 0.0)),
                    rejection_quality_score=float(getattr(level, "rejection_quality_score", 0.0)),
                    structure_break_count=int(getattr(level, "break_count", 0)),
                    quality_score=float(getattr(level, "quality_score", 0.0)),
                )
                if not self._candidate_passes(candidate):
                    self._trace_candidate(candidate)
                    continue
                candidate.bearish_bias_bonus = self._bias_bonus(candidate)
                candidate.structure_break_bonus = self._structure_break_bonus(candidate)
                candidate.quality_rejection_bonus = self._quality_rejection_bonus(candidate)
                self._bump("session_scored")
                candidate.session_bonus = self._session_bonus(candidate)
                candidate.shortlist_score = self._candidate_score(candidate, current_price=current_price)
                raw_candidates.append(candidate)

        selected = self._select_best_candidates(raw_candidates, current_price=current_price)
        logger.info("ENGULF SHORTLIST RELAXED: selected %d from %d candidates", len(selected), len(raw_candidates))
        self._bump("shortlisted_candidates", len(selected))
        not_shortlisted = max(0, len(raw_candidates) - len(selected))
        if not_shortlisted:
            self._reject("not_shortlisted")
            self.reject_counters["not_shortlisted"] += not_shortlisted - 1
        for candidate in selected:
            candidate.shortlisted = True
            store[candidate.key] = candidate
            logger.info(
                "ENGULF SELECTED: timeframe=%s | direction=%s | Q=%.0f | quality_rejections=%d",
                candidate.timeframe,
                candidate.level.trade_direction,
                candidate.quality_score,
                candidate.quality_rejection_count,
            )
        for candidate in raw_candidates:
            if not candidate.shortlisted:
                candidate.final_state = "rejected"
                candidate.reject_reason = "not_shortlisted"
                self._trace_candidate(candidate)
        return len(selected)

    def _candidate_passes(self, candidate: ResearchCandidate) -> bool:
        if candidate.timeframe not in ENGULF_ALLOWED_RESEARCH_TIMEFRAMES:
            self._reject("m15_disabled" if candidate.timeframe == "M15" else "disabled_timeframe", candidate)
            logger.info("ENGULF REJECTED: %s disabled for research refinement", candidate.timeframe)
            return False

        self._bump("historical_rejection_checked")
        if candidate.historical_rejection_count > 0:
            self._bump("historical_rejection_passed")
        else:
            self._reject("no_historical_rejection", candidate)
            return False

        self._bump("quality_rejection_checked")
        bias = (candidate.dominant_bias or "neutral").lower()
        bias_strength = (candidate.bias_strength or "weak").lower()
        direction = (candidate.level.trade_direction or "").upper()
        self._bump("bias_checked")
        if bias in {"mixed", "neutral"}:
            self.research_counters["rejected_weak_bias"] += 1
            self._reject("weak_bias", candidate)
            logger.info("ENGULF REJECTED: weak bias environment | bias=%s", bias)
            return False
        if bias_strength == "weak":
            self._bump("weak_bias_rejected_count")
            self._reject("weak_bias_blocked", candidate)
            logger.info("ENGULF REJECTED: weak bias blocked")
            return False
        if (direction == "BUY" and bias != "bullish") or (direction == "SELL" and bias != "bearish"):
            self._bump("counter_bias_rejected_count")
            self._reject("counter_bias", candidate)
            logger.info("ENGULF REJECTED: direction not aligned with dominant bias")
            return False
        self._bump("bias_passed")

        if candidate.quality_rejection_count < ENGULF_MIN_QUALITY_REJECTIONS:
            self.research_counters["rejected_low_quality_rejections"] += 1
            self._reject("insufficient_quality_rejections", candidate)
            logger.info(
                "ENGULF REJECTED: insufficient quality rejections | quality=%d < %d",
                candidate.quality_rejection_count,
                ENGULF_MIN_QUALITY_REJECTIONS,
            )
            return False
        self._bump("quality_rejection_passed")

        required_quality = ENGULF_MIN_QUALITY_SCORE
        if candidate.timeframe == "H1" and candidate.quality_rejection_count >= 5:
            required_quality = ENGULF_H1_RELAXED_QUALITY_SCORE
        self._bump("quality_score_checked")
        if candidate.quality_score < required_quality:
            self.research_counters["rejected_low_quality_score"] += 1
            self._reject("low_quality_score", candidate)
            logger.info("ENGULF REJECTED: quality below threshold | Q=%.0f < %.0f", candidate.quality_score, required_quality)
            return False

        self._bump("quality_score_passed")
        logger.info("ENGULF QUALITY PASS: Q=%.0f", candidate.quality_score)
        return True

    @staticmethod
    def _structure_break_bonus(candidate: ResearchCandidate) -> int:
        count = candidate.structure_break_count
        if count == 1:
            bonus = 5
        elif count == 2:
            bonus = -4
        elif count >= 3:
            bonus = 5
        else:
            bonus = 1
        logger.info("ENGULF STRUCTURE BREAK SCORE: count=%d | bonus=%d", count, bonus)
        return bonus

    @staticmethod
    def _quality_rejection_bonus(candidate: ResearchCandidate) -> int:
        if candidate.quality_rejection_count >= 8:
            return 3
        if candidate.quality_rejection_count >= 5:
            return 3
        return 2

    @staticmethod
    def _timeframe_priority(timeframe: str) -> int:
        return {"H1": 8, "M30": 6, "M15": 1}.get(timeframe, 0)

    @staticmethod
    def _session_bonus(candidate: ResearchCandidate) -> int:
        session = (candidate.session_name or "off_session").lower()
        bonus = {"asia": 4, "off_session": 2, "london": 0, "new_york": -5}.get(session, 0)
        logger.info("ENGULF SESSION SCORE: session=%s | bonus=%d", session, bonus)
        return bonus

    @staticmethod
    def _bias_bonus(candidate: ResearchCandidate) -> int:
        direction = (candidate.level.trade_direction or "").upper()
        bias = (candidate.dominant_bias or "").lower()
        bias_strength = (candidate.bias_strength or "").lower()
        bonus = 0
        if direction == "SELL" and bias == "bearish":
            bonus = BEARISH_ENGULF_BIAS_BONUS
        elif direction == "BUY" and bias == "bullish":
            bonus = ENGULF_BULLISH_DIRECTION_BONUS
        if bias_strength == "strong":
            bonus += ENGULF_STRONG_BIAS_BONUS
        elif bias_strength == "moderate":
            bonus += ENGULF_MODERATE_BIAS_BONUS
        logger.info("ENGULF DIRECTION SCORE: direction=%s | bonus=%d", direction or "unknown", bonus)
        return bonus

    def _candidate_score(self, candidate: ResearchCandidate, *, current_price: float) -> float:
        distance_pips = abs(candidate.level.price - current_price) / PIP_SIZE
        bias_alignment = 2 if (
            (candidate.dominant_bias == "bearish" and candidate.level.trade_direction == "SELL")
            or (candidate.dominant_bias == "bullish" and candidate.level.trade_direction == "BUY")
        ) else 0
        return (
            candidate.quality_score
            + candidate.bearish_bias_bonus
            + candidate.structure_break_bonus
            + candidate.quality_rejection_bonus
            + candidate.session_bonus
            + bias_alignment
            + self._timeframe_priority(candidate.timeframe)
            - min(distance_pips / 50.0, 8.0)
        )

    @staticmethod
    def _minimum_push_pips(timeframe: str) -> float:
        return {"M15": 12.0, "M30": 18.0, "H1": 25.0}.get(timeframe, 12.0)

    @staticmethod
    def _wick_metrics(candle: pd.Series, direction: str) -> tuple[float, float]:
        high = float(candle["high"])
        low = float(candle["low"])
        open_ = float(candle["open"])
        close = float(candle["close"])
        body = max(abs(close - open_), PIP_SIZE * 0.2)
        range_ = max(high - low, PIP_SIZE * 0.2)
        wick = (high - max(open_, close)) if direction == "SELL" else (min(open_, close) - low)
        wick = max(wick, 0.0)
        return wick / body, (wick / range_) * 100.0

    def _evaluate_revisit_confirmation(
        self,
        *,
        candidate: ResearchCandidate,
        confirm_df: pd.DataFrame,
        current_time: datetime,
    ) -> Dict[str, object]:
        timeframe_minutes = _TF_MINUTES.get(candidate.timeframe, 15)
        deadline = (candidate.revisit_time or current_time) + timedelta(minutes=timeframe_minutes * 3)
        if current_time > deadline:
            return {"status": "expired", "reason": "no_rejection_confirmation"}

        recent = confirm_df.tail(4).reset_index(drop=True)
        if recent.empty:
            return {"status": "pending", "reason": "unknown"}

        level = candidate.level
        direction = level.trade_direction
        touch_indices = [
            i for i, row in recent.iterrows()
            if float(row["high"]) >= level.zone_low and float(row["low"]) <= level.zone_high
        ]
        if not touch_indices:
            return {"status": "pending", "reason": "no_revisit"}

        revisit_idx = touch_indices[0]
        revisit_candle = recent.iloc[revisit_idx]
        follow_up = recent.iloc[revisit_idx + 1:]
        revisit_close = float(revisit_candle["close"])
        revisit_open = float(revisit_candle["open"])
        wick_ratio, wick_percent = self._wick_metrics(revisit_candle, direction)
        bearish_bias = direction == "SELL" and candidate.dominant_bias == "bearish"
        bullish_bias = direction == "BUY" and candidate.dominant_bias == "bullish"
        score = 10 if (bearish_bias or bullish_bias) else 0
        paths: List[str] = []
        reasons: List[str] = []

        invalid_break = False
        if direction == "SELL":
            invalid_break = any(float(row["close"]) > level.zone_high for _, row in recent.iterrows())
        else:
            invalid_break = any(float(row["close"]) < level.zone_low for _, row in recent.iterrows())
        if invalid_break:
            return {"status": "hard_reject", "reason": "hard_zone_break"}

        midpoint_reclaimed = (
            revisit_close < level.price if direction == "SELL" else revisit_close > level.price
        )

        push_min = self._minimum_push_pips(candidate.timeframe)
        if direction == "SELL":
            future_extreme = min([float(revisit_candle["low"])] + [float(row["low"]) for _, row in follow_up.iterrows()])
            push_pips = max(0.0, (level.price - future_extreme) / PIP_SIZE)
            push_ok = push_pips >= push_min
            bearish_close = revisit_close < revisit_open
            wick_valid = wick_ratio >= 1.0 or wick_percent >= 30.0
            sweep_reclaim = float(revisit_candle["high"]) > level.zone_high and revisit_close <= level.zone_high and (
                follow_up.empty or float(follow_up.iloc[-1]["close"]) < revisit_close
            )
            close_reject = revisit_close < level.price and not any(float(row["close"]) > level.zone_high for _, row in follow_up.iterrows())
        else:
            future_extreme = max([float(revisit_candle["high"])] + [float(row["high"]) for _, row in follow_up.iterrows()])
            push_pips = max(0.0, (future_extreme - level.price) / PIP_SIZE)
            push_ok = push_pips >= push_min
            bearish_close = revisit_close > revisit_open
            wick_valid = wick_ratio >= 1.0 or wick_percent >= 30.0
            sweep_reclaim = float(revisit_candle["low"]) < level.zone_low and revisit_close >= level.zone_low and (
                follow_up.empty or float(follow_up.iloc[-1]["close"]) > revisit_close
            )
            close_reject = revisit_close > level.price and not any(float(row["close"]) < level.zone_low for _, row in follow_up.iterrows())

        if wick_valid and midpoint_reclaimed:
            score += 25
            paths.append("wick_rejection")
            self._bump("confirmation_wick_passed")
        else:
            reasons.append("weak_rejection_wick")

        if close_reject:
            score += 20
            paths.append("close_rejection")
            self._bump("confirmation_close_passed")
        else:
            reasons.append("no_midpoint_reclaim")

        if sweep_reclaim:
            score += 30
            paths.append("micro_sweep")
            self._bump("confirmation_sweep_passed")

        if push_ok:
            score += 20
            paths.append("momentum_rejection")
            self._bump("confirmation_momentum_passed")
        else:
            reasons.append("no_pushaway")

        if not bearish_close:
            score -= 20

        if score >= 35:
            path = "combined" if len(paths) > 1 else (paths[0] if paths else "combined")
            return {
                "status": "pass",
                "score": float(score),
                "path": path,
                "revisit_time": candidate.revisit_time,
                "confirmation_time": current_time,
                "confirmation_candles_used": len(recent) - revisit_idx,
                "entry_price": level.price,
                "sl_price": level.zone_high if direction == "SELL" else level.zone_low,
            }

        self._bump("confirmation_score_failed")
        reason = "confirmation_score_too_low"
        if "no_pushaway" in reasons:
            reason = "no_pushaway"
        elif "no_midpoint_reclaim" in reasons:
            reason = "no_midpoint_reclaim"
        elif "weak_rejection_wick" in reasons:
            reason = "weak_rejection_wick"
        return {
            "status": "reject",
            "reason": reason,
            "score": float(score),
            "confirmation_candles_used": len(recent) - revisit_idx,
        }

    def _select_best_candidates(self, candidates: List[ResearchCandidate], *, current_price: float) -> List[ResearchCandidate]:
        per_bucket: Dict[tuple, List[ResearchCandidate]] = defaultdict(list)
        for candidate in candidates:
            bucket = (candidate.timeframe, candidate.level.trade_direction, candidate.session_name)
            per_bucket[bucket].append(candidate)

        shortlisted: List[ResearchCandidate] = []
        for bucket_candidates in per_bucket.values():
            bucket_candidates.sort(
                key=lambda c: (
                    c.shortlist_score,
                    c.quality_score,
                    c.quality_rejection_count,
                    self._timeframe_priority(c.timeframe),
                    -abs(c.level.price - current_price),
                ),
                reverse=True,
            )
            shortlisted.extend(bucket_candidates[:ENGULF_MAX_PER_TIMEFRAME_DIRECTION_SESSION])

        shortlisted.sort(
            key=lambda c: (
                c.shortlist_score,
                c.quality_score,
                c.quality_rejection_count,
                self._timeframe_priority(c.timeframe),
                -abs(c.level.price - current_price),
            ),
            reverse=True,
        )
        final = shortlisted[:ENGULF_MAX_ACTIVE_CANDIDATES_PER_SYMBOL]
        self.research_counters["shortlisted_candidates"] += len(final)
        return final

    def _quality_rejection_bucket(self, count: int) -> str:
        if count <= 4:
            return "3-4"
        if count <= 7:
            return "5-7"
        if count <= 12:
            return "8-12"
        return "13+"

    def _shortlist_candidates(
        self,
        *,
        candidates: Dict[str, ResearchCandidate],
        current_price: float,
    ) -> set[str]:
        selected = self._select_best_candidates(list(candidates.values()), current_price=current_price)
        return {candidate.key for candidate in selected}

    def _process_candidates(
        self,
        *,
        run_id: int,
        bar_time: datetime,
        m15_row: pd.Series,
        snapshot: Dict[str, pd.DataFrame],
        ctx,
        candidates: Dict[str, ResearchCandidate],
        shortlisted_keys: set[str],
        active_trades: Dict[str, ResearchTrade],
        closed_trades: List[ResearchTrade],
        symbol: str,
    ) -> int:
        failed_breaks = 0
        for key, candidate in list(candidates.items()):
            if key not in shortlisted_keys:
                if candidate.activated:
                    logger.warning("ENGULF WARNING: activated trade bypassed shortlist")
                continue
            if key in active_trades:
                continue
            level = candidate.level
            high = float(m15_row["high"])
            low = float(m15_row["low"])
            close = float(m15_row["close"])
            zone_touched = high >= level.zone_low and low <= level.zone_high
            if zone_touched and not candidate.revisited:
                candidate.revisited = True
                candidate.revisit_time = bar_time
                self._bump("zone_revisited")

            if not candidate.revisited:
                continue
            self._bump("revisit_checked")

            if self._zone_broken(level, close):
                if not candidate.failed_break_logged:
                    failed_breaks += 1
                    candidate.failed_break_logged = True
                    candidate.final_state = "failed_break_retest"
                    candidate.reject_reason = "revisit_broke_zone"
                    self._bump("failed_engulf_break_retest_candidates")
                    self._bump("confirmation_hard_invalidated")
                    self._reject("revisit_broke_zone", candidate)
                    self._reject("hard_zone_break", candidate)
                    payload = {
                        "run_id": run_id,
                        "research_run_id": run_id,
                        "source": "strategy_research",
                        "strategy_type": self.strategy_type,
                        "symbol": symbol,
                        "direction": level.trade_direction,
                        "timeframe": candidate.timeframe,
                        "timeframe_pair": candidate.timeframe,
                        "session_name": getattr(ctx, "session_name", candidate.session_name),
                        "market_condition": candidate.market_condition,
                        "dominant_bias": candidate.dominant_bias,
                        "bias_strength": candidate.bias_strength,
                        "engulf_high": level.zone_high,
                        "engulf_low": level.zone_low,
                        "engulf_mid": level.price,
                        "engulf_time": candidate.detected_at,
                        "historical_rejection_count": candidate.historical_rejection_count,
                        "quality_rejection_count": candidate.quality_rejection_count,
                        "avg_rejection_wick_ratio": candidate.avg_rejection_wick_ratio,
                        "avg_push_away_pips": candidate.avg_push_away_pips,
                        "strongest_rejection_pips": candidate.strongest_rejection_pips,
                        "rejection_quality_score": candidate.rejection_quality_score,
                        "structure_break_count": candidate.structure_break_count,
                        "quality_score": float(getattr(level, "quality_score", 0.0)),
                        "engulf_body_pips": round(abs(float(snapshot[candidate.timeframe].iloc[-1]['close']) - float(snapshot[candidate.timeframe].iloc[-1]['open'])) / PIP_SIZE, 1),
                        "engulf_range_pips": round((float(snapshot[candidate.timeframe].iloc[-1]['high']) - float(snapshot[candidate.timeframe].iloc[-1]['low'])) / PIP_SIZE, 1),
                        "engulf_type": "bearish" if level.trade_direction == "SELL" else "bullish",
                        "entry": level.price,
                        "sl": None,
                        "tp1": None,
                        "tp2": None,
                        "tp3": None,
                        "activated_at": None,
                        "closed_at": bar_time,
                        "completed_at": bar_time,
                        "final_result": "potential_failed_engulf_break_retest",
                        "status": "potential_failed_engulf_break_retest",
                        "final_pips": 0.0,
                        "reward_score": 0.0,
                        "failure_reason": "price broke through engulf zone before rejection confirmation",
                        "notes": json.dumps({
                            "original_engulf_id": key,
                            "broken_level_high": level.zone_high,
                            "broken_level_low": level.zone_low,
                            "broken_direction": level.trade_direction,
                            "break_time": bar_time.isoformat(),
                            "break_close_price": close,
                            "retest_pending": True,
                        }, default=str),
                        "created_at": bar_time,
                    }
                    self.db.insert_strategy_research_trade(payload)
                    logger.info("FAILED ENGULF STORED: waiting for break-retest research")
                    self._trace_candidate(candidate)
                candidates.pop(key, None)
                continue

            confirm_df = snapshot.get(candidate.timeframe)
            if confirm_df is None:
                continue
            self._bump("rejection_confirmation_checked")
            decision = self._evaluate_revisit_confirmation(
                candidate=candidate,
                confirm_df=confirm_df,
                current_time=bar_time,
            )
            if decision.get("status") == "pending":
                continue
            if decision.get("status") == "expired":
                self._reject(str(decision.get("reason") or "no_rejection_confirmation"), candidate)
                candidate.final_state = "expired"
                self._trace_candidate(candidate)
                candidates.pop(key, None)
                continue
            if decision.get("status") == "hard_reject":
                self._bump("confirmation_hard_invalidated")
                self._reject(str(decision.get("reason") or "hard_zone_break"), candidate)
                logger.info("ENGULF CONFIRMATION REJECT: score=0 | reason=%s", decision.get("reason"))
                candidate.final_state = "failed_break_retest"
                self._trace_candidate(candidate)
                candidates.pop(key, None)
                continue
            if decision.get("status") != "pass":
                self._reject(str(decision.get("reason") or "confirmation_score_too_low"), candidate)
                logger.info(
                    "ENGULF CONFIRMATION REJECT: score=%s | reason=%s",
                    int(decision.get("score") or 0),
                    decision.get("reason") or "confirmation_score_too_low",
                )
                continue

            candidate.rejection_confirmed = True
            self._bump("rejection_confirmation_passed")

            direction = level.trade_direction
            sign = 1 if direction == "BUY" else -1
            entry_price = float(decision["entry_price"])
            sl_price = float(decision["sl_price"])
            tp1 = round(entry_price + sign * TP_PIPS[0] * PIP_SIZE, 2)
            tp2 = round(entry_price + sign * TP_PIPS[1] * PIP_SIZE, 2)
            tp3 = round(entry_price + sign * TP_PIPS[2] * PIP_SIZE, 2)
            active_trades[key] = ResearchTrade(
                key=key,
                strategy_type=self.strategy_type,
                symbol=symbol,
                direction=direction,
                timeframe=candidate.timeframe,
                session_name=getattr(ctx, "session_name", candidate.session_name),
                market_condition=candidate.market_condition,
                dominant_bias=candidate.dominant_bias,
                bias_strength=candidate.bias_strength,
                engulf_high=level.zone_high,
                engulf_low=level.zone_low,
                engulf_mid=level.price,
                engulf_time=candidate.detected_at,
                historical_rejection_count=candidate.historical_rejection_count,
                quality_rejection_count=candidate.quality_rejection_count,
                avg_rejection_wick_ratio=candidate.avg_rejection_wick_ratio,
                avg_push_away_pips=candidate.avg_push_away_pips,
                strongest_rejection_pips=candidate.strongest_rejection_pips,
                rejection_quality_score=candidate.rejection_quality_score,
                structure_break_count=candidate.structure_break_count,
                quality_score=float(getattr(level, "quality_score", 0.0)),
                timeframe_pair=candidate.timeframe,
                engulf_body_pips=round(abs(float(confirm_df.iloc[-2]["close"]) - float(confirm_df.iloc[-2]["open"])) / PIP_SIZE, 1),
                engulf_range_pips=round((float(confirm_df.iloc[-2]["high"]) - float(confirm_df.iloc[-2]["low"])) / PIP_SIZE, 1),
                engulf_type="bearish" if direction == "SELL" else "bullish",
                entry=entry_price,
                sl=sl_price,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                activated_at=bar_time,
                confirmation_path=str(decision.get("path") or "combined"),
                confirmation_score=float(decision.get("score") or 0.0),
                revisit_time=decision.get("revisit_time"),
                confirmation_time=decision.get("confirmation_time"),
                confirmation_candles_used=int(decision.get("confirmation_candles_used") or 0),
            )
            candidate.activated = True
            candidate.final_state = "activated"
            self._bump("activated_trades")
            self._trace_candidate(candidate)
            logger.info(
                "ENGULF CONFIRMATION PASS: path=%s | score=%d",
                decision.get("path") or "combined",
                int(decision.get("score") or 0),
            )
            candidates.pop(key, None)
            logger.info(
                "ENGULF RESEARCH ACTIVATED: %s %s %s | entry=%.2f sl=%.2f",
                direction,
                symbol,
                candidate.timeframe,
                entry_price,
                sl_price,
            )
        return failed_breaks

    def _update_active_trades(
        self,
        *,
        run_id: int,
        bar_time: datetime,
        row: pd.Series,
        active_trades: Dict[str, ResearchTrade],
        closed_trades: List[ResearchTrade],
    ) -> None:
        high = float(row["high"])
        low = float(row["low"])
        for key, trade in list(active_trades.items()):
            if self._sl_hit(trade, high, low):
                trade.closed_at = bar_time
                trade.final_result = self._classify_result(trade)
                if trade.final_result == "OPEN":
                    trade.final_result = "BREAKEVEN_WIN" if trade.protected_after_tp1 else "LOSS"
                    trade.failure_reason = "stop_loss_hit_after_tp1" if trade.protected_after_tp1 else "stop_loss_hit_before_tp1"
                trade.final_pips = self._final_pips(trade)
                trade.reward_score = self._reward_score(trade.final_result)
                self.db.insert_strategy_research_trade(trade.to_payload(run_id))
                closed_trades.append(trade)
                active_trades.pop(key, None)
                continue

            while trade.tp_progress < 3 and self._tp_hit(trade, high, low, trade.tp_progress + 1):
                trade.tp_progress += 1
                if trade.tp_progress == 1:
                    trade.protected_after_tp1 = True
                    trade.sl = trade.entry
            if trade.tp_progress >= 3:
                trade.closed_at = bar_time
                trade.final_result = "STRONG_WIN"
                trade.final_pips = self._final_pips(trade)
                trade.reward_score = self._reward_score(trade.final_result)
                self.db.insert_strategy_research_trade(trade.to_payload(run_id))
                closed_trades.append(trade)
                active_trades.pop(key, None)

    def _store_stats(self, run_id: int, symbol: str, result: Dict) -> None:
        rows = [
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "summary", "stats_value": result.get("activated_trades", 0), "payload": result, "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "funnel_summary", "stats_value": 0, "payload": result.get("funnel_summary", {}), "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "reject_summary", "stats_value": 0, "payload": result.get("reject_summary", {}), "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "performance_by_timeframe", "stats_value": 0, "payload": result.get("performance_by_timeframe", {}), "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "performance_by_session", "stats_value": 0, "payload": result.get("performance_by_session", {}), "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "performance_by_bias", "stats_value": 0, "payload": result.get("performance_by_bias", {}), "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "performance_by_confirmation_path", "stats_value": 0, "payload": result.get("performance_by_confirmation_path", {}), "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "performance_by_rejection_count", "stats_value": 0, "payload": result.get("performance_by_rejection_count", {}), "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
            {"run_id": run_id, "strategy_type": self.strategy_type, "symbol": symbol, "stats_key": "performance_by_structure_break_count", "stats_value": 0, "payload": result.get("performance_by_structure_break_count", {}), "funnel_summary": result.get("funnel_summary", {}), "reject_summary": result.get("reject_summary", {})},
        ]
        for row in rows:
            self.db.insert_strategy_research_stats(row)

    def _build_result(
        self,
        *,
        run_id: int,
        symbol: str,
        start: datetime,
        end: datetime,
        candidates: int,
        shortlisted_total: int,
        failed_structures: int,
        trades: Iterable[ResearchTrade],
        show_trades: int,
    ) -> Dict:
        trades = [t for t in trades if t.final_result in {"LOSS", "BREAKEVEN_WIN", "WIN", "STRONG_WIN"}]
        wins = sum(1 for t in trades if t.final_result != "LOSS")
        losses = sum(1 for t in trades if t.final_result == "LOSS")
        tp1_hits = sum(1 for t in trades if t.tp_progress >= 1)
        tp2_hits = sum(1 for t in trades if t.tp_progress >= 2)
        tp3_hits = sum(1 for t in trades if t.tp_progress >= 3)
        net_pips = round(sum(t.final_pips for t in trades), 2)
        avg_pips = round(net_pips / len(trades), 2) if trades else 0.0
        avg_confirmation_score_winners = round(sum(t.confirmation_score for t in trades if t.final_result != "LOSS") / max(1, sum(1 for t in trades if t.final_result != "LOSS")), 2)
        avg_confirmation_score_losses = round(sum(t.confirmation_score for t in trades if t.final_result == "LOSS") / max(1, sum(1 for t in trades if t.final_result == "LOSS")), 2)
        confirmation_score_warning = avg_confirmation_score_losses > avg_confirmation_score_winners
        funnel_summary = {
            "raw_engulf_candles_detected": self.research_counters.get("raw_engulf_candles_detected", 0),
            "engulf_zones_created": self.research_counters.get("engulf_zones_created", 0),
            "historical_rejection_checked": self.research_counters.get("historical_rejection_checked", 0),
            "historical_rejection_passed": self.research_counters.get("historical_rejection_passed", 0),
            "quality_rejection_checked": self.research_counters.get("quality_rejection_checked", 0),
            "quality_rejection_passed": self.research_counters.get("quality_rejection_passed", 0),
            "bias_checked": self.research_counters.get("bias_checked", 0),
            "bias_passed": self.research_counters.get("bias_passed", 0),
            "quality_score_checked": self.research_counters.get("quality_score_checked", 0),
            "quality_score_passed": self.research_counters.get("quality_score_passed", 0),
            "shortlisted_candidates": self.research_counters.get("shortlisted_candidates", 0),
            "revisit_checked": self.research_counters.get("revisit_checked", 0),
            "zone_revisited": self.research_counters.get("zone_revisited", 0),
            "rejection_confirmation_checked": self.research_counters.get("rejection_confirmation_checked", 0),
            "rejection_confirmation_passed": self.research_counters.get("rejection_confirmation_passed", 0),
            "confirmation_wick_passed": self.research_counters.get("confirmation_wick_passed", 0),
            "confirmation_close_passed": self.research_counters.get("confirmation_close_passed", 0),
            "confirmation_sweep_passed": self.research_counters.get("confirmation_sweep_passed", 0),
            "confirmation_momentum_passed": self.research_counters.get("confirmation_momentum_passed", 0),
            "confirmation_hard_invalidated": self.research_counters.get("confirmation_hard_invalidated", 0),
            "confirmation_score_failed": self.research_counters.get("confirmation_score_failed", 0),
            "activated_trades": self.research_counters.get("activated_trades", len(trades)),
            "failed_engulf_break_retest_candidates": self.research_counters.get("failed_engulf_break_retest_candidates", failed_structures),
            "expired_candidates": self.research_counters.get("expired_candidates", 0),
            "duplicate_candidates_removed": self.research_counters.get("duplicate_candidates_removed", 0),
            "distance_rejected": self.research_counters.get("distance_rejected", 0),
            "session_scored": self.research_counters.get("session_scored", 0),
            "session_blocked_if_any": self.research_counters.get("session_blocked_if_any", 0),
            "m15_disabled_count": self.research_counters.get("m15_disabled_count", 0),
            "weak_bias_rejected_count": self.research_counters.get("weak_bias_rejected_count", 0),
            "counter_bias_rejected_count": self.research_counters.get("counter_bias_rejected_count", 0),
        }
        reject_summary = {
            "no_historical_rejection": self.reject_counters.get("no_historical_rejection", 0),
            "insufficient_quality_rejections": self.reject_counters.get("insufficient_quality_rejections", 0),
            "weak_bias": self.reject_counters.get("weak_bias", 0),
            "low_quality_score": self.reject_counters.get("low_quality_score", 0),
            "duplicate_zone": self.reject_counters.get("duplicate_zone", 0),
            "not_shortlisted": self.reject_counters.get("not_shortlisted", 0),
            "no_revisit": self.reject_counters.get("no_revisit", 0),
            "revisit_broke_zone": self.reject_counters.get("revisit_broke_zone", 0),
            "no_rejection_confirmation": self.reject_counters.get("no_rejection_confirmation", 0),
            "expired_before_revisit": self.reject_counters.get("expired_before_revisit", 0),
            "distance_too_far": self.reject_counters.get("distance_too_far", 0),
            "session_blocked": self.reject_counters.get("session_blocked", 0),
            "no_pushaway": self.reject_counters.get("no_pushaway", 0),
            "no_midpoint_reclaim": self.reject_counters.get("no_midpoint_reclaim", 0),
            "weak_rejection_wick": self.reject_counters.get("weak_rejection_wick", 0),
            "hard_zone_break": self.reject_counters.get("hard_zone_break", 0),
            "confirmation_score_too_low": self.reject_counters.get("confirmation_score_too_low", 0),
            "unknown": self.reject_counters.get("unknown", 0),
        }

        logger.info("ENGULF RESEARCH FUNNEL:")
        for key, value in funnel_summary.items():
            logger.info("%s=%s", key, value)
        logger.info("ENGULF REJECT REASONS: %s", json.dumps(reject_summary, default=str))
        if confirmation_score_warning:
            logger.warning("ENGULF WARNING: confirmation score not predictive yet")

        return {
            "run_id": run_id,
            "strategy": self.strategy_type,
            "symbol": symbol,
            "period": f"{start.date()} -> {end.date()}",
            "candidates": candidates,
            "shortlisted_candidates": funnel_summary["shortlisted_candidates"],
            "failed_engulf_break_retests": failed_structures,
            "rejected_due_to_weak_bias": self.research_counters.get("rejected_weak_bias", 0),
            "rejected_due_to_low_quality_rejections": self.research_counters.get("rejected_low_quality_rejections", 0),
            "rejected_due_to_low_quality_score": self.research_counters.get("rejected_low_quality_score", 0),
            "activated_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / len(trades)) * 100, 1) if trades else 0.0,
            "tp1_rate": round((tp1_hits / len(trades)) * 100, 1) if trades else 0.0,
            "tp2_rate": round((tp2_hits / len(trades)) * 100, 1) if trades else 0.0,
            "tp3_rate": round((tp3_hits / len(trades)) * 100, 1) if trades else 0.0,
            "net_pips": net_pips,
            "avg_pips_per_trade": avg_pips,
            "performance_by_timeframe": self._group(trades, "timeframe"),
            "performance_by_session": self._group(trades, "session_name"),
            "performance_by_bias": self._group(trades, "dominant_bias"),
            "performance_by_bias_strength": self._group(trades, "bias_strength"),
            "performance_by_direction": self._group(trades, "direction"),
            "performance_by_bias_alignment": self._group_bias_alignment(trades),
            "performance_by_confirmation_path": self._group(trades, "confirmation_path"),
            "performance_by_rejection_count": self._group(trades, "historical_rejection_count"),
            "performance_by_quality_rejection_bucket": self._group_buckets(trades),
            "performance_by_structure_break_count": self._group(trades, "structure_break_count"),
            "avg_confirmation_score_winners": avg_confirmation_score_winners,
            "avg_confirmation_score_losses": avg_confirmation_score_losses,
            "confirmation_score_warning": confirmation_score_warning,
            "funnel_summary": funnel_summary,
            "reject_summary": reject_summary,
            "candidate_trace_sample": self.candidate_traces[:self._trace_limit] if not DEBUG_ENGULF_TRACE else self.candidate_traces,
            "sample_trades": [t.to_payload(run_id) for t in trades[:show_trades]],
        }

    @staticmethod
    def _group(trades: Iterable[ResearchTrade], attr: str) -> Dict:
        grouped = defaultdict(lambda: {"activated": 0, "wins": 0, "losses": 0, "net_pips": 0.0})
        for trade in trades:
            key = getattr(trade, attr, "unknown")
            bucket = grouped[str(key)]
            bucket["activated"] += 1
            bucket["wins"] += 1 if trade.final_result != "LOSS" else 0
            bucket["losses"] += 1 if trade.final_result == "LOSS" else 0
            bucket["net_pips"] += trade.final_pips
        for bucket in grouped.values():
            bucket["win_rate"] = round((bucket["wins"] / bucket["activated"]) * 100, 1) if bucket["activated"] else 0.0
            bucket["net_pips"] = round(bucket["net_pips"], 2)
            bucket["avg_pips"] = round(bucket["net_pips"] / bucket["activated"], 2) if bucket["activated"] else 0.0
        return dict(grouped)

    def _group_buckets(self, trades: Iterable[ResearchTrade]) -> Dict:
        grouped = defaultdict(lambda: {"activated": 0, "wins": 0, "losses": 0, "net_pips": 0.0})
        for trade in trades:
            key = self._quality_rejection_bucket(trade.quality_rejection_count)
            bucket = grouped[key]
            bucket["activated"] += 1
            bucket["wins"] += 1 if trade.final_result != "LOSS" else 0
            bucket["losses"] += 1 if trade.final_result == "LOSS" else 0
            bucket["net_pips"] += trade.final_pips
        for bucket in grouped.values():
            bucket["win_rate"] = round((bucket["wins"] / bucket["activated"]) * 100, 1) if bucket["activated"] else 0.0
            bucket["net_pips"] = round(bucket["net_pips"], 2)
            bucket["avg_pips"] = round(bucket["net_pips"] / bucket["activated"], 2) if bucket["activated"] else 0.0
        return dict(grouped)

    @staticmethod
    def _group_bias_alignment(trades: Iterable[ResearchTrade]) -> Dict:
        grouped = defaultdict(lambda: {"activated": 0, "wins": 0, "losses": 0, "net_pips": 0.0})
        for trade in trades:
            aligned = (
                (trade.direction == "BUY" and trade.dominant_bias == "bullish")
                or (trade.direction == "SELL" and trade.dominant_bias == "bearish")
            )
            key = "aligned" if aligned else "counter_bias"
            bucket = grouped[key]
            bucket["activated"] += 1
            bucket["wins"] += 1 if trade.final_result != "LOSS" else 0
            bucket["losses"] += 1 if trade.final_result == "LOSS" else 0
            bucket["net_pips"] += trade.final_pips
        for bucket in grouped.values():
            bucket["win_rate"] = round((bucket["wins"] / bucket["activated"]) * 100, 1) if bucket["activated"] else 0.0
            bucket["net_pips"] = round(bucket["net_pips"], 2)
            bucket["avg_pips"] = round(bucket["net_pips"] / bucket["activated"], 2) if bucket["activated"] else 0.0
        return dict(grouped)

    @staticmethod
    def _zone_broken(level: LevelInfo, close: float) -> bool:
        if level.trade_direction == "SELL":
            return close > level.zone_high
        return close < level.zone_low

    @staticmethod
    def _sl_hit(trade: ResearchTrade, high: float, low: float) -> bool:
        if trade.direction == "SELL":
            return high >= trade.sl
        return low <= trade.sl

    @staticmethod
    def _tp_hit(trade: ResearchTrade, high: float, low: float, tp_index: int) -> bool:
        target = [trade.tp1, trade.tp2, trade.tp3][tp_index - 1]
        if trade.direction == "SELL":
            return low <= target
        return high >= target

    @staticmethod
    def _classify_result(trade: ResearchTrade) -> str:
        if trade.tp_progress <= 0:
            return "OPEN"
        if trade.tp_progress == 1:
            return "BREAKEVEN_WIN"
        if trade.tp_progress == 2:
            return "WIN"
        return "STRONG_WIN"

    @staticmethod
    def _final_pips(trade: ResearchTrade) -> float:
        if trade.tp_progress >= 3:
            return abs(trade.tp3 - trade.entry) / PIP_SIZE
        if trade.tp_progress == 2:
            return abs(trade.tp2 - trade.entry) / PIP_SIZE
        if trade.tp_progress == 1:
            return abs(trade.tp1 - trade.entry) / PIP_SIZE
        if trade.final_result == "LOSS":
            return -abs(trade.entry - trade.sl) / PIP_SIZE
        return 0.0

    @staticmethod
    def _reward_score(final_result: str) -> float:
        return {
            "LOSS": -3.0,
            "BREAKEVEN_WIN": 1.0,
            "WIN": 2.0,
            "STRONG_WIN": 3.5,
        }.get(final_result, 0.0)

    @staticmethod
    def _snapshot(history: Dict[str, pd.DataFrame], current_time: datetime) -> Dict[str, pd.DataFrame]:
        snapshot = {}
        for timeframe, df in history.items():
            minutes = _TF_MINUTES.get(timeframe, 60)
            close_time = df["time"] + pd.to_timedelta(minutes, unit="m")
            visible = df[close_time <= current_time]
            if not visible.empty:
                snapshot[timeframe] = visible.reset_index(drop=True)
        return snapshot

    @staticmethod
    def _snapshot_ready(snapshot: Dict[str, pd.DataFrame]) -> bool:
        minimums = {"D1": 30, "H4": 50, "H1": 60, "M30": 80, "M15": 80}
        return all(tf in snapshot and len(snapshot[tf]) >= minimums[tf] for tf in minimums)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _to_datetime(value) -> datetime:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.to_pydatetime()
