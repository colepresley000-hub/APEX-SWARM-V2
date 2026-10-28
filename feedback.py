"""
feedback.py — performance feedback loop.
Pulls public engagement metrics for recently posted tweets, scores them,
and writes the top performers to <brand>_winners.json. social_core injects
those winners as few-shot examples so content compounds in quality.

Run on a schedule (e.g. daily):
  python feedback.py apex
  python feedback.py maasai
  python feedback.py all
"""

import sys
import json
import logging

import tweepy
import social_core as sc
from brands import BRANDS

log = logging.getLogger("feedback")

# Engagement weighting — replies and RTs signal reach far more than likes
W_LIKE, W_RT, W_REPLY, W_QUOTE = 1.0, 3.0, 4.0, 3.0
MAX_WINNERS = 10
LOOKBACK = 80  # most recent posted tweets to score


def _score(m: dict) -> float:
    return (
        m.get("like_count", 0)   * W_LIKE +
        m.get("retweet_count", 0) * W_RT +
        m.get("reply_count", 0)  * W_REPLY +
        m.get("quote_count", 0)  * W_QUOTE
    )


def refresh_winners(brand) -> dict:
    if not getattr(brand, "active", True):
        return {"brand": brand.key, "skipped": "inactive"}
    if not brand.has_tokens():
        msg = f"[{brand.key}] no tokens set; skipping feedback"
        log.warning(msg); print(msg)
        return {"brand": brand.key, "skipped": True}

    posted = sc.load_posted(brand)
    if not posted:
        print(f"[{brand.key}] no posted tweets yet")
        return {"brand": brand.key, "scored": 0}

    # Collect tweet ids we have on record (skip threads' child ids).
    # Normalize ids to str so they match str(t.id) from the API regardless of
    # whether older entries stored ids as int or str.
    recent = posted[-LOOKBACK:]
    id_map = {str(e["id"]): e for e in recent if e.get("id")}
    ids = list(id_map.keys())

    if not ids:
        print(f"[{brand.key}] no scoreable tweet ids on record")
        return {"brand": brand.key, "scored": 0}

    client = sc.v2_client(brand)
    scored = []
    any_batch_ok = False
    # X API allows 100 ids per get_tweets call
    for i in range(0, len(ids), 100):
        batch = ids[i:i+100]
        if not batch:
            continue
        try:
            resp = client.get_tweets(batch, tweet_fields=["public_metrics"])
        except tweepy.TweepyException as e:
            log.warning(f"[{brand.key}] metrics fetch failed: {e}")
            continue
        any_batch_ok = True
        for t in (resp.data or []):
            m = t.public_metrics or {}
            entry = id_map.get(str(t.id), {})
            scored.append({
                "id": str(t.id),
                "text": entry.get("text", ""),
                "category": entry.get("category", ""),
                "metrics": m,
                "score": _score(m),
            })

    if not any_batch_ok:
        # Every request failed (outage/rate limit). Keep the existing winners
        # instead of overwriting them with an empty list.
        print(f"[{brand.key}] metrics unavailable; keeping existing winners")
        return {"brand": brand.key, "scored": 0, "error": "metrics unavailable"}

    scored.sort(key=lambda x: x["score"], reverse=True)
    winners = [w for w in scored if w["score"] > 0][:MAX_WINNERS]

    # Atomic-ish write: temp file + rename so a crash mid-write can't corrupt JSON.
    wf = brand.winners_file
    tmp = wf.with_suffix(wf.suffix + ".tmp")
    tmp.write_text(json.dumps(winners, indent=2))
    tmp.replace(wf)

    # Category performance summary
    cat_perf = {}
    for s in scored:
        c = s["category"] or "unknown"
        cat_perf.setdefault(c, []).append(s["score"])
    cat_avg = {c: round(sum(v) / len(v), 1) for c, v in cat_perf.items()}
    ranked = sorted(cat_avg.items(), key=lambda x: x[1], reverse=True)

    print(f"\n[{brand.key}] scored {len(scored)} tweets, saved {len(winners)} winners")
    if winners:
        print(f"  Top tweet (score {winners[0]['score']:.0f}): {winners[0]['text'][:80]}")
    if ranked:
        print("  Best categories: " + ", ".join(f"{c}={s}" for c, s in ranked[:5]))

    return {"brand": brand.key, "scored": len(scored),
            "winners": len(winners), "category_avg": cat_avg}


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target == "all":
        brands = list(BRANDS.values())
    elif target in BRANDS:
        brands = [BRANDS[target]]
    else:
        print(f"Unknown brand '{target}'. Available: {list(BRANDS)} or 'all'")
        sys.exit(1)
    summary = [refresh_winners(b) for b in brands]
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
