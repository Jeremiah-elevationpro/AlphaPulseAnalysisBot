"""Spencer Core Strategy Engine — 8 spec tests.

Run:
    python -m tests.test_core_strategy_engine
or
    python -m pytest tests/test_core_strategy_engine.py

These tests focus on STRATEGY RULES — what gates a candidate as actionable,
not strategy hit rate. Hit rate is a backtest concern (see
historical_replay.run_core_strategy_replay).
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Allow direct invocation without installing
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.core_strategy_engine import (
    ALLOWED_BUY_CONFIRMATIONS,
    ALLOWED_SELL_CONFIRMATIONS,
    CORE_ALLOWED_STRATEGY_TYPES,
    CoreStrategyEngine,
    DISABLED_STANDALONE_CONFIRMATIONS,
    StrategySetup,
    validate_setup_risk,
)


def _make_bar(t: datetime, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "tick_volume": v}


def _to_df(bars: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def _flat_bars(start: datetime, count: int, base: float, step_minutes: int = 15) -> list[dict]:
    """Generate flat-range candles."""
    out: list[dict] = []
    for i in range(count):
        t = start + timedelta(minutes=step_minutes * i)
        out.append(_make_bar(t, base, base + 0.5, base - 0.5, base + 0.1))
    return out


class TestCoreStrategyEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = CoreStrategyEngine()
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    # ──────────────────────────────────────────────────────────────────────
    # Test 1: Raw QM setup appears.
    # Expected: Not actionable by itself. Only confluence.
    # ──────────────────────────────────────────────────────────────────────
    def test_1_raw_qm_not_actionable_alone(self):
        """raw_qm_confirmation is in the disabled-standalone set."""
        self.assertIn("raw_qm_confirmation", DISABLED_STANDALONE_CONFIRMATIONS)
        # Verify the allowed sets do NOT include raw QM
        self.assertNotIn("raw_qm_confirmation", ALLOWED_BUY_CONFIRMATIONS)
        self.assertNotIn("raw_qm_confirmation", ALLOWED_SELL_CONFIRMATIONS)
        print("[TEST 1] PASS — raw QM cannot trigger an actionable setup alone")

    # ──────────────────────────────────────────────────────────────────────
    # Test 2: Raw gap imbalance appears.
    # Expected: Not actionable by itself. Only confluence.
    # ──────────────────────────────────────────────────────────────────────
    def test_2_raw_gap_not_actionable_alone(self):
        self.assertIn("raw_gap_confirmation", DISABLED_STANDALONE_CONFIRMATIONS)
        self.assertNotIn("raw_gap_confirmation", ALLOWED_BUY_CONFIRMATIONS)
        self.assertNotIn("raw_gap_confirmation", ALLOWED_SELL_CONFIRMATIONS)
        print("[TEST 2] PASS — raw gap cannot trigger an actionable setup alone")

    # ──────────────────────────────────────────────────────────────────────
    # Test 3: Asian high swept and closes back below.
    # Expected: Session Liquidity Sweep Reversal SELL candidate.
    # ──────────────────────────────────────────────────────────────────────
    def test_3_asian_high_sweep_sell(self):
        # 12 flat M15 bars near 2050, then one bar that wicks above 2055
        # (asian high) and closes back to 2049.
        base = 2050.0
        asian_high = 2055.0
        bars = _flat_bars(self.now, 11, base)
        # add the sweep bar: wick to 2056, close at 2048 (clean sweep + close back below)
        t_sweep = self.now + timedelta(minutes=15 * 11)
        bars.append(_make_bar(t_sweep, 2050.5, 2056.0, 2047.5, 2048.5))
        m15 = _to_df(bars)
        # H1 just mirrors the M15 base — not used by this strategy
        data = {"M15": m15}
        plan = {
            "session_liquidity": {
                "sessions": {
                    # Sell-side levels positioned to give the SELL sweep a
                    # meaningful TP1 (must satisfy CORE_MIN_TP1_RR ≥ 0.8 with
                    # SL ≈ sweep_high + 5p above 2056).
                    "asia":     {"high": asian_high, "low": 2030.0},
                    "london":   {"high": 2058.0,     "low": 2035.0},
                    "new_york": {"high": 2060.0,     "low": 2040.0},
                }
            }
        }
        result = self.engine.run(data, current_price=2048.5, plan=plan)
        sell_candidates = [
            c for c in result.candidates
            if c.strategy_type == "session_liquidity_sweep_reversal"
            and c.direction == "SELL"
        ]
        self.assertTrue(
            len(sell_candidates) >= 1,
            f"Expected SELL session liquidity sweep candidate; got {result.candidates}",
        )
        print(f"[TEST 3] PASS — Asian high sweep produced {len(sell_candidates)} SELL candidate(s)")

    # ──────────────────────────────────────────────────────────────────────
    # Test 4: Demand zone retested and bullish rejection forms.
    # Expected: Supply/Demand Retest BUY candidate.
    # ──────────────────────────────────────────────────────────────────────
    def test_4_demand_retest_buy(self):
        # H1: 10 bars where bar 1 is a strong bullish displacement out of a base.
        # Demand base sits at 2042-2046 (4-pip range) so SL below demand is
        # far enough from entry to satisfy CORE_MIN_RISK_PIPS (5p) and TP1 RR.
        h1_bars = []
        t0 = self.now - timedelta(hours=15)
        # base candle — deeper so SL has room
        h1_bars.append(_make_bar(t0, 2044.0, 2046.0, 2042.0, 2045.0))
        # displacement: ~30 pip bullish body away from the base
        h1_bars.append(_make_bar(t0 + timedelta(hours=1), 2045.0, 2049.0, 2045.0, 2048.0))
        # subsequent flats above the base
        for k in range(2, 10):
            t = t0 + timedelta(hours=k)
            h1_bars.append(_make_bar(t, 2054.0, 2055.0, 2053.5, 2054.5))
        h1 = _to_df(h1_bars)

        # M15: prior flats above demand, then the latest bar wicks into the
        # 2042-2046 demand zone (low=2042.5) and closes bullish above (2050.5).
        m15_bars = _flat_bars(self.now - timedelta(hours=2), 7, 2054.0)
        t_last = self.now
        m15_bars.append(_make_bar(t_last, 2046.5, 2052.0, 2042.5, 2050.5))
        m15 = _to_df(m15_bars)
        data = {"H1": h1, "M15": m15}
        result = self.engine.run(data, current_price=2050.5, plan=None)
        buys = [
            c for c in result.candidates
            if c.strategy_type == "supply_demand_retest" and c.direction == "BUY"
        ]
        self.assertTrue(
            len(buys) >= 1,
            f"Expected BUY supply/demand retest candidate; got {result.candidates}",
        )
        print(f"[TEST 4] PASS — Demand retest produced {len(buys)} BUY candidate(s)")

    # ──────────────────────────────────────────────────────────────────────
    # Test 5: Resistance breaks, price retests as support, bullish confirmation forms.
    # Expected: Break and Retest Continuation BUY candidate.
    # ──────────────────────────────────────────────────────────────────────
    def test_5_break_retest_continuation_buy(self):
        resistance = 2055.0
        # M15: pre-break range, then a strong bullish break above resistance,
        # then a retest pullback, then a clean bullish close.
        m15_bars = []
        t = self.now - timedelta(hours=12)
        # 20 flat bars below resistance
        for i in range(20):
            m15_bars.append(_make_bar(t + timedelta(minutes=15 * i), 2052.0, 2054.5, 2050.5, 2053.0))
        # break bar: 30+ pip bullish close above 2055.5
        t_break = t + timedelta(minutes=15 * 20)
        m15_bars.append(_make_bar(t_break, 2053.0, 2059.5, 2052.5, 2059.0))
        # a few continuation bars
        for i in range(21, 25):
            m15_bars.append(_make_bar(t + timedelta(minutes=15 * i), 2059.0, 2061.0, 2058.0, 2060.0))
        # retest: pull back to 2055.5 with low 2054.7
        t_retest = t + timedelta(minutes=15 * 25)
        m15_bars.append(_make_bar(t_retest, 2059.0, 2059.5, 2054.7, 2056.5))
        # one or two recovery bars
        m15_bars.append(_make_bar(t + timedelta(minutes=15 * 26), 2056.5, 2058.5, 2056.0, 2058.0))
        # confirmation bar: bullish, closes well above resistance + buffer
        m15_bars.append(_make_bar(t + timedelta(minutes=15 * 27), 2058.0, 2062.5, 2057.5, 2062.0))
        m15 = _to_df(m15_bars)
        data = {"M15": m15}
        # Provide resistance via plan so strategy finds it without swing-high detection
        plan = {"key_resistances": [resistance]}
        result = self.engine.run(data, current_price=2062.0, plan=plan)
        candidates = [
            c for c in result.candidates
            if c.strategy_type == "break_retest_continuation" and c.direction == "BUY"
        ]
        self.assertTrue(
            len(candidates) >= 1,
            f"Expected BUY break+retest continuation; got candidates={result.candidates} "
            f"rejected={[(r.strategy_type, r.reason) for r in result.rejected[:6]]}",
        )
        print(f"[TEST 5] PASS — Break+retest produced {len(candidates)} BUY candidate(s)")

    # ──────────────────────────────────────────────────────────────────────
    # Test 6: Psychological level touched with no strategy confirmation.
    # Expected: No actionable setup.
    # ──────────────────────────────────────────────────────────────────────
    def test_6_psych_level_touch_alone_no_setup(self):
        # 12 flat M15 bars where last bar barely touches 2000 (psych level)
        # but no zone / sweep / break-retest pattern.
        bars = _flat_bars(self.now, 11, 2000.5)
        bars.append(_make_bar(self.now + timedelta(minutes=15 * 11), 2000.5, 2000.7, 1999.9, 2000.4))
        m15 = _to_df(bars)
        result = self.engine.run({"M15": m15}, current_price=2000.4, plan=None)
        self.assertEqual(
            len(result.candidates), 0,
            f"Expected zero candidates from a lone psych touch; got {result.candidates}",
        )
        self.assertIn(
            "psychological_level_touch", DISABLED_STANDALONE_CONFIRMATIONS
        )
        print("[TEST 6] PASS — Psych level touch alone produces no actionable setup")

    # ──────────────────────────────────────────────────────────────────────
    # Test 7: Invalid SL equals entry.
    # Expected: Candidate rejected by validate_setup_risk.
    # ──────────────────────────────────────────────────────────────────────
    def test_7_invalid_sl_equals_entry_rejected(self):
        bad = StrategySetup(
            strategy_type="break_retest_continuation",
            symbol="XAUUSD",
            direction="BUY",
            entry_zone_low=2050.0,
            entry_zone_high=2052.0,
            trigger_level=2050.0,
            confirmation_required=["break_retest_close_above"],
            entry=2052.0,
            sl=2052.0,                # same as entry → invalid
            tp1=2055.0,
            tp2=2058.0,
            tp3=2061.0,
            invalidation=2050.0,
            reason="test_invalid_sl",
            confidence_internal=0.0,
        )
        ok, why = validate_setup_risk(bad)
        self.assertFalse(ok)
        self.assertTrue("risk_too_small" in why or "sl_not_below_entry" in why,
                        f"Unexpected reason: {why}")
        print(f"[TEST 7] PASS — entry==SL rejected with reason '{why}'")

    # ──────────────────────────────────────────────────────────────────────
    # Test 8: No valid setup.
    # Expected: No Telegram setup alert, dashboard shows waiting reason.
    # We assert: result has no primary, candidates_count == 0.
    # ──────────────────────────────────────────────────────────────────────
    def test_8_no_valid_setup_silent(self):
        # Pure flat data — no zones, no sweeps, no breaks.
        m15 = _to_df(_flat_bars(self.now, 30, 2055.0))
        result = self.engine.run({"M15": m15}, current_price=2055.1, plan=None)
        self.assertIsNone(result.primary)
        self.assertEqual(len(result.candidates), 0)
        # Result must serialize cleanly for dashboard
        d = result.to_dict()
        self.assertIsNone(d["primary"])
        self.assertEqual(d["candidates_count"], 0)
        print("[TEST 8] PASS — silent when no valid setup; dashboard payload safe")


def main():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCoreStrategyEngine)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
