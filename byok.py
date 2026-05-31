"""
APEX SWARM — BYOK (bring-your-own-key) helpers.

Persist and resolve a user's personal Anthropic API key. get_user_anthropic_key
is the resolver used by agent execution: it returns the user's key when set,
otherwise the platform fallback (ANTHROPIC_API_KEY). System/channel pseudo-keys
(daemon:, telegram:, discord:, slack:) always use the platform key.

Leaf module (no main import) so both main.py and routes/byok.py can use it.
"""
import logging

from config import ANTHROPIC_API_KEY
from db import get_db

logger = logging.getLogger("apex-swarm")


def save_user_anthropic_key(user_api_key: str, anthropic_key: str):
    """Persist user's personal Anthropic key into the users table."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET anthropic_key = ? WHERE api_key = ?",
            (anthropic_key, user_api_key),
        )
        conn.commit()
        logger.info(f"Saved anthropic_key for user {user_api_key[:8]}...")
    except Exception as e:
        logger.error(f"save_user_anthropic_key failed: {e}")
    finally:
        conn.close()


def get_user_anthropic_key(user_api_key: str) -> str:
    """Return user's personal Anthropic key, falling back to platform key."""
    if not user_api_key or user_api_key.startswith(("daemon:", "telegram:", "discord:", "slack:")):
        return ANTHROPIC_API_KEY
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT anthropic_key FROM users WHERE api_key = ?",
                (user_api_key,),
            ).fetchone()
            if row and row[0] and row[0].startswith("sk-ant-"):
                return row[0]
        finally:
            conn.close()
    except Exception:
        pass
    return ANTHROPIC_API_KEY
