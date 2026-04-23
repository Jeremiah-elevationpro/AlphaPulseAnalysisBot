"""
AlphaPulse - Streamlit Dashboard
===================================
Real-time monitoring dashboard for active trades, daily stats, and learning insights.

Run with:
  streamlit run dashboard/app.py
"""

import sys
import os

# Allow imports from project root when running from dashboard/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
from datetime import datetime, timezone
from typing import List

import streamlit as st
import pandas as pd

from config.settings import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, USE_SUPABASE
)

# ─────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AlphaPulse — XAUUSD",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────
# DB CONNECTION (cached)
# ─────────────────────────────────────────────────────────

@st.cache_resource
def get_db():
    from db.database import Database
    db = Database()
    try:
        db.init()
    except Exception as e:
        st.warning(f"Database not connected: {e}. Running in demo mode.")
        return None
    return db


db = get_db()


# ─────────────────────────────────────────────────────────
# DEMO DATA (when DB not available)
# ─────────────────────────────────────────────────────────

def demo_active_trades():
    return [
        {
            "uuid": "abc123xx",
            "pair": "XAUUSD",
            "direction": "SELL",
            "entry": 3248.50,
            "sl": 3251.50,
            "tp1": 3245.00, "tp2": 3240.00, "tp3": 3235.00, "tp4": 3230.00, "tp5": 3225.00,
            "tp_hit": [True, False, False, False, False],
            "status": "TP1_HIT",
            "level_type": "A",
            "tf": "H1→M30",
            "confidence": 0.72,
            "created": "2026-04-14 08:30",
        },
        {
            "uuid": "def456yy",
            "pair": "XAUUSD",
            "direction": "BUY",
            "entry": 3220.00,
            "sl": 3217.00,
            "tp1": 3224.20, "tp2": 3228.50, "tp3": 3232.75, "tp4": 3237.00, "tp5": 3241.25,
            "tp_hit": [False, False, False, False, False],
            "status": "PENDING",
            "level_type": "V",
            "tf": "H4→H1",
            "confidence": 0.58,
            "created": "2026-04-14 10:15",
        },
    ]


def demo_daily_stats():
    return {"total_setups": 6, "activated": 4, "wins": 3, "losses": 1, "win_rate": 75.0}


def demo_perf_stats():
    return pd.DataFrame([
        {"Level Type": "A", "TF Pair": "H1-M30", "Wins": 8, "Losses": 2, "Win Rate": "80%", "Reward": 6.4},
        {"Level Type": "Gap", "TF Pair": "H4-H1", "Wins": 5, "Losses": 3, "Win Rate": "63%", "Reward": 2.0},
        {"Level Type": "V", "TF Pair": "M30-M15", "Wins": 4, "Losses": 4, "Win Rate": "50%", "Reward": 0.0},
    ])


# ─────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://via.placeholder.com/200x60/1a1a2e/ffffff?text=AlphaPulse", width=200)
    st.markdown("### ⚙️ Settings")
    auto_refresh = st.toggle("Auto-refresh (30s)", value=True)
    if st.button("🔄 Refresh Now"):
        st.rerun()

    st.divider()
    st.markdown(f"""
    **Symbol:** `XAUUSD`
    **DB:** `{'Supabase' if USE_SUPABASE else 'PostgreSQL'}`
    **Updated:** `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`
    """)

    st.divider()
    st.markdown("### 📖 Legend")
    st.markdown("""
    - 🔴 **A-Level** — Resistance
    - 🟢 **V-Level** — Support
    - 🟡 **Gap** — Imbalance
    - ✅ TP Hit
    - ⏳ TP Pending
    """)


# ─────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────

col_title, col_status = st.columns([4, 1])
with col_title:
    st.title("📊 AlphaPulse — XAUUSD Monitor")
with col_status:
    st.success("● LIVE" if db else "● DEMO")

st.divider()

# ─────────────────────────────────────────────────────────
# SECTION 1: DAILY STATS
# ─────────────────────────────────────────────────────────

st.subheader("📅 Today's Performance")

if db:
    try:
        today_rows = db.get_today_trades()
        wins = sum(
            1 for r in today_rows
            if isinstance(r, tuple) and len(r) > 14 and r[14] in ("WIN", "STRONG_WIN")
        )
        losses = sum(1 for r in today_rows if isinstance(r, tuple) and len(r) > 14 and r[14] == "LOSS")
        activated = sum(1 for r in today_rows
                       if isinstance(r, tuple) and len(r) > 12 and r[12] not in ("PENDING", "CANCELLED"))
        total = len(today_rows)
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
        stats = {"total_setups": total, "activated": activated, "wins": wins, "losses": losses, "win_rate": win_rate}
    except Exception:
        stats = demo_daily_stats()
else:
    stats = demo_daily_stats()

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("📊 Total Setups", stats["total_setups"])
m2.metric("▶️ Activated", stats["activated"])
m3.metric("✅ Wins", stats["wins"])
m4.metric("❌ Losses", stats["losses"])
m5.metric("📈 Win Rate", f"{stats['win_rate']:.1f}%",
          delta=f"{stats['win_rate'] - 50:.1f}% vs random")

st.divider()

# ─────────────────────────────────────────────────────────
# SECTION 2: ACTIVE TRADES
# ─────────────────────────────────────────────────────────

st.subheader("🔥 Active & Pending Trades")

if db:
    try:
        active_rows = db.get_active_trades()
        trades_display = []
        for row in active_rows:
            if not isinstance(row, tuple) or len(row) < 17:
                continue
            tp_hit = list(row[11]) if row[11] else [False] * 5
            tp_levels = [row[6], row[7], row[8], row[9], row[10]]
            trades_display.append({
                "uuid": str(row[1])[:8],
                "pair": row[2],
                "direction": row[3],
                "entry": float(row[4]),
                "sl": float(row[5]),
                "tp1": tp_levels[0], "tp2": tp_levels[1], "tp3": tp_levels[2],
                "tp4": tp_levels[3], "tp5": tp_levels[4],
                "tp_hit": tp_hit,
                "status": row[12],
                "level_type": row[13] or "",
                "tf": f"{row[15] or ''}→{row[16] or ''}",
                "confidence": float(row[17]) if row[17] else 0.5,
                "created": str(row[18])[:16] if row[18] else "",
            })
    except Exception as e:
        trades_display = demo_active_trades()
else:
    trades_display = demo_active_trades()

if not trades_display:
    st.info("No active trades at the moment. Scanning for setups...")
else:
    for trade in trades_display:
        direction_icon = "📈" if trade["direction"] == "BUY" else "📉"
        level_icon = {"A": "🔴", "V": "🟢", "Gap": "🟡"}.get(trade["level_type"], "⚪")

        with st.expander(
            f"{direction_icon} {trade['direction']} XAUUSD | Entry: {trade['entry']:.2f} "
            f"| {level_icon} {trade['level_type']}-Level | {trade['tf']} "
            f"| Status: {trade['status']}",
            expanded=True,
        ):
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown("**Trade Details**")
                st.markdown(f"- 🆔 `{trade['uuid']}`")
                st.markdown(f"- 📐 Timeframe: `{trade['tf']}`")
                st.markdown(f"- 🕐 Created: `{trade['created']}`")
                st.markdown(f"- 📊 Confidence: `{trade['confidence']*100:.0f}%`")

            with col2:
                st.markdown("**Price Levels**")
                st.markdown(f"- 🎯 Entry: `{trade['entry']:.2f}`")
                st.markdown(f"- 🛑 Stop Loss: `{trade['sl']:.2f}`")

            with col3:
                st.markdown("**TP Progress**")
                tp_values = [trade.get(f"tp{i+1}") for i in range(5)]
                for i, (tp_val, hit) in enumerate(zip(tp_values, trade["tp_hit"])):
                    if tp_val:
                        icon = "✅" if hit else "⏳"
                        st.markdown(f"- {icon} TP{i+1}: `{float(tp_val):.2f}`")

            # Progress bar
            hits = sum(trade["tp_hit"])
            st.progress(hits / 5, text=f"TP Progress: {hits}/5")

st.divider()

# ─────────────────────────────────────────────────────────
# SECTION 3: PERFORMANCE BY SETUP TYPE
# ─────────────────────────────────────────────────────────

st.subheader("🧠 Learning — Setup Performance")

if db:
    try:
        perf_rows = db.get_all_performance()
        if perf_rows:
            perf_data = []
            for row in perf_rows:
                if isinstance(row, tuple) and len(row) >= 7:
                    perf_data.append({
                        "Level Type": row[1] or "",
                        "TF Pair": row[2] or "",
                        "Wins": int(row[3] or 0),
                        "Losses": int(row[4] or 0),
                        "Total": int(row[5] or 0),
                        "Win Rate": f"{float(row[6] or 0)*100:.0f}%",
                        "Reward": float(row[7] or 0),
                    })
            perf_df = pd.DataFrame(perf_data) if perf_data else demo_perf_stats()
        else:
            perf_df = demo_perf_stats()
    except Exception:
        perf_df = demo_perf_stats()
else:
    perf_df = demo_perf_stats()

col_perf, col_chart = st.columns([2, 1])
with col_perf:
    st.dataframe(perf_df, use_container_width=True, hide_index=True)

with col_chart:
    if "Wins" in perf_df.columns and "Losses" in perf_df.columns:
        chart_df = perf_df[["Level Type", "Wins", "Losses"]].set_index("Level Type")
        st.bar_chart(chart_df)

st.divider()

# ─────────────────────────────────────────────────────────
# SECTION 4: TRADE HISTORY
# ─────────────────────────────────────────────────────────

st.subheader("📜 Closed Trade History")

if db:
    try:
        closed_rows = db.get_all_closed_trades()
        history = []
        for row in closed_rows[:50]:  # limit to last 50
            if isinstance(row, tuple) and len(row) >= 17:
                tp_hit = list(row[11]) if row[11] else [False] * 5
                history.append({
                    "ID": str(row[1])[:8],
                    "Dir": row[3],
                    "Entry": f"{float(row[4]):.2f}",
                    "SL": f"{float(row[5]):.2f}",
                    "TP1": f"{float(row[6]):.2f}" if row[6] else "—",
                    "TPs Hit": f"{sum(tp_hit)}/5",
                    "Level": row[13] or "—",
                    "TF": f"{row[15] or ''}→{row[16] or ''}",
                    "Result": row[14] or "—",
                    "Status": row[12],
                    "Closed": str(row[20])[:16] if len(row) > 20 and row[20] else "—",
                })
        if history:
            hist_df = pd.DataFrame(history)
            # Colour result column
            def colour_result(val):
                if val == "STRONG_WIN":
                    return "background-color: #0d3b1e; color: #00ff88"
                elif val == "WIN":
                    return "background-color: #1a472a; color: #90ee90"
                elif val == "LOSS":
                    return "background-color: #4a1a1a; color: #ff9999"
                return ""
            st.dataframe(
                hist_df.style.applymap(colour_result, subset=["Result"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No closed trades yet.")
    except Exception as e:
        st.warning(f"Could not load history: {e}")
else:
    st.info("Connect to database to see trade history.")

st.divider()

# ─────────────────────────────────────────────────────────
# SECTION 5: DAILY SUMMARY CHART
# ─────────────────────────────────────────────────────────

st.subheader("📈 30-Day Win Rate Trend")

if db:
    try:
        summaries = db.get_daily_summaries(30)
        if summaries:
            dates, win_rates = [], []
            for row in reversed(summaries):
                if isinstance(row, tuple) and len(row) >= 6:
                    dates.append(str(row[1]))
                    win_rates.append(float(row[5] or 0) * 100)
            if dates:
                trend_df = pd.DataFrame({"Date": dates, "Win Rate (%)": win_rates})
                trend_df = trend_df.set_index("Date")
                st.line_chart(trend_df)
            else:
                st.info("Not enough historical data yet.")
        else:
            st.info("No daily summaries available.")
    except Exception:
        st.info("Database not connected.")
else:
    # Show demo trend
    import numpy as np
    demo_dates = pd.date_range("2026-03-01", periods=30, freq="D")
    demo_rates = 50 + np.cumsum(np.random.normal(0.5, 5, 30))
    demo_rates = np.clip(demo_rates, 0, 100)
    demo_df = pd.DataFrame({"Win Rate (%)": demo_rates}, index=demo_dates)
    st.line_chart(demo_df)

# ─────────────────────────────────────────────────────────
# AUTO REFRESH
# ─────────────────────────────────────────────────────────

if auto_refresh:
    time.sleep(30)
    st.rerun()
