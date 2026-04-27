"""
AlphaPulse — Manual Setup Alert Test
=====================================
Sends a dry-run Telegram test alert for all three manual-setup alert types.

Usage:
    python -m tools.test_manual_setup_alert
    python -m tools.test_manual_setup_alert --type saved
    python -m tools.test_manual_setup_alert --type approaching
    python -m tools.test_manual_setup_alert --type confirmed
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

_DUMMY_SETUP = {
    "id": 9999,
    "symbol": "XAUUSD",
    "direction": "BUY",
    "entry_price": 3250.00,
    "stop_loss": 3238.50,
    "tp1": 3265.00,
    "tp2": 3278.00,
    "tp3": 3295.00,
    "session": "london",
    "notes": "[DRY RUN] Test alert from tools.test_manual_setup_alert",
    "enable_telegram_alerts": True,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test Telegram alert for manual setups.")
    parser.add_argument(
        "--type",
        choices=["saved", "approaching", "confirmed", "all"],
        default="all",
        help="Which alert type to test (default: all)",
    )
    args = parser.parse_args()

    from notifications.telegram_bot import TelegramBot
    tg = TelegramBot()

    results: list[tuple[str, bool]] = []

    if args.type in ("saved", "all"):
        ok = tg.send_manual_setup_saved(_DUMMY_SETUP)
        results.append(("MANUAL SETUP SAVED", ok))

    if args.type in ("approaching", "all"):
        ok = tg.send_manual_setup_approaching(_DUMMY_SETUP, current_price=3258.40, distance_pips=8.4)
        results.append(("MANUAL SETUP APPROACHING", ok))

    if args.type in ("confirmed", "all"):
        ok = tg.send_manual_setup_confirmed(
            _DUMMY_SETUP, current_price=3250.30, confirmation="liquidity_sweep_reclaim"
        )
        results.append(("MANUAL SETUP CONFIRMED", ok))

    print("\n── Test Results ──────────────────────────────")
    all_ok = True
    for label, sent in results:
        status = "OK" if sent else "FAILED"
        print(f"  {label:<30} {status}")
        if not sent:
            all_ok = False
    print("──────────────────────────────────────────────")
    if all_ok:
        print("All alerts sent successfully.")
    else:
        print("One or more alerts failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        sys.exit(1)


if __name__ == "__main__":
    main()
