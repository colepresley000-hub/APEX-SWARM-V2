"""
APEX SWARM — Centralized configuration.

All environment-derived constants live here so they have a single source of
truth. `main.py` and any future modules import from this file rather than
re-reading os.getenv() in scattered places.

Behavior note: this is a straight extraction from main.py — same names, same
defaults, same os.getenv() calls. Nothing here should have side effects beyond
reading environment variables.
"""
import os

# ─── CORE / MODEL ─────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20241022")

# ─── TELEGRAM ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── SLACK ────────────────────────────────────────────────
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_DEFAULT_CHANNEL = os.getenv("SLACK_DEFAULT_CHANNEL", "#ai-workforce")

# ─── STRIPE / BILLING ─────────────────────────────────────
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_STARTER_PRICE = os.getenv("STRIPE_STARTER_PRICE", "")
STRIPE_PRO_PRICE = os.getenv("STRIPE_PRO_PRICE", "")
STRIPE_ENTERPRISE_PRICE = os.getenv("STRIPE_ENTERPRISE_PRICE", "")

# ─── AUTH / SECRETS ───────────────────────────────────────
# NOTE: despite the name, JWT_SECRET is used as an HMAC key for password
# hashing in main.py, not for JWTs. Set in Railway in production.
JWT_SECRET = os.getenv("JWT_SECRET", "apex-swarm-jwt-secret-change-in-prod")

# ─── GOOGLE OAUTH ─────────────────────────────────────────
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# ─── PLATFORM / RUNTIME ───────────────────────────────────
BASE_URL = os.getenv("BASE_URL", "https://apex-swarm-v2-production.up.railway.app")
DATABASE_PATH = os.getenv("DATABASE_PATH", "apex_swarm.db")
PORT = int(os.getenv("PORT", "8080"))
VERSION = "4.0.0"

# ─── TWITTER BOT BRIDGE ───────────────────────────────────
TWITTERBOT_URL = os.getenv("TWITTERBOT_URL", "")          # e.g. https://apex-twitterbot.up.railway.app
TWITTERBOT_SECRET = os.getenv("TWITTERBOT_SECRET", "")    # shared secret (CONTROL_API_SECRET in bot_service)
