"""
Market snapshot + market context endpoints.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

import api.state as state
from config.settings import (
    BOT_ACTIVE_END_HOUR,
    BOT_ACTIVE_START_HOUR,
    BOT_TIMEZONE,
    SESSION_ASIA_UTC,
    SESSION_LONDON_UTC,
    SESSION_NEW_YORK_UTC,
)

router = APIRouter()

_cache: dict = {}


def _session_label(utc_dt: datetime) -> str:
    hour = utc_dt.hour
    in_asia = SESSION_ASIA_UTC[0] <= hour < SESSION_ASIA_UTC[1]
    in_london = SESSION_LONDON_UTC[0] <= hour < SESSION_LONDON_UTC[1]
    in_new_york = SESSION_NEW_YORK_UTC[0] <= hour < SESSION_NEW_YORK_UTC[1]
    if in_asia and (in_london or in_new_york):
        return "overlap"
    if in_london and in_new_york:
        return "overlap"
    if in_asia:
        return "asia"
    if in_london:
        return "london"
    if in_new_york:
        return "new_york"
    return "quiet_session"


def _bot_window(utc_dt: datetime) -> tuple[bool, str]:
    # 24/7 mode — always active; return local time for display only
    try:
        tz = ZoneInfo(BOT_TIMEZONE)
    except Exception:
        tz = datetime.now().astimezone().tzinfo or timezone.utc
    local_dt = utc_dt.astimezone(tz)
    return True, local_dt.strftime("%H:%M")


def _mt5_tick(symbol: str) -> dict:
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return {}
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return {}
        bid = float(tick.bid)
        ask = float(tick.ask)
        spread = ask - bid
        return {
            "currentPrice": round((bid + ask) / 2, 2),
            "bid": bid,
            "ask": ask,
            "spread": round(spread, 2),
            "spreadPips": round(spread / 0.01, 1),
            "source": "mt5",
        }
    except Exception:
        return {}


def _heartbeat_context(symbol: str) -> dict:
    hb = state.read_heartbeat() or {}
    return {
        "symbol": hb.get("current_symbol") or symbol,
        "currentPrice": hb.get("current_price"),
        "bid": hb.get("bid"),
        "ask": hb.get("ask"),
        "spread": hb.get("spread"),
        "spreadPips": hb.get("spread_pips"),
        "bias": {
            "d1": hb.get("d1_bias", "neutral"),
            "h4": hb.get("h4_bias", "neutral"),
            "h1": hb.get("h1_bias", "neutral"),
            "dominant": hb.get("dominant_bias", "neutral"),
            "strength": hb.get("bias_strength", "weak"),
        },
        "session": {
            "botWindowActive": True,
            "sessionName": hb.get("session_name") or hb.get("current_session") or "quiet_session",
            "localTime": hb.get("local_time", ""),
            "activeUntil": hb.get("active_until", "24/7"),
            "operatingMode": hb.get("operating_mode", "24_7"),
        },
        "lastMarketUpdateAt": hb.get("last_market_update_at"),
    }


@router.get("/market/context")
def get_market_context(symbol: str = Query(default="XAUUSD")):
    global _cache

    now_utc = datetime.now(timezone.utc)
    session_name = _session_label(now_utc)
    bot_window_active, local_time = _bot_window(now_utc)
    hb_ctx = _heartbeat_context(symbol)
    tick = _mt5_tick(symbol)

    current_price = tick.get("currentPrice", hb_ctx.get("currentPrice"))
    bid = tick.get("bid", hb_ctx.get("bid"))
    ask = tick.get("ask", hb_ctx.get("ask"))
    spread = tick.get("spread", hb_ctx.get("spread"))
    spread_pips = tick.get("spreadPips", hb_ctx.get("spreadPips"))

    response = {
        "success": current_price is not None,
        "symbol": symbol,
        "message": "Live market context loaded" if current_price is not None else "Live market data unavailable",
        "currentPrice": current_price,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "spreadPips": spread_pips,
        "bias": {
            "d1": hb_ctx["bias"]["d1"],
            "h4": hb_ctx["bias"]["h4"],
            "h1": hb_ctx["bias"]["h1"],
            "dominant": hb_ctx["bias"]["dominant"],
            "strength": hb_ctx["bias"]["strength"],
        },
        "session": {
            "botWindowActive": hb_ctx["session"].get("botWindowActive")
            if hb_ctx["session"].get("botWindowActive") is not None else bot_window_active,
            "sessionName": hb_ctx["session"].get("sessionName") or session_name,
            "localTime": hb_ctx["session"].get("localTime") or local_time,
            "activeUntil": hb_ctx["session"].get("activeUntil") or f"{BOT_ACTIVE_END_HOUR:02d}:00",
        },
        "timestamp": now_utc.isoformat(),
        "source": tick.get("source", "heartbeat" if current_price is not None else "unavailable"),
    }

    if response["success"]:
        _cache = response
        return response
    if _cache:
        return {**_cache, "success": True, "message": "Using cached market context", "timestamp": now_utc.isoformat(), "source": "cache"}
    return response


@router.get("/market")
def get_market():
    ctx = get_market_context("XAUUSD")
    return {
        "source": ctx.get("source", "unavailable"),
        "price": ctx.get("currentPrice"),
        "bid": ctx.get("bid"),
        "ask": ctx.get("ask"),
        "spread": ctx.get("spreadPips"),
        "change": None,
        "change_pct": None,
        "high_24h": None,
        "low_24h": None,
        "bias": ctx.get("bias", {}).get("dominant", "neutral"),
        "session": ctx.get("session", {}).get("sessionName", "off_session"),
        "timestamp": ctx.get("timestamp"),
    }
