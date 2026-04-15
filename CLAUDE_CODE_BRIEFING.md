# APEX SWARM — Claude Code Briefing
> Context for future Claude Code sessions. Keep this current.

Last updated: 2026-04-15

---

## Platform
- **URL**: https://swarmsfall.com
- **Hosting**: Railway (auto-deploy from git push)
- **DB**: PostgreSQL (Railway managed), SQLite fallback for local dev
- **Backend**: Python FastAPI (`main.py` — ~8400 lines)
- **Auth**: Gumroad license validation
- **Bot**: @ClawdClauBot on Telegram

---

## Key Files
| File | Purpose |
|------|---------|
| `main.py` | Everything — FastAPI app, all endpoints, agents, DB init |
| `channels.py` | Unified Telegram/Discord/Slack command router |
| `slash_skills.py` | 10 specialist skill modes |
| `gig_hunter.py` | Upwork RSS scraper + Firecrawl + proposal writer |
| `monte_carlo.py` | Polymarket/prediction market simulation engine |
| `cole_persona.py` | Brand voice for content agents (Cole OS) |

---

## Architecture

### Agents
85 agents defined in `AGENTS` dict in `main.py`. Each has `name`, `system` prompt, `description`. Agent execution goes through `execute_task()`.

### Channels
`channels.py` has a `CommandRouter` class with a `handle()` method. All platforms (Telegram, Discord, Slack) normalize messages to `ChannelMessage` then route through the same handler.

Command handlers in `handle()` — order matters. All handlers must `return` before reaching the agent execution fallthrough at the bottom.

### Database
PostgreSQL in production. `init_db()` runs on startup and handles migrations via `migration_columns` list (PostgreSQL path) and `sqlite_migrations` list (SQLite path).

---

## BYOK System (as of 2026-04-15)

Users can bring their own Anthropic API key to use their own credits.

### How it works
1. User sends `/setkey sk-ant-...` in Telegram
2. `channels.py` calls `POST /api/v1/byok/set-key-by-chat` with `{chat_id, anthropic_key}`
3. Backend looks up user by `telegram_chat_id` column, saves key to `users.anthropic_key`
4. Agent execution calls `get_user_anthropic_key(user_api_key)` to get the right key

### DB columns added
- `users.telegram_chat_id TEXT DEFAULT ''` — links Telegram chat to user account
- `users.anthropic_key TEXT DEFAULT ''` — stores BYOK key (server-side only)

### Endpoints
- `POST /api/v1/byok/set-key-by-chat` — set key by Telegram chat_id (no auth header needed)
- `GET /api/v1/byok/status` — check if BYOK is active for an API key

### Dependency
User must have signed up at swarmsfall.com AND connected their Telegram (so `telegram_chat_id` is populated). The `/connect` flow for linking Telegram accounts is not yet built — `/setkey` will return a 404 until a user's `telegram_chat_id` is populated.

---

## Slash Skills
`/skills` in Telegram/Discord now lists all 10 skills from `slash_skills.py`. The handler in `channels.py` must come before the agent execution fallthrough.

---

## Daemon System
Daemons are persistent background agents. Config stored in `daemon_configs` table.

- **Start**: `POST /api/v1/daemons` or `/start_daemon <preset>` in Telegram
- **Stop**: `DELETE /api/v1/daemons/{daemon_id}` or `/stop_daemon <id>` — does SET enabled=0 → stop → cancel asyncio task → hard DELETE
- **Restore**: `restore_daemons()` runs on every deploy, restores rows WHERE enabled=1. Stopped daemons are hard-deleted so they don't restore.
- **Presets**: crypto-monitor, defi-yield-scanner, news-sentinel, whale-watcher, competitor-tracker, polymarket-hunter, gig-hunter

---

## Railway Variables Required
| Variable | Value |
|----------|-------|
| `ANTHROPIC_API_KEY` | Platform fallback key |
| `TELEGRAM_BOT_TOKEN` | Full bot token from BotFather |
| `TELEGRAM_BOT_USERNAME` | `ClawdClauBot` |
| `TELEGRAM_CHAT_ID` | `7747390935` |
| `BASE_URL` | `https://swarmsfall.com` |
| `CLAUDE_MODEL` | `claude-haiku-4-5` |
| `DATABASE_URL` | Auto-set by Railway PostgreSQL |
| `FIRECRAWL_API_KEY` | Optional — enables gig_hunter web scraping |

---

## Deploy Flow
```bash
cd /Users/revcole/apex-agent/apex-swarm-v2
python3 -m py_compile main.py  # always syntax-check first
git add main.py channels.py
git commit -m "your message"
railway up
railway logs --lines 30
```

---

## Telegram Account Linking (as of 2026-04-15)

Full `/connect` flow is live:

### User flow
1. User goes to swarmsfall.com/dashboard → Settings → "Connect Telegram"
2. Clicks "Generate Connect Code" → gets a 6-char code (10 min TTL, single-use)
3. Opens Telegram, sends `/connect YOURCODE` to @ClawdClauBot
4. Bot calls `POST /api/v1/telegram/connect` → saves `telegram_chat_id` to `users` table
5. Now `/setkey sk-ant-...` works from Telegram

### Endpoints
- `POST /api/v1/telegram/generate-connect-token` — authenticated, returns `{token, expires_in, instructions, bot_url}`
- `POST /api/v1/telegram/connect` — no auth, body `{token, chat_id}`, writes `telegram_chat_id`
- `GET /api/v1/telegram/status` — authenticated, returns `{connected, chat_id}`

### channels.py handlers
- `/connect` (no args) — shows step-by-step instructions
- `/connect <CODE>` — links chat to account
- `/setkey` now shows helpful "link first with /connect" message if not linked

### Token store
In-memory dict `_tg_connect_tokens` — survives process lifetime, resets on redeploy. Tokens expire after 10 min. Single-use (consumed on verify).

---

## Known Issues / TODOs
- Gumroad license validation sometimes returns errors for valid keys (likely rate limiting or network issues on Railway).
- `cole_persona.py` exists locally but is untracked in git — not deployed to Railway.
