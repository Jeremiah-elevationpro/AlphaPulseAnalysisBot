"""
AlphaPulse REST API
====================
FastAPI bridge between the React frontend and the Python trading bot.

Run with:
    uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import api.state as state
from api.routes import health, trades, signals, analytics, market, setups, alerts, bot, replay, logs
from utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────
    try:
        state.db.init()
        state.db_ready = True
        logger.info("AlphaPulse API: database connected")
    except Exception as exc:
        logger.warning("AlphaPulse API: database not available — %s", exc)
        state.db_ready = False
    yield
    # ── Shutdown ─────────────────────────────────────────────
    try:
        state.db.close()
    except Exception:
        pass


app = FastAPI(
    title="AlphaPulse API",
    description="REST bridge for the AlphaPulse XAUUSD analysis bot",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS (allow the React dev server and production build) ───────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────
app.include_router(health.router,    prefix="/api", tags=["Status"])
app.include_router(trades.router,    prefix="/api", tags=["Trades"])
app.include_router(signals.router,   prefix="/api", tags=["Signals"])
app.include_router(analytics.router, prefix="/api", tags=["Analytics"])
app.include_router(market.router,    prefix="/api", tags=["Market"])
app.include_router(setups.router,    prefix="/api", tags=["Manual Setups"])
app.include_router(alerts.router,    prefix="/api", tags=["Alerts"])
app.include_router(bot.router,       prefix="/api", tags=["Bot"])
app.include_router(replay.router,    prefix="/api", tags=["Replay"])
app.include_router(logs.router,      prefix="/api", tags=["Logs"])
