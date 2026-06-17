"""
social_core.py — shared engine for all brand Twitter/X bots.
=============================================================
One brand-config-driven core powering Apex Swarm, Maasai VC, and future brands.

Provides:
  - Module-level Anthropic client with cache_control structured system blocks
  - Tweepy v2 (Client) + v1.1 (API, for media) factories
  - Rotation tracking (anti-repeat)
  - Tweet generation: 280-char enforcement, em-dash cleanup, auto-shorten retry
  - Performance feedback loop: winners injected as few-shot examples
  - Single-tweet + thread posting
  - Visual quote-card attachment hook
  - Telegram send/approval helpers

Each brand defines a BrandConfig; the functions here do the work.
"""

import os
import json
import random
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from dotenv import load_dotenv
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path, override=True)

import tweepy
import anthropic

# ── Logging ──────────────────────────────────────────────────────────────────
log = logging.getLogger("social_core")
if not log.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Shared developer-app credentials (one X app, per-account user tokens)
APP_API_KEY    = os.getenv("TWITTER_API_KEY", "")
APP_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
APP_BEARER     = os.getenv("TWITTER_BEARER_TOKEN", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

DATA_DIR = Path(__file__).resolve().parent / "social_data"
DATA_DIR.mkdir(exist_ok=True)


# ── Module-level Anthropic client (per global API standards) ──────────────────
_anthropic_client: Optional[anthropic.Anthropic] = None

def anthropic_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


# ── Brand configuration ───────────────────────────────────────────────────────
@dataclass
class BrandConfig:
    key: str                       # short id, e.g. "apex" / "maasai"
    name: str                      # display name
    handle: str                    # @handle (no @)
    url: str                       # primary link, appended to tweets
    user_id: str                   # numeric X user id (for timeline reads), "" if unknown
    system_prompt: str             # brand voice rules
    product_context: str           # facts injected into prompts
    categories: list               # list of category dicts
    access_token_env: str          # env var name for this brand's access token
    access_secret_env: str         # env var name for this brand's access secret
    draft_env: str                 # env var name toggling draft mode
    # Visual theme (used by visual_cards.py)
    theme: dict = field(default_factory=dict)
    # Reply engine config
    reply_keywords: list = field(default_factory=list)   # search terms to monitor
    reply_accounts: list = field(default_factory=list)    # @handles to monitor (no @)
    reply_guidance: str = ""                              # how to reply for this brand
    reply_daily_cap: int = 15                             # max approved replies/day

    # ── derived ──
    @property
    def access_token(self) -> str:
        return os.getenv(self.access_token_env, "")

    @property
    def access_secret(self) -> str:
        return os.getenv(self.access_secret_env, "")

    @property
    def draft_mode(self) -> bool:
        return os.getenv(self.draft_env, "false").lower() == "true"

    @property
    def rotation_file(self) -> Path:
        return DATA_DIR / f"{self.key}_rotation.json"

    @property
    def posted_file(self) -> Path:
        return DATA_DIR / f"{self.key}_posted.json"

    @property
    def winners_file(self) -> Path:
        return DATA_DIR / f"{self.key}_winners.json"

    @property
    def reply_queue_file(self) -> Path:
        return DATA_DIR / f"{self.key}_reply_queue.json"

    @property
    def drafts_dir(self) -> Path:
        d = DATA_DIR / f"{self.key}_drafts"
        return d

    @property
    def weighted_pool(self) -> list:
        pool = []
        for c in self.categories:
            pool.extend([c] * c.get("weight", 1))
        return pool

    def has_tokens(self) -> bool:
        return bool(self.access_token and self.access_secret)


# ── Tweepy clients ────────────────────────────────────────────────────────────
def v2_client(brand: BrandConfig) -> tweepy.Client:
    return tweepy.Client(
        bearer_token=APP_BEARER or None,
        consumer_key=APP_API_KEY,
        consumer_secret=APP_API_SECRET,
        access_token=brand.access_token,
        access_token_secret=brand.access_secret,
        wait_on_rate_limit=True,
    )

def v1_api(brand: BrandConfig) -> tweepy.API:
    """v1.1 API — required for media upload (quote cards)."""
    auth = tweepy.OAuth1UserHandler(
        APP_API_KEY, APP_API_SECRET,
        brand.access_token, brand.access_secret,
    )
    return tweepy.API(auth)


# ── Rotation tracking ─────────────────────────────────────────────────────────
def load_rotation(brand: BrandConfig) -> list:
    if brand.rotation_file.exists():
        try:
            return json.loads(brand.rotation_file.read_text())
        except Exception:
            pass
    return []

def _atomic_write(path: Path, text: str):
    """Write via temp file + rename so a crash mid-write can't corrupt the JSON."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)

def save_rotation(brand: BrandConfig, used: list):
    _atomic_write(brand.rotation_file, json.dumps(used[-15:]))

def pick_category(brand: BrandConfig, force: Optional[str] = None) -> dict:
    pool = brand.weighted_pool or brand.categories
    if not pool:
        raise ValueError(f"[{brand.key}] no categories configured")
    if force:
        for c in brand.categories:
            if c["name"] == force:
                return c
        return random.choice(pool)
    recent = load_rotation(brand)
    avoid = set(recent[-5:])
    available = [c for c in pool if c["name"] not in avoid]
    return random.choice(available or pool)


# ── Posted log + winners (feedback loop) ──────────────────────────────────────
def load_posted(brand: BrandConfig) -> list:
    if brand.posted_file.exists():
        try:
            return json.loads(brand.posted_file.read_text())
        except Exception:
            pass
    return []

def append_posted(brand: BrandConfig, entry: dict):
    data = load_posted(brand)
    data.append(entry)
    _atomic_write(brand.posted_file, json.dumps(data[-500:], indent=2))

def load_winners(brand: BrandConfig) -> list:
    if brand.winners_file.exists():
        try:
            return json.loads(brand.winners_file.read_text())
        except Exception:
            pass
    return []


# ── Recent-tweet fetch (dedup) ────────────────────────────────────────────────
def get_recent_tweets(brand: BrandConfig, n: int = 10) -> list:
    try:
        client = v2_client(brand)
        if brand.user_id:
            result = client.get_users_tweets(id=brand.user_id, max_results=max(5, n),
                                             tweet_fields=["text"])
        else:
            me = client.get_me()
            if not me or not me.data:
                log.warning(f"[{brand.key}] get_me returned no user; skipping recent fetch")
                return []
            result = client.get_users_tweets(id=me.data.id, max_results=max(5, n),
                                             tweet_fields=["text"])
        if result and result.data:
            return [t.text for t in result.data]
    except Exception as e:
        log.warning(f"[{brand.key}] could not fetch recent tweets: {e}")
    return []


# ── Text hygiene ──────────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    import re
    text = text.strip().strip('"').strip("'")
    # Normalize em/en dashes to a comma without creating "word,word"
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    # Collapse any accidental double spaces / space-before-punctuation
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ── Tweet generation ──────────────────────────────────────────────────────────
STYLE_VARIATIONS = [
    "Open with a bold statement.",
    "Open with a specific scenario or moment.",
    "Open with a number or surprising fact.",
    "Open with a short question.",
    "Open with a contrast (Old way / New way).",
    "Open with an action (imperative verb).",
    "Make it feel like something said in a founders WhatsApp group.",
]

def _first_text(msg) -> str:
    """Pull the first text block from a Claude response, tolerating empty/non-text content."""
    for block in getattr(msg, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""

def _system_blocks(brand: BrandConfig):
    """Structured system blocks: stable cached prompt + product context."""
    return [
        {
            "type": "text",
            "text": brand.system_prompt,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": brand.product_context,
        },
    ]

def _winners_block(brand: BrandConfig) -> str:
    winners = load_winners(brand)
    if not winners:
        return ""
    top = winners[:5]
    lines = "\n".join(
        f"• {str(w.get('text', ''))[:160]}"
        for w in top
        if isinstance(w, dict) and w.get("text")
    )
    if not lines:
        return ""
    return (
        "\n\nThese past tweets performed BEST (high engagement). "
        "Match their energy, structure, and specificity — but do NOT copy them:\n"
        + lines
    )

def generate_tweet(brand: BrandConfig, category: dict, recent_tweets=None) -> str:
    avoid_block = ""
    if recent_tweets:
        avoid_block = (
            "\n\nDO NOT repeat or echo these recent tweets:\n"
            + "\n".join(f"• {t[:120]}" for t in recent_tweets[:8])
            + "\nUse a completely different opening, structure, and angle."
        )

    prompt = (
        f"TWEET ANGLE: {category['style'].upper()}\n\n"
        f"BRIEF:\n{category['brief']}\n\n"
        f"STYLE NOTE: {random.choice(STYLE_VARIATIONS)}"
        f"{_winners_block(brand)}"
        f"{avoid_block}\n\n"
        "Write ONE tweet. Count characters — must be under 280 including the URL. "
        "Return only the tweet text, no quotes, no commentary."
    )

    client = anthropic_client()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=350,
        system=_system_blocks(brand),
        messages=[{"role": "user", "content": prompt}],
    )
    _log_usage(brand, msg.usage)
    tweet = clean_text(_first_text(msg))

    if len(tweet) > 280:
        tweet = _shorten(brand, tweet)
    return tweet

def _shorten(brand: BrandConfig, tweet: str) -> str:
    log.warning(f"[{brand.key}] tweet too long ({len(tweet)}), shortening")
    client = anthropic_client()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        system=[{"type": "text", "text": brand.system_prompt,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": (
            f"This tweet is {len(tweet)} chars, too long. Shorten to under 275 chars "
            f"without losing the core message. Keep {brand.url} at the end. No hashtags. "
            f"Output only the shortened tweet.\n\n{tweet}"
        )}],
    )
    out = clean_text(_first_text(msg))
    if not out:
        # Shorten returned nothing usable; hard-truncate the original as a last resort.
        return tweet[:277] + "..."
    return out if len(out) <= 280 else out[:277] + "..."

def _log_usage(brand: BrandConfig, usage):
    log.info(
        f"[{brand.key}] tokens in={usage.input_tokens} "
        f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)} "
        f"cache_create={getattr(usage, 'cache_creation_input_tokens', 0)} "
        f"out={usage.output_tokens}"
    )


# ── Thread generation ─────────────────────────────────────────────────────────
def generate_thread(brand: BrandConfig, topic_brief: str, n_tweets: int = 5) -> list:
    """Generate a chained thread of `n_tweets` tweets, each <280 chars."""
    prompt = (
        f"Write a Twitter/X thread of exactly {n_tweets} tweets about:\n{topic_brief}\n\n"
        "Rules:\n"
        f"- Tweet 1 is a scroll-stopping hook (no URL, end with a reason to keep reading)\n"
        "- Each subsequent tweet delivers one concrete, specific point\n"
        f"- The FINAL tweet has a clear CTA and ends with {brand.url}\n"
        "- Each tweet MUST be under 280 characters\n"
        "- No hashtags, no em dashes\n"
        "- Do NOT number the tweets\n\n"
        "Return the tweets separated by a line containing only '---' (three dashes)."
    )
    client = anthropic_client()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=900,
        system=_system_blocks(brand),
        messages=[{"role": "user", "content": prompt}],
    )
    _log_usage(brand, msg.usage)
    raw = _first_text(msg).strip()
    parts = [clean_text(p) for p in raw.split("---")]
    tweets = [p for p in parts if p]
    # Enforce length
    tweets = [t if len(t) <= 280 else t[:277] + "..." for t in tweets]
    return tweets


# ── Credit / billing alerts ───────────────────────────────────────────────────
_CREDIT_ALERT_FILE = DATA_DIR / "_credit_alert.json"
_CREDIT_ALERT_COOLDOWN = 6 * 3600  # ping at most once per 6h

def is_credit_error(exc) -> bool:
    """True if a Tweepy exception is an X API 402 / no-credits / billing error."""
    s = str(exc).lower()
    return ("402" in s or "payment required" in s
            or "does not have any credits" in s or "not have any credits" in s)

def notify_credits_empty(brand: BrandConfig, context: str = ""):
    """Ping Telegram that X credits are exhausted — deduped to once per cooldown."""
    import time as _t
    now = _t.time()
    try:
        last = json.loads(_CREDIT_ALERT_FILE.read_text()).get("at", 0)
    except Exception:
        last = 0
    if now - last < _CREDIT_ALERT_COOLDOWN:
        return  # already alerted recently
    msg = (f"⚠️ <b>X API credits empty</b> — posting paused for {brand.name}.\n"
           f"All tweets/replies will fail (402 Payment Required) until you add "
           f"credits at console.x.com → Buy Credits.")
    if context:
        msg += f"\n\n<i>{context}</i>"
    telegram_send(msg)
    try:
        _atomic_write(_CREDIT_ALERT_FILE, json.dumps({"at": now}))
    except Exception:
        pass


# ── Posting ───────────────────────────────────────────────────────────────────
def _save_draft(brand: BrandConfig, text: str, tag: str) -> Path:
    brand.drafts_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = brand.drafts_dir / f"{ts}_{tag}.txt"
    path.write_text(text)
    log.info(f"[{brand.key}] draft saved → {path}")
    return path

def post_tweet(brand: BrandConfig, text: str, category_name: str,
               media_path: Optional[str] = None) -> Optional[dict]:
    if brand.draft_mode:
        _save_draft(brand, text, category_name)
        return {"mode": "draft", "text": text}

    client = v2_client(brand)
    media_ids = None
    if media_path:
        try:
            api = v1_api(brand)
            media = api.media_upload(media_path)
            media_ids = [media.media_id]
        except Exception as e:
            log.warning(f"[{brand.key}] media upload failed, posting without: {e}")

    try:
        resp = client.create_tweet(text=text, media_ids=media_ids)
        if not resp or not resp.data or not resp.data.get("id"):
            log.error(f"[{brand.key}] create_tweet returned no tweet id: {resp}")
            return None
        tweet_id = resp.data["id"]
        entry = {
            "id": str(tweet_id),
            "category": category_name,
            "text": text,
            "has_media": bool(media_ids),
            "posted_at": datetime.now().isoformat(),
        }
        append_posted(brand, entry)
        recent = load_rotation(brand)
        recent.append(category_name)
        save_rotation(brand, recent)
        log.info(f"[{brand.key}] posted [{category_name}] id={tweet_id}: {text}")
        return entry
    except tweepy.errors.Forbidden as e:
        log.error(f"[{brand.key}] forbidden (dupe/spam): {e}")
        return None
    except tweepy.TweepyException as e:
        log.error(f"[{brand.key}] post failed: {e}")
        if is_credit_error(e):
            notify_credits_empty(brand, "Scheduled tweet could not post.")
        return None

def post_thread(brand: BrandConfig, tweets: list) -> Optional[dict]:
    tweets = [t for t in (tweets or []) if t]
    if not tweets:
        log.error(f"[{brand.key}] post_thread called with no tweets")
        return None

    if brand.draft_mode:
        _save_draft(brand, "\n\n---\n\n".join(tweets), "thread")
        return {"mode": "draft", "tweets": tweets}

    client = v2_client(brand)
    reply_to = None
    ids = []
    try:
        for t in tweets:
            resp = client.create_tweet(text=t, in_reply_to_tweet_id=reply_to)
            if not resp or not resp.data or not resp.data.get("id"):
                raise tweepy.TweepyException(f"create_tweet returned no id: {resp}")
            reply_to = resp.data["id"]
            ids.append(str(reply_to))
            time.sleep(1)
    except tweepy.TweepyException as e:
        # Partial failure: record whatever was actually posted so the thread isn't
        # silently orphaned on Twitter, then report failure.
        if ids:
            entry = {
                "id": ids[0],
                "category": "thread",
                "text": tweets[0],
                "thread_ids": ids,
                "partial": True,
                "expected_len": len(tweets),
                "posted_at": datetime.now().isoformat(),
            }
            append_posted(brand, entry)
            log.error(f"[{brand.key}] thread PARTIALLY posted {len(ids)}/{len(tweets)} "
                      f"(root id={ids[0]}), then failed: {e}")
        else:
            log.error(f"[{brand.key}] thread failed before any tweet posted: {e}")
        if is_credit_error(e):
            notify_credits_empty(brand, "Thread could not post.")
        return None

    entry = {
        "id": ids[0],
        "category": "thread",
        "text": tweets[0],
        "thread_ids": ids,
        "posted_at": datetime.now().isoformat(),
    }
    append_posted(brand, entry)
    recent = load_rotation(brand)
    recent.append("thread")
    save_rotation(brand, recent)
    log.info(f"[{brand.key}] posted thread of {len(ids)} tweets, root id={ids[0]}")
    return entry


# ── High-level autopost ───────────────────────────────────────────────────────
def autopost(brand: BrandConfig, category_name: Optional[str] = None,
             with_visual: bool = False) -> Optional[dict]:
    if not brand.has_tokens():
        msg = (f"[{brand.key}] ERROR: {brand.access_token_env}/{brand.access_secret_env} "
               f"not set in .env. Run get_twitter_tokens.py to generate them.")
        log.error(msg)
        print(msg)
        return None

    recent = get_recent_tweets(brand, 10)
    tried = set()
    for attempt in range(3):
        category = pick_category(brand, force=category_name if attempt == 0 else None)
        while category["name"] in tried and len(tried) < len(brand.categories):
            category = pick_category(brand)
        tried.add(category["name"])

        tweet = generate_tweet(brand, category, recent_tweets=recent)
        log.info(f"[{brand.key}] attempt {attempt+1}/3 [{category['name']}]: {tweet}")

        media_path = None
        if with_visual:
            try:
                import visual_cards
                media_path = visual_cards.make_card(brand, tweet)
            except Exception as e:
                log.warning(f"[{brand.key}] visual card failed: {e}")

        result = post_tweet(brand, tweet, category["name"], media_path=media_path)
        if result:
            print(f"[autopost:{brand.key}] {category['name']}: {tweet}")
            return result
        log.warning(f"[{brand.key}] attempt {attempt+1} failed, retrying")
    log.error(f"[{brand.key}] all 3 attempts failed")
    return None


def preview(brand: BrandConfig, n: int = 5):
    print(f"\n=== {brand.name} — PREVIEW ({n}) ===\n")
    rotation = load_rotation(brand)
    seen = set()
    count = 0
    for _ in range(n * 4):
        if count >= n:
            break
        category = pick_category(brand)
        if category["name"] in seen:
            continue
        seen.add(category["name"])
        rotation.append(category["name"])
        tweet = generate_tweet(brand, category)
        print(f"[{category['name']}] ({len(tweet)} chars)\n{tweet}\n")
        count += 1


# ── Telegram helpers ──────────────────────────────────────────────────────────
def telegram_send(text: str, reply_markup: Optional[dict] = None) -> Optional[dict]:
    """Send a Telegram message to the configured chat. Returns the API response."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured")
        return None
    import requests
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload, timeout=15,
        )
        return r.json()
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return None
