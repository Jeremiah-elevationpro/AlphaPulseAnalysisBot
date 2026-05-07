"""
AlphaPulse - MetaTrader 5 Client
Handles connection, symbol selection, reconnection, and OHLCV data fetching.

Key behaviour:
  - After login, auto-selects XAUUSD into Market Watch via symbol_select()
  - If the exact symbol name fails, scans available symbols for the gold pair
  - DEMO MODE activates when MT5 package is absent OR terminal is unreachable
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Optional, List
import pandas as pd
from utils.logger import get_logger
from config.settings import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH,
    SYMBOL, TF_MAP, CANDLE_COUNT,
)

logger = get_logger(__name__)

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package not installed — running in DEMO mode.")

# Candidate symbol names for gold across different brokers
_GOLD_SYMBOL_CANDIDATES = [
    "XAUUSD",
    "XAUUSDm",
    "XAUUSD.",
    "XAUUSD+",
    "Gold",
    "GOLD",
    "XAU/USD",
]


class MT5Client:
    """
    Manages the MT5 terminal connection and exposes a clean
    `get_ohlcv(timeframe, count)` interface that returns a DataFrame.

    DEMO MODE activates automatically when:
      - The MetaTrader5 package is not installed, OR
      - MT5 terminal is not running / connection fails
    In demo mode all data requests return realistic synthetic XAUUSD data.
    """

    def __init__(self):
        self._connected = False
        self._demo_mode = not MT5_AVAILABLE
        self._symbol: str = SYMBOL     # resolved symbol name (may differ from config)

    # ──────────────────────────────────────────────
    # CONNECTION
    # ──────────────────────────────────────────────

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            logger.info("DEMO MODE: MetaTrader5 package not installed — using synthetic data.")
            self._connected = True
            self._demo_mode = True
            return True

        kwargs = {}
        if MT5_PATH:
            kwargs["path"] = MT5_PATH

        if not mt5.initialize(**kwargs):
            logger.warning(
                "MT5 terminal not available (%s) — switching to DEMO MODE with synthetic data.",
                mt5.last_error(),
            )
            self._connected = True
            self._demo_mode = True
            return True

        if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
            result = mt5.login(
                login=MT5_LOGIN,
                password=MT5_PASSWORD,
                server=MT5_SERVER,
            )
            if not result:
                logger.error("MT5 login failed: %s", mt5.last_error())
                mt5.shutdown()
                return False

        info = mt5.terminal_info()
        logger.info(
            "MT5 connected — build %s, broker: %s",
            info.build if info else "?",
            MT5_SERVER or "default",
        )

        # Enable symbol in Market Watch and resolve correct name
        resolved = self._setup_symbol()
        if resolved:
            self._symbol = resolved
            logger.info("Symbol ready: %s", self._symbol)
        else:
            logger.warning(
                "Could not enable %s in Market Watch — switching to DEMO MODE.", SYMBOL
            )
            self._demo_mode = True

        self._connected = True
        return True

    def disconnect(self):
        if MT5_AVAILABLE and self._connected and not self._demo_mode:
            mt5.shutdown()
            self._connected = False
            logger.info("MT5 disconnected.")

    def ensure_connected(self) -> bool:
        if self._connected:
            return True
        logger.warning("MT5 not connected — attempting reconnect...")
        return self.connect()

    # ──────────────────────────────────────────────
    # SYMBOL SETUP
    # ──────────────────────────────────────────────

    def _setup_symbol(self) -> Optional[str]:
        """
        Enable the gold symbol in Market Watch and return the resolved name.

        MT5 returns 'Call failed' when a symbol is not in Market Watch.
        symbol_select(name, True) subscribes it; we then wait briefly
        for the terminal to fetch its data feed.

        Tries the configured SYMBOL first, then common broker variants.
        Returns the working symbol name, or None if none work.
        """
        candidates = [SYMBOL] + [s for s in _GOLD_SYMBOL_CANDIDATES if s != SYMBOL]

        for name in candidates:
            info = mt5.symbol_info(name)
            if info is None:
                continue  # symbol doesn't exist on this broker

            # Enable in Market Watch
            if not info.visible:
                if not mt5.symbol_select(name, True):
                    logger.debug("symbol_select(%s) failed: %s", name, mt5.last_error())
                    continue
                # Give MT5 a moment to subscribe the feed
                time.sleep(0.5)

            # Verify data is now available
            bars = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M15, 0, 5)
            if bars is not None and len(bars) > 0:
                logger.info("Symbol '%s' confirmed available (%d bars test-fetched).",
                            name, len(bars))
                return name
            else:
                logger.debug("Symbol '%s' selected but no data yet: %s",
                             name, mt5.last_error())

        # Last resort: scan all broker symbols for gold
        return self._scan_for_gold_symbol()

    def _scan_for_gold_symbol(self) -> Optional[str]:
        """
        Iterate every symbol on the broker and return the first one
        that contains 'XAU' or 'GOLD' and returns live data.
        """
        all_symbols = mt5.symbols_get()
        if not all_symbols:
            return None

        for sym in all_symbols:
            name = sym.name.upper()
            if "XAU" in name or "GOLD" in name:
                mt5.symbol_select(sym.name, True)
                time.sleep(0.3)
                bars = mt5.copy_rates_from_pos(sym.name, mt5.TIMEFRAME_M15, 0, 3)
                if bars is not None and len(bars) > 0:
                    logger.info("Auto-detected gold symbol: '%s'", sym.name)
                    return sym.name

        logger.error("No working gold symbol found on this broker.")
        return None

    # ──────────────────────────────────────────────
    # DATA FETCHING
    # ──────────────────────────────────────────────

    def get_ohlcv(self, timeframe: str, count: Optional[int] = None) -> pd.DataFrame:
        """
        Fetch OHLCV data for the gold symbol.

        Returns a DataFrame with columns:
            time, open, high, low, close, tick_volume
        Sorted oldest → newest (ascending time).

        Falls back to synthetic data automatically when in demo mode.
        """
        count = count or CANDLE_COUNT.get(timeframe, 300)

        if not self.ensure_connected():
            raise ConnectionError("MT5 is not connected.")

        if self._demo_mode:
            return self._synthetic_ohlcv(timeframe, count)

        tf_int = self._resolve_tf(timeframe)

        # Retry once — first call sometimes fails immediately after symbol_select
        for attempt in range(2):
            bars = mt5.copy_rates_from_pos(self._symbol, tf_int, 0, count)
            if bars is not None and len(bars) > 0:
                break
            if attempt == 0:
                logger.debug("[%s] Retrying data fetch after 1s...", timeframe)
                time.sleep(1.0)
        else:
            logger.error("No data for %s %s: %s", self._symbol, timeframe, mt5.last_error())
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        df.sort_values("time", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_ohlcv_range(
        self,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV between two UTC datetimes.

        Used by historical replay so the live scan path can stay unchanged.
        """
        if not self.ensure_connected():
            raise ConnectionError("MT5 is not connected.")

        start = self._as_utc(start)
        end = self._as_utc(end)
        if end <= start:
            return pd.DataFrame()

        if self._demo_mode:
            minutes = self._tf_minutes(timeframe)
            count = max(2, int((end - start).total_seconds() // (minutes * 60)))
            df = self._synthetic_ohlcv(timeframe, count)
            df["time"] = [
                start + timedelta(minutes=minutes * i)
                for i in range(len(df))
            ]
            return df[df["time"] <= end].reset_index(drop=True)

        tf_int = self._resolve_tf(timeframe)
        bars = mt5.copy_rates_range(self._symbol, tf_int, start, end)
        if bars is None or len(bars) == 0:
            logger.error(
                "No historical data for %s %s %s-%s: %s",
                self._symbol, timeframe, start, end, mt5.last_error(),
            )
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        df.sort_values("time", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_current_price(self) -> Optional[float]:
        """Return the latest bid price. Returns synthetic price in demo mode."""
        if self._demo_mode:
            df = self._synthetic_ohlcv("M15", 2)
            return float(df.iloc[-1]["close"]) if len(df) > 0 else None
        if not self.ensure_connected():
            return None
        tick = mt5.symbol_info_tick(self._symbol)
        return tick.bid if tick else None

    def get_tick(self) -> Optional[dict]:
        """Return live tick context for the active symbol."""
        if self._demo_mode:
            price = self.get_current_price()
            if price is None:
                return None
            bid = float(price)
            ask = float(price + 0.08)
            spread = ask - bid
            return {
                "bid": bid,
                "ask": ask,
                "mid": round((bid + ask) / 2, 2),
                "spread": round(spread, 2),
                "spread_pips": round(spread / 0.01, 1),
            }
        if not self.ensure_connected():
            return None
        tick = mt5.symbol_info_tick(self._symbol)
        if not tick:
            return None
        bid = float(tick.bid)
        ask = float(tick.ask)
        spread = ask - bid
        return {
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2, 2),
            "spread": round(spread, 2),
            "spread_pips": round(spread / 0.01, 1),
        }

    # ──────────────────────────────────────────────
    # INTERNAL HELPERS
    # ──────────────────────────────────────────────

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _tf_minutes(timeframe: str) -> int:
        return {
            "M1": 1,
            "M5": 5,
            "M15": 15,
            "M30": 30,
            "H1": 60,
            "H4": 240,
            "D1": 1440,
        }.get(timeframe, 60)

    @staticmethod
    def _resolve_tf(timeframe: str) -> int:
        """Map string like 'H1' to MT5 timeframe constant."""
        if not MT5_AVAILABLE:
            return TF_MAP.get(timeframe, 60)

        mapping = {
            "M1":  mt5.TIMEFRAME_M1,
            "M5":  mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1":  mt5.TIMEFRAME_H1,
            "H4":  mt5.TIMEFRAME_H4,
            "D1":  mt5.TIMEFRAME_D1,
        }
        if timeframe not in mapping:
            raise ValueError(f"Unknown timeframe: {timeframe}")
        return mapping[timeframe]

    @staticmethod
    def _synthetic_ohlcv(timeframe: str, count: int) -> pd.DataFrame:
        """
        Generate realistic synthetic XAUUSD data for testing without MT5.
        Uses a seeded random walk starting near 3200.
        """
        import numpy as np
        from datetime import datetime, timedelta, timezone

        rng = {"M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
        minutes_per_bar = rng.get(timeframe, 60)

        np.random.seed(42)
        base_price = 3200.0
        prices = [base_price]
        for _ in range(count - 1):
            change = np.random.normal(0, 0.5)
            prices.append(round(prices[-1] + change, 2))

        now = datetime.now(timezone.utc)
        times = [
            now - timedelta(minutes=minutes_per_bar * (count - i))
            for i in range(count)
        ]

        rows = []
        for i, (t, c) in enumerate(zip(times, prices)):
            o = prices[i - 1] if i > 0 else c
            noise = abs(np.random.normal(0, 0.3))
            h = max(o, c) + noise
            l = min(o, c) - noise
            rows.append({
                "time": t, "open": o, "high": h, "low": l,
                "close": c,
                "tick_volume": int(np.random.randint(100, 1000)),
            })

        return pd.DataFrame(rows)
