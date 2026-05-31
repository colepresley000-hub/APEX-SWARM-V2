"""
APEX SWARM — Telegram primitives.

Two self-contained pieces extracted from main.py:
  1. send_telegram(chat_id, text) — outbound message sender (Markdown w/ plain
     fallback).
  2. Connect-token helpers — short-lived 6-char codes that link a Telegram
     chat_id to a user account, persisted in the connect_tokens table.

The stateful, deeply-coupled Telegram code (inbound message handling, the
long-poll loop, webhook setup) stays in main.py because it drives agent
execution and the channels router.
"""
import logging
import random
import string
from datetime import datetime, timedelta, timezone

import httpx

from config import TELEGRAM_BOT_TOKEN
from db import get_db

logger = logging.getLogger("apex-swarm")

_TOKEN_TTL = 600  # 10 minutes


async def send_telegram(chat_id: int, text: str):
    try:
        # Try with Markdown first, fall back to plain text if it fails
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown"},
            )
            if r.status_code == 400:
                # Markdown parse error — retry as plain text
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": text[:4000]},
                )
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


# ─── TELEGRAM CONNECT TOKENS ────────────────────────────────
# Short-lived tokens for linking a Telegram chat_id to a user account,
# persisted in the connect_tokens table.

def _ensure_connect_tokens_table():
    """Create connect_tokens table if it doesn't exist (idempotent)."""
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS connect_tokens (
                token TEXT PRIMARY KEY,
                api_key TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    except Exception as e:
        logger.warning(f"connect_tokens table init: {e}")
    finally:
        conn.close()


def _generate_connect_token(api_key: str) -> str:
    """Create a fresh 6-char connect token, persisted to DB."""
    _ensure_connect_tokens_table()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires_at = (now_dt.replace(microsecond=0) + timedelta(seconds=_TOKEN_TTL)).isoformat()
    token = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    conn = get_db()
    try:
        # Revoke existing tokens for this user and purge all expired
        conn.execute("DELETE FROM connect_tokens WHERE api_key = ? OR expires_at < ?", (api_key, now))
        conn.execute(
            "INSERT INTO connect_tokens (token, api_key, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, api_key, expires_at, now),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"_generate_connect_token DB error: {e}")
    finally:
        conn.close()
    return token


def _consume_connect_token(token: str) -> str | None:
    """Verify and consume a connect token from DB. Returns api_key or None."""
    _ensure_connect_tokens_table()
    token = token.upper()
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT api_key, expires_at FROM connect_tokens WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        if row[1] < now:
            conn.execute("DELETE FROM connect_tokens WHERE token = ?", (token,))
            conn.commit()
            return None
        conn.execute("DELETE FROM connect_tokens WHERE token = ?", (token,))
        conn.commit()
        return row[0]
    except Exception as e:
        logger.error(f"_consume_connect_token DB error: {e}")
        return None
    finally:
        conn.close()
