"""
Maasai VC Twitter Bot
---------------------
High-conversion content engine for maasai.vc.
15 distinct angles, rotation tracking, anti-repeat logic.
Posts as @maasai_vc using OAuth 1.0a user tokens.
"""

import os
import sys
import json
import random
import logging
import time
from datetime import datetime
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
        logging.FileHandler("twitter_bot_maasai.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
# Shared app credentials (iamseguncole developer app)
TWITTER_API_KEY       = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET    = os.getenv("TWITTER_API_SECRET", "")
# maasai_vc-specific user tokens (set after OAuth flow)
MAASAI_ACCESS_TOKEN   = os.getenv("MAASAI_ACCESS_TOKEN", "")
MAASAI_ACCESS_SECRET  = os.getenv("MAASAI_ACCESS_SECRET", "")
TWITTER_BEARER_TOKEN  = os.getenv("TWITTER_BEARER_TOKEN", "")
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
DRAFT_MODE            = os.getenv("MAASAI_DRAFT_MODE", "false").lower() == "true"

DRAFTS_DIR    = Path("maasai_drafts")
POSTED_LOG    = Path("maasai_posted.json")
ROTATION_LOG  = Path("maasai_rotation.json")

# ── Product context ───────────────────────────────────────────────────────────
PRODUCT_CONTEXT = """
MAASAI VC — Facts (use these for specificity, never fabricate beyond this):

What it is:
- Pan-African SaaS Capital Marketplace — NOT a traditional VC fund
- A platform/marketplace connecting founders building SaaS for Africa with capital and investors
- Deal-by-deal model, not a closed fund with a fixed pool
- URL: maasai.vc

Who it serves:
- Founders building SaaS products for African markets (any nationality, any location)
- The problem they're solving: African SaaS founders are systematically underfunded and
  overlooked by global VCs who don't understand the market
- African SaaS is one of the fastest-growing segments in emerging markets

What Maasai offers:
- Access to capital — connecting founders to investors who actually get Africa
- Network & intros — warm introductions to relevant investors, strategic partners, customers
- NOT hands-on operational support — lean, high-signal, capital-first
- A marketplace model: deal flow meets capital in one place

The macro thesis:
- Africa has 1.4B+ people, median age 19, mobile-first, growing middle class
- African SaaS penetration is <5% vs 40%+ in developed markets = massive headroom
- Most global investors are missing Africa because they lack trusted deal flow
- Maasai bridges the gap: curated, high-quality African SaaS deals to global capital

Who Segun Cole is:
- Founder of Maasai VC
- Also founder of Apex Swarm (swarmsfall.com) and ShieldClaw (shieldclaw.xyz)
- Nigerian founder, based in Lagos — building from inside the market, not outside it
- Non-traditional: no Ivy League, no Sand Hill Road — building the African capital stack from scratch

Tone notes:
- Maasai VC tweets should feel like insider African tech twitter, not generic VC twitter
- Direct, confident, no buzzword soup
- Occasional local flavor (Lagos, Nairobi, Accra, Kigali — the real hubs)
- Conviction about Africa's SaaS moment — not hope, certainty
"""

# ── Tweet categories ──────────────────────────────────────────────────────────
TWEET_CATEGORIES = [
    {
        "name": "market_size_conviction",
        "weight": 3,
        "style": "confident macro take",
        "brief": """Write a tweet about the African SaaS opportunity that feels like insider knowledge.
Not generic "Africa is growing" — pick ONE specific angle: mobile-first infrastructure,
underpenetrated verticals (healthcare, logistics, fintech, agriculture SaaS), young workforce,
or the gap between global SaaS adoption and Africa.
State it with conviction, not hope. Short, punchy.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "founder_pain_point",
        "weight": 4,
        "style": "empathetic frustration voice",
        "brief": """Write a tweet voicing the frustration of an African SaaS founder trying to raise capital.
Specific pain: VCs who don't reply, being told "too early for Africa," valuation expectations
built for SV not Lagos/Nairobi, investors who want traction you can't get without capital.
Make the founder feel seen. End with: Maasai exists for exactly this. maasai.vc. No hashtags.""",
    },
    {
        "name": "contrarian_vc_take",
        "weight": 3,
        "style": "contrarian, slightly provocative",
        "brief": """Write a contrarian take about African VC or global investors missing Africa.
Something that challenges conventional wisdom: e.g. why the best African SaaS deals aren't
in pitch competitions, why Silicon Valley VCs get Africa wrong, why the "Africa risk premium"
is a myth built on ignorance, why African SaaS multiples will outperform Western ones.
Sharp, specific, no hedging. End with maasai.vc. No hashtags.""",
    },
    {
        "name": "marketplace_model_pitch",
        "weight": 3,
        "style": "clear value prop, founder-facing",
        "brief": """Write a tweet pitching the Maasai capital marketplace model to founders.
Explain why a marketplace (not a traditional fund) is better for African SaaS founders:
no gatekeeping from a single GP, multiple investors see your deal, faster decisions,
right-fit capital vs. forced fit. Keep it simple — no jargon.
End with a CTA to maasai.vc. No hashtags.""",
    },
    {
        "name": "city_spotlight",
        "weight": 2,
        "style": "local color, insider tone",
        "brief": """Write a tweet that spotlights one specific African tech city: Lagos, Nairobi, Accra,
Kigali, Cairo, or Johannesburg. Give a real, specific observation about the SaaS ecosystem there —
what's happening, what's being built, what's been overlooked.
Don't be generic. Sound like someone who's been there, talked to founders.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "investor_angle",
        "weight": 2,
        "style": "investor-facing, FOMO-driven",
        "brief": """Write a tweet aimed at investors (angels, family offices, global VCs) who are missing Africa.
The angle: the best deals are being done quietly, global capital is sleeping on this, early-mover
advantage is closing fast. Make them feel FOMO without being cheesy.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "segun_founder_story",
        "weight": 3,
        "style": "personal, first-person, vulnerable but confident",
        "brief": """Write a first-person tweet from Segun Cole (founder) about why he built Maasai VC.
The honest version: built from inside Lagos, saw brilliant African SaaS founders hitting walls
with investors who didn't get the market, decided to build the bridge himself.
No preamble, no mission statement language — just real talk.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "africa_saas_stat",
        "weight": 2,
        "style": "data-driven, punchy",
        "brief": """Write a tweet using a striking statistic or number about African SaaS, African tech,
or African startup funding. Could be: funding gaps, mobile penetration, internet users growth,
underserved verticals, or comparison to other emerging markets.
Let the number do the work — one sharp observation after it.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "what_we_look_for",
        "weight": 2,
        "style": "direct, specific, founder-facing",
        "brief": """Write a tweet about what Maasai looks for in a deal — what makes a great African SaaS pitch.
Be specific: vertical SaaS > horizontal, deep distribution moats, founders who know the terrain,
solving real pain (not copying SV), recurring revenue with African market unit economics.
Make it feel like inside advice, not a checklist.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "problem_with_african_vc",
        "weight": 3,
        "style": "critical, honest, insider voice",
        "brief": """Write a tweet about what's broken in how capital currently flows to African startups.
Could be: too much concentrated in Nairobi/Lagos and not enough elsewhere, pitch competitions
as a substitute for real funding, impact investors who want NGO returns, VCs who parachute in
for one trip and think they understand the market.
Sharp, not bitter. Solution-framed at the end.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "direct_cta_founders",
        "weight": 4,
        "style": "direct call to action, no fluff",
        "brief": """Write a direct CTA tweet aimed at African SaaS founders looking for capital.
Zero warm-up. Hit the pain immediately (raising is hard, wrong investors worse than none),
then explain what Maasai does differently in one line.
End with a direct "submit your deck" or "let's talk" CTA at maasai.vc. No hashtags.""",
    },
    {
        "name": "diaspora_angle",
        "weight": 2,
        "style": "diaspora-aware, global yet grounded",
        "brief": """Write a tweet about the African diaspora's role in African SaaS: diaspora founders
building for the continent they know, diaspora angels who want to invest back home but don't
have a trusted channel, or the cultural advantage diaspora founders have vs. outsiders.
Specific and real — not feel-good platitudes.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "vertical_deep_dive",
        "weight": 2,
        "style": "specific, analytical, insider",
        "brief": """Write a tweet doing a deep dive on one specific African SaaS vertical:
fintech infrastructure, health-tech, agri-tech SaaS, logistics/supply chain, HR/payroll,
education, or B2B productivity. Why is that vertical at an inflection point RIGHT NOW in Africa?
One vertical, one sharp insight, specific to Africa's context.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "urgency_early_mover",
        "weight": 2,
        "style": "urgency, momentum, FOMO",
        "brief": """Write a tweet about the window of opportunity closing for early movers in African SaaS —
for founders AND investors. The next big African unicorns are being built right now. The founders
doing it aren't waiting for permission. The investors who get in early will look like geniuses in 5 years.
Create real urgency, not manufactured hype.
End with maasai.vc. No hashtags.""",
    },
    {
        "name": "question_hook",
        "weight": 2,
        "style": "engaging question, thought-provoking",
        "brief": """Write a tweet that opens with a sharp question about African SaaS, African startup funding,
or building tech on the continent. Something that stops the scroll because it challenges assumptions.
Then give your (Maasai's) perspective in 1-2 lines.
End with maasai.vc. No hashtags.""",
    },
]

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are the voice behind @maasai_vc — a Pan-African SaaS capital marketplace.

HARD RULES — violations make the tweet unusable:
- MUST be under 280 characters total (including maasai.vc). Count every character.
- NO hashtags, ever
- NO em dashes (—). Use commas or line breaks instead.
- Do NOT start with "I" as the first word
- End with maasai.vc

STYLE:
- Sound like insider African tech Twitter — direct, sharp, zero VC buzzword soup
- Build genuine FOMO and credibility, not manufactured hype
- NEVER sound like a generic VC account tweeting about "impact" and "ecosystem"
- Write like a founder who knows the African market from the inside
- Be specific: name real cities, real verticals, real founder frustrations
- When in doubt, cut. Shorter is almost always better.

CHARACTER BUDGET: maasai.vc takes 10 chars. You have 270 chars for your actual message.
Count before you output. If it's over 280, cut words not meaning.

Your goal: attract African SaaS founders to submit deals + attract global investors to the platform.
Every tweet should make someone feel either seen (founders) or like they're missing out (investors)."""


# ── Tweepy clients ────────────────────────────────────────────────────────────
def get_tweepy_client():
    return tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=MAASAI_ACCESS_TOKEN,
        access_token_secret=MAASAI_ACCESS_SECRET,
    )

def get_api_v1():
    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_SECRET,
        MAASAI_ACCESS_TOKEN, MAASAI_ACCESS_SECRET,
    )
    return tweepy.API(auth)


# ── Rotation tracking ─────────────────────────────────────────────────────────
def load_rotation():
    if ROTATION_LOG.exists():
        try:
            return json.loads(ROTATION_LOG.read_text())
        except Exception:
            pass
    return {"recent": []}

def save_rotation(data):
    ROTATION_LOG.write_text(json.dumps(data, indent=2))

def pick_category(rotation):
    recent = rotation.get("recent", [])[-5:]
    pool = [c for c in TWEET_CATEGORIES if c["name"] not in recent]
    if not pool:
        pool = TWEET_CATEGORIES[:]
    weights = [c["weight"] for c in pool]
    chosen = random.choices(pool, weights=weights, k=1)[0]
    rotation.setdefault("recent", []).append(chosen["name"])
    if len(rotation["recent"]) > 10:
        rotation["recent"] = rotation["recent"][-10:]
    return chosen, rotation


# ── Recent tweets (dedup) ─────────────────────────────────────────────────────
def get_recent_tweets():
    try:
        api = get_api_v1()
        me = api.verify_credentials()
        tweets = api.user_timeline(user_id=me.id, count=10, tweet_mode="extended")
        return [t.full_text for t in tweets]
    except Exception as e:
        log.warning(f"Could not fetch recent tweets: {e}")
        return []


# ── Anthropic client ──────────────────────────────────────────────────────────
_anthropic_client = None

def get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


# ── Tweet generation ──────────────────────────────────────────────────────────
def generate_tweet(category, recent_tweets):
    style_variation = random.choice([
        "Make it punchy and under 200 characters if possible.",
        "Use a short line break mid-tweet for rhythm.",
        "Open with a bold one-liner that could stand alone.",
        "Lead with a number or stat to anchor the tweet.",
        "Make it feel like something you'd say in a founders WhatsApp group.",
    ])

    recent_block = ""
    if recent_tweets:
        recent_block = "\n\nAVOID these recent tweets (don't repeat themes or phrases):\n" + \
                       "\n".join(f"- {t[:120]}" for t in recent_tweets[:8])

    client = get_anthropic_client()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT + "\n\n" + PRODUCT_CONTEXT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": (
                f"Category: {category['name']} ({category['style']})\n\n"
                f"Brief:\n{category['brief']}\n\n"
                f"Style note: {style_variation}"
                f"{recent_block}\n\n"
                "Write ONE tweet. No quotes, no commentary, just the tweet text."
            ),
        }],
    )

    usage = response.usage
    log.info(
        f"Tokens — input: {usage.input_tokens}, "
        f"cache_read: {getattr(usage, 'cache_read_input_tokens', 0)}, "
        f"cache_create: {getattr(usage, 'cache_creation_input_tokens', 0)}, "
        f"output: {usage.output_tokens}"
    )

    tweet = response.content[0].text.strip().strip('"').strip("'")
    # Clean up em dashes
    tweet = tweet.replace("—", ",").replace("–", ",")

    # If still over 280, ask Claude to shorten it once
    if len(tweet) > 280:
        log.warning(f"Tweet too long ({len(tweet)} chars), asking Claude to shorten")
        shorten_response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": (
                    f"This tweet is {len(tweet)} chars — too long. Shorten it to under 275 chars "
                    f"without losing the core message. Keep maasai.vc at the end. No hashtags.\n\n"
                    f"Tweet:\n{tweet}\n\n"
                    "Output only the shortened tweet, nothing else."
                ),
            }],
        )
        tweet = shorten_response.content[0].text.strip().strip('"').strip("'")
        tweet = tweet.replace("—", ",").replace("–", ",")

    return tweet


# ── Posting ───────────────────────────────────────────────────────────────────
def post_tweet(text):
    if DRAFT_MODE:
        DRAFTS_DIR.mkdir(exist_ok=True)
        fname = DRAFTS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        fname.write_text(text)
        log.info(f"[DRAFT] saved to {fname}")
        return None

    client = get_tweepy_client()
    response = client.create_tweet(text=text)
    tweet_id = response.data["id"]
    log.info(f"Posted id={tweet_id}: {text}")
    return tweet_id


# ── Main autopost ─────────────────────────────────────────────────────────────
def cmd_autopost():
    if not MAASAI_ACCESS_TOKEN or not MAASAI_ACCESS_SECRET:
        print("[autopost] ERROR: MAASAI_ACCESS_TOKEN / MAASAI_ACCESS_SECRET not set in .env")
        print("Run: python3 get_twitter_tokens.py  to generate them first.")
        return

    rotation = load_rotation()
    recent_tweets = get_recent_tweets()

    for attempt in range(1, 4):
        category, rotation = pick_category(rotation)
        tweet = generate_tweet(category, recent_tweets)

        if len(tweet) > 280:
            log.warning(f"Attempt {attempt}/3 [{category['name']}]: tweet too long ({len(tweet)} chars), retrying")
            continue

        log.info(f"Attempt {attempt}/3 [{category['name']}]: {tweet}")

        try:
            tweet_id = post_tweet(tweet)
            save_rotation(rotation)
            print(f"[autopost] {category['name']}: {tweet}")
            return
        except tweepy.TweepyException as e:
            log.error(f"Attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(2)

    log.error("All 3 attempts failed.")


# ── Preview (test without posting) ───────────────────────────────────────────
def cmd_preview():
    rotation = load_rotation()
    print("\n=== MAASAI VC — TWEET PREVIEW (5 categories) ===\n")
    seen = set()
    count = 0
    for _ in range(20):
        if count >= 5:
            break
        category, rotation = pick_category(rotation)
        if category["name"] in seen:
            continue
        seen.add(category["name"])
        tweet = generate_tweet(category, [])
        print(f"[{category['name']}] ({len(tweet)} chars)")
        print(tweet)
        print()
        count += 1


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "autopost"
    if cmd == "autopost":
        cmd_autopost()
    elif cmd == "preview":
        cmd_preview()
    else:
        print(f"Unknown command: {cmd}. Use: autopost | preview")
