"""
Apex Swarm Twitter Bot — v2
-----------------------------
High-conversion content engine for swarmsfall.com.
15 distinct angles, rotation tracking, anti-repeat logic.
"""

import os
import json
import random
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path, override=True)

import tweepy
import anthropic

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("twitter_bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TWITTER_API_KEY       = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET    = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN  = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")
TWITTER_BEARER_TOKEN  = os.getenv("TWITTER_BEARER_TOKEN", "")
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
DRAFT_MODE            = os.getenv("TWITTER_DRAFT_MODE", "false").lower() == "true"

DRAFTS_DIR   = Path("twitter_drafts")
POSTED_LOG   = Path("twitter_posted.json")
ROTATION_LOG = Path("twitter_rotation.json")  # tracks recently used angles

# ── Product context (injected into every prompt) ──────────────────────────────
PRODUCT_CONTEXT = """
APEX SWARM — Product Facts (use these for specificity, don't make things up beyond this):
- URL: swarmsfall.com
- 85 specialist AI agents in one platform
- Controlled entirely from Telegram — text commands, no dashboard needed
- No coding required — built for non-technical founders
- BYOK: users bring their own Anthropic API key to use their own credits
- Key agents/features:
  • Gig Hunter: scrapes Upwork RSS, finds relevant jobs, writes tailored proposals automatically
  • Whale Watcher: monitors crypto wallets and large on-chain movements in real time
  • Competitor Tracker: monitors rival products, pricing changes, new features
  • News Sentinel: tracks breaking news in any niche and summarises for you
  • Polymarket Engine: simulates and analyses prediction market positions
  • Daemon System: always-on background agents that run 24/7 without you lifting a finger
  • Proposal Writer: generates custom client proposals from job descriptions
  • Market Monitor: tracks price movements and sends alerts via Telegram
- Pricing: paid via Gumroad license key
- Target user: solo founders, freelancers, indie hackers — people doing the work of 5 people alone
- Core promise: replace your VA + tool stack with one AI swarm you control from your phone
"""

# ── Tweet categories ──────────────────────────────────────────────────────────
# 15 distinct angles. Each fires with a specific brief and style instruction.
TWEET_CATEGORIES = [
    {
        "name": "before_after",
        "weight": 3,
        "style": "before/after contrast",
        "brief": """Write a before/after tweet showing life before vs after Apex Swarm.
Before: doing something manually, painfully, expensively (VA, tools, hours of work).
After: Apex Swarm handling it automatically from Telegram.
Be specific — name one real task (e.g. writing Upwork proposals, tracking whale wallets, monitoring competitors).
End with swarmsfall.com. No hashtags.""",
    },
    {
        "name": "specific_feature",
        "weight": 3,
        "style": "feature spotlight",
        "brief": """Write a tweet spotlighting ONE specific Apex Swarm feature.
Pick one: Gig Hunter, Whale Watcher, Competitor Tracker, Daemon System, Proposal Writer, News Sentinel, or Polymarket Engine.
Explain exactly what it does in concrete terms — what it saves, what it automates, what result it produces.
Make it feel real and specific, not vague. End with swarmsfall.com.""",
    },
    {
        "name": "solo_founder_story",
        "weight": 3,
        "style": "first-person founder story",
        "brief": """Write a first-person tweet from the perspective of a solo founder who uses Apex Swarm.
Tell a mini-story: a specific moment or realisation — e.g. waking up to proposals already written, seeing competitor changes before the market did, finding 8 qualified gigs while asleep.
Make it feel authentic, not salesy. End with swarmsfall.com.""",
    },
    {
        "name": "pain_point_hook",
        "weight": 3,
        "style": "pain → solution",
        "brief": """Write a tweet that opens with a sharp, relatable pain point for solo founders or freelancers.
Examples: spending hours writing proposals, missing market moves, paying for 6 SaaS tools that barely talk to each other, hiring VAs who quit.
Twist to: Apex Swarm solved this. Be specific about HOW. End with swarmsfall.com.""",
    },
    {
        "name": "contrarian_take",
        "weight": 2,
        "style": "contrarian / hot take",
        "brief": """Write a contrarian or counter-intuitive tweet that challenges how people think about building solo businesses.
Example angles: you don't need a team, hiring a VA is the wrong move, most SaaS tools are overhead, one swarm beats five employees.
Be confident and slightly edgy. Apex Swarm / swarmsfall.com should appear as the natural answer, not the focus.
No hashtags.""",
    },
    {
        "name": "numbers_proof",
        "weight": 2,
        "style": "specific numbers",
        "brief": """Write a tweet built around specific numbers that make Apex Swarm feel real and concrete.
Use numbers like: 85 agents, 0 employees needed, 1 Telegram message, 24/7 uptime, instant proposals, $0 VA cost.
Don't invent fake metrics or testimonials — stick to product facts. End with swarmsfall.com.""",
    },
    {
        "name": "telegram_angle",
        "weight": 2,
        "style": "Telegram control angle",
        "brief": """Write a tweet focused on the fact that Apex Swarm runs entirely from Telegram.
The idea: your entire business back-office is one Telegram bot away. Text it like a team member.
Examples of commands you can send: ask it to find gigs, monitor a wallet, track a competitor, write a proposal, check the news.
Make this feel like a superpower for solo operators. End with swarmsfall.com.""",
    },
    {
        "name": "direct_cta",
        "weight": 2,
        "style": "direct conversion CTA",
        "brief": """Write a short, punchy direct-response tweet designed to make someone click swarmsfall.com RIGHT NOW.
Lead with the biggest benefit or most surprising fact about Apex Swarm.
End with a clear, single action: go to swarmsfall.com. No fluff. No hashtags.
Vary the hook — don't start with "Apex Swarm" or "85 agents".""",
    },
    {
        "name": "question_hook",
        "weight": 2,
        "style": "question that reframes",
        "brief": """Write a tweet that opens with a sharp question that stops a solo founder or freelancer mid-scroll.
Examples: "What if your VA never slept?", "How many hours did you spend on proposals this week?", "What would you do if your back-office ran itself?"
Answer it with Apex Swarm / swarmsfall.com. Keep it under 240 chars so there's room for the URL.""",
    },
    {
        "name": "competitor_comparison",
        "weight": 1,
        "style": "vs the old way",
        "brief": """Write a tweet comparing the old way of doing things vs Apex Swarm.
Old way: juggling Notion + Zapier + Upwork + a VA + Slack + manual research.
New way: one swarm, one Telegram bot, 85 agents, zero overhead.
Don't name specific competitors. Frame it as "the old stack" vs "a swarm". End with swarmsfall.com.""",
    },
    {
        "name": "use_case_listicle",
        "weight": 2,
        "style": "short list of use cases",
        "brief": """Write a tweet that lists 3-4 specific things Apex Swarm can do for you, formatted as a punchy list.
Use line breaks or dashes. Pick concrete, varied examples from the product (gig hunting, whale watching, proposal writing, competitor tracking, etc.).
End with: "All from Telegram. swarmsfall.com" or similar.""",
    },
    {
        "name": "urgency_scarcity",
        "weight": 2,
        "style": "urgency without fakeness",
        "brief": """Write a tweet that creates real urgency around getting into Apex Swarm early.
Angle: early adopters get the advantage — while others are still doing things manually, swarm users are compounding.
Don't use fake waitlist numbers. Frame it as a competitive advantage that compounds over time.
End with swarmsfall.com.""",
    },
    {
        "name": "byok_angle",
        "weight": 1,
        "style": "BYOK / cost angle",
        "brief": """Write a tweet about Apex Swarm's BYOK (Bring Your Own Key) feature.
Angle: you bring your own Anthropic API key, so you only pay for what you use — no markup, no subscription trap.
85 agents at cost price. Compare this to bloated SaaS pricing. End with swarmsfall.com.""",
    },
    {
        "name": "thread_opener",
        "weight": 1,
        "style": "thread hook",
        "brief": """Write the opening tweet of a thread about Apex Swarm or running AI agents as a solo operator.
Bold, specific claim or a surprising fact. Must make someone want to click "show more" or "read thread".
End with 🧵. Include swarmsfall.com somewhere. Under 220 chars to leave room.""",
    },
    {
        "name": "midnight_grind",
        "weight": 2,
        "style": "always-on / while you sleep",
        "brief": """Write a tweet about the daemon system — agents that run 24/7 without you.
Angle: while you sleep, eat, or focus on real work, your swarm is hunting gigs, watching markets, monitoring competitors, writing proposals.
Make it feel aspirational — this is what leverage actually looks like for a solo founder.
End with swarmsfall.com.""",
    },
]

# Weighted pool
TWEET_POOL = []
for cat in TWEET_CATEGORIES:
    TWEET_POOL.extend([cat] * cat["weight"])

# ── Rotation tracker ──────────────────────────────────────────────────────────
def load_rotation() -> list:
    """Return list of recently used category names (last 10)."""
    if ROTATION_LOG.exists():
        try:
            return json.loads(ROTATION_LOG.read_text())
        except Exception:
            pass
    return []


def save_rotation(used: list):
    ROTATION_LOG.write_text(json.dumps(used[-15:]))


def pick_category(force: str = None):
    """Pick a category, avoiding recently used ones."""
    if force:
        cats = [c for c in TWEET_CATEGORIES if c["name"] == force]
        return cats[0] if cats else random.choice(TWEET_POOL)

    recent = load_rotation()
    # Exclude last 5 used angles to force variety
    avoid = set(recent[-5:])
    available = [c for c in TWEET_POOL if c["name"] not in avoid]
    if not available:
        available = TWEET_POOL
    return random.choice(available)


# ── Twitter client ────────────────────────────────────────────────────────────
def get_twitter_client():
    return tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_SECRET,
        wait_on_rate_limit=True,
    )


def get_recent_tweets(n: int = 10):
    """Fetch recent tweet texts to avoid duplicate content."""
    try:
        client = get_twitter_client()
        result = client.get_users_tweets(id="66307166", max_results=n, tweet_fields=["text"])
        if result.data:
            return [t.text for t in result.data]
    except Exception as e:
        log.warning(f"Could not fetch recent tweets: {e}")
    return []


# ── Claude content generation ─────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a world-class Twitter copywriter for Apex Swarm — a product for solo founders and freelancers.

Brand voice: direct, confident, specific, slightly edgy. Like a founder who ships and has receipts.
You write tweets that stop people mid-scroll and make them click.

Hard rules:
- UNDER 280 characters always (count carefully)
- Always include swarmsfall.com (counts toward 280)
- No generic filler: never say "game-changing", "revolutionary", "unlock", "unleash", "supercharge"
- No more than 1 hashtag (usually zero)
- Never start with "Apex Swarm" — vary your openings
- Be specific: name real features, real tasks, real outcomes
- No fake testimonials or made-up stats
- Return ONLY the tweet text — no quotes, no explanation"""


def generate_tweet(category: dict, recent_tweets=None) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    avoid_block = ""
    if recent_tweets:
        avoid_block = (
            "\n\nDO NOT repeat or closely echo these recently posted tweets:\n"
            + "\n".join(f"• {t[:120]}" for t in recent_tweets[:8])
            + "\n\nUse a completely different opening, structure, and angle."
        )

    # Pick a random style variation to further prevent repetition
    style_variations = [
        "Open with a bold statement.",
        "Open with a specific scenario or moment.",
        "Open with a number or surprising fact.",
        "Open with a short question.",
        "Open with a contrast (Old way: ... / New way: ...).",
        "Open with an action (imperative verb).",
    ]
    style_var = random.choice(style_variations)

    prompt = (
        f"{PRODUCT_CONTEXT}\n\n"
        f"TWEET ANGLE: {category['style'].upper()}\n\n"
        f"BRIEF: {category['brief']}\n\n"
        f"STYLE NOTE: {style_var}\n"
        f"{avoid_block}\n\n"
        f"Write the tweet now. Count characters carefully — must be under 280 including the URL."
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    tweet = message.content[0].text.strip().strip('"').strip("'")
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."
    return tweet


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
    POSTED_LOG.write_text(json.dumps(log_data[-500:], indent=2))


def post_tweet(tweet: str, category_name: str):
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
        # Update rotation tracker
        recent = load_rotation()
        recent.append(category_name)
        save_rotation(recent)
        log.info(f"Posted [{category_name}] id={tweet_id}: {tweet}")
        return entry
    except tweepy.errors.Forbidden as e:
        log.error(f"Tweet forbidden (duplicate/spam): {e}")
        return None
    except tweepy.TweepyException as e:
        log.error(f"Tweet failed: {e}")
        return None


# ── Autopost (non-interactive, used by cron/CI) ───────────────────────────────
def cmd_autopost(category_name: str = None):
    """Post one tweet. Retries up to 3x with different categories on failure."""
    recent_tweets = get_recent_tweets(10)
    tried = set()

    for attempt in range(3):
        category = pick_category(force=category_name if attempt == 0 else None)
        # Avoid retrying same category
        while category["name"] in tried and len(tried) < len(TWEET_CATEGORIES):
            category = pick_category()
        tried.add(category["name"])

        tweet = generate_tweet(category, recent_tweets=recent_tweets)
        log.info(f"Attempt {attempt+1}/3 [{category['name']}]: {tweet}")
        result = post_tweet(tweet, category["name"])
        if result:
            print(f"[autopost] {category['name']}: {tweet}")
            return result
        log.warning(f"Attempt {attempt+1} failed — retrying with different angle...")

    log.error("All 3 attempts failed.")
    return None


# ── Preview (shows tweets without posting) ────────────────────────────────────
def cmd_preview(n: int = 5):
    print(f"Generating {n} preview tweets...\n")
    for i in range(n):
        category = random.choice(TWEET_POOL)
        tweet = generate_tweet(category)
        print(f"[{i+1}/{n}] {category['name']} ({category['style']})\n{tweet}\n{'─'*60}")


# ── Interactive post (with confirmation) ──────────────────────────────────────
def cmd_post_now(category_name: str = None):
    recent_tweets = get_recent_tweets(10)
    category = pick_category(force=category_name)
    tweet = generate_tweet(category, recent_tweets=recent_tweets)
    print(f"\n[{category['name']}]\n{tweet}\n")
    confirm = input("Post? [y/N] ").strip().lower()
    if confirm == "y":
        post_tweet(tweet, category["name"])
    else:
        save_draft(tweet, category["name"])
        print("Saved to drafts.")


# ── Scheduler daemon ──────────────────────────────────────────────────────────
def build_daily_schedule(n: int) -> list:
    start, end = 7 * 3600, 23 * 3600
    return sorted(random.sample(range(start, end, 3600), n))


def run_scheduler():
    log.info(f"Scheduler started — DRAFT_MODE={DRAFT_MODE}")
    while True:
        now = datetime.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        n = random.randint(4, 6)
        schedule = build_daily_schedule(n)
        log.info(f"Today: {n} tweets at " + ", ".join(
            (midnight + timedelta(seconds=s)).strftime("%H:%M") for s in schedule))
        for s in schedule:
            target = midnight + timedelta(seconds=s)
            wait = (target - datetime.now()).total_seconds()
            if wait > 0:
                time.sleep(wait)
            cmd_autopost()
        tomorrow = midnight + timedelta(days=1, seconds=30)
        time.sleep(max((tomorrow - datetime.now()).total_seconds(), 1))


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    usage = (
        "Usage:\n"
        "  python twitter_bot.py autopost          — post one tweet (cron/CI)\n"
        "  python twitter_bot.py autopost <cat>    — post specific category\n"
        "  python twitter_bot.py post              — interactive post\n"
        "  python twitter_bot.py preview [n]       — preview N tweets\n"
        "  python twitter_bot.py run               — start scheduler daemon\n"
        "  python twitter_bot.py categories        — list all categories\n"
    )

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "autopost":
        cmd_autopost(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "post":
        cmd_post_now(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "preview":
        cmd_preview(int(sys.argv[2]) if len(sys.argv) > 2 else 5)
    elif cmd == "run":
        run_scheduler()
    elif cmd == "categories":
        for c in TWEET_CATEGORIES:
            print(f"  {c['name']:25} weight={c['weight']}  [{c['style']}]")
    else:
        print(f"Unknown command: {cmd}\n{usage}")
        sys.exit(1)
