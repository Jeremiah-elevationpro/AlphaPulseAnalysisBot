from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config.settings import PIP_SIZE
from db.database import Database
from utils.logger import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Timeframe normalization
# ─────────────────────────────────────────────────────────────────────────────

_TF_ALIAS: Dict[str, str] = {
    "h1": "H1", "1h": "H1", "60m": "H1", "60": "H1",
    "h4": "H4", "4h": "H4", "240m": "H4",
    "m30": "M30", "30m": "M30", "30": "M30",
    "m15": "M15", "15m": "M15", "15": "M15",
    "m5":  "M5",  "5m":  "M5",
}


def _normalize_timeframe(raw: Optional[str], fallbacks: List[Optional[str]] = None) -> str:
    """Return canonical timeframe (H1/M30/M15/…) from any raw string, or '' if unknown."""
    sources = [raw] + (fallbacks or [])
    for val in sources:
        if not val:
            continue
        val = str(val).strip()
        # Already canonical
        if val in ("H1", "H4", "M30", "M15", "M5", "D1"):
            return val
        # Pair format: take first leg — "M30->M15" -> "M30", "H1->M30" -> "H1"
        first_leg = val.split("->")[0].split("-")[0].split("/")[0].strip()
        key = first_leg.lower()
        if key in _TF_ALIAS:
            normalized = _TF_ALIAS[key]
            if normalized != val:
                logger.info("FAILED ENGULF TIMEFRAME NORMALIZED: raw=%s normalized=%s", val, normalized)
            return normalized
    return ""


@dataclass
class FailedEngulfCandidateRecord:
    original_engulf_high: float
    original_engulf_low: float
    original_engulf_mid: float
    original_engulf_direction: str
    failed_direction: str
    continuation_direction: str
    failed_at: datetime
    break_level: float
    break_time: datetime
    break_close: float
    break_distance_pips: float
    timeframe: str
    session_name: str
    dominant_bias: str
    bias_strength: str
    quality_rejection_count: int
    structure_break_count: int
    quality_score: float
    reason_failed: str
    symbol: str
    source_strategy_type: str = "engulfing_rejection"


def get_failed_engulf_candidates(
    db: Database,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    source_strategy: str = "engulfing_rejection",
) -> List[FailedEngulfCandidateRecord]:
    rows = db.get_strategy_research_trade_rows(
        strategy_type=source_strategy,
        final_result="potential_failed_engulf_break_retest",
        symbol=symbol,
        limit=20000,
    )
    out: List[FailedEngulfCandidateRecord] = []
    start = _as_utc(start)
    end = _as_utc(end)
    for row in rows:
        notes = _coerce_notes(row.get("notes"))
        break_time = _parse_dt(notes.get("break_time")) or _parse_dt(row.get("closed_at"))
        if break_time is None or break_time < start or break_time > end:
            continue
        original_direction = str(
            row.get("direction")
            or notes.get("broken_direction")
            or row.get("original_engulf_direction")
            or ""
        ).upper()
        continuation_direction = "SELL" if original_direction == "BUY" else "BUY"
        engulf_high = _to_float(
            row.get("original_engulf_high"),
            row.get("engulf_high"),
            row.get("level_high"),
            notes.get("broken_level_high"),
        )
        engulf_low = _to_float(
            row.get("original_engulf_low"),
            row.get("engulf_low"),
            row.get("level_low"),
            notes.get("broken_level_low"),
        )
        engulf_mid = _to_float(
            row.get("original_engulf_mid"),
            row.get("engulf_mid"),
            row.get("level_mid"),
            row.get("entry"),
        )
        break_close = _to_float(notes.get("break_close_price"), row.get("break_close"), row.get("entry"))
        if engulf_high is None or engulf_low is None or engulf_mid is None or break_close is None:
            continue
        # Normalize timeframe — try primary field then common fallbacks
        tf_raw = (
            row.get("timeframe")
            or row.get("source_timeframe")
            or row.get("engulf_timeframe")
            or notes.get("timeframe")
            or notes.get("timeframe_pair")
            or ""
        )
        timeframe = _normalize_timeframe(
            str(tf_raw),
            fallbacks=[
                str(row.get("timeframe_pair") or ""),
                str(notes.get("confirmation_timeframe") or ""),
            ],
        )
        break_level = engulf_low if continuation_direction == "SELL" else engulf_high
        break_distance_pips = (
            (break_level - break_close) / PIP_SIZE
            if continuation_direction == "SELL"
            else (break_close - break_level) / PIP_SIZE
        )
        out.append(
            FailedEngulfCandidateRecord(
                original_engulf_high=engulf_high,
                original_engulf_low=engulf_low,
                original_engulf_mid=engulf_mid,
                original_engulf_direction=original_direction,
                failed_direction=original_direction,
                continuation_direction=continuation_direction,
                failed_at=break_time,
                break_level=break_level,
                break_time=break_time,
                break_close=break_close,
                break_distance_pips=round(max(0.0, break_distance_pips), 2),
                timeframe=timeframe,
                session_name=str(row.get("session_name") or "off_session"),
                dominant_bias=str(row.get("dominant_bias") or "neutral"),
                bias_strength=str(row.get("bias_strength") or "weak"),
                quality_rejection_count=int(row.get("quality_rejection_count") or 0),
                structure_break_count=int(row.get("structure_break_count") or 0),
                quality_score=float(row.get("quality_score") or 0.0),
                reason_failed=str(
                    row.get("failure_reason")
                    or notes.get("reason_failed")
                    or "price broke through engulf zone before rejection confirmation"
                ),
                symbol=str(row.get("symbol") or symbol),
            )
        )
    logger.info("FAILED ENGULF ADAPTER: loaded %d candidate(s) from %s", len(out), source_strategy)
    return out


def _coerce_notes(value: object) -> Dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_dt(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str) and value:
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except Exception:
            return None
    return None


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_float(*values: object) -> Optional[float]:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None
