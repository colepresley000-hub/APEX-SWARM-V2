#!/usr/bin/env python3
"""
OAuth 1.0a PIN-based token generator — two-step.

  Step 1 (get URL):   python3 get_twitter_tokens.py
  Step 2 (get tokens): python3 get_twitter_tokens.py <PIN>
"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv
import tweepy

load_dotenv(Path(__file__).resolve().parent / ".env")

API_KEY    = os.getenv("TWITTER_API_KEY")
API_SECRET = os.getenv("TWITTER_API_SECRET")
STATE_FILE = "/tmp/twitter_oauth_state.json"

if not API_KEY or not API_SECRET:
    print("ERROR: TWITTER_API_KEY / TWITTER_API_SECRET not found in .env")
    sys.exit(1)

# ── STEP 2: exchange PIN for tokens ──────────────────────────────────────────
if len(sys.argv) == 2:
    pin = sys.argv[1].strip()
    try:
        state = json.loads(open(STATE_FILE).read())
    except Exception:
        print("ERROR: no saved OAuth state. Run without args first to get the URL.")
        sys.exit(1)

    handler = tweepy.OAuth1UserHandler(API_KEY, API_SECRET, callback="oob")
    handler.request_token = {
        "oauth_token":        state["oauth_token"],
        "oauth_token_secret": state["oauth_token_secret"],
    }
    try:
        access_token, access_token_secret = handler.get_access_token(pin)
    except tweepy.TweepyException as e:
        print(f"ERROR: PIN rejected ({e}). The request token may have expired "
              f"or the PIN is wrong; run without args for a fresh URL.")
        sys.exit(1)

    # Verify account
    client = tweepy.Client(
        consumer_key=API_KEY,
        consumer_secret=API_SECRET,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )
    me = client.get_me()
    username = me.data.username if me and me.data else "(unverified)"

    print()
    print("=" * 60)
    print(f"  SUCCESS — authorized as @{username}")
    print("=" * 60)
    print()
    print(f"MAASAI_ACCESS_TOKEN={access_token}")
    print(f"MAASAI_ACCESS_SECRET={access_token_secret}")
    print()
    os.remove(STATE_FILE)
    sys.exit(0)

# ── STEP 1: generate authorization URL ───────────────────────────────────────
handler = tweepy.OAuth1UserHandler(API_KEY, API_SECRET, callback="oob")
auth_url = handler.get_authorization_url()

# Save request token secret for step 2
with open(STATE_FILE, "w") as _f:
    json.dump({
        "oauth_token":        handler.request_token["oauth_token"],
        "oauth_token_secret": handler.request_token["oauth_token_secret"],
    }, _f)
os.chmod(STATE_FILE, 0o600)  # request-token secret: owner-only

print()
print("=" * 60)
print("  STEP 1 — Authorize as @maasai_vc")
print("=" * 60)
print()
print(f"Auth URL: {auth_url}")
print()
print("Log in as maasai_vc → Authorize app → copy the PIN")
print("Then run:  python3 get_twitter_tokens.py <PIN>")
print()
