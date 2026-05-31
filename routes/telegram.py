"""
APEX SWARM — Telegram account-linking routes.

The /connect flow: a user generates a short-lived 6-char token in the
dashboard, sends `/connect <token>` to the bot, and the bot calls
/api/v1/telegram/connect to bind their chat_id to their account.

Pilot router for the main.py -> routes/ decomposition. Imports only from the
shared leaf modules (config/db/deps/telegram) — never from main — so there is
no circular import.
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from db import get_db
from deps import get_api_key
from telegram import _TOKEN_TTL, _consume_connect_token, _generate_connect_token

logger = logging.getLogger("apex-swarm")

router = APIRouter()


@router.post("/api/v1/telegram/generate-connect-token")
async def generate_telegram_connect_token(api_key: str = Depends(get_api_key)):
    """Generate a short-lived 6-char token for linking a Telegram chat to this account."""
    # Verify user exists
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM users WHERE api_key = ?", (api_key,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    token = _generate_connect_token(api_key)
    bot = os.getenv("TELEGRAM_BOT_USERNAME", "ClawdClauBot")
    return {
        "token": token,
        "expires_in": _TOKEN_TTL,
        "instructions": f"Send this to @{bot} on Telegram:\n/connect {token}",
        "bot_url": f"https://t.me/{bot}",
    }


@router.post("/api/v1/telegram/connect")
async def telegram_connect(request: Request):
    """Link a Telegram chat_id to a user account via a connect token.
    No API key required — the token already encodes the user identity."""
    data = await request.json()
    token = data.get("token", "").strip().upper()
    chat_id = str(data.get("chat_id", "")).strip()
    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="token and chat_id required")
    api_key = _consume_connect_token(token)
    if not api_key:
        raise HTTPException(status_code=400, detail="Invalid or expired token. Generate a new one at swarmsfall.com > Settings > Connect Telegram")
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET telegram_chat_id = ? WHERE api_key = ?",
            (chat_id, api_key),
        )
        conn.commit()
        logger.info(f"Telegram chat_id {chat_id} linked to user {api_key[:8]}")
    except Exception as e:
        logger.error(f"Telegram connect failed: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        conn.close()
    return {"status": "linked", "message": "Your Telegram is now connected to swarmsfall.com"}


@router.get("/api/v1/telegram/status")
async def telegram_status(api_key: str = Depends(get_api_key)):
    """Check if this user's Telegram is connected."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT telegram_chat_id FROM users WHERE api_key = ?", (api_key,)
        ).fetchone()
    finally:
        conn.close()
    connected = bool(row and row[0])
    return {"connected": connected, "chat_id": row[0] if connected else None}
