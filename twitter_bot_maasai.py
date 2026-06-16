"""
Maasai VC Twitter Bot — thin entrypoint.
All logic lives in social_core.py; brand config in brands.py.

Usage:
  python twitter_bot_maasai.py autopost [category]   — post one tweet (cron)
  python twitter_bot_maasai.py autopost --visual      — post with a quote-card image
  python twitter_bot_maasai.py thread "<topic>"       — post a thread
  python twitter_bot_maasai.py preview [n]            — preview without posting
  python twitter_bot_maasai.py categories             — list categories
"""
import sys
import social_core as sc
from brands import MAASAI


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "autopost"

    if cmd == "autopost":
        rest = sys.argv[2:]
        with_visual = "--visual" in rest
        cat = next((a for a in rest if not a.startswith("--")), None)
        sc.autopost(MAASAI, category_name=cat, with_visual=with_visual)

    elif cmd == "thread":
        topic = sys.argv[2] if len(sys.argv) > 2 else (
            "Why global investors are sleeping on African SaaS, and the window is closing"
        )
        tweets = sc.generate_thread(MAASAI, topic, n_tweets=5)
        print(f"\n=== {MAASAI.name} THREAD ({len(tweets)} tweets) ===\n")
        for i, t in enumerate(tweets, 1):
            print(f"[{i}] ({len(t)} chars) {t}\n")
        sc.post_thread(MAASAI, tweets)

    elif cmd == "preview":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        sc.preview(MAASAI, n)

    elif cmd == "categories":
        for c in MAASAI.categories:
            print(f"  {c['name']:25} weight={c['weight']}  [{c['style']}]")

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
