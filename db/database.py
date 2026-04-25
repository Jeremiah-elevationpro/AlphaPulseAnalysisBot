"""
AlphaPulse - Database Connection Manager
==========================================
Supports both direct PostgreSQL (psycopg2) and Supabase REST API.

When USE_SUPABASE=true the bot communicates with Supabase entirely over
HTTPS (port 443) — no direct PostgreSQL port required.  All table operations
are translated to Supabase REST calls.
"""

import json
import re
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from config.settings import (
    USE_SUPABASE,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    SUPABASE_URL, SUPABASE_KEY,
    ACTIVE_TIMEFRAME_PAIR_LABELS,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def _normalise_tf_pair(value: str) -> str:
    return str(value).replace("->", "-").replace(" ", "")


# ─────────────────────────────────────────────────────────
# PostgreSQL Connection Pool (direct psycopg2)
# ─────────────────────────────────────────────────────────

class PostgresDB:
    """Thin psycopg2 wrapper with a simple threaded connection pool."""

    def __init__(self):
        self._pool = None

    def init(self):
        try:
            from psycopg2 import pool as pg_pool
            self._pool = pg_pool.ThreadedConnectionPool(
                minconn=1, maxconn=10,
                host=DB_HOST, port=DB_PORT,
                dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            )
            logger.info("PostgreSQL pool initialized — %s@%s:%s/%s",
                        DB_USER, DB_HOST, DB_PORT, DB_NAME)
        except Exception as e:
            logger.error("Failed to init PostgreSQL pool: %s", e)
            raise

    @contextmanager
    def _get_conn(self):
        conn = self._pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def execute(self, sql: str, params: tuple = ()):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)

    def fetchall(self, sql: str, params: tuple = ()) -> List[tuple]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[tuple]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    def close(self):
        if self._pool:
            self._pool.closeall()


# ─────────────────────────────────────────────────────────
# Supabase REST Client (HTTPS only — no direct PG port)
# ─────────────────────────────────────────────────────────

class SupabaseDB:
    """
    Full Supabase REST implementation using the PostgREST API.
    Communicates entirely over HTTPS (port 443).

    All CRUD operations map to:
      GET/POST/PATCH  https://<project>.supabase.co/rest/v1/<table>
    """

    def __init__(self):
        self._url: str = ""
        self._headers: Dict = {}
        self._request_timeout = (10, 45)
        self._retry_delays = (1.0, 2.5, 5.0)

    def init(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY must be set in .env when USE_SUPABASE=true"
            )
        self._url = SUPABASE_URL.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        # Verify connectivity and that tables exist
        try:
            self._get("trades", limit=1)
        except Exception as e:
            err = str(e)
            if "404" in err or "Not Found" in err:
                raise RuntimeError(
                    "Supabase tables not found. "
                    "Run setup_supabase.sql in your Supabase SQL Editor first:\n"
                    "  Dashboard -> SQL Editor -> New query -> paste setup_supabase.sql -> Run"
                ) from e
            raise
        logger.info("Supabase REST client ready — %s", SUPABASE_URL)

    # ── Low-level helpers ────────────────────────────────

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
    ):
        import requests

        url = f"{self._url}/{table}"
        request_headers = headers or self._headers
        last_exc: Optional[Exception] = None

        for attempt, delay in enumerate((0.0, *self._retry_delays), start=1):
            if delay > 0:
                time.sleep(delay)
            try:
                return requests.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    params=params,
                    data=json.dumps(data, default=str) if data is not None else None,
                    timeout=self._request_timeout,
                )
            except requests.exceptions.Timeout as exc:
                last_exc = exc
                logger.warning(
                    "Supabase %s %s timed out on attempt %d/%d; retrying...",
                    method,
                    table,
                    attempt,
                    len(self._retry_delays) + 1,
                )
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                logger.warning(
                    "Supabase %s %s connection error on attempt %d/%d: %s",
                    method,
                    table,
                    attempt,
                    len(self._retry_delays) + 1,
                    exc,
                )
            except requests.exceptions.SSLError as exc:
                last_exc = exc
                logger.warning(
                    "Supabase %s %s SSL error on attempt %d/%d: %s",
                    method,
                    table,
                    attempt,
                    len(self._retry_delays) + 1,
                    exc,
                )

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Supabase {method} {table} failed without a captured exception")

    def _get(self, table: str, params: Optional[Dict] = None,
             limit: int = 1000) -> List[Dict]:
        p = params or {}
        p["limit"] = limit
        r = self._request("GET", table, params=p)
        r.raise_for_status()
        return r.json()

    def _post(self, table: str, data: Dict) -> Optional[Dict]:
        r = self._request("POST", table, data=data)
        if not r.ok:
            # Log the Supabase error body before raising — this tells you exactly
            # which column is invalid/missing (e.g. "column X does not exist").
            try:
                err_body = r.json()
            except Exception:
                err_body = r.text
            logger.error(
                "Supabase POST %s failed %d: %s",
                table, r.status_code, err_body,
            )
            try:
                r.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"Supabase POST {table} failed {r.status_code}: {err_body}"
                ) from exc
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

    def _patch(self, table: str, filters: Dict, data: Dict):
        params = {k: f"eq.{v}" for k, v in filters.items()}
        r = self._request("PATCH", table, params=params, data=data)
        r.raise_for_status()

    def _upsert(self, table: str, data: Dict, on_conflict: str):
        headers = {**self._headers, "Prefer": f"resolution=merge-duplicates,return=representation"}
        r = self._request("POST", table, params={"on_conflict": on_conflict}, data=data, headers=headers)
        r.raise_for_status()


# ─────────────────────────────────────────────────────────
# Unified Database Interface
# ─────────────────────────────────────────────────────────

class Database:
    """
    Single entry point for all DB operations.
    Delegates to either PostgresDB or SupabaseDB based on USE_SUPABASE setting.
    """

    def __init__(self):
        self._pg: Optional[PostgresDB] = None
        self._sb: Optional[SupabaseDB] = None
        self._schema_warnings_seen: set[tuple[str, str]] = set()

    def init(self):
        if USE_SUPABASE:
            sb = SupabaseDB()
            sb.init()
            self._sb = sb
            logger.info("Using Supabase REST API for persistence.")
        else:
            pg = PostgresDB()
            pg.init()
            self._pg = pg
            self._create_schema()

    # ─────────────────────────────────────────────────────
    # SCHEMA CREATION (PostgreSQL direct only)
    # Supabase: run setup_supabase.sql in the SQL editor once.
    # ─────────────────────────────────────────────────────

    def _create_schema(self):
        """Create all tables (PostgreSQL direct mode only)."""
        logger.info("Ensuring database schema exists...")

        self._pg.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id              SERIAL PRIMARY KEY,
                trade_uuid      UUID UNIQUE NOT NULL,
                pair            VARCHAR(20) NOT NULL DEFAULT 'XAUUSD',
                direction       VARCHAR(4)  NOT NULL,
                entry_price     NUMERIC(10,2) NOT NULL,
                sl_price        NUMERIC(10,2) NOT NULL,
                tp1             NUMERIC(10,2),
                tp2             NUMERIC(10,2),
                tp3             NUMERIC(10,2),
                tp4             NUMERIC(10,2),
                tp5             NUMERIC(10,2),
                tp1_hit         BOOLEAN DEFAULT FALSE,
                tp2_hit         BOOLEAN DEFAULT FALSE,
                tp3_hit         BOOLEAN DEFAULT FALSE,
                tp4_hit         BOOLEAN DEFAULT FALSE,
                tp5_hit         BOOLEAN DEFAULT FALSE,
                status          VARCHAR(30) NOT NULL DEFAULT 'PENDING',
                level_type      VARCHAR(10),
                level_price     NUMERIC(10,2),
                higher_tf       VARCHAR(5),
                lower_tf        VARCHAR(5),
                result          VARCHAR(10),
                confidence      NUMERIC(5,3) DEFAULT 0.5,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                activated_at    TIMESTAMPTZ,
                closed_at       TIMESTAMPTZ,
                be_moved        BOOLEAN DEFAULT FALSE,
                tp_progress_reached INT DEFAULT 0,
                protected_after_tp1 BOOLEAN DEFAULT FALSE,
                tp1_alert_sent  BOOLEAN DEFAULT FALSE,
                breakeven_exit  BOOLEAN DEFAULT FALSE,
                notes           TEXT
            );
        """)

        self._pg.execute("""
            CREATE TABLE IF NOT EXISTS performance_stats (
                id              SERIAL PRIMARY KEY,
                level_type      VARCHAR(10),
                tf_pair         VARCHAR(20),
                wins            INT DEFAULT 0,
                losses          INT DEFAULT 0,
                total_trades    INT DEFAULT 0,
                win_rate        NUMERIC(5,3) DEFAULT 0.5,
                reward_score    NUMERIC(8,3) DEFAULT 0.0,
                updated_at      TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(level_type, tf_pair)
            );
        """)

        self._pg.execute("""
            CREATE TABLE IF NOT EXISTS daily_summaries (
                id              SERIAL PRIMARY KEY,
                summary_date    DATE UNIQUE NOT NULL,
                total_setups    INT DEFAULT 0,
                activated       INT DEFAULT 0,
                wins            INT DEFAULT 0,
                losses          INT DEFAULT 0,
                win_rate        NUMERIC(5,3) DEFAULT 0.0,
                best_level_type VARCHAR(10),
                best_tf_pair    VARCHAR(20),
                created_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        self._pg.execute("""
            CREATE TABLE IF NOT EXISTS confidence_scores (
                id              SERIAL PRIMARY KEY,
                level_type      VARCHAR(10) NOT NULL,
                tf_pair         VARCHAR(20) NOT NULL,
                score           NUMERIC(5,3) DEFAULT 0.5,
                reward_total    NUMERIC(8,3) DEFAULT 0.0,
                updated_at      TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(level_type, tf_pair)
            );
        """)

        self._pg.execute("""
            CREATE TABLE IF NOT EXISTS manual_setups (
                id                              SERIAL PRIMARY KEY,
                symbol                          VARCHAR(20) NOT NULL DEFAULT 'XAUUSD',
                direction                       VARCHAR(10) NOT NULL,
                timeframe_pair                  VARCHAR(20) NOT NULL,
                entry_price                     NUMERIC(12,2) NOT NULL,
                stop_loss                       NUMERIC(12,2) NOT NULL,
                tp1                             NUMERIC(12,2) NOT NULL,
                tp2                             NUMERIC(12,2),
                tp3                             NUMERIC(12,2),
                bias                            VARCHAR(40),
                confirmation_type               VARCHAR(50),
                session                         VARCHAR(30),
                notes                           TEXT,
                activation_mode                 VARCHAR(50),
                move_sl_to_be_after_tp1         BOOLEAN DEFAULT TRUE,
                enable_telegram_alerts          BOOLEAN DEFAULT TRUE,
                high_priority                   BOOLEAN DEFAULT FALSE,
                status                          VARCHAR(30) NOT NULL DEFAULT 'draft',
                created_at                      TIMESTAMPTZ DEFAULT NOW(),
                updated_at                      TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        logger.info("Schema verified/created.")

    # ─────────────────────────────────────────────────────
    # TRADE CRUD
    # ─────────────────────────────────────────────────────

    def insert_trade(self, trade: Dict[str, Any]) -> Optional[int]:
        """Insert a new trade. Returns the new trade ID."""
        if self._sb:
            payload = {
                "trade_uuid":   str(trade["trade_uuid"]),
                "pair":         trade.get("pair", "XAUUSD"),
                "direction":    trade["direction"],
                "entry_price":  trade["entry_price"],
                "sl_price":     trade["sl_price"],
                "tp1":          trade.get("tp1"),
                "tp2":          trade.get("tp2"),
                "tp3":          trade.get("tp3"),
                "tp4":          trade.get("tp4"),
                "tp5":          trade.get("tp5"),
                "level_type":   trade.get("level_type"),
                "level_price":  trade.get("level_price"),
                "higher_tf":    trade.get("higher_tf"),
                "lower_tf":     trade.get("lower_tf"),
                "confidence":   trade.get("confidence", 0.5),
                "status":       trade.get("status", "PENDING"),
                "tp_progress_reached": trade.get("tp_progress_reached", 0),
                "protected_after_tp1": trade.get("protected_after_tp1", False),
                "tp1_alert_sent": trade.get("tp1_alert_sent", False),
                "breakeven_exit": trade.get("breakeven_exit", False),
                "setup_type": trade.get("setup_type"),
                "is_qm": trade.get("is_qm", False),
                "is_psychological": trade.get("is_psychological", False),
                "is_liquidity_sweep": trade.get("is_liquidity_sweep", False),
                "session_name": trade.get("session_name"),
                "h4_bias": trade.get("h4_bias"),
                "trend_aligned": trade.get("trend_aligned", True),
                "confirmation_type": trade.get("confirmation_type"),
                "strategy_type": trade.get("strategy_type"),
                "source": trade.get("source"),
                "dominant_bias": trade.get("dominant_bias"),
                "bias_strength": trade.get("bias_strength"),
                "confirmation_score": trade.get("confirmation_score"),
                "confirmation_path": trade.get("confirmation_path"),
                "quality_rejection_count": trade.get("quality_rejection_count"),
                "structure_break_count": trade.get("structure_break_count"),
                "level_timeframe": trade.get("level_timeframe"),
                "confluence_with": trade.get("confluence_with"),
            }
            try:
                row = self._sb._post("trades", payload)
            except Exception as exc:
                if not self._is_400(exc):
                    raise
                missing = [
                    col for col in self._extract_missing_columns(exc)
                    if col in payload and col in self._OPTIONAL_LIVE_TRADE_COLUMNS
                ]
                if not missing:
                    raise
                for col in missing:
                    payload.pop(col, None)
                    self._warn_missing_schema_once("trades", col)
                row = self._sb._post("trades", payload)
            return row.get("id") if row else None

        if self._pg:
            row = self._pg.fetchone("""
                INSERT INTO trades (
                    trade_uuid, pair, direction, entry_price, sl_price,
                    tp1, tp2, tp3, tp4, tp5,
                    level_type, level_price, higher_tf, lower_tf,
                    confidence, status, tp_progress_reached,
                    protected_after_tp1, tp1_alert_sent, breakeven_exit
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                trade["trade_uuid"], trade.get("pair", "XAUUSD"),
                trade["direction"], trade["entry_price"], trade["sl_price"],
                trade.get("tp1"), trade.get("tp2"), trade.get("tp3"),
                trade.get("tp4"), trade.get("tp5"),
                trade.get("level_type"), trade.get("level_price"),
                trade.get("higher_tf"), trade.get("lower_tf"),
                trade.get("confidence", 0.5), trade.get("status", "PENDING"),
                trade.get("tp_progress_reached", 0),
                trade.get("protected_after_tp1", False),
                trade.get("tp1_alert_sent", False),
                trade.get("breakeven_exit", False),
            ))
            return row[0] if row else None

    def update_trade_status(self, trade_uuid: str, status: str, **kwargs):
        """Update a trade's status and any additional fields."""
        if self._sb:
            self._sb._patch("trades", {"trade_uuid": trade_uuid},
                            {"status": status, **kwargs})
            return

        if self._pg:
            set_clauses = ["status = %s"]
            params = [status]
            for k, v in kwargs.items():
                set_clauses.append(f"{k} = %s")
                params.append(v)
            params.append(trade_uuid)
            self._pg.execute(
                f"UPDATE trades SET {', '.join(set_clauses)} WHERE trade_uuid = %s",
                tuple(params),
            )

    def update_tp_hit(self, trade_uuid: str, tp_index: int):
        """Mark a specific TP level as hit (0-based index)."""
        col = f"tp{tp_index + 1}_hit"

        if self._sb:
            self._sb._patch("trades", {"trade_uuid": trade_uuid}, {col: True})
            return

        if self._pg:
            self._pg.execute(
                f"UPDATE trades SET {col} = TRUE WHERE trade_uuid = %s",
                (trade_uuid,),
            )

    def get_trade(self, trade_uuid: str) -> Optional[Dict]:
        if self._sb:
            rows = self._sb._get("trades", {"trade_uuid": f"eq.{trade_uuid}"}, limit=1)
            return rows[0] if rows else None

        if self._pg:
            row = self._pg.fetchone(
                "SELECT * FROM trades WHERE trade_uuid = %s", (trade_uuid,)
            )
            return dict(row) if row else None

    def get_active_trades(self) -> List[Dict]:
        if self._sb:
            return self._sb._get(
                "trades",
                {"status": "not.in.(COMPLETED,STOP_LOSS_HIT,CANCELLED)",
                 "order": "created_at.desc"},
            )

        if self._pg:
            rows = self._pg.fetchall("""
                SELECT * FROM trades
                WHERE status NOT IN ('COMPLETED','STOP_LOSS_HIT','CANCELLED')
                ORDER BY created_at DESC
            """)
            return list(rows)
        return []

    def get_today_trades(self) -> List[Dict]:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if self._sb:
            return self._sb._get(
                "trades",
                {"created_at": f"gte.{today}T00:00:00Z",
                 "order": "created_at.desc"},
            )

        if self._pg:
            rows = self._pg.fetchall("""
                SELECT * FROM trades
                WHERE DATE(created_at) = CURRENT_DATE
                ORDER BY created_at DESC
            """)
            return list(rows)
        return []

    def get_closed_trades(self, limit: int = 50) -> List[Dict]:
        rows = self.get_all_closed_trades()
        return rows[:limit]

    def get_all_closed_trades(self) -> List[Dict]:
        if self._sb:
            return self._sb._get(
                "trades",
                {"status": "in.(COMPLETED,STOP_LOSS_HIT)",
                 "order": "closed_at.desc"},
            )

        if self._pg:
            rows = self._pg.fetchall("""
                SELECT * FROM trades
                WHERE status IN ('COMPLETED','STOP_LOSS_HIT')
                ORDER BY closed_at DESC
            """)
            return list(rows)
        return []

    # ─────────────────────────────────────────────────────
    # PERFORMANCE STATS
    # ─────────────────────────────────────────────────────

    # Historical replay persistence. Replay is Supabase-only so that results
    # are available for later learning without affecting live trade tables.
    def create_replay_run(self, payload: Dict[str, Any]) -> Optional[int]:
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        row = self._sb._post("historical_replay_runs", payload)
        return row.get("id") if row else None

    def update_replay_run(self, replay_run_id: int, payload: Dict[str, Any]):
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        self._sb._patch("historical_replay_runs", {"id": replay_run_id}, payload)

    # All columns added by optional migrations. On a 400, the insert is retried
    # without these fields so the replay always succeeds even when a migration
    # hasn't been run yet. Run the migration SQLs to start capturing these values.
    _OPTIONAL_TRADE_COLUMNS: frozenset = frozenset({
        # migrate_replay_pip_metrics.sql
        "pips_to_tp1", "pips_to_tp2", "pips_to_tp3", "pips_to_tp4", "pips_to_tp5",
        "realized_pips", "max_potential_pips", "final_pips",
        # migrate_replay_micro_confirmation.sql
        "micro_confirmation_type", "micro_confirmation_score", "micro_layer_decision",
        # migrate_replay_execution_filters.sql
        "h1_liquidity_sweep", "h1_sweep_direction", "h1_reclaim_confirmed",
        "pd_location", "pd_filter_score", "bias_gate_result",
        # quality refinement tags
        "high_quality_trade", "micro_strength",
    })

    _OPTIONAL_STATS_COLUMNS: frozenset = frozenset({
        # migrate_replay_pip_metrics.sql (stats table)
        "pip_summary", "pip_by_timeframe_pair", "pip_by_bias", "pip_by_session",
        # migrate_replay_micro_confirmation.sql (stats table)
        "performance_by_micro_confirmation",
        # migrate_replay_execution_filters.sql (stats table)
        "performance_by_h1_sweep", "performance_by_pd_location", "performance_by_bias_gate",
    })

    _OPTIONAL_STRATEGY_RESEARCH_TRADE_COLUMNS: frozenset = frozenset({
        "research_run_id",
        "source",
        "strategy_type",
        "symbol",
        "direction",
        "timeframe",
        "timeframe_pair",
        "session_name",
        "market_condition",
        "dominant_bias",
        "bias_strength",
        "engulf_high",
        "engulf_low",
        "engulf_mid",
        "engulf_time",
        "engulf_body_pips",
        "engulf_range_pips",
        "engulf_type",
        "historical_rejection_count",
        "quality_rejection_count",
        "avg_rejection_wick_ratio",
        "avg_push_away_pips",
        "strongest_rejection_pips",
        "rejection_quality_score",
        "structure_break_count",
        "quality_score",
        "entry",
        "sl",
        "tp1",
        "tp2",
        "tp3",
        "activated_at",
        "closed_at",
        "completed_at",
        "final_result",
        "final_pips",
        "reward_score",
        "failure_reason",
        "run_id",
        "created_at",
        "status",
        "level_high",
        "level_low",
        "level_mid",
        "confirmation_path",
        "confirmation_score",
        "revisit_time",
        "confirmation_time",
        "confirmation_candles_used",
        "notes",
        # break+retest research columns
        "break_level",
        "break_time",
        "break_close",
        "break_distance_pips",
        "source_level_type",
        "source_strategy_type",
        "retest_level",
        "retest_time",
        "retest_confirmation_type",
        "retest_confirmation_score",
        "original_engulf_high",
        "original_engulf_low",
        "original_engulf_mid",
        "original_engulf_direction",
        "original_engulf_time",
        "original_quality_rejection_count",
        "original_structure_break_count",
        "original_quality_score",
    })

    _REQUIRED_STRATEGY_RESEARCH_TRADE_COLUMNS: frozenset = frozenset({
        "symbol",
        "strategy_type",
        "direction",
        "entry",
        "final_result",
    })

    _OPTIONAL_STRATEGY_RESEARCH_RUN_COLUMNS: frozenset = frozenset({
        "strategy_type",
        "started_at",
        "finished_at",
        "notes",
        "replay_start",
        "replay_end",
        "status",
        "funnel_summary",
        "reject_summary",
        "created_at",
        "updated_at",
    })

    _OPTIONAL_STRATEGY_RESEARCH_STATS_COLUMNS: frozenset = frozenset({
        "run_id",
        "strategy_type",
        "symbol",
        "stats_key",
        "stats_value",
        "payload",
        "funnel_summary",
        "reject_summary",
    })

    _OPTIONAL_LIVE_TRADE_COLUMNS: frozenset = frozenset({
        "setup_type",
        "is_qm",
        "is_psychological",
        "is_liquidity_sweep",
        "session_name",
        "h4_bias",
        "trend_aligned",
        "confirmation_type",
        "strategy_type",
        "source",
        "dominant_bias",
        "bias_strength",
        "confirmation_score",
        "confirmation_path",
        "quality_rejection_count",
        "structure_break_count",
        "level_timeframe",
        "confluence_with",
    })

    def insert_replay_trade(self, payload: Dict[str, Any]) -> Optional[int]:
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        try:
            row = self._sb._post("historical_replay_trades", payload)
        except Exception as exc:
            if not self._is_400(exc):
                raise
            # At least one optional column is missing from the table.
            # Strip ALL migration-optional fields and retry once. This handles
            # any combination of missing migrations (pip, micro, or both).
            fallback = {k: v for k, v in payload.items() if k not in self._OPTIONAL_TRADE_COLUMNS}
            stripped = sorted(set(payload) & self._OPTIONAL_TRADE_COLUMNS)
            logger.warning(
                "Replay trade insert 400 — retrying without optional columns %s. "
                "Run migrate_replay_pip_metrics.sql, migrate_replay_micro_confirmation.sql, "
                "and migrate_replay_execution_filters.sql "
                "in Supabase to capture full trade data.",
                stripped,
            )
            row = self._sb._post("historical_replay_trades", fallback)
        return row.get("id") if row else None

    def insert_replay_stats(self, payload: Dict[str, Any]) -> Optional[int]:
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        try:
            row = self._sb._post("historical_replay_stats", payload)
        except Exception as exc:
            if not self._is_400(exc):
                raise
            fallback = {k: v for k, v in payload.items() if k not in self._OPTIONAL_STATS_COLUMNS}
            stripped = sorted(set(payload) & self._OPTIONAL_STATS_COLUMNS)
            logger.warning(
                "Replay stats insert 400 — retrying without optional columns %s. "
                "Run migrate_replay_pip_metrics.sql, migrate_replay_micro_confirmation.sql, "
                "and migrate_replay_execution_filters.sql.",
                stripped,
            )
            row = self._sb._post("historical_replay_stats", fallback)
        return row.get("id") if row else None

    @staticmethod
    def _extract_missing_columns(exc: Exception) -> List[str]:
        message = str(exc)
        patterns = [
            r"Could not find the '([^']+)' column",
            r'column "([^"]+)" does not exist',
            r"'([^']+)' column",
        ]
        found: List[str] = []
        for pattern in patterns:
            found.extend(re.findall(pattern, message))
        return list(dict.fromkeys(found))

    def _post_with_missing_column_fallback(
        self,
        table: str,
        payload: Dict[str, Any],
        optional_columns: frozenset[str],
        context_label: str,
        required_columns: Optional[frozenset[str]] = None,
    ) -> Optional[int]:
        if not self._sb:
            raise RuntimeError(f"{context_label} persistence requires USE_SUPABASE=true")

        attempt_payload = dict(payload)
        removed: List[str] = []
        required_columns = required_columns or frozenset()
        while True:
            try:
                row = self._sb._post(table, attempt_payload)
                if removed:
                    logger.warning(
                        "%s insert succeeded after dropping missing columns %s",
                        context_label,
                        removed,
                    )
                return row.get("id") if row else None
            except Exception as exc:
                if not self._is_400(exc):
                    raise
                extracted_missing = [col for col in self._extract_missing_columns(exc) if col in attempt_payload]
                required_missing = [col for col in extracted_missing if col in required_columns]
                if required_missing:
                    raise RuntimeError(
                        f"{context_label} requires DB migration: missing required column(s) {required_missing} in {table}"
                    ) from exc
                missing = [
                    col for col in extracted_missing
                    if col in attempt_payload and col in optional_columns
                ]
                if not missing:
                    fallback = {k: v for k, v in attempt_payload.items() if k not in optional_columns}
                    stripped = sorted(set(attempt_payload) & optional_columns)
                    logger.warning(
                        "%s insert hit schema mismatch; retrying without optional columns %s",
                        context_label,
                        stripped,
                    )
                    row = self._sb._post(table, fallback)
                    return row.get("id") if row else None
                for col in missing:
                    removed.append(col)
                    attempt_payload.pop(col, None)
                    self._warn_missing_schema_once(table, col)

    def _patch_with_missing_column_fallback(
        self,
        table: str,
        filters: Dict[str, Any],
        payload: Dict[str, Any],
        optional_columns: frozenset[str],
        context_label: str,
    ) -> None:
        if not self._sb:
            raise RuntimeError(f"{context_label} persistence requires USE_SUPABASE=true")

        attempt_payload = dict(payload)
        removed: List[str] = []
        while True:
            try:
                self._sb._patch(table, filters, attempt_payload)
                if removed:
                    logger.warning(
                        "%s update succeeded after dropping missing columns %s",
                        context_label,
                        removed,
                    )
                return
            except Exception as exc:
                if not self._is_400(exc):
                    raise
                missing = [
                    col for col in self._extract_missing_columns(exc)
                    if col in attempt_payload and col in optional_columns
                ]
                if not missing:
                    fallback = {k: v for k, v in attempt_payload.items() if k not in optional_columns}
                    stripped = sorted(set(attempt_payload) & optional_columns)
                    logger.warning(
                        "%s update hit schema mismatch; retrying without optional columns %s",
                        context_label,
                        stripped,
                    )
                    self._sb._patch(table, filters, fallback)
                    return
                for col in missing:
                    removed.append(col)
                    attempt_payload.pop(col, None)
                    self._warn_missing_schema_once(table, col)

    @staticmethod
    def _is_400(exc: Exception) -> bool:
        """Return True when exc is an HTTP 400 from the Supabase client.

        Supabase REST errors are first raised as ``requests.HTTPError`` inside
        ``_post`` and then wrapped as ``RuntimeError`` with the response body.
        Keep this helper tolerant so optional replay-column fallbacks still
        work when a local Supabase schema is missing a recently added column.
        """
        try:
            from requests import HTTPError

            if (
                isinstance(exc, HTTPError)
                and exc.response is not None
                and exc.response.status_code == 400
            ):
                return True
        except Exception:
            pass

        message = str(exc)
        retryable_markers = (
            "failed 400",
            "400 Client Error",
            "PGRST204",
            "schema cache",
            "Could not find the",
        )
        return any(marker in message for marker in retryable_markers)

    def _warn_missing_schema_once(self, table: str, column: str) -> None:
        key = (table, column)
        if key in self._schema_warnings_seen:
            return
        self._schema_warnings_seen.add(key)
        logger.warning(
            "SCHEMA WARNING: missing column %s on %s — run migration",
            column,
            table,
        )

    def get_replay_trades_for_learning(
        self,
        limit: int = 1000,
        replay_run_id: Optional[int] = None,
    ) -> List[Dict]:
        """Fetch learning-grade activated replay trades from Supabase."""
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        filters = {
            "source": "eq.historical_replay",
            "final_result": "in.(PARTIAL_WIN,BREAKEVEN_WIN,WIN,STRONG_WIN,LOSS)",
            "order": "timestamp.asc",
        }
        if replay_run_id:
            filters["replay_run_id"] = f"eq.{replay_run_id}"
        rows = self._sb._get(
            "historical_replay_trades",
            filters,
            limit=limit,
        )
        active_rows = [
            row for row in rows
            if _normalise_tf_pair(row.get("timeframe_pair", "")) in ACTIVE_TIMEFRAME_PAIR_LABELS
        ]
        skipped = len(rows) - len(active_rows)
        if skipped:
            logger.info("Replay learning skipped %d disabled timeframe-pair trade(s).", skipped)
        return active_rows

    def get_latest_replay_run(self) -> Optional[Dict]:
        """Return the most recent historical replay run from Supabase."""
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        rows = self._sb._get(
            "historical_replay_runs",
            {"order": "id.desc"},
            limit=1,
        )
        return rows[0] if rows else None

    def get_replay_run(self, replay_run_id: int) -> Optional[Dict]:
        """Return one historical replay run by id."""
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        rows = self._sb._get(
            "historical_replay_runs",
            {"id": f"eq.{replay_run_id}"},
            limit=1,
        )
        return rows[0] if rows else None

    def get_replay_trades(self, replay_run_id: int, limit: int = 10000) -> List[Dict]:
        """Return activated replay trades for one replay run."""
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        return self._sb._get(
            "historical_replay_trades",
            {"replay_run_id": f"eq.{replay_run_id}", "order": "timestamp.asc"},
            limit=limit,
        )

    def get_replay_stats(self, replay_run_id: int) -> Optional[Dict]:
        """Return aggregate replay stats for one replay run."""
        if not self._sb:
            raise RuntimeError("Historical replay persistence requires USE_SUPABASE=true")
        rows = self._sb._get(
            "historical_replay_stats",
            {"replay_run_id": f"eq.{replay_run_id}", "order": "id.desc"},
            limit=1,
        )
        return rows[0] if rows else None

    def create_strategy_research_run(self, payload: Dict[str, Any]) -> Optional[int]:
        """Create a strategy research run row in Supabase."""
        return self._post_with_missing_column_fallback(
            "strategy_research_runs",
            payload,
            self._OPTIONAL_STRATEGY_RESEARCH_RUN_COLUMNS,
            "strategy research run",
        )

    def update_strategy_research_run(self, run_id: int, payload: Dict[str, Any]):
        """Update a strategy research run row."""
        self._patch_with_missing_column_fallback(
            "strategy_research_runs",
            {"id": run_id},
            payload,
            self._OPTIONAL_STRATEGY_RESEARCH_RUN_COLUMNS,
            "strategy research run",
        )

    def insert_strategy_research_trade(self, payload: Dict[str, Any]) -> Optional[int]:
        """Insert one strategy research trade row without touching live/replay trade tables."""
        return self._post_with_missing_column_fallback(
            "strategy_research_trades",
            payload,
            self._OPTIONAL_STRATEGY_RESEARCH_TRADE_COLUMNS,
            "strategy research trade",
            self._REQUIRED_STRATEGY_RESEARCH_TRADE_COLUMNS,
        )

    def insert_strategy_research_stats(self, payload: Dict[str, Any]) -> Optional[int]:
        """Insert aggregate strategy research stats payloads."""
        return self._post_with_missing_column_fallback(
            "strategy_research_stats",
            payload,
            self._OPTIONAL_STRATEGY_RESEARCH_STATS_COLUMNS,
            "strategy research stats",
        )

    def get_strategy_research_run(self, run_id: int) -> Optional[Dict]:
        if not self._sb:
            raise RuntimeError("Strategy research persistence requires USE_SUPABASE=true")
        runs = self._sb._get(
            "strategy_research_runs",
            {"id": f"eq.{run_id}"},
            limit=1,
        )
        return runs[0] if runs else None

    def _get_strategy_research_related_rows(
        self,
        table: str,
        run_id: int,
        *,
        limit: int,
    ) -> List[Dict]:
        """Fetch strategy research child rows with schema-tolerant run filtering.

        Some local Supabase schemas expose `run_id`, others `research_run_id`,
        and some PostgREST schema caches may temporarily reject one or both as
        query params. Try server-side filters first, then fall back to a broader
        fetch plus client-side filtering so evaluation does not crash.
        """
        if not self._sb:
            raise RuntimeError("Strategy research persistence requires USE_SUPABASE=true")

        filter_options = (
            {"run_id": f"eq.{run_id}", "order": "id.asc"},
            {"research_run_id": f"eq.{run_id}", "order": "id.asc"},
        )
        for params in filter_options:
            try:
                return self._sb._get(table, params, limit=limit)
            except Exception as exc:
                if not self._is_400(exc):
                    raise
                filter_name = next((k for k in ("run_id", "research_run_id") if k in params), "unknown")
                logger.warning(
                    "strategy research %s filter fallback: %s not queryable in Supabase schema cache; retrying",
                    table,
                    filter_name,
                )

        rows = self._sb._get(table, {"order": "id.asc"}, limit=limit)
        filtered = [
            row for row in rows
            if row.get("run_id") == run_id or row.get("research_run_id") == run_id
        ]
        logger.warning(
            "strategy research %s using client-side run filter for run_id=%s (%d/%d rows matched)",
            table,
            run_id,
            len(filtered),
            len(rows),
        )
        return filtered

    def get_latest_strategy_research_results(self, strategy_type: Optional[str] = None) -> Optional[Dict]:
        """Return the latest strategy research run plus its stats and trades."""
        if not self._sb:
            raise RuntimeError("Strategy research persistence requires USE_SUPABASE=true")
        params = {"order": "id.desc"}
        if strategy_type:
            params["strategy_group"] = f"eq.{strategy_type}"
        runs = self._sb._get("strategy_research_runs", params, limit=1)
        if not runs:
            return None
        run = runs[0]
        run_id = run.get("id")
        stats = self._get_strategy_research_related_rows(
            "strategy_research_stats",
            run_id,
            limit=5000,
        )
        trades = self._get_strategy_research_related_rows(
            "strategy_research_trades",
            run_id,
            limit=10000,
        )
        return {
            "run": run,
            "stats": stats,
            "trades": trades,
        }

    def get_strategy_research_results(self, run_id: int) -> Optional[Dict]:
        """Return one strategy research run plus its stats and trades."""
        if not self._sb:
            raise RuntimeError("Strategy research persistence requires USE_SUPABASE=true")
        run = self.get_strategy_research_run(run_id)
        if not run:
            return None
        stats = self._get_strategy_research_related_rows(
            "strategy_research_stats",
            run_id,
            limit=5000,
        )
        trades = self._get_strategy_research_related_rows(
            "strategy_research_trades",
            run_id,
            limit=10000,
        )
        return {
            "run": run,
            "stats": stats,
            "trades": trades,
        }

    def upsert_performance(self, level_type: str, tf_pair: str,
                           wins: int, losses: int, reward: float):
        total = wins + losses
        win_rate = round(wins / total, 3) if total > 0 else 0.5

        if self._sb:
            self._sb._upsert("performance_stats", {
                "level_type": level_type, "tf_pair": tf_pair,
                "wins": wins, "losses": losses,
                "total_trades": total, "win_rate": win_rate,
                "reward_score": reward,
            }, on_conflict="level_type,tf_pair")
            return

        if self._pg:
            self._pg.execute("""
                INSERT INTO performance_stats
                    (level_type, tf_pair, wins, losses, total_trades, win_rate, reward_score)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (level_type, tf_pair) DO UPDATE SET
                    wins=EXCLUDED.wins, losses=EXCLUDED.losses,
                    total_trades=EXCLUDED.total_trades, win_rate=EXCLUDED.win_rate,
                    reward_score=EXCLUDED.reward_score, updated_at=NOW()
            """, (level_type, tf_pair, wins, losses, total, win_rate, reward))

    def get_all_performance(self) -> List[Dict]:
        if self._sb:
            return self._sb._get("performance_stats",
                                 {"order": "win_rate.desc"})

        if self._pg:
            return list(self._pg.fetchall(
                "SELECT * FROM performance_stats ORDER BY win_rate DESC"
            ))
        return []

    # ─────────────────────────────────────────────────────
    # CONFIDENCE SCORES
    # ─────────────────────────────────────────────────────

    def upsert_confidence(self, level_type: str, tf_pair: str,
                          score: float, reward_total: float):
        if self._sb:
            self._sb._upsert("confidence_scores", {
                "level_type": level_type, "tf_pair": tf_pair,
                "score": score, "reward_total": reward_total,
            }, on_conflict="level_type,tf_pair")
            return

        if self._pg:
            self._pg.execute("""
                INSERT INTO confidence_scores (level_type, tf_pair, score, reward_total)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (level_type, tf_pair) DO UPDATE SET
                    score=EXCLUDED.score, reward_total=EXCLUDED.reward_total,
                    updated_at=NOW()
            """, (level_type, tf_pair, score, reward_total))

    def get_confidence(self, level_type: str, tf_pair: str) -> float:
        if self._sb:
            rows = self._sb._get("confidence_scores", {
                "level_type": f"eq.{level_type}",
                "tf_pair": f"eq.{tf_pair}",
            }, limit=1)
            return float(rows[0]["score"]) if rows else 0.5

        if self._pg:
            row = self._pg.fetchone(
                "SELECT score FROM confidence_scores WHERE level_type=%s AND tf_pair=%s",
                (level_type, tf_pair),
            )
            return float(row[0]) if row else 0.5
        return 0.5

    def get_learned_combo_scores(self) -> List[Dict]:
        """Return persisted learned setup-combination scores."""
        if self._sb:
            return self._sb._get(
                "confidence_scores",
                {"level_type": "eq.learned_combo"},
                limit=5000,
            )

        if self._pg:
            rows = self._pg.fetchall(
                """
                SELECT level_type, tf_pair, score, reward_total
                FROM confidence_scores
                WHERE level_type=%s
                """,
                ("learned_combo",),
            )
            return [
                {
                    "level_type": row[0],
                    "tf_pair": row[1],
                    "score": row[2],
                    "reward_total": row[3],
                }
                for row in rows
            ]
        return []

    # ─────────────────────────────────────────────────────────────────────
    # MANUAL SETUPS
    # ─────────────────────────────────────────────────────────────────────

    def get_manual_setups(self, limit: int = 500) -> List[Dict]:
        if self._sb:
            return self._sb._get(
                "manual_setups",
                {"order": "updated_at.desc"},
                limit=limit,
            )

        if self._pg:
            rows = self._pg.fetchall(
                """
                SELECT
                    id, symbol, direction, timeframe_pair, entry_price, stop_loss,
                    tp1, tp2, tp3, bias, confirmation_type, session, notes,
                    activation_mode, move_sl_to_be_after_tp1, enable_telegram_alerts,
                    high_priority, status, created_at, updated_at
                FROM manual_setups
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [
                {
                    "id": row[0],
                    "symbol": row[1],
                    "direction": row[2],
                    "timeframe_pair": row[3],
                    "entry_price": float(row[4]) if row[4] is not None else None,
                    "stop_loss": float(row[5]) if row[5] is not None else None,
                    "tp1": float(row[6]) if row[6] is not None else None,
                    "tp2": float(row[7]) if row[7] is not None else None,
                    "tp3": float(row[8]) if row[8] is not None else None,
                    "bias": row[9],
                    "confirmation_type": row[10],
                    "session": row[11],
                    "notes": row[12],
                    "activation_mode": row[13],
                    "move_sl_to_be_after_tp1": row[14],
                    "enable_telegram_alerts": row[15],
                    "high_priority": row[16],
                    "status": row[17],
                    "created_at": str(row[18]) if row[18] else None,
                    "updated_at": str(row[19]) if row[19] else None,
                }
                for row in rows
            ]
        return []

    def insert_manual_setup(self, payload: Dict[str, Any]) -> Optional[Dict]:
        if self._sb:
            return self._sb._post("manual_setups", payload)

        if self._pg:
            row = self._pg.fetchone(
                """
                INSERT INTO manual_setups (
                    symbol, direction, timeframe_pair, entry_price, stop_loss,
                    tp1, tp2, tp3, bias, confirmation_type, session, notes,
                    activation_mode, move_sl_to_be_after_tp1, enable_telegram_alerts,
                    high_priority, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING
                    id, symbol, direction, timeframe_pair, entry_price, stop_loss,
                    tp1, tp2, tp3, bias, confirmation_type, session, notes,
                    activation_mode, move_sl_to_be_after_tp1, enable_telegram_alerts,
                    high_priority, status, created_at, updated_at
                """,
                (
                    payload["symbol"],
                    payload["direction"],
                    payload["timeframe_pair"],
                    payload["entry_price"],
                    payload["stop_loss"],
                    payload["tp1"],
                    payload.get("tp2"),
                    payload.get("tp3"),
                    payload.get("bias"),
                    payload.get("confirmation_type"),
                    payload.get("session"),
                    payload.get("notes"),
                    payload.get("activation_mode"),
                    payload.get("move_sl_to_be_after_tp1", True),
                    payload.get("enable_telegram_alerts", True),
                    payload.get("high_priority", False),
                    payload.get("status", "draft"),
                ),
            )
            return {
                "id": row[0],
                "symbol": row[1],
                "direction": row[2],
                "timeframe_pair": row[3],
                "entry_price": float(row[4]) if row[4] is not None else None,
                "stop_loss": float(row[5]) if row[5] is not None else None,
                "tp1": float(row[6]) if row[6] is not None else None,
                "tp2": float(row[7]) if row[7] is not None else None,
                "tp3": float(row[8]) if row[8] is not None else None,
                "bias": row[9],
                "confirmation_type": row[10],
                "session": row[11],
                "notes": row[12],
                "activation_mode": row[13],
                "move_sl_to_be_after_tp1": row[14],
                "enable_telegram_alerts": row[15],
                "high_priority": row[16],
                "status": row[17],
                "created_at": str(row[18]) if row[18] else None,
                "updated_at": str(row[19]) if row[19] else None,
            }
        return None

    def update_manual_setup(self, setup_id: int, payload: Dict[str, Any]) -> Optional[Dict]:
        payload = {**payload, "updated_at": "now()" if self._sb else None}

        if self._sb:
            clean_payload = {k: v for k, v in payload.items() if v is not None}
            if "updated_at" in clean_payload:
                clean_payload.pop("updated_at")
            self._sb._patch("manual_setups", {"id": setup_id}, clean_payload)
            rows = self._sb._get("manual_setups", {"id": f"eq.{setup_id}"}, limit=1)
            return rows[0] if rows else None

        if self._pg:
            assignments = []
            params: List[Any] = []
            for key, value in payload.items():
                if key == "updated_at":
                    continue
                assignments.append(f"{key} = %s")
                params.append(value)
            assignments.append("updated_at = NOW()")
            params.append(setup_id)
            self._pg.execute(
                f"UPDATE manual_setups SET {', '.join(assignments)} WHERE id = %s",
                tuple(params),
            )
            rows = self.get_manual_setups(limit=500)
            return next((row for row in rows if row.get("id") == setup_id), None)
        return None

    def delete_manual_setup(self, setup_id: int) -> bool:
        if self._sb:
            r = self._sb._request(
                "DELETE",
                "manual_setups",
                params={"id": f"eq.{setup_id}"},
            )
            r.raise_for_status()
            return True

        if self._pg:
            self._pg.execute("DELETE FROM manual_setups WHERE id = %s", (setup_id,))
            return True
        return False

    # ─────────────────────────────────────────────────────
    # DAILY SUMMARY
    # ─────────────────────────────────────────────────────

    def upsert_daily_summary(self, summary: Dict[str, Any]):
        if self._sb:
            self._sb._upsert("daily_summaries", {
                "summary_date": str(summary["date"]),
                "total_setups": summary["total_setups"],
                "activated":    summary["activated"],
                "wins":         summary["wins"],
                "losses":       summary["losses"],
                "win_rate":     summary["win_rate"],
            }, on_conflict="summary_date")
            return

        if self._pg:
            self._pg.execute("""
                INSERT INTO daily_summaries
                    (summary_date, total_setups, activated, wins, losses, win_rate)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (summary_date) DO UPDATE SET
                    total_setups=EXCLUDED.total_setups, activated=EXCLUDED.activated,
                    wins=EXCLUDED.wins, losses=EXCLUDED.losses, win_rate=EXCLUDED.win_rate
            """, (
                summary["date"], summary["total_setups"],
                summary["activated"], summary["wins"],
                summary["losses"], summary["win_rate"],
            ))

    def get_daily_summaries(self, days: int = 30) -> List[Dict]:
        if self._sb:
            return self._sb._get("daily_summaries",
                                 {"order": "summary_date.desc"}, limit=days)

        if self._pg:
            return list(self._pg.fetchall(
                "SELECT * FROM daily_summaries ORDER BY summary_date DESC LIMIT %s",
                (days,),
            ))
        return []

    # ─────────────────────────────────────────────────────
    # CLOSE
    # ─────────────────────────────────────────────────────

    def close(self):
        if self._pg:
            self._pg.close()
