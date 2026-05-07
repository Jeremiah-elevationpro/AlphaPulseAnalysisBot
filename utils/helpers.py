"""
AlphaPulse - General Helper Utilities
"""

from datetime import datetime, timezone
from typing import Optional
from config.settings import PIP_SIZE


def price_to_pips(price_diff: float) -> float:
    """Convert a raw price difference to pips (XAUUSD: 1 pip = $1.00)."""
    return round(abs(price_diff) / PIP_SIZE, 1)


def pips_to_price(pips: float) -> float:
    """Convert pips to raw price distance (XAUUSD: 30 pips = $30.00)."""
    return round(pips * PIP_SIZE, 5)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_price(price: float) -> str:
    return f"{price:.2f}"


def fmt_pips(pips: float) -> str:
    return f"{pips:.1f} pips"


def candle_body(open_: float, close: float) -> float:
    """Return absolute candle body size."""
    return abs(close - open_)


def upper_wick(open_: float, high: float, close: float) -> float:
    return high - max(open_, close)


def lower_wick(open_: float, low: float, close: float) -> float:
    return min(open_, close) - low


def is_bullish(open_: float, close: float) -> bool:
    return close > open_


def is_bearish(open_: float, close: float) -> bool:
    return close < open_


def wick_ratio(open_: float, high: float, low: float, close: float, side: str) -> float:
    """
    Return wick-to-body ratio for the given side ('upper' or 'lower').
    A ratio > 1 means wick is larger than the body — classic rejection signal.
    """
    body = candle_body(open_, close)
    if body == 0:
        return 0.0
    if side == "upper":
        return upper_wick(open_, high, close) / body
    return lower_wick(open_, low, close) / body


def build_tp_levels(entry: float, sl: float, multipliers: list) -> list:
    """
    Build TP price levels from entry and SL using risk multipliers.
    For SELL: TPs are below entry. For BUY: TPs are above entry.
    """
    risk = abs(entry - sl)
    direction = 1 if entry > sl else -1  # sell → TPs above entry (wrong), let caller handle
    # Actually: BUY → entry > sl → TPs go UP. SELL → entry < sl → TPs go DOWN.
    if entry < sl:
        # SELL
        return [round(entry - risk * m, 2) for m in multipliers]
    else:
        # BUY
        return [round(entry + risk * m, 2) for m in multipliers]


def tp_emoji(index: int, hit: bool) -> str:
    return "✅" if hit else "⏳"


def trade_direction_emoji(direction: str) -> str:
    return "📈" if direction == "BUY" else "📉"
