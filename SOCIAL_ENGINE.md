# Social Engine — Operations Guide

One brand-config-driven engine powering automated, compounding social growth for
**Apex Swarm** (live) and **Maasai VC** (pending account access). Built for leverage:
generate once, distribute everywhere, learn from what works.

## Architecture

```
social_core.py      Engine: generation, posting, threads, rotation, feedback, Telegram
brands.py           Brand configs (voice, facts, categories, themes, reply targets)
visual_cards.py     Branded quote-card PNGs (Pillow)
feedback.py         Pulls engagement metrics → winners → few-shot examples
reply_engine.py     Keyword-scan → Claude reply drafts → Telegram approval → post
crosspost.py        One idea → X + LinkedIn + Telegram broadcast
twitter_bot.py          Apex entrypoint (thin)
twitter_bot_maasai.py   Maasai entrypoint (thin)
get_twitter_tokens.py   OAuth PIN flow to mint per-account tokens
social_data/        Runtime state (rotation, posted log, winners, queues, cards)
```

Adding a brand = one `BrandConfig` in `brands.py`. Adding a channel = one
`Channel` subclass in `crosspost.py`.

## Daily automation (macOS cron + launchd)

| When (WAT) | What | Job |
|---|---|---|
| 07:30 | Refresh winners from metrics | `feedback.py all` |
| 9/12/15/18/21 | Post 1 tweet (Apex) | `twitter_bot.py autopost` |
| 9/12/15/18/21 | Post 1 tweet (Maasai) | `twitter_bot_maasai.py autopost` |
| 10/14/19 | Scan + draft replies | `reply_engine.py scan all` |
| always | Approve replies | `reply_engine.py worker` (launchd) |

## Commands

```bash
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3

# Single tweet (cron uses this)
$PY twitter_bot.py autopost                 # random angle, anti-repeat
$PY twitter_bot.py autopost specific_feature # forced category
$PY twitter_bot.py autopost --visual         # attach a branded quote card

# Threads (3-5x reach)
$PY twitter_bot.py thread "Why solo founders should run agent swarms"

# Preview without posting
$PY twitter_bot.py preview 5

# Feedback loop (which angles win)
$PY feedback.py apex

# Cross-post one idea everywhere
$PY crosspost.py apex --dry      # preview per-channel
$PY crosspost.py apex            # post to enabled channels

# Reply engine
$PY reply_engine.py scan apex    # draft replies → Telegram
$PY reply_engine.py queue apex   # show pending
$PY reply_engine.py worker       # process approvals (needs dedicated bot)
```

## What's live vs. what needs config

| Feature | Status | To enable |
|---|---|---|
| Apex tweets (5x/day) | ✅ LIVE | — |
| Threads | ✅ LIVE | — |
| Visual quote cards | ✅ LIVE | add `--visual` to autopost |
| Feedback loop | ✅ LIVE | runs daily 07:30 |
| Reply drafting | ✅ works | — |
| Reply approval worker | ⚙️ needs token | `TELEGRAM_APPROVAL_BOT_TOKEN` (see below) |
| Maasai tweets | ⚙️ needs tokens | regain @maasai_vc → `get_twitter_tokens.py` |
| LinkedIn cross-post | ⚙️ needs token | `LINKEDIN_ACCESS_TOKEN` + `LINKEDIN_AUTHOR_URN` |
| Telegram broadcast | ⚙️ needs chat | `TELEGRAM_BROADCAST_CHAT` |

### Enabling the reply approval worker
The live bot (`main.py` + `apexunclebot.py`) already polls Telegram on the main
token. A second poller on that token causes **409 conflicts**, so the approval
worker needs its **own** bot:

1. Message **@BotFather** → `/newbot` → get a token
2. Add to `.env`: `TELEGRAM_APPROVAL_BOT_TOKEN=<token>`
3. Send that new bot any message once (so it can DM you)
4. Load the worker:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.shieldclaw.replyworker.plist
   ```
5. Reply drafts now arrive with **Approve / Skip** buttons; only approved
   replies post. Daily cap per brand (Apex 12, Maasai 10).

### Enabling Maasai posting
Once you regain @maasai_vc (X support identity verification, then update the
email): `$PY get_twitter_tokens.py` → authorize as maasai_vc → paste PIN →
add `MAASAI_ACCESS_TOKEN` + `MAASAI_ACCESS_SECRET` to `.env`. Cron already
scheduled; it posts at the next slot.

## Safety
- Reply engine is **human-in-the-loop** — nothing posts without your tap.
- Per-brand daily caps, seen-id dedup, quality gating (min likes on targets).
- All generation enforces <280 chars, no hashtags, no em dashes, anti-repeat.
- `*_DRAFT_MODE=true` in `.env` makes any bot write drafts instead of posting.
