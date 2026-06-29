"""
reply_engine.py — engagement growth via approved replies.
=========================================================
The real X growth lever: contextual, value-first replies to relevant
conversations. ALWAYS human-in-the-loop — nothing posts without your tap.

Flow:
  1. scan  : find candidate tweets (keywords/accounts), draft replies with
             Claude, enqueue them, and push each to Telegram with
             Approve / Skip inline buttons.
  2. worker: long-poll Telegram; on Approve -> post the reply; on Skip -> drop.

Commands:
  python reply_engine.py scan apex          # find + draft + send to Telegram
  python reply_engine.py scan maasai
  python reply_engine.py worker             # process approvals (run persistently)
  python reply_engine.py queue apex         # show pending queue

SAFETY:
  - Nothing posts automatically. You approve each reply in Telegram.
  - Daily cap per brand (brand.reply_daily_cap).
  - Already-seen tweets are never re-queued.

API NOTE:
  Tweet search (search_recent_tweets) requires X API **Basic tier or higher**
  ($100/mo). On the Free tier it returns 403. The engine degrades gracefully
  and tells you. Posting replies works on any write-enabled tier.
"""

import sys
import json
import time
import logging
from datetime import datetime, date

import tweepy
import social_core as sc
from brands import BRANDS

log = logging.getLogger("reply_engine")

import os

MIN_LIKES = 0          # recent-search returns fresh tweets (~0 likes); replying
                       # early is the play. Human approval is the quality gate.
MAX_CANDIDATES = 8     # per scan, per brand
TG_API = "https://api.telegram.org/bot{token}/{method}"

# Use a DEDICATED approval bot to avoid 409 conflicts with the live bot's
# getUpdates poller (main.py + apexunclebot.py already poll TELEGRAM_BOT_TOKEN).
# Create one via @BotFather, put its token in .env as TELEGRAM_APPROVAL_BOT_TOKEN.
APPROVAL_TOKEN = os.getenv("TELEGRAM_APPROVAL_BOT_TOKEN", "") or sc.TELEGRAM_BOT_TOKEN
_DEDICATED = bool(os.getenv("TELEGRAM_APPROVAL_BOT_TOKEN", ""))


# ── Queue persistence ─────────────────────────────────────────────────────────
def load_queue(brand) -> dict:
    f = brand.reply_queue_file
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {"pending": [], "seen_ids": [], "history": []}

def save_queue(brand, q: dict):
    q["seen_ids"] = q["seen_ids"][-2000:]
    q["history"] = q["history"][-500:]
    brand.reply_queue_file.write_text(json.dumps(q, indent=2))

def _approved_today(q: dict) -> int:
    today = date.today().isoformat()
    return sum(1 for h in q["history"]
               if h.get("status") == "approved" and h.get("at", "").startswith(today))


# ── Candidate search ──────────────────────────────────────────────────────────
def find_candidates(brand, q: dict) -> list:
    client = sc.v2_client(brand)
    seen = set(q["seen_ids"])
    found = []

    # Build a single OR query from keywords; exclude RTs/replies; English.
    # Strip embedded quotes from keywords so they can't break out of the
    # quoted phrase and corrupt the query syntax.
    kw = [k.replace('"', "").strip() for k in (brand.reply_keywords or [])[:10]]
    kw = [k for k in kw if k]
    if kw:
        terms = " OR ".join(f'"{k}"' for k in kw)
        query = f"({terms}) -is:retweet -is:reply lang:en"
        try:
            resp = client.search_recent_tweets(
                query=query, max_results=20,
                tweet_fields=["public_metrics", "author_id", "created_at", "lang"],
            )
            for t in (resp.data or []):
                tid = str(t.id)
                if tid in seen:
                    continue
                seen.add(tid)  # dedup within this scan's batch too
                m = t.public_metrics or {}
                if m.get("like_count", 0) < MIN_LIKES:
                    continue
                found.append({
                    "id": str(t.id),
                    "text": t.text,
                    "author_id": str(t.author_id),
                    "metrics": m,
                })
        except tweepy.errors.Forbidden:
            print(f"[{brand.key}] ⚠️  search_recent_tweets returned 403 — "
                  f"X API Basic tier ($100/mo) required for search. "
                  f"Reply posting still works; only auto-discovery is gated.")
        except tweepy.TweepyException as e:
            log.warning(f"[{brand.key}] search failed: {e}")
        except Exception as e:
            # Raw network errors (ConnectionReset, DNS, timeouts) are NOT
            # TweepyExceptions and would otherwise crash the scan.
            log.warning(f"[{brand.key}] search network error: {e}")

    return found[:MAX_CANDIDATES]


# ── Reply drafting ────────────────────────────────────────────────────────────
def draft_reply(brand, tweet_text: str) -> str:
    client = sc.anthropic_client()
    msg = client.messages.create(
        model=sc.CLAUDE_MODEL,
        max_tokens=160,
        system=[{"type": "text",
                 "text": brand.system_prompt + "\n\nREPLY MODE:\n" + brand.reply_guidance,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": brand.product_context}],
        messages=[{"role": "user", "content": (
            f"Someone tweeted:\n\"{tweet_text}\"\n\n"
            "Write a single reply that adds genuine value and feels human. "
            "Follow the REPLY MODE rules exactly. Output only the reply text."
        )}],
    )
    return sc.clean_text(msg.content[0].text)


# ── Telegram approval ─────────────────────────────────────────────────────────
def _tg(method, payload):
    import requests
    url = TG_API.format(token=APPROVAL_TOKEN, method=method)
    try:
        return requests.post(url, json=payload, timeout=35).json()
    except Exception as e:
        log.error(f"Telegram {method} failed: {e}")
        return None

def send_for_approval(brand, item: dict):
    orig = item["candidate_text"]
    reply = item["reply_text"]
    tweet_url = f"https://x.com/i/web/status/{item['tweet_id']}"
    text = (
        f"<b>🔁 {brand.name} reply draft</b>\n\n"
        f"<b>They said:</b>\n{orig[:280]}\n\n"
        f"<b>Your reply ({len(reply)} chars):</b>\n{reply}\n\n"
        f"<a href=\"{tweet_url}\">view original tweet</a>"
    )
    markup = {"inline_keyboard": [[
        {"text": "✅ Approve & post", "callback_data": f"rok:{brand.key}:{item['id']}"},
        {"text": "✏️ Skip",           "callback_data": f"rno:{brand.key}:{item['id']}"},
    ]]}
    _tg("sendMessage", {
        "chat_id": sc.TELEGRAM_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
        "reply_markup": markup,
    })


# ── Scan command ──────────────────────────────────────────────────────────────
def scan(brand):
    if not _DEDICATED:
        print(f"[{brand.key}] skipped — set TELEGRAM_APPROVAL_BOT_TOKEN in .env first "
              f"(dedicated @BotFather bot, avoids conflict with the live bot).")
        return
    if not brand.has_tokens():
        print(f"[{brand.key}] no tokens set; skipping (run get_twitter_tokens.py)")
        return
    q = load_queue(brand)
    remaining = brand.reply_daily_cap - _approved_today(q)
    if remaining <= 0:
        print(f"[{brand.key}] daily cap ({brand.reply_daily_cap}) reached")
        return

    candidates = find_candidates(brand, q)
    if not candidates:
        print(f"[{brand.key}] no new candidates this scan")
        return

    n_new = 0
    for c in candidates:
        # Respect the daily cap: never queue more than remaining slots.
        if n_new >= remaining:
            break
        # Mark every candidate seen first so a network failure mid-draft
        # doesn't cause us to retry the same tweet next scan.
        q["seen_ids"].append(c["id"])
        try:
            reply = draft_reply(brand, c["text"])
            if not reply or len(reply) > 280:
                continue
            item = {
                "id": f"{brand.key}_{c['id']}",
                "tweet_id": c["id"],
                "candidate_text": c["text"],
                "reply_text": reply,
                "queued_at": datetime.now().isoformat(),
                "status": "pending",
            }
            q["pending"].append(item)
            send_for_approval(brand, item)
            n_new += 1
            time.sleep(1)
        except Exception as e:
            # One bad candidate (network/API blip) must not abort the scan.
            log.warning(f"[{brand.key}] candidate {c['id']} failed: {e}")
            continue

    save_queue(brand, q)
    print(f"[{brand.key}] queued {n_new} draft replies → check Telegram to approve")


# ── Approval worker (long-poll Telegram) ──────────────────────────────────────
STALE_HOURS = 24  # drafts older than this target tweets likely gone/changed

def _is_stale(item) -> bool:
    ts = item.get("queued_at", "")
    if not ts:
        return False
    try:
        age = datetime.now() - datetime.fromisoformat(ts)
        return age.total_seconds() > STALE_HOURS * 3600
    except Exception:
        return False

def _post_reply(brand, item):
    """Returns (ok: bool, note: str). note is a human-friendly result."""
    if _is_stale(item):
        return False, "skipped — draft too old, target tweet likely gone (run a fresh scan)"
    if brand.draft_mode:
        log.info(f"[{brand.key}] DRAFT MODE — would reply: {item['reply_text']}")
        return True, "posted ✅ (draft mode)"
    client = sc.v2_client(brand)
    try:
        resp = client.create_tweet(text=item["reply_text"],
                                   in_reply_to_tweet_id=item["tweet_id"])
        if not resp or not resp.data or not resp.data.get("id"):
            return False, "post FAILED ❌ (no tweet id returned)"
        return True, "posted ✅"
    except tweepy.errors.Forbidden as e:
        log.error(f"[{brand.key}] reply post forbidden: {e}")
        return False, "post FAILED ❌ — target tweet deleted, protected, or you're blocked"
    except tweepy.TweepyException as e:
        log.error(f"[{brand.key}] reply post failed: {e}")
        if sc.is_credit_error(e):
            sc.notify_credits_empty(brand, "Approved reply could not post.")
            return False, "post FAILED ❌ — X API credits empty"
        return False, f"post FAILED ❌ — {str(e)[:80]}"

def _find_item(brand_key, item_id):
    brand = BRANDS[brand_key]
    q = load_queue(brand)
    for it in q["pending"]:
        if it["id"] == item_id:
            return brand, q, it
    return brand, q, None

def worker():
    if not APPROVAL_TOKEN:
        print("Telegram not configured"); return
    if not _DEDICATED:
        print("⚠️  TELEGRAM_APPROVAL_BOT_TOKEN not set — falling back to the main bot "
              "token. This WILL conflict (409) with main.py/apexunclebot pollers.\n"
              "    Create a dedicated bot via @BotFather and set "
              "TELEGRAM_APPROVAL_BOT_TOKEN in .env before running the worker.")
        return
    print("Reply approval worker started — waiting for taps. Ctrl-C to stop.")
    offset = None
    while True:
        resp = _tg("getUpdates", {"timeout": 30, "offset": offset,
                                  "allowed_updates": ["callback_query"]})
        if not resp or not resp.get("ok"):
            time.sleep(3); continue
        result = resp.get("result")
        if not isinstance(result, list):
            time.sleep(3); continue
        for upd in result:
            # Always advance the offset first so a malformed/failed update is
            # never re-polled into an infinite loop, and a mid-update crash
            # can't replay the same update on restart.
            try:
                offset = upd["update_id"] + 1
            except (KeyError, TypeError):
                continue
            try:
                cq = upd.get("callback_query")
                if not cq or "id" not in cq:
                    continue
                data = cq.get("data", "")
                if not (data.startswith("rok:") or data.startswith("rno:")):
                    continue
                parts = data.split(":", 2)
                if len(parts) != 3 or not parts[1] or not parts[2]:
                    continue
                _, brand_key, item_id = parts
                if brand_key not in BRANDS:
                    continue
                action = "approved" if data.startswith("rok:") else "skipped"
                brand, q, item = _find_item(brand_key, item_id)
                if not item:
                    _tg("answerCallbackQuery", {"callback_query_id": cq["id"],
                                                "text": "Already handled"})
                    continue

                note = "skipped"
                if action == "approved":
                    ok, note = _post_reply(brand, item)
                    item["status"] = "approved" if ok else "failed"
                else:
                    item["status"] = "skipped"

                q["pending"] = [p for p in q["pending"] if p["id"] != item_id]
                q["history"].append({"id": item_id, "status": item["status"],
                                     "at": datetime.now().isoformat()})
                save_queue(brand, q)
                _tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": note})
                # edit the message to show resolution
                msg = cq.get("message", {})
                if msg and "chat" in msg and "message_id" in msg:
                    _tg("editMessageText", {
                        "chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                        "text": msg.get("text", "") + f"\n\n— {note}",
                        "disable_web_page_preview": True,
                    })
            except Exception as e:
                # Never let one bad update kill the long-poll worker.
                log.error(f"worker: failed to process update {upd.get('update_id')}: {e}")
                continue


def show_queue(brand):
    q = load_queue(brand)
    print(f"[{brand.key}] pending: {len(q['pending'])}, "
          f"approved today: {_approved_today(q)}/{brand.reply_daily_cap}")
    for it in q["pending"]:
        print(f"  - reply: {it['reply_text'][:90]}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "scan":
        target = sys.argv[2] if len(sys.argv) > 2 else "all"
        brands = BRANDS.values() if target == "all" else [BRANDS[target]]
        for b in brands:
            scan(b)
    elif cmd == "worker":
        worker()
    elif cmd == "queue":
        target = sys.argv[2] if len(sys.argv) > 2 else "apex"
        show_queue(BRANDS[target])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
