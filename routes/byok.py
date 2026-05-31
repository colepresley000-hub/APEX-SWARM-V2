"""
APEX SWARM — BYOK (bring-your-own-key) routes.

Lets a user store their personal Anthropic key so agent runs bill to their own
credits. Three ways in:
  - /set-key       — authenticated via platform API key
  - /set-key-by-chat — identity proven by linked Telegram chat_id (used by the
                       bot's /setkey command), no auth header needed
  - /status        — report whether a BYOK key is active

Imports only leaf modules (byok/db/deps) — never main.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from byok import save_user_anthropic_key
from db import get_db
from deps import get_api_key

router = APIRouter()


@router.post("/api/v1/byok/set-key-by-chat")
async def byok_set_key_by_chat(request: Request):
    """Set Anthropic key by Telegram chat_id — chat_id proves identity, no auth token needed."""
    data = await request.json()
    chat_id = str(data.get("chat_id", "")).strip()
    anthropic_key = data.get("anthropic_key", "").strip()
    if not anthropic_key.startswith("sk-ant-"):
        raise HTTPException(status_code=400, detail="Invalid Anthropic key format")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id required")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT api_key FROM users WHERE telegram_chat_id = ?", (chat_id,)
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="No account linked to this Telegram. Sign up at swarmsfall.com then link with /connect"
            )
        save_user_anthropic_key(row[0], anthropic_key)
    finally:
        conn.close()
    return {"status": "saved"}


@router.get("/api/v1/byok/status")
async def byok_status(api_key: str = Depends(get_api_key)):
    """Check whether a BYOK key is set for this user."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT anthropic_key FROM users WHERE api_key = ?", (api_key,)
        ).fetchone()
    finally:
        conn.close()
    has_key = bool(row and row[0] and row[0].startswith("sk-ant-"))
    return {"byok_active": has_key}


@router.post("/api/v1/byok/set-key")
async def byok_set_key(request: Request, api_key: str = Depends(get_api_key)):
    """Save user's Anthropic key server-side. Requires platform API key auth."""
    data = await request.json()
    anthropic_key = data.get("anthropic_key", "").strip()
    if not anthropic_key.startswith("sk-ant-"):
        raise HTTPException(status_code=400, detail="Invalid Anthropic key format — must start with sk-ant-")
    save_user_anthropic_key(api_key, anthropic_key)
    return {"status": "saved"}
