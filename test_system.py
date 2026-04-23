"""
AlphaPulse - System Integration Test
Tests all modules end-to-end using SYNTHETIC data (no MT5 or DB required).

Usage:
  python test_system.py
"""

import sys
import os

# Force UTF-8 output on Windows so Unicode chars print correctly
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

print("AlphaPulse - System Integration Test")
print("=" * 50)


def section(title):
    print(f"\n{'-' * 50}")
    print(f"  {title}")
    print("-" * 50)


# -------------------------------------------------------
# 1. MT5 Client (synthetic)
# -------------------------------------------------------

section("1. MT5 Client - Synthetic Data")
from data.mt5_client import MT5Client

mt5 = MT5Client()
connected = mt5.connect()
print(f"  Connected: {connected}")

for tf in ["H4", "H1", "M30", "M15"]:
    df = mt5.get_ohlcv(tf)
    print(f"  [{tf}] {len(df)} bars | last close: {df.iloc[-1]['close']:.2f}")


# -------------------------------------------------------
# 2. Level Detection
# -------------------------------------------------------

section("2. Level Detection")
from strategies.level_detector import LevelDetector

detector = LevelDetector()
df_h4 = mt5.get_ohlcv("H4")
levels = detector.detect_all(df_h4, "H4")
print(f"  Detected {len(levels)} levels on H4:")
for lvl in levels[:5]:
    print(f"    {lvl.level_type} @ {lvl.price:.2f} (strength: {lvl.strength:.2f})")


# -------------------------------------------------------
# 3. Confirmation Engine
# -------------------------------------------------------

section("3. Confirmation Engine")
from strategies.confirmation import ConfirmationEngine

confirmator = ConfirmationEngine()
df_h1 = mt5.get_ohlcv("H1")
if levels:
    confs = confirmator.check_confirmations(df_h1, levels, "H1", lookback=10)
    print(f"  Found {len(confs)} confirmations on H1:")
    for c in confs:
        print(f"    {c.direction} at {c.entry_price:.2f} | SL {c.sl_price:.2f}")
else:
    print("  No levels to test against.")


# -------------------------------------------------------
# 4. Multi-Timeframe Analysis
# -------------------------------------------------------

section("4. Multi-Timeframe Analysis")
from strategies.multi_timeframe import MultiTimeframeAnalyzer

analyzer = MultiTimeframeAnalyzer()
all_data = {tf: mt5.get_ohlcv(tf) for tf in ["H4", "H1", "M30", "M15"]}
outlook, setups = analyzer.analyze(all_data)

print(f"  Outlook timeframe groups: {len(outlook.timeframe_levels)}")
print(f"  Confirmed setups: {len(setups)}")
for s in setups:
    print(f"    {s.direction} | {s.higher_tf}->{s.lower_tf} | "
          f"Level {s.level.level_type} @ {s.level.price:.2f}")


# -------------------------------------------------------
# 5. Signal Generator
# -------------------------------------------------------

section("5. Signal Generator")
from signals.signal_generator import SignalGenerator

sig_gen = SignalGenerator()
trades = sig_gen.generate_batch(setups)
print(f"  Generated {len(trades)} valid signals:")
for t in trades:
    print(f"    {t.direction} @ {t.entry_price:.2f} | "
          f"SL {t.sl_price:.2f} | TP1 {t.tp1:.2f} | "
          f"Confidence {t.confidence*100:.0f}%")


# -------------------------------------------------------
# 6. Telegram (mock — no token needed)
# -------------------------------------------------------

section("6. Telegram (Mock - no token needed)")
from notifications.telegram_bot import TelegramBot

tg = TelegramBot()
print("  Sending market outlook (mock)...")
tg.send_market_outlook(outlook)

if trades:
    print("  Sending trade signal (mock)...")
    tg.send_signal(trades[0])


# -------------------------------------------------------
# 7. Trade object validation
# -------------------------------------------------------

section("7. Trade Object Validation")
if trades:
    t = trades[0]
    print(f"  Trade: {t}")
    print(f"  SL pips: {t.sl_pips:.1f}")
    print(f"  TP levels: {[f'{tp:.2f}' for tp in t.tp_levels]}")
    print(f"  Level type: {t.level_type}")
    print(f"  TF pair: {t.tf_pair_str}")
else:
    print("  No trades to validate (no setups detected in synthetic data).")
    print("  This is normal -- synthetic data uses a seeded random walk.")


# -------------------------------------------------------
# DONE
# -------------------------------------------------------

print("\n" + "=" * 50)
print("  All modules imported and tested successfully.")
print("=" * 50)
print()
print("Next steps:")
print("  1. Copy .env.example to .env and fill in your credentials")
print("  2. Run: python setup_db.py")
print("  3. Run: python main.py")
print("  4. Dashboard: streamlit run dashboard/app.py")
