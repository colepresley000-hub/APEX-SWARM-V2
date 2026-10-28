"""
crosspost.py — one idea, many channels.
========================================
Generate a single piece of content per brand, then fan it out to every
enabled channel, reformatted appropriately for each. Solo-founder leverage:
the creative work is done once; distribution multiplies it.

Channels:
  - x         : Twitter/X (LIVE — uses social_core)
  - linkedin  : LinkedIn (implemented; enable by setting LINKEDIN_ACCESS_TOKEN
                + LINKEDIN_AUTHOR_URN in .env)
  - telegram  : broadcast to a Telegram channel (enable with TELEGRAM_BROADCAST_CHAT)

apex_marketer already handles IG/TikTok/YouTube; this complements it for
text-first channels.

Usage:
  python crosspost.py apex            # generate 1 idea, fan out to enabled channels
  python crosspost.py maasai
  python crosspost.py apex --dry      # show what would post, post nothing
"""

import os
import sys
import json
import logging
from abc import ABC, abstractmethod
from typing import Optional

import social_core as sc
from brands import BRANDS

log = logging.getLogger("crosspost")


# ── Channel adapters ──────────────────────────────────────────────────────────
class Channel(ABC):
    name = "base"
    @abstractmethod
    def enabled(self) -> bool: ...
    @abstractmethod
    def adapt(self, brand, idea: str) -> str:
        """Reformat the core idea for this channel's norms."""
    @abstractmethod
    def post(self, brand, text: str, media_path: Optional[str] = None) -> Optional[dict]: ...


class XChannel(Channel):
    name = "x"
    def enabled(self): return True  # primary channel
    def adapt(self, brand, idea):
        # X idea is already tweet-shaped; ensure length. _shorten may itself
        # return something >280 (it's a best-effort rewrite), so hard-cap as
        # a final guard before posting.
        if len(idea) <= 280:
            return idea
        shortened = sc._shorten(brand, idea)
        return shortened if len(shortened) <= 280 else sc.truncate_keep_url(shortened, brand.url)
    def post(self, brand, text, media_path=None):
        return sc.post_tweet(brand, text, "crosspost", media_path=media_path)


class LinkedInChannel(Channel):
    name = "linkedin"
    def __init__(self):
        self.token = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
        self.author = os.getenv("LINKEDIN_AUTHOR_URN", "")  # e.g. urn:li:person:XXXX
    def enabled(self):
        return bool(self.token and self.author)
    def adapt(self, brand, idea):
        """Expand the tweet into a short LinkedIn post (longer-form allowed)."""
        client = sc.anthropic_client()
        msg = client.messages.create(
            model=sc.CLAUDE_MODEL, max_tokens=400,
            system=[{"type": "text", "text": brand.system_prompt,
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": brand.product_context}],
            messages=[{"role": "user", "content": (
                f"Turn this tweet into a LinkedIn post (3-5 short lines, a touch more "
                f"professional, still no buzzwords, end with {brand.url}). "
                f"No hashtags beyond at most 2 relevant ones.\n\nTweet:\n{idea}"
            )}],
        )
        return sc.clean_text(sc._first_text(msg))
    def post(self, brand, text, media_path=None):
        import requests
        body = {
            "author": self.author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        try:
            r = requests.post(
                "https://api.linkedin.com/v2/ugcPosts",
                headers={"Authorization": f"Bearer {self.token}",
                         "X-Restli-Protocol-Version": "2.0.0",
                         "Content-Type": "application/json"},
                json=body, timeout=20,
            )
            ok = r.status_code in (200, 201)
            if not ok:
                log.error(f"LinkedIn post failed {r.status_code}: {r.text[:200]}")
            return {"channel": "linkedin", "ok": ok}
        except Exception as e:
            log.error(f"LinkedIn error: {e}")
            return {"channel": "linkedin", "ok": False}


class TelegramChannel(Channel):
    name = "telegram"
    def __init__(self):
        self.chat = os.getenv("TELEGRAM_BROADCAST_CHAT", "")
    def enabled(self):
        return bool(self.chat and sc.TELEGRAM_BOT_TOKEN)
    def adapt(self, brand, idea):
        return idea  # tweet text works fine for a Telegram broadcast
    def post(self, brand, text, media_path=None):
        import requests
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{sc.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": self.chat, "text": text}, timeout=15)
            return {"channel": "telegram", "ok": r.ok}
        except Exception as e:
            log.error(f"Telegram broadcast error: {e}")
            return {"channel": "telegram", "ok": False}


CHANNELS = [XChannel(), LinkedInChannel(), TelegramChannel()]


# ── Fan-out ───────────────────────────────────────────────────────────────────
def fan_out(brand, dry: bool = False):
    # 1) Generate ONE core idea (a strong tweet) using the live engine
    recent = sc.get_recent_tweets(brand, 10)
    category = sc.pick_category(brand)
    idea = sc.generate_tweet(brand, category, recent_tweets=recent)
    print(f"\n=== {brand.name} core idea [{category['name']}] ===\n{idea}\n")

    results = []
    for ch in CHANNELS:
        if not ch.enabled():
            print(f"[{ch.name}] disabled (missing config) — skipping")
            results.append({"channel": ch.name, "enabled": False})
            continue
        # X posting needs this brand's user tokens (e.g. Maasai has none yet);
        # without this guard we'd attempt a doomed post on every run.
        if ch.name == "x" and not brand.has_tokens() and not dry:
            print(f"[x] {brand.key} has no access tokens — skipping")
            results.append({"channel": "x", "enabled": False, "reason": "no tokens"})
            continue
        # One channel failing (API/network error) must not abort the others.
        try:
            text = ch.adapt(brand, idea)
            print(f"\n--- {ch.name} version ---\n{text}\n")
            if dry:
                results.append({"channel": ch.name, "dry": True})
                continue
            res = ch.post(brand, text)
            results.append({"channel": ch.name, "result": res})
        except Exception as e:
            log.error(f"[{ch.name}] failed: {e}")
            results.append({"channel": ch.name, "error": str(e)[:200]})

    return results


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    brand = BRANDS.get(sys.argv[1])
    if not brand:
        print(f"Unknown brand. Available: {list(BRANDS)}"); return
    dry = "--dry" in sys.argv
    out = fan_out(brand, dry=dry)
    print("\n" + json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
