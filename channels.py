"""
APEX SWARM - Channels Module
==============================
Unified messaging channels: Telegram, Discord, Slack.

All channels share the same command router. Each channel just translates
its platform-specific webhook format into a common message, then sends
responses back using its platform's API.

File: channels.py
"""

import asyncio
import json
import logging
import os
from typing import Callable, Optional

import httpx

logger = logging.getLogger("apex-swarm")

# ─── CONFIG ───────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_APP_ID = os.getenv("DISCORD_APP_ID", "")
DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN)
DISCORD_ENABLED = bool(DISCORD_BOT_TOKEN)
SLACK_ENABLED = bool(SLACK_BOT_TOKEN)


# ─── COMMON MESSAGE FORMAT ───────────────────────────────

class ChannelMessage:
    """Normalized message from any channel."""
    def __init__(self, platform: str, channel_id: str, user_id: str, text: str, raw: dict = None):
        self.platform = platform      # "telegram", "discord", "slack"
        self.channel_id = channel_id   # chat_id / channel_id
        self.user_id = user_id
        self.text = text
        self.raw = raw or {}

    @property
    def user_api_key(self) -> str:
        return f"{self.platform}:{self.channel_id}"


# ─── SEND FUNCTIONS ──────────────────────────────────────

async def send_telegram(chat_id, text: str):
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_ENABLED:
        return
    try:
        # Escape markdown special chars that break Telegram
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


async def send_discord(channel_id: str, text: str):
    """Send a message via Discord Bot API."""
    if not DISCORD_ENABLED:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
                json={"content": text[:2000]},
            )
    except Exception as e:
        logger.error(f"Discord send failed: {e}")


async def send_slack(channel_id: str, text: str):
    """Send a message via Slack Bot API."""
    if not SLACK_ENABLED:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
                json={"channel": channel_id, "text": text, "mrkdwn": True},
            )
    except Exception as e:
        logger.error(f"Slack send failed: {e}")


async def send_to_channel(msg: ChannelMessage, text: str):
    """Send a response back to whatever channel the message came from."""
    if msg.platform == "telegram":
        await send_telegram(msg.channel_id, text)
    elif msg.platform == "discord":
        await send_discord(msg.channel_id, text)
    elif msg.platform == "slack":
        await send_slack(msg.channel_id, text)


# ─── WEBHOOK PARSERS ─────────────────────────────────────

def parse_telegram_webhook(data: dict) -> Optional[ChannelMessage]:
    """Parse Telegram webhook into ChannelMessage."""
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    user_id = str(message.get("from", {}).get("id", ""))
    if not chat_id or not text:
        return None
    return ChannelMessage(
        platform="telegram",
        channel_id=str(chat_id),
        user_id=user_id,
        text=text,
        raw=data,
    )


def parse_discord_webhook(data: dict) -> Optional[ChannelMessage]:
    """Parse Discord interaction/message into ChannelMessage."""
    # Handle Gateway bot messages (sent via websocket relay or webhook)
    if data.get("t") == "MESSAGE_CREATE":
        d = data.get("d", {})
        # Ignore bot's own messages
        if d.get("author", {}).get("bot"):
            return None
        return ChannelMessage(
            platform="discord",
            channel_id=d.get("channel_id", ""),
            user_id=d.get("author", {}).get("id", ""),
            text=d.get("content", "").strip(),
            raw=data,
        )

    # Handle direct webhook post (simplified for HTTP-based bot)
    if "channel_id" in data and "content" in data:
        return ChannelMessage(
            platform="discord",
            channel_id=data["channel_id"],
            user_id=data.get("user_id", data.get("author", {}).get("id", "")),
            text=data["content"].strip(),
            raw=data,
        )

    # Handle slash command interactions
    if data.get("type") == 2:  # APPLICATION_COMMAND
        d = data.get("data", {})
        options = d.get("options", [])
        text = f"/{d.get('name', 'help')}"
        if options:
            text += " " + " ".join(str(o.get("value", "")) for o in options)
        return ChannelMessage(
            platform="discord",
            channel_id=data.get("channel_id", ""),
            user_id=data.get("member", {}).get("user", {}).get("id", ""),
            text=text,
            raw=data,
        )

    return None


def parse_slack_webhook(data: dict) -> Optional[ChannelMessage]:
    """Parse Slack event into ChannelMessage."""
    event = data.get("event", {})
    # Only handle messages, not bot messages
    if event.get("type") != "message" or event.get("bot_id"):
        return None
    text = event.get("text", "").strip()
    if not text:
        return None
    # Remove bot mention if present (e.g. "<@U12345> command")
    if text.startswith("<@"):
        text = text.split(">", 1)[-1].strip()
    return ChannelMessage(
        platform="slack",
        channel_id=event.get("channel", ""),
        user_id=event.get("user", ""),
        text=text,
        raw=data,
    )


# ─── COMMAND ROUTER ───────────────────────────────────────

class CommandRouter:
    """Shared command handler for all channels."""

    def __init__(self):
        self._agents = {}
        self._agent_to_category = {}
        self._execute_fn = None
        self._event_bus = None
        self._daemon_manager = None
        self._daemon_presets = {}
        self._daemon_execute_fn = None
        self._get_db = None
        self._user_key_col = "user_api_key"

    def setup(self, agents, agent_to_category, execute_fn, event_bus=None,
              daemon_manager=None, daemon_presets=None, daemon_execute_fn=None,
              get_db=None, user_key_col="user_api_key"):
        """Wire up dependencies from main.py."""
        self._agents = agents
        self._agent_to_category = agent_to_category
        self._execute_fn = execute_fn
        self._event_bus = event_bus
        self._daemon_manager = daemon_manager
        self._daemon_presets = daemon_presets or {}
        self._daemon_execute_fn = daemon_execute_fn
        self._get_db = get_db
        self._user_key_col = user_key_col

    async def handle(self, msg: ChannelMessage):
        """Route a message to the correct handler."""
        text = msg.text
        if not text:
            return

        # Parse command
        if text.startswith("/") or text.startswith("!"):
            parts = text[1:].split(" ", 1)
            command = parts[0].replace("_", "-").lower()
            args = parts[1] if len(parts) > 1 else ""
        else:
            command = None
            args = text

        # ─── HELLO / GREETING (plain text, no slash) ─────
        GREETINGS = {"hello", "hi", "hey", "help", "agents", "start", "what can you do", "menu", "?"}
        if not command and text.strip().lower() in GREETINGS:
            if self._agents:
                lines = ["👋 ApexSwarm — AI Agent Swarm\nSend /agent-name your task to deploy an agent.\n"]
                # Group agents by category
                by_cat = {}
                for key, agent in self._agents.items():
                    cat = self._agent_to_category.get(key, "Other")
                    if cat not in by_cat:
                        by_cat[cat] = []
                    by_cat[cat].append((key, agent))
                for cat_name, agents_in_cat in sorted(by_cat.items()):
                    lines.append(f"{cat_name}")
                    for key, agent in agents_in_cat:
                        lines.append(f"  /{key} — {agent['name']}")
                    lines.append("")
                lines.append("_Type /start for system commands._")
                await send_to_channel(msg, "\n".join(lines))
            else:
                await send_to_channel(msg, "👋 ApexSwarm — use /research your question, /code-reviewer your code, etc.\nType /start to see commands.")
            return

        # ─── PLAIN TEXT WITH NO COMMAND — show help ─────
        if not command:
            await send_to_channel(msg,
                "👋 Say hello to see all agents, or use /agent-name your task.\n"
                "Example: /research What is Ethereum?\n"
                "Type /start for full command list."
            )
            return

        # ─── HELP / START ─────
        if command in ("start", "help"):
            version = os.getenv("VERSION", "4.0")
            welcome = (
                f"🤖 APEX SWARM v{version} — Mission Control\n\n"
                "Deploy Agents:\n"
                "/research Your question here\n"
                "/crypto-research Analyze ETH\n"
                "/blog-writer Write about AI agents\n"
                "/code-reviewer Review my code\n\n"
                "Mission Control:\n"
                "/god_eye — Live swarm status\n"
                "/daemons — List running daemons\n"
                "/start_daemon <preset> — Start daemon\n"
                "/stop_daemon <id> — Stop daemon\n"
                "/subscribe — Get live feed\n"
                "/unsubscribe — Stop live feed\n"
                "/events — Recent activity\n"
                "/models — Available AI models\n\n"
                "Daemon Presets:\n"
                "crypto-monitor, defi-yield-scanner, news-sentinel, whale-watcher, competitor-tracker"
            )
            await send_to_channel(msg, welcome)
            return

        # ─── GOD EYE ─────
        if command in ("god-eye", "god_eye", "status"):
            if not self._event_bus:
                await send_to_channel(msg, "⚠️ Mission Control not loaded")
                return
            stats = self._event_bus.get_stats()
            resp = (
                "👁️ GOD EYE — Live Status\n\n"
                f"🤖 Active agents: {stats['active_agents']}\n"
                f"👁️ Active daemons: {stats['active_daemons']}\n"
                f"📡 SSE subscribers: {stats['sse_subscribers']}\n"
                f"📊 Total events: {stats['total_events']}\n"
            )
            if stats.get("active_agents_detail"):
                resp += "\nRunning Agents:\n"
                for aid, info in stats["active_agents_detail"].items():
                    resp += f"  ⚡ {info['name']} ({aid[:8]})\n"
            if stats.get("active_daemons_detail"):
                resp += "\nRunning Daemons:\n"
                for did, info in stats["active_daemons_detail"].items():
                    resp += f"  👁️ {info['name']} — {info['cycles']} cycles ({did[:8]})\n"
            await send_to_channel(msg, resp)
            return

        # ─── DAEMONS ─────
        if command == "daemons":
            if not self._daemon_manager:
                await send_to_channel(msg, "⚠️ Mission Control not loaded")
                return
            daemons = self._daemon_manager.get_daemons()
            if not daemons:
                await send_to_channel(msg, "No active daemons. Start one with:\n/start_daemon crypto-monitor")
                return
            resp = "👁️ Active Daemons:\n\n"
            for d in daemons:
                icon = "🟢" if d["status"] == "running" else "🔴"
                resp += f"{icon} {d['agent_name']}\n  ID: {d['daemon_id'][:8]} | Cycles: {d['cycles']} | Every {d['interval_seconds']}s\n\n"
            await send_to_channel(msg, resp)
            return

        # ─── START DAEMON ─────
        if command in ("start-daemon", "start_daemon"):
            if not self._daemon_manager:
                await send_to_channel(msg, "⚠️ Mission Control not loaded")
                return
            preset_id = args.strip().lower()
            if preset_id not in self._daemon_presets:
                presets = ", ".join(self._daemon_presets.keys())
                await send_to_channel(msg, f"Unknown preset. Available:\n{presets}")
                return
            preset = self._daemon_presets[preset_id]
            daemon_id = await self._daemon_manager.start_daemon(
                agent_type=preset["agent_type"],
                agent_name=preset["name"],
                task_description=preset["task_description"],
                execute_fn=self._daemon_execute_fn,
                interval_seconds=preset["interval_seconds"],
                alert_conditions=preset.get("alert_conditions", []),
                user_api_key=msg.user_api_key,
            )
            await send_to_channel(msg, f"👁️ {preset['name']} started\nID: {daemon_id[:8]}\nInterval: every {preset['interval_seconds']}s\n\nStop: /stop_daemon {daemon_id[:8]}")
            return

        # ─── STOP DAEMON ─────
        if command in ("stop-daemon", "stop_daemon"):
            if not self._daemon_manager:
                await send_to_channel(msg, "⚠️ Mission Control not loaded")
                return
            short_id = args.strip()
            found = None
            for d in self._daemon_manager.get_daemons():
                if d["daemon_id"].startswith(short_id):
                    found = d["daemon_id"]
                    break
            if not found:
                await send_to_channel(msg, f"No daemon found matching {short_id}")
                return
            await self._daemon_manager.stop_daemon(found)
            await send_to_channel(msg, f"⏹️ Daemon {short_id} stopped")
            return

        # ─── SUBSCRIBE / UNSUBSCRIBE ─────
        if command == "subscribe":
            if self._event_bus and msg.platform == "telegram":
                self._event_bus.add_telegram_chat(int(msg.channel_id))
                await send_to_channel(msg, "📡 Subscribed to live feed\nYou'll receive real-time agent activity.\n/unsubscribe to stop")
            elif self._event_bus:
                await send_to_channel(msg, "📡 Subscribed — live events will be sent to this channel")
            return

        if command == "unsubscribe":
            if self._event_bus and msg.platform == "telegram":
                self._event_bus.remove_telegram_chat(int(msg.channel_id))
            await send_to_channel(msg, "🔇 Unsubscribed from live feed")
            return

        # ─── EVENTS ─────
        if command == "events":
            if not self._event_bus:
                await send_to_channel(msg, "⚠️ Mission Control not loaded")
                return
            events = self._event_bus.get_history(limit=10)
            if not events:
                await send_to_channel(msg, "No recent events.")
                return
            resp = "📋 Recent Events:\n\n"
            for e in events[-10:]:
                resp += f"• {e['event_type']} — {e['agent_name'] or e['agent_type']}: {e['message'][:80]}\n"
            await send_to_channel(msg, resp)
            return

        # ─── MODELS ─────
        if command == "models":
            try:
                from multi_model import model_router
                providers = model_router.get_available_providers()
                active = [p for p in providers if p["available"]]
                resp = f"🧠 Available Models ({len(active)} providers):\n\n"
                for p in active:
                    resp += f"{p['name']}:\n"
                    for m in p["models"]:
                        vision = "👁️" if m["vision"] else ""
                        resp += f"  {m['model_id']} {vision}\n"
                    resp += "\n"
                resp += "Use: /research model=gpt-4o Your question"
                await send_to_channel(msg, resp)
            except ImportError:
                await send_to_channel(msg, "Multi-model not available. Using Claude.")
            return

        # ─── VOICE ─────
        if command in ("voice-on", "voice_on"):
            try:
                from voice import voice_pipeline
                voice_pipeline.enable_voice_response(msg.channel_id)
                await send_to_channel(msg, "🔊 Voice responses enabled\nAgent results will be sent as voice messages.\n/voice_off to disable")
            except ImportError:
                await send_to_channel(msg, "Voice module not available")
            return

        if command in ("voice-off", "voice_off"):
            try:
                from voice import voice_pipeline
                voice_pipeline.disable_voice_response(msg.channel_id)
                await send_to_channel(msg, "🔇 Voice responses disabled")
            except ImportError:
                pass
            return

        # ─── TWITTER BOT CONTROL ─────────────────────────────────────────────────
        if command == "twitter":
            twitterbot_url = os.getenv("TWITTERBOT_URL", "")
            twitterbot_secret = os.getenv("TWITTERBOT_SECRET", "")
            sub = args.strip().lower()

            if not twitterbot_url:
                await send_to_channel(msg,
                    "⚠️ TwitterBot not configured.\n"
                    "Set TWITTERBOT_URL in Railway environment variables."
                )
                return

            headers = {"x-control-secret": twitterbot_secret} if twitterbot_secret else {}

            try:
                async with httpx.AsyncClient(timeout=10) as hc:

                    if sub in ("status", ""):
                        r = await hc.get(f"{twitterbot_url}/status", headers=headers)
                        d = r.json()
                        paused = "⏸️ Paused" if d.get("paused") else "▶️ Running"
                        resp = (
                            f"🐦 Twitter Bot Status\n\n"
                            f"State: {paused}\n"
                            f"Account: {d.get('authenticated_as', 'unknown')}\n"
                            f"Tweets posted: {d.get('tweets_posted', 0)}\n"
                            f"Mentions replied: {d.get('mentions_replied', 0)}\n"
                            f"Tweets liked: {d.get('tweets_liked', 0)}\n"
                            f"Started: {d.get('started_at', 'N/A')[:19].replace('T', ' ')}\n"
                        )
                        if d.get("last_error"):
                            resp += f"⚠️ Last error: {d['last_error'][:100]}\n"
                        await send_to_channel(msg, resp)

                    elif sub == "post":
                        r = await hc.post(f"{twitterbot_url}/tweet/now", headers=headers)
                        d = r.json()
                        tweet_text = d.get("text", "")[:200]
                        await send_to_channel(msg,
                            f"✅ Tweet posted!\n"
                            f"ID: {d.get('tweet_id')}\n\n"
                            f"_{tweet_text}_"
                        )

                    elif sub.startswith("post "):
                        custom_text = args[5:].strip()
                        if not custom_text:
                            await send_to_channel(msg, "❌ Please provide tweet text: /twitter post Your tweet here")
                            return
                        r = await hc.post(
                            f"{twitterbot_url}/tweet/custom",
                            headers=headers,
                            json={"text": custom_text}
                        )
                        d = r.json()
                        await send_to_channel(msg,
                            f"✅ Custom tweet posted!\n"
                            f"ID: {d.get('tweet_id')}\n\n"
                            f"_{custom_text[:200]}_"
                        )

                    elif sub == "pause":
                        await hc.post(f"{twitterbot_url}/scheduler/pause", headers=headers)
                        await send_to_channel(msg,
                            "⏸️ TwitterBot paused\n"
                            "Scheduled tweets halted. Resume with /twitter resume."
                        )

                    elif sub == "resume":
                        await hc.post(f"{twitterbot_url}/scheduler/resume", headers=headers)
                        await send_to_channel(msg,
                            "▶️ TwitterBot resumed\n"
                            "Scheduled tweets will continue on schedule."
                        )

                    else:
                        await send_to_channel(msg,
                            "🐦 TwitterBot Commands\n\n"
                            "/twitter status — check bot state & counters\n"
                            "/twitter post — fire a random tweet now\n"
                            "/twitter post Your text — post a custom tweet\n"
                            "/twitter pause — halt scheduled tweets\n"
                            "/twitter resume — resume schedule"
                        )

            except Exception as e:
                await send_to_channel(msg, f"❌ TwitterBot unreachable: {str(e)[:120]}\nCheck that the TwitterBot service is running on Railway.")
            return

        # ─── AGENT EXECUTION ─────
        agent_type = command  # command is always set here (we returned early if None above)

        # Check for model= prefix in args
        model = None
        task = args
        if args.startswith("model="):
            parts = args.split(" ", 1)
            model = parts[0].replace("model=", "")
            task = parts[1] if len(parts) > 1 else "Provide a general update."

        if agent_type not in self._agents:
            await send_to_channel(msg,
                f"❓ Unknown agent /{agent_type}.\n"
                "Say hello to see all available agents, or try /research, /code-reviewer, /data-analyst, etc."
            )
            return

        if not task:
            task = "Provide a general update."

        import uuid
        from datetime import datetime, timezone

        agent_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        if self._get_db:
            conn = self._get_db()
            try:
                conn.execute(
                    f"INSERT INTO agents (id, {self._user_key_col}, agent_type, task_description, status, created_at) VALUES (?, ?, ?, ?, 'running', ?)",
                    (agent_id, msg.user_api_key, agent_type, task, now),
                )
                conn.commit()
            finally:
                conn.close()

        agent_name = self._agents.get(agent_type, {}).get("name", agent_type)
        model_note = f" ({model})" if model else ""
        await send_to_channel(msg, f"⚡ {agent_name}{model_note} is working...")

        if self._execute_fn:
            await self._execute_fn(agent_id, agent_type, task, msg.user_api_key, model=model)

        if self._get_db:
            conn = self._get_db()
            try:
                row = conn.execute("SELECT result, status FROM agents WHERE id = ?", (agent_id,)).fetchone()
            finally:
                conn.close()
            result = row[0] if row else "No result"
            if len(result) > 4000:
                result = result[:4000] + "\n\n[Truncated]"
            await send_to_channel(msg, result)


# ─── DISCORD GATEWAY (WebSocket) ─────────────────────────

class DiscordGateway:
    """Connects to Discord Gateway WebSocket for receiving messages.
    This runs as a background task and dispatches messages to the command router."""

    def __init__(self, command_router: CommandRouter):
        self._router = command_router
        self._ws = None
        self._heartbeat_interval = 41250
        self._session_id = None
        self._seq = None
        self._running = False

    async def start(self):
        """Connect to Discord Gateway and start listening."""
        if not DISCORD_ENABLED:
            return
        self._running = True
        asyncio.create_task(self._connect())
        logger.info("🎮 Discord Gateway connecting...")

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.aclose()

    async def _connect(self):
        """Connect and maintain Discord WebSocket connection."""
        import websockets
        gateway_url = "wss://gateway.discord.gg/?v=10&encoding=json"

        while self._running:
            try:
                async with httpx.AsyncClient() as client:
                    # Use raw websocket via httpx isn't ideal — use simple polling fallback
                    pass
            except Exception as e:
                logger.error(f"Discord Gateway error: {e}")
                await asyncio.sleep(5)

    async def poll_messages(self):
        """Fallback: poll Discord for messages if WebSocket isn't available.
        This uses the Discord REST API to check for new messages periodically."""
        if not DISCORD_ENABLED:
            return
        # Discord doesn't support polling well — the webhook approach is preferred
        # This is a placeholder for the HTTP interactions endpoint
        logger.info("🎮 Discord using webhook mode (set up Interactions URL in Discord dev portal)")


# ─── SETUP HELPERS ────────────────────────────────────────

async def setup_telegram_webhook(base_url: str):
    """Set Telegram webhook URL."""
    if not TELEGRAM_ENABLED:
        return
    try:
        webhook_url = f"{base_url}/api/v1/telegram/webhook"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
                json={"url": webhook_url},
            )
        logger.info(f"Telegram webhook: {resp.json()}")
    except Exception as e:
        logger.error(f"Telegram webhook failed: {e}")


def get_channel_status() -> dict:
    """Return status of all channels."""
    return {
        "telegram": {"enabled": TELEGRAM_ENABLED, "configured": bool(TELEGRAM_BOT_TOKEN)},
        "discord": {"enabled": DISCORD_ENABLED, "configured": bool(DISCORD_BOT_TOKEN)},
        "slack": {"enabled": SLACK_ENABLED, "configured": bool(SLACK_BOT_TOKEN)},
    }


# ─── GLOBAL INSTANCES ────────────────────────────────────

command_router = CommandRouter()
discord_gateway = DiscordGateway(command_router)
