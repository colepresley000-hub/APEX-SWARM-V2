"""
APEX SWARM — Slack outbound helpers.

Thin wrappers over Slack incoming-webhooks: a generic message sender plus two
formatted notifiers (agent-complete and daemon-alert). Falls back to the
platform default webhook (SLACK_WEBHOOK_URL) when no per-call URL is given.

Leaf module (no main import). Extracted from main.py, where these functions
had been accidentally defined three times — the last (plain-text) definition
was the one in effect, so that is what's preserved here.
"""
import logging

import httpx

from config import SLACK_WEBHOOK_URL

logger = logging.getLogger("apex-swarm")


async def send_slack_message(text, webhook_url=None, channel=None, blocks=None):
    url = webhook_url or SLACK_WEBHOOK_URL
    if not url:
        return False
    payload = {"text": text}
    if channel:
        payload["channel"] = channel
    if blocks:
        payload["blocks"] = blocks
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.error("Slack send failed: " + str(e))
        return False


async def send_slack_agent_result(agent_type, agent_name, task, result, webhook_url=None, channel=None):
    url = webhook_url or SLACK_WEBHOOK_URL
    if not url:
        return
    preview = (result or "")[:500]
    await send_slack_message(
        "Agent " + agent_name + " completed: " + preview,
        webhook_url=url, channel=channel
    )


async def send_slack_daemon_alert(agent_name, condition, result, webhook_url=None, channel=None):
    url = webhook_url or SLACK_WEBHOOK_URL
    if not url:
        return
    preview = (result or "")[:600]
    await send_slack_message(
        "ALERT from " + agent_name + " [" + condition + "]: " + preview,
        webhook_url=url, channel=channel
    )
