"""
AlphaPulse - Telegram Chat ID Finder
======================================
Run this script AFTER sending any message to your bot on Telegram.

Steps:
  1. Open Telegram, search for your bot by name
  2. Press START or send any message (e.g. "hello")
  3. Run: python get_chat_id.py
  4. Copy the Chat ID printed and paste it into your .env file

Usage:
  python get_chat_id.py
"""

import sys
import requests
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

if not TOKEN:
    print("[ERROR] TELEGRAM_BOT_TOKEN not found in .env")
    sys.exit(1)

print(f"Using bot token: {TOKEN[:10]}...")
print("Fetching updates from Telegram...\n")

try:
    resp = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/getUpdates",
        timeout=10,
    )
    data = resp.json()
except Exception as e:
    print(f"[ERROR] Could not reach Telegram API: {e}")
    sys.exit(1)

if not data.get("ok"):
    print(f"[ERROR] Telegram API error: {data}")
    sys.exit(1)

updates = data.get("result", [])

if not updates:
    print("No messages found yet.")
    print()
    print("ACTION REQUIRED:")
    print("  1. Open Telegram")
    print("  2. Find your bot (search by bot username)")
    print("  3. Send it ANY message (e.g. just type 'hi')")
    print("  4. Run this script again")
    sys.exit(0)

print("Found the following chats:\n")
seen = set()
for update in updates:
    # Check both message and channel_post
    msg = update.get("message") or update.get("channel_post") or {}
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type", "unknown")
    chat_title = chat.get("title") or chat.get("username") or chat.get("first_name", "")
    sender = msg.get("from", {}).get("username", "")

    if chat_id and chat_id not in seen:
        seen.add(chat_id)
        print(f"  Chat ID   : {chat_id}")
        print(f"  Type      : {chat_type}")
        print(f"  Name      : {chat_title}")
        if sender:
            print(f"  From user : @{sender}")
        print()

print("=" * 50)
if seen:
    first_id = list(seen)[0]
    print(f"Copy this Chat ID into your .env file:")
    print()
    print(f"  TELEGRAM_CHAT_ID={first_id}")
    print()
    print("Then re-run: python main.py")
