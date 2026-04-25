"""
AlphaPulse - Reinforcement Learning Engine (Enhanced)
======================================================
Combines statistical win-rate data with reward-based Q-learning to
produce confidence scores that improve with every closed trade.

State dimensions:
  Primary  : (level_type, tf_pair)             — always tracked
  Secondary: (setup_type)                       — recent_leg vs major vs qm etc.
  Tertiary : (session_name)                     — london / new_york / off

Reward shaping:
  LOSS before TP1      -> strong negative
  TP1 then BE/partial  -> small positive
  TP2                  -> moderate positive
  TP3+                 -> strong positive

Confidence formula (weighted blend):
  stat_score  = historical win rate   (stable, long-run)
  rl_score    = EMA of recent rewards (fast-adapting, recency-weighted)
  final       = 0.55 × stat_score + 0.45 × rl_score

EMA decay (α = 0.3):  new_ema = α × reward + (1-α) × old_ema
This gives ~70% weight to the last 3 trades — the engine adapts quickly
to regime changes without forgetting historical performance entirely.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from learning.stats_learner import StatisticalLearner
from learning.setup_ranker import SetupRanker, RankResult
from db.database import Database
from config.settings import (
    BASE_CONFIDENCE, MAX_CONFIDENCE, MIN_CONFIDENCE, MIN_TRADES_FOR_LEARNING,
    ACTIVE_TIMEFRAME_PAIR_LABELS,
    LEARNED_EDGE_DOUBLE_PATTERN_MULTIPLIER,
    LEARNED_EDGE_ENABLED,
    LEARNED_EDGE_LIQUIDITY_MULTIPLIER,
    LEARNED_EDGE_MAX_BONUS,
    LEARNED_EDGE_MAX_PENALTY,
    LEARNED_EDGE_MIN_TRADES,
    LEARNED_EDGE_REWARD_SCALE,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# EMA decay factor — higher = adapts faster to recent trades
EMA_ALPHA = 0.30


class RewardTable:
    """
    Stores EMA-smoothed reward and trade count per state key.
    """

    def __init__(self):
        # key → (ema_reward, total_updates)
        self._table: Dict[str, Tuple[float, int]] = {}

    def update(self, key: str, reward: float):
        ema, n = self._table.get(key, (0.0, 0))
        new_ema = EMA_ALPHA * reward + (1 - EMA_ALPHA) * ema if n > 0 else reward
        self._table[key] = (new_ema, n + 1)

    def get(self, key: str) -> Tuple[float, int]:
        return self._table.get(key, (0.0, 0))

    def items(self):
        return self._table.items()


class LearningEngine:
    """
    Unified engine — combines StatisticalLearner + EMA reward table.
    Called after every trade closes to update all state dimensions.
    """

    def __init__(self, db: Database, stats_learner: StatisticalLearner):
        self._db     = db
        self._stats  = stats_learner
        self._ranker = SetupRanker(db)
        self._primary   = RewardTable()   # (level_type|tf_pair)
        self._secondary = RewardTable()   # (setup_type)
        self._tertiary  = RewardTable()   # (session_name)
        self._strategy  = RewardTable()   # "default"
        self._combo     = RewardTable()   # exact active setup combination
        self._confidence_cache: Dict[str, float] = {}
        self._load_from_db()

    # ─────────────────────────────────────────────────────
    # RESULT PROCESSING — called after every closed trade
    # ─────────────────────────────────────────────────────

    def process_trade_result(
        self,
        level_type: str,
        tf_pair: str,
        result: str,              # "PARTIAL_WIN" | "BREAKEVEN_WIN" | "WIN" | "STRONG_WIN" | "LOSS"
        tps_hit: int = 0,
        setup_type: str = "major",
        session_name: str = "",
        is_qm: bool = False,
        is_psychological: bool = False,
        micro_confirmation_type: str = "",
        bias_gate_result: str = "",
        pd_location: str = "",
        realized_pips: float = 0.0,
        strategy_type: str = "gap_sweep",
    ):
        """
        Update all reward dimensions for a closed trade, then recompute
        the primary confidence score and persist to DB.

        Reward mapping:
          LOSS          -> -3.0  (SL hit before TP1)
          PARTIAL/BE    -> +1.0  (TP1 reached, protected exit)
          WIN           -> +2.0  (TP2 reached)
          STRONG_WIN    -> +3.5  (TP3+ reached)
        Bonuses: +0.50 QM, +0.25 psychological, +0.25 premium session
        """
        if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS:
            logger.info("Learning update skipped: disabled timeframe pair %s", tf_pair)
            return

        # ── Reward shaping ───────────────────────────────────────────
        if result in ("PARTIAL_WIN", "BREAKEVEN_WIN"):
            base      = 1.0
            qm_bonus  = 0.25 if is_qm else 0.0
            psy_bonus = 0.15 if is_psychological else 0.0
            sess_bon  = 0.10 if session_name in ("asia", "london", "new_york", "overlap") else 0.0
            reward = base + qm_bonus + psy_bonus + sess_bon
        elif result == "LOSS":
            reward = -3.0   # SL hit before TP1 protection.
        elif result == "STRONG_WIN":
            base      = 3.5
            qm_bonus  = 0.50 if is_qm else 0.0
            psy_bonus = 0.25 if is_psychological else 0.0
            sess_bon  = 0.25 if session_name in ("asia", "london", "new_york", "overlap") else 0.0
            reward = base + qm_bonus + psy_bonus + sess_bon
        else:
            # WIN (TP2 reached) is positive but smaller than STRONG_WIN.
            base      = 2.0
            qm_bonus  = 0.50 if is_qm else 0.0
            psy_bonus = 0.25 if is_psychological else 0.0
            sess_bon  = 0.25 if session_name in ("asia", "london", "new_york", "overlap") else 0.0
            reward = base + qm_bonus + psy_bonus + sess_bon

        reward = self._apply_micro_reward_multiplier(reward, micro_confirmation_type)
        if realized_pips:
            reward += max(-1.0, min(1.0, float(realized_pips) / 100.0))

        # ── Update all three dimensions ──────────────────────────────
        primary_key   = f"{level_type}|{tf_pair}"
        secondary_key = setup_type
        tertiary_key  = session_name or "off"
        combo_key = self._combo_key(
            level_type=level_type,
            tf_pair=tf_pair,
            micro_confirmation_type=micro_confirmation_type,
            bias_gate_result=bias_gate_result,
            pd_location=pd_location,
            session_name=session_name,
        )

        strategy_key  = strategy_type or ("lsd" if setup_type in ("lsd_swing", "lsd_scalp") else "gap_sweep")

        self._primary.update(primary_key, reward)
        self._secondary.update(secondary_key, reward)
        self._tertiary.update(tertiary_key, reward)
        self._strategy.update(strategy_key, reward)
        self._combo.update(combo_key, reward)

        # ── Recompute and cache primary confidence ───────────────────
        new_score = self._compute_confidence(level_type, tf_pair)
        self._confidence_cache[primary_key] = new_score

        # ── Persist to DB ────────────────────────────────────────────
        ema, n = self._primary.get(primary_key)
        try:
            self._db.upsert_confidence(level_type, tf_pair, new_score, ema)
        except Exception as e:
            logger.error("Failed to persist confidence for %s: %s", primary_key, e)
        try:
            combo_ema, combo_n = self._combo.get(combo_key)
            combo_bonus = self._edge_bonus_from_ema(combo_ema, combo_n)
            persisted_score = round(max(0.0, min(1.0, 0.5 + combo_bonus / 100.0)), 3)
            self._db.upsert_confidence(
                "learned_combo",
                combo_key,
                persisted_score,
                round(combo_ema, 3),
            )
            logger.info(
                "LEARNED COMBO UPDATE: %s | reward=%+.2f ema=%+.2f n=%d edge=%+.1f",
                combo_key,
                reward,
                combo_ema,
                combo_n,
                combo_bonus,
            )
        except Exception as e:
            logger.error("Failed to persist learned combo for %s: %s", combo_key, e)

        # ── Refresh statistical learner and ranker ───────────────────
        try:
            self._stats.refresh()
        except Exception as e:
            logger.warning("Stats refresh failed: %s", e)
        try:
            self._ranker.refresh()
        except Exception as e:
            logger.warning("Ranker refresh failed: %s", e)

        logger.info(
            "Learning update [%s|%s] result=%s setup=%s session=%s "
            "| strategy=%s micro=%s bias_gate=%s pd=%s | reward=%+.2f | ema=%.2f (n=%d) | confidence → %.0f%%",
            level_type, tf_pair, result, setup_type, session_name or "off",
            strategy_key,
            micro_confirmation_type or "unknown",
            bias_gate_result or "unknown",
            pd_location or "unknown",
            reward, ema, n, new_score * 100,
        )

    # ─────────────────────────────────────────────────────
    # CONFIDENCE QUERY
    # ─────────────────────────────────────────────────────

    def get_confidence(self, level_type: str, tf_pair: str) -> float:
        """Return current confidence score for a (level_type, tf_pair)."""
        if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS:
            logger.info("Confidence lookup skipped: disabled timeframe pair %s", tf_pair)
            return BASE_CONFIDENCE
        key = f"{level_type}|{tf_pair}"
        if key in self._confidence_cache:
            return self._confidence_cache[key]
        try:
            score = self._db.get_confidence(level_type, tf_pair)
            self._confidence_cache[key] = score
            return score
        except Exception:
            return BASE_CONFIDENCE

    def get_setup_type_bonus(self, setup_type: str) -> float:
        """
        Return an additional confidence adjustment based on setup_type
        historical performance. Used by multi_timeframe _adjust_confidence.
        """
        ema, n = self._secondary.get(setup_type)
        if n < 3:
            return 0.0
        # Map EMA (typically -1 to +1.6) to a ±0.10 adjustment
        return round(max(-0.10, min(0.10, ema * 0.07)), 3)

    def get_session_bonus(self, session_name: str) -> float:
        """Historical performance bonus/penalty for a trading session."""
        ema, n = self._tertiary.get(session_name or "off")
        if n < 3:
            return 0.0
        return round(max(-0.08, min(0.08, ema * 0.05)), 3)

    def get_rank_result(
        self,
        session: str,
        h4_bias: str,
        direction: str,
        setup_type: str,
        confirmation_type: str,
    ) -> RankResult:
        """
        Return a rank multiplier (0.80–1.20) for a live setup based on historical
        multi-dimensional performance. Safe to call before any trades are closed
        — returns neutral 1.0 when history is insufficient.
        """
        return self._ranker.rank(session, h4_bias, direction, setup_type, confirmation_type)

    def get_learned_edge_bonus(self, setup) -> Tuple[float, str]:
        """Return bounded score bonus/penalty for the exact active setup combination."""
        if not LEARNED_EDGE_ENABLED:
            return 0.0, "learned edge disabled"
        tf_pair = f"{setup.higher_tf}-{setup.lower_tf}"
        key = self._combo_key(
            level_type=getattr(setup.level, "level_type", ""),
            tf_pair=tf_pair,
            micro_confirmation_type=getattr(setup, "micro_confirmation_type", ""),
            bias_gate_result=getattr(setup, "bias_gate_result", ""),
            pd_location=getattr(setup, "pd_location", ""),
            session_name=getattr(setup, "session_name", ""),
        )
        ema, n = self._combo.get(key)
        if n < LEARNED_EDGE_MIN_TRADES:
            return 0.0, f"insufficient learned combo history ({n}/{LEARNED_EDGE_MIN_TRADES})"
        bonus = self._edge_bonus_from_ema(ema, n)
        direction = "BONUS" if bonus >= 0 else "PENALTY"
        logger.info("LEARNED EDGE %s: %s = %+.1f | ema=%+.2f n=%d", direction, key, bonus, ema, n)
        return bonus, key

    def process_replay_trade_dict(self, trade: Dict):
        """Feed one activated historical replay trade into combination learning."""
        tf_pair = str(trade.get("timeframe_pair") or "").replace("->", "-").replace(" ", "")
        self.process_trade_result(
            level_type=trade.get("level_type") or "unknown",
            tf_pair=tf_pair,
            result=trade.get("final_result") or "",
            tps_hit=int(trade.get("tp_progress") or trade.get("tp_progress_reached") or 0),
            setup_type=trade.get("setup_type") or "major",
            session_name=trade.get("session") or trade.get("session_name") or "",
            is_qm=False,
            is_psychological=False,
            micro_confirmation_type=trade.get("micro_confirmation_type") or "",
            bias_gate_result=trade.get("bias_gate_result") or "",
            pd_location=trade.get("pd_location") or "",
            realized_pips=float(trade.get("realized_pips") or trade.get("final_pips") or 0.0),
        )

    def get_strategy_score(self, strategy_name: str) -> Tuple[float, int]:
        """
        Return (score_0_to_1, total_trades) for the named strategy.

        Used by StrategyManager to rank strategies before each scan.
        Neutral score 0.5 returned when fewer than 3 trades exist.
        EMA mapping mirrors _compute_confidence: BASE + ema * 0.18.
        """
        ema, n = self._strategy.get(strategy_name)
        if n < 3:
            return 0.5, n
        score = BASE_CONFIDENCE + (ema * 0.18)
        return round(max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, score)), 3), n

    # ─────────────────────────────────────────────────────
    # CONFIDENCE COMPUTATION
    # ─────────────────────────────────────────────────────

    def _compute_confidence(self, level_type: str, tf_pair: str) -> float:
        """
        Blend historical win rate (stats) with EMA reward (RL).

          stat_score : long-run win rate (stable)
          rl_score   : EMA-smoothed recent reward (adapts to regime)
          final      : 0.55 × stat + 0.45 × rl_score   → clamped
        """
        stats = self._stats.get_stats(level_type, tf_pair)
        ema, n = self._primary.get(f"{level_type}|{tf_pair}")

        # Statistical component
        if stats and stats.total_trades >= MIN_TRADES_FOR_LEARNING:
            stat_score = stats.win_rate
        else:
            stat_score = BASE_CONFIDENCE

        # RL component — map EMA from reward space to [0, 1]
        # EMA of -1.0 → 0.20,  0.0 → 0.50,  +1.6 → 0.80
        if n > 0:
            rl_score = BASE_CONFIDENCE + (ema * 0.18)
            rl_score = max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, rl_score))
        else:
            rl_score = BASE_CONFIDENCE

        final = 0.55 * stat_score + 0.45 * rl_score
        return round(max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, final)), 3)

    @staticmethod
    def _combo_key(
        level_type: str,
        tf_pair: str,
        micro_confirmation_type: str,
        bias_gate_result: str,
        pd_location: str,
        session_name: str,
    ) -> str:
        return "|".join(
            [
                level_type or "unknown",
                tf_pair or "unknown",
                micro_confirmation_type or "none",
                bias_gate_result or "unknown",
                pd_location or "unknown",
                session_name or "off",
            ]
        )

    @staticmethod
    def _apply_micro_reward_multiplier(reward: float, micro_confirmation_type: str) -> float:
        if reward <= 0:
            return reward
        micro = micro_confirmation_type or "none"
        if micro == "liquidity_sweep_reclaim":
            return reward * LEARNED_EDGE_LIQUIDITY_MULTIPLIER
        if micro == "double_pattern":
            return reward * LEARNED_EDGE_DOUBLE_PATTERN_MULTIPLIER
        return reward

    @staticmethod
    def _edge_bonus_from_ema(ema: float, n: int) -> float:
        if n < LEARNED_EDGE_MIN_TRADES:
            return 0.0
        raw = ema * LEARNED_EDGE_REWARD_SCALE
        return round(max(-LEARNED_EDGE_MAX_PENALTY, min(LEARNED_EDGE_MAX_BONUS, raw)), 1)

    # ─────────────────────────────────────────────────────
    # STARTUP / PERSISTENCE
    # ─────────────────────────────────────────────────────

    def _load_from_db(self):
        """Restore cached confidence scores from DB on startup."""
        try:
            self._stats.refresh()
            for stats in self._stats.get_all_stats():
                key = f"{stats.level_type}|{stats.tf_pair}"
                score = self._db.get_confidence(stats.level_type, stats.tf_pair)
                self._confidence_cache[key] = score
            logger.info("LearningEngine loaded %d confidence states.",
                        len(self._confidence_cache))
        except Exception as e:
            logger.warning("Could not load learning state from DB: %s", e)
        try:
            self._ranker.refresh()
        except Exception as e:
            logger.warning("Could not load ranker state from DB: %s", e)
        try:
            loaded = 0
            for row in self._db.get_learned_combo_scores():
                key = row.get("tf_pair")
                if not key:
                    continue
                ema = float(row.get("reward_total") or 0.0)
                self._combo._table[str(key)] = (ema, LEARNED_EDGE_MIN_TRADES)
                loaded += 1
            if loaded:
                logger.info("LearningEngine loaded %d learned combo state(s).", loaded)
        except Exception as e:
            logger.warning("Could not load learned combo state from DB: %s", e)

    # ─────────────────────────────────────────────────────
    # REPORTING
    # ─────────────────────────────────────────────────────

    def get_full_report(self) -> str:
        lines = ["📊 *AlphaPulse Learning Report*\n"]
        all_stats = self._stats.get_leaderboard()

        if not all_stats:
            return "📊 Not enough data yet (minimum 5 trades per setup type)."

        for s in all_stats:
            score = self.get_confidence(s.level_type, s.tf_pair)
            bar   = "█" * round(score * 10) + "░" * (10 - round(score * 10))
            lines.append(
                f"• `{s.level_type}` | `{s.tf_pair}` — "
                f"WR: `{s.win_rate*100:.0f}%` | "
                f"Score: `[{bar}]` `{score*100:.0f}%` "
                f"({s.wins}W/{s.losses}L)"
            )

        # Setup type performance
        lines.append("\n*Setup Type Performance:*")
        for stype in ("qm_level", "psychological_confluence", "recent_leg",
                      "previous_leg", "major"):
            ema, n = self._secondary.get(stype)
            if n >= 2:
                sentiment = "✅" if ema > 0 else "❌"
                lines.append(f"  {sentiment} `{stype}`: EMA={ema:+.2f} ({n} trades)")

        # Session performance
        lines.append("\n*Session Performance:*")
        for sess in ("overlap", "asia", "london", "new_york", "off_session"):
            ema, n = self._tertiary.get(sess)
            if n >= 2:
                sentiment = "✅" if ema > 0 else "❌"
                lines.append(f"  {sentiment} `{sess}`: EMA={ema:+.2f} ({n} trades)")

        lines.append(f"\n🏆 Best: _{self._stats.get_best_setup_str()}_")
        return "\n".join(lines)
