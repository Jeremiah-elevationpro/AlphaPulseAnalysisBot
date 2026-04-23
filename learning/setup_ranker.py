"""
AlphaPulse - Setup Ranker
==========================
Ranks live setups by historical performance across multiple dimensions.

Returns a rank_score multiplier (0.80–1.20) applied to confidence AFTER the
learning engine score. This softly boosts historically strong setup combinations
and dampens weak ones — without ever hard-blocking a signal.

Dimensions tracked
──────────────────
  session × h4_bias × direction  — regime-aligned performance (highest weight 50%)
  setup_type                     — major / recent_leg / previous_leg / qm_level (30%)
  confirmation_type              — rejection / sweep_reclaim / double_pattern / engulfing (20%)

rank_score multiplier range: 0.80 (poor history) → 1.00 (neutral/insufficient) → 1.20 (strong)

Example log output
──────────────────
  [RANKER] BUY london/bullish | SBD WR 72% | type WR 68% | conf WR 61% → rank ×1.14
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from db.database import Database
from db.models import TradeResult
from config.settings import MIN_TRADES_FOR_LEARNING
from utils.logger import get_logger

logger = get_logger(__name__)

_WIN_RESULTS = frozenset({
    TradeResult.PARTIAL_WIN,
    TradeResult.BREAKEVEN_WIN,
    TradeResult.WIN,
    TradeResult.STRONG_WIN,
})


@dataclass
class DimStats:
    wins: int = 0
    total: int = 0
    tp1_hits: int = 0
    total_pips: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.total if self.total > 0 else 0.5

    @property
    def tp1_rate(self) -> float:
        return self.tp1_hits / self.total if self.total > 0 else 0.5

    @property
    def avg_pips(self) -> float:
        return self.total_pips / self.total if self.total > 0 else 0.0


@dataclass
class RankResult:
    """Complete ranking output for one live setup."""
    rank_score: float               # 0.80–1.20 multiplier applied to confidence
    session_win_rate: float         # session × bias × direction win rate
    setup_type_win_rate: float
    confirmation_type_win_rate: float
    tp1_hit_rate: float
    net_pip_expectancy: float       # avg realized pips per trade (positive = profitable)
    sample_size: int                # total trades contributing to the rank
    note: str = ""


class SetupRanker:
    """
    Queries all closed trades from the DB and computes multi-dimensional
    win-rate statistics. Called by LearningEngine; refreshed after each
    closed trade and on startup.

    Usage (via LearningEngine):
        rank = learning.get_rank_result(session, h4_bias, direction,
                                        setup_type, confirmation_type)
        adjusted = min(1.0, base_confidence * rank.rank_score)
    """

    def __init__(self, db: Database):
        self._db = db
        # dimension → DimStats
        self._sbd:  Dict[str, DimStats] = {}   # session|bias|direction
        self._stype: Dict[str, DimStats] = {}  # setup_type
        self._ctype: Dict[str, DimStats] = {}  # confirmation_type
        self._loaded = False

    # ─────────────────────────────────────────────────────
    # PUBLIC INTERFACE
    # ─────────────────────────────────────────────────────

    def refresh(self):
        """Re-read closed trades and recompute all dimension stats."""
        try:
            trades = self._db.get_all_closed_trades()
            self._load_stats(trades)
            self._loaded = True
            logger.info(
                "SetupRanker refreshed — %d SBD combos | %d setup types | %d conf types",
                len(self._sbd), len(self._stype), len(self._ctype),
            )
        except Exception as e:
            logger.warning("SetupRanker.refresh() failed: %s", e)

    def rank(
        self,
        session: str,
        h4_bias: str,
        direction: str,
        setup_type: str,
        confirmation_type: str,
    ) -> RankResult:
        """
        Compute rank_score multiplier for a live setup.

        Composite formula:
          composite = sbd_wr × 0.50 + st_wr × 0.30 + ct_wr × 0.20
          rank_score = 0.80 + composite × 0.40    (range: 0.80–1.20)

        Dimensions without MIN_TRADES_FOR_LEARNING history default to 0.5
        (neutral — no boost or penalty).
        """
        if not self._loaded:
            return RankResult(
                rank_score=1.0,
                session_win_rate=0.5,
                setup_type_win_rate=0.5,
                confirmation_type_win_rate=0.5,
                tp1_hit_rate=0.5,
                net_pip_expectancy=0.0,
                sample_size=0,
                note="ranker not loaded — neutral rank applied",
            )

        sbd_key = f"{session or 'off'}|{h4_bias or 'neutral'}|{direction}"
        sbd     = self._sbd.get(sbd_key, DimStats())
        st      = self._stype.get(setup_type or "major", DimStats())
        ct      = self._ctype.get(confirmation_type or "rejection", DimStats())

        min_n = MIN_TRADES_FOR_LEARNING
        sbd_wr = sbd.win_rate  if sbd.total  >= min_n else 0.5
        st_wr  = st.win_rate   if st.total   >= min_n else 0.5
        ct_wr  = ct.win_rate   if ct.total   >= min_n else 0.5
        tp1_r  = sbd.tp1_rate  if sbd.total  >= min_n else 0.5
        pip_e  = sbd.avg_pips  if sbd.total  >= min_n else 0.0

        composite  = sbd_wr * 0.50 + st_wr * 0.30 + ct_wr * 0.20
        rank_score = round(0.80 + composite * 0.40, 3)
        total_n    = sbd.total + st.total + ct.total

        parts = []
        if sbd.total >= min_n:
            parts.append(f"SBD WR {sbd_wr:.0%} ({sbd.total}T)")
        if st.total >= min_n:
            parts.append(f"type WR {st_wr:.0%}")
        if ct.total >= min_n:
            parts.append(f"conf WR {ct_wr:.0%}")
        note = " | ".join(parts) if parts else "insufficient history — neutral rank"

        logger.info(
            "[RANKER] %s %s/%s | %s → rank ×%.2f",
            direction, session or "off", h4_bias or "neutral", note, rank_score,
        )

        return RankResult(
            rank_score=rank_score,
            session_win_rate=sbd_wr,
            setup_type_win_rate=st_wr,
            confirmation_type_win_rate=ct_wr,
            tp1_hit_rate=tp1_r,
            net_pip_expectancy=pip_e,
            sample_size=total_n,
            note=note,
        )

    # ─────────────────────────────────────────────────────
    # INTERNAL STAT LOADING
    # ─────────────────────────────────────────────────────

    def _load_stats(self, trades: list):
        self._sbd.clear()
        self._stype.clear()
        self._ctype.clear()

        for row in trades:
            try:
                if isinstance(row, dict):
                    result        = row.get("result")
                    session       = row.get("session_name", "") or "off"
                    h4_bias       = row.get("h4_bias", "") or "neutral"
                    direction     = row.get("direction", "")
                    setup_type    = row.get("setup_type", "") or "major"
                    conf_type     = row.get("confirmation_type", "") or "rejection"
                    tp_progress   = int(row.get("tp_progress_reached", 0) or 0)
                    sl_pips_val   = float(row.get("sl_pips", 20) or 20)
                elif isinstance(row, tuple):
                    # Column order: adjust indices based on your CREATE TABLE order
                    result      = row[21] if len(row) > 21 else None
                    direction   = row[3]  if len(row) > 3  else ""
                    setup_type  = (row[9]  if len(row) > 9  else "") or "major"
                    session     = (row[27] if len(row) > 27 else "") or "off"
                    h4_bias     = (row[28] if len(row) > 28 else "") or "neutral"
                    conf_type   = (row[29] if len(row) > 29 else "") or "rejection"
                    tp_progress = int(row[16] if len(row) > 16 else 0)
                    sl_pips_val = 20.0
                else:
                    continue

                if not result or not direction:
                    continue

                is_win  = result in _WIN_RESULTS
                is_tp1  = result in _WIN_RESULTS  # TP1 is hit in all win variants
                pip_val = (tp_progress * 20.0) if is_win else -sl_pips_val

                sbd_key = f"{session}|{h4_bias}|{direction}"
                for key, store in (
                    (sbd_key,   self._sbd),
                    (setup_type, self._stype),
                    (conf_type,  self._ctype),
                ):
                    if key not in store:
                        store[key] = DimStats()
                    s = store[key]
                    s.total     += 1
                    s.wins      += int(is_win)
                    s.tp1_hits  += int(is_tp1)
                    s.total_pips += pip_val

            except Exception as e:
                logger.debug("SetupRanker: skipping malformed row: %s", e)
