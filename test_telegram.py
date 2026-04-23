"""
AlphaPulse - Telegram Group Diagnostics
Run this to find the correct chat ID and test the bot connection.

Usage:  python test_telegram.py
"""

import requests

TOKEN = "8423015469:AAETTIiz9ydz83aMOECVFAwpTFVbTYhfrE8"
BASE  = f"https://api.telegram.org/bot{TOKEN}"

# ── 1. Verify bot is alive ────────────────────────────────────────────────────
print("=" * 55)
print("  AlphaPulse — Telegram Diagnostics")
print("=" * 55)

r = requests.get(f"{BASE}/getMe", timeout=10)
if r.status_code != 200:
    print(f"[ERROR] Bot token invalid or network issue: {r.text}")
    exit(1)

bot_info = r.json()["result"]
print(f"\n✅ Bot online:  @{bot_info['username']}  (id={bot_info['id']})")

# ── 2. Fetch recent updates to discover the actual group chat ID ──────────────
print("\n🔍 Checking recent messages the bot can see...")
r = requests.get(f"{BASE}/getUpdates", params={"limit": 50, "timeout": 5}, timeout=15)
updates = r.json().get("result", [])

chats_seen = {}
for upd in updates:
    for key in ("message", "channel_post", "my_chat_member", "chat_member"):
        msg = upd.get(key)
        if msg:
            chat = msg.get("chat", {})
            cid  = chat.get("id")
            ctype = chat.get("type", "?")
            ctitle = chat.get("title") or chat.get("username") or chat.get("first_name", "?")
            if cid:
                chats_seen[cid] = {"type": ctype, "title": ctitle}

if chats_seen:
    print("\n📋 Chats visible to the bot:")
    for cid, info in chats_seen.items():
        print(f"   Chat ID: {cid}  | Type: {info['type']}  | Name: {info['title']}")
else:
    print("   (No recent messages found — make sure the bot is in the group")
    print("    and that someone sent a message there recently.)")

# ── 3. Try sending a test message to all common ID variants ──────────────────
raw_id = 1003937713982
candidates = [
    raw_id,
    -raw_id,
    int(f"-100{raw_id}"),
]

print(f"\n🚀 Attempting test message to {len(candidates)} chat ID variants...")
for cid in candidates:
    resp = requests.post(
        f"{BASE}/sendMessage",
        json={"chat_id": cid,
              "text": "✅ AlphaPulse test — if you see this, this chat ID is correct!",
              "parse_mode": "Markdown"},
        timeout=10,
    )
    if resp.status_code == 200:
        print(f"\n   ✅ SUCCESS  →  Chat ID is:  {cid}")
        print(f"      Update your .env:  TELEGRAM_CHAT_ID={cid}")
    else:
        err = resp.json().get("description", resp.text)
        print(f"   ❌ {cid}  →  {err}")

print("\n" + "=" * 55)
print("  Fix: copy the working chat ID into your .env file")
print("       TELEGRAM_CHAT_ID=<the ID that showed SUCCESS>")
print("=" * 55)
