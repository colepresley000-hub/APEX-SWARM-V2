"""
Apex Swarm Twitter Bot
----------------------
Claude-powered bot that posts FOMO/lead-gen/conversion tweets for swarmsfall.com.
Runs as a daemon: posts 4-6 times per day on a randomised schedule.
"""

import os
import json
import random
import logging
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Load .env from same directory as this script
from dotenv import load_dotenv
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path, override=True)

import tweepy
import anthropic

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("twitter_bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
TWITTER_API_KEY          = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET       = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN     = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET    = os.getenv("TWITTER_ACCESS_SECRET", "")
TWITTER_BEARER_TOKEN     = os.getenv("TWITTER_BEARER_TOKEN", "")
ANTHROPIC_API_KEY        = os.getenv("ANTHROPIC_API_KEY", "")

# How many tweets per day (randomised within this range)
TWEETS_PER_DAY_MIN = 4
TWEETS_PER_DAY_MAX = 6

# Draft-only mode: set to True to write tweets to drafts/ folder instead of posting
DRAFT_MODE = os.getenv("TWITTER_DRAFT_MODE", "false").lower() == "true"

DRAFTS_DIR = Path("twitter_drafts")
POSTED_LOG = Path("twitter_posted.json")

# ── Tweet Strategy ───────────────────────────────────────────────────────────
# Each category maps to a distinct psychological hook
TWEET_CATEGORIES = [
    {
        "name": "fomo_scarcity",
        "weight": 3,
        "brief": (
            "Write a FOMO tweet. Apex Swarm is a 85-agent AI swarm platform at swarmsfall.com. "
            "Imply limited access / early mover advantage / spots filling up. "
            "Make it feel urgent and exclusive without being cringe. No hashtag spam."
        ),
    },
    {
        "name": "social_proof",
        "weight": 2,
        "brief": (
            "Write a social proof / traction tweet. Apex Swarm (swarmsfall.com) has users running "
            "85+ AI agents to hunt gigs, monitor markets, write proposals, research competitors, "
            "and automate their entire workflow. Show momentum — real activity, real results. "
            "No hashtag spam."
        ),
    },
    {
        "name": "use_case_hook",
        "weight": 3,
        "brief": (
            "Write a use-case tweet that hooks with a relatable pain point. "
            "Apex Swarm (swarmsfall.com) lets solo founders and freelancers run an entire "
            "AI back-office: gig hunting, proposal writing, market monitoring, crypto tracking, "
            "competitor intel, content — all from Telegram with no coding. "
            "Lead with the pain → twist with the solution. No hashtag spam."
        ),
    },
    {
        "name": "feature_drop",
        "weight": 2,
        "brief": (
            "Write a feature announcement / product update tweet about Apex Swarm (swarmsfall.com). "
            "Pick one of these features and make it feel exciting: "
            "85 specialist AI agents, BYOK (bring your own Anthropic key), Telegram bot control, "
            "gig hunter (Upwork scraper + proposal writer), Polymarket prediction engine, "
            "crypto/whale watcher daemons, competitor tracker, daemon system (always-on agents). "
            "Tone: founder shipping in public. No hashtag spam."
        ),
    },
    {
        "name": "founder_insight",
        "weight": 2,
        "brief": (
            "Write a short contrarian or insight tweet from the perspective of a solo founder "
            "who replaced most of their VA/tool stack with an AI swarm. "
            "The hook should make devs / indie hackers / AI builders stop scrolling. "
            "Subtly reference swarmsfall.com as what made this possible. No hashtag spam."
        ),
    },
    {
        "name": "conversion_cta",
        "weight": 2,
        "brief": (
            "Write a direct conversion tweet. Apex Swarm is live at swarmsfall.com. "
            "85 AI agents. Telegram control. No-code. BYOK. "
            "Make someone click the link RIGHT NOW. "
            "Use one clear CTA, one URL (swarmsfall.com), no hashtag spam."
        ),
    },
    {
        "name": "thread_hook",
        "weight": 1,
        "brief": (
            "Write the opening tweet of a thread about Apex Swarm (swarmsfall.com). "
            "The hook should be a bold claim or surprising fact about what AI swarms can do "
            "for a solo operator. End with '🧵' to signal a thread. "
            "Just write tweet 1/N — make it irresistible to click 'show more'."
        ),
    },
]

# Weighted pool so some categories fire more often
TWEET_POOL = []
for cat in TWEET_CATEGORIES:
    TWEET_POOL.extend([cat] * cat["weight"])


# ── Claude content generation ─────────────────────────────────────────────────
def generate_tweet(category: dict) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system = (
        "You are a sharp Twitter copywriter for Apex Swarm — an AI agent platform at swarmsfall.com. "
        "Brand voice: direct, confident, slightly edgy. Like a founder who ships and doesn't care about fluff. "
        "Rules: under 280 characters (hard limit), no generic filler like 'game-changing' or 'revolutionary', "
        "no more than 2 hashtags (often zero), always authentic and specific, never salesy-sounding. "
        "Return ONLY the tweet text — no quotes, no explanation, no commentary."
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{category['brief']}\n\n"
                    f"Today's date context: {datetime.now().strftime('%B %Y')}.\n"
                    f"Return only the tweet, under 280 characters."
                ),
            }
        ],
        system=system,
    )

    tweet = message.content[0].text.strip().strip('"').strip("'")

    # Hard-trim to 280 just in case Claude slips
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    return tweet


# ── Twitter client ────────────────────────────────────────────────────────────
def get_twitter_client() -> tweepy.Client:
    return tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_SECRET,
        wait_on_rate_limit=True,
    )


# ── Draft / posting ───────────────────────────────────────────────────────────
def save_draft(tweet: str, category_name: str) -> Path:
    DRAFTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DRAFTS_DIR / f"{ts}_{category_name}.txt"
    path.write_text(tweet)
    log.info(f"Draft saved → {path}")
    return path


def load_posted_log() -> list:
    if POSTED_LOG.exists():
        return json.loads(POSTED_LOG.read_text())
    return []


def append_posted_log(entry: dict):
    log_data = load_posted_log()
    log_data.append(entry)
    # Keep last 500 entries
    log_data = log_data[-500:]
    POSTED_LOG.write_text(json.dumps(log_data, indent=2))


def post_tweet(tweet: str, category_name: str) -> dict | None:
    if DRAFT_MODE:
        save_draft(tweet, category_name)
        log.info(f"[DRAFT MODE] {category_name}: {tweet}")
        return {"mode": "draft", "tweet": tweet}

    client = get_twitter_client()
    try:
        response = client.create_tweet(text=tweet)
        tweet_id = response.data["id"]
        entry = {
            "id": tweet_id,
            "category": category_name,
            "text": tweet,
            "posted_at": datetime.now().isoformat(),
        }
        append_posted_log(entry)
        log.info(f"Posted [{category_name}] id={tweet_id}: {tweet}")
        return entry
    except tweepy.TweepyException as e:
        log.error(f"Tweet failed: {e}")
        return None


# ── Main post cycle ───────────────────────────────────────────────────────────
def post_one():
    category = random.choice(TWEET_POOL)
    log.info(f"Generating tweet — category: {category['name']}")
    tweet = generate_tweet(category)
    log.info(f"Generated: {tweet}")
    result = post_tweet(tweet, category["name"])
    return result


def build_daily_schedule(n: int) -> list[int]:
    """Return n sorted seconds-since-midnight spread across 7am–11pm."""
    start = 7 * 3600   # 7am
    end   = 23 * 3600  # 11pm
    slots = sorted(random.sample(range(start, end, 3600), n))
    return slots


def run_scheduler():
    """Block forever, posting on a fresh randomised daily schedule."""
    log.info(
        f"Scheduler started — DRAFT_MODE={DRAFT_MODE}, "
        f"{TWEETS_PER_DAY_MIN}–{TWEETS_PER_DAY_MAX} tweets/day"
    )
    while True:
        now       = datetime.now()
        midnight  = now.replace(hour=0, minute=0, second=0, microsecond=0)
        n_today   = random.randint(TWEETS_PER_DAY_MIN, TWEETS_PER_DAY_MAX)
        schedule  = build_daily_schedule(n_today)

        log.info(
            f"Today's schedule ({n_today} tweets): "
            + ", ".join(
                (midnight + timedelta(seconds=s)).strftime("%H:%M") for s in schedule
            )
        )

        for seconds_since_midnight in schedule:
            target = midnight + timedelta(seconds=seconds_since_midnight)
            wait   = (target - datetime.now()).total_seconds()
            if wait > 0:
                log.info(f"Next tweet at {target.strftime('%H:%M')} (in {wait/60:.1f} min)")
                time.sleep(wait)
            post_one()

        # Sleep until midnight + 30s
        tomorrow = midnight + timedelta(days=1, seconds=30)
        wait = (tomorrow - datetime.now()).total_seconds()
        log.info(f"Day done. Sleeping until tomorrow ({wait/3600:.1f} h).")
        time.sleep(max(wait, 1))


# ── CLI helpers ───────────────────────────────────────────────────────────────
def cmd_post_now(category_name: str | None = None):
    """Post one tweet immediately. Optionally specify category name."""
    if category_name:
        cats = [c for c in TWEET_CATEGORIES if c["name"] == category_name]
        if not cats:
            print(f"Unknown category. Valid: {[c['name'] for c in TWEET_CATEGORIES]}")
            return
        category = cats[0]
    else:
        category = random.choice(TWEET_POOL)

    tweet = generate_tweet(category)
    print(f"\n--- Generated tweet [{category['name']}] ---\n{tweet}\n")
    confirm = input("Post? [y/N] ").strip().lower()
    if confirm == "y":
        post_tweet(tweet, category["name"])
    else:
        save_draft(tweet, category["name"])
        print("Saved to drafts instead.")


def cmd_autopost(category_name: str | None = None):
    """Non-interactive post — used by remote/scheduled agents."""
    if category_name:
        cats = [c for c in TWEET_CATEGORIES if c["name"] == category_name]
        category = cats[0] if cats else random.choice(TWEET_POOL)
    else:
        category = random.choice(TWEET_POOL)

    tweet = generate_tweet(category)
    result = post_tweet(tweet, category["name"])
    print(f"[autopost] {category['name']}: {tweet}")
    return result


def cmd_preview(n: int = 5):
    """Generate n draft tweets without posting, for review."""
    print(f"Generating {n} preview tweets...\n")
    for i in range(n):
        category = random.choice(TWEET_POOL)
        tweet = generate_tweet(category)
        print(f"[{i+1}/{n}] {category['name']}\n{tweet}\n{'-'*60}")


def cmd_post_drafts():
    """Post all saved drafts."""
    drafts = sorted(DRAFTS_DIR.glob("*.txt"))
    if not drafts:
        print("No drafts found.")
        return
    client = get_twitter_client()
    for draft in drafts:
        tweet = draft.read_text().strip()
        print(f"\n{draft.name}\n{tweet}")
        confirm = input("Post? [y/N/q] ").strip().lower()
        if confirm == "q":
            break
        if confirm == "y":
            try:
                r = client.create_tweet(text=tweet)
                print(f"Posted: {r.data['id']}")
                draft.unlink()
            except tweepy.TweepyException as e:
                print(f"Failed: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  python twitter_bot.py run              — start scheduler daemon\n"
            "  python twitter_bot.py autopost          — post one tweet (non-interactive, for remote agents)\n"
            "  python twitter_bot.py autopost <cat>    — post specific category (non-interactive)\n"
            "  python twitter_bot.py post              — post one tweet now (interactive)\n"
            "  python twitter_bot.py post <cat>        — post specific category\n"
            "  python twitter_bot.py preview [n]       — preview N tweets (default 5)\n"
            "  python twitter_bot.py drafts            — review and post saved drafts\n"
            "\nCategories: " + ", ".join(c["name"] for c in TWEET_CATEGORIES)
        )
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "run":
        run_scheduler()
    elif cmd == "autopost":
        cat = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_autopost(cat)
    elif cmd == "post":
        cat = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_post_now(cat)
    elif cmd == "preview":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        cmd_preview(n)
    elif cmd == "drafts":
        cmd_post_drafts()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
