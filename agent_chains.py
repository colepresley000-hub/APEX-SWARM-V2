"""
APEX SWARM - Agent Chains Module
Pipelines, multi-agent collaboration, and scheduled execution.

Features:
  1. Agent Chaining: Sequential pipelines where output feeds into next agent
  2. Multi-Agent Collaboration: Parallel execution + synthesis
  3. Scheduled Agents: Cron-style recurring tasks

File: agent_chains.py
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("apex-swarm")


# ─── PRESET PIPELINES ─────────────────────────────────────

PRESET_PIPELINES = {
    "full-crypto-analysis": {
        "name": "Full Crypto Analysis",
        "description": "Research + On-chain analysis + Portfolio recommendation",
        "category": "Crypto & DeFi",
        "steps": [
            {"agent": "research", "prompt_template": "Research the current state of {input}. Focus on recent news, developments, and market sentiment."},
            {"agent": "onchain-analyst", "prompt_template": "Based on this research:\n\n{previous_result}\n\nProvide on-chain analysis for {input}. Analyze active addresses, transaction volume, exchange flows."},
            {"agent": "portfolio-manager", "prompt_template": "Based on this research and on-chain analysis:\n\n{previous_result}\n\nProvide portfolio allocation recommendations for {input}. Include risk assessment and position sizing."},
        ],
    },
    "token-deep-dive": {
        "name": "Token Deep Dive",
        "description": "Token analysis + Whale tracking + Macro analysis",
        "category": "Crypto & DeFi",
        "steps": [
            {"agent": "token-analysis", "prompt_template": "Perform a comprehensive token analysis of {input}. Evaluate tokenomics, utility, team, and market position."},
            {"agent": "whale-tracker", "prompt_template": "Based on this token analysis:\n\n{previous_result}\n\nAnalyze whale movements and large wallet activity for {input}."},
            {"agent": "macro-analyst", "prompt_template": "Based on this token and whale analysis:\n\n{previous_result}\n\nAnalyze macro factors affecting {input}. Include Fed policy impact and global events."},
        ],
    },
    "content-pipeline": {
        "name": "Content Pipeline",
        "description": "Research + Blog post + Social media + SEO",
        "category": "Writing & Content",
        "steps": [
            {"agent": "market-researcher", "prompt_template": "Research the topic: {input}. Identify key trends, data points, and interesting angles for content creation."},
            {"agent": "blog-writer", "prompt_template": "Using this research:\n\n{previous_result}\n\nWrite a compelling blog post about {input}. Include SEO headings and engaging hooks."},
            {"agent": "social-media", "prompt_template": "Based on this blog post:\n\n{previous_result}\n\nCreate social media content for Twitter, LinkedIn, and Instagram promoting this post about {input}. Include hashtags and hooks."},
        ],
    },
    "startup-analysis": {
        "name": "Startup Analysis",
        "description": "Market research + Competitor analysis + Strategy",
        "category": "Business & Strategy",
        "steps": [
            {"agent": "market-researcher", "prompt_template": "Research the market for: {input}. Identify market size, growth trends, and customer segments."},
            {"agent": "competitor-analyst", "prompt_template": "Based on this market research:\n\n{previous_result}\n\nAnalyze key competitors in the space of {input}. Include strengths, weaknesses, and positioning."},
            {"agent": "startup-advisor", "prompt_template": "Based on this market and competitive analysis:\n\n{previous_result}\n\nProvide strategic advice for a startup in {input}. Include go-to-market strategy, risks, and key success factors."},
        ],
    },
    "code-review-pipeline": {
        "name": "Code Review Pipeline",
        "description": "Code review + Security audit + Optimized output",
        "category": "Coding & Dev",
        "steps": [
            {"agent": "code-reviewer", "prompt_template": "Review this code for bugs, best practices, and maintainability:\n\n{input}"},
            {"agent": "security-analyst", "prompt_template": "Based on this code review:\n\n{previous_result}\n\nPerform a security audit. Check for vulnerabilities, injection risks, auth issues in:\n\n{input}"},
            {"agent": "fullstack-dev", "prompt_template": "Based on this review and security audit:\n\n{previous_result}\n\nProvide the optimized, corrected version of the code with all fixes applied:\n\n{input}"},
        ],
    },
    "defi-yield-hunt": {
        "name": "DeFi Yield Hunt",
        "description": "Yield scan + Risk analysis + Gas optimization",
        "category": "Crypto & DeFi",
        "steps": [
            {"agent": "defi", "prompt_template": "Scan current DeFi yield opportunities for {input}. Include protocols, APYs, TVL, and risk levels."},
            {"agent": "smart-contract-auditor", "prompt_template": "Based on these yield opportunities:\n\n{previous_result}\n\nAssess the smart contract risks for the top protocols mentioned. Focus on audit status and known vulnerabilities."},
            {"agent": "gas-optimizer", "prompt_template": "Based on this yield and risk analysis:\n\n{previous_result}\n\nRecommend optimal entry strategies for {input} including gas optimization, timing, and transaction batching."},
        ],
    },
}

# ─── COLLABORATION TEMPLATES ──────────────────────────────

COLLAB_TEMPLATES = {
    "bull-bear-debate": {
        "name": "Bull vs Bear Debate",
        "description": "Two agents argue opposing sides, then a third synthesizes",
        "category": "Crypto & DeFi",
        "parallel_agents": [
            {"agent": "research", "prompt_template": "Make the BULLISH case for {input}. Present the strongest arguments, catalysts, and data supporting why {input} will increase in value. Be specific with numbers and evidence."},
            {"agent": "research", "prompt_template": "Make the BEARISH case for {input}. Present the strongest arguments, risks, and data supporting why {input} will decrease in value. Be specific with numbers and evidence."},
        ],
        "synthesizer": {
            "agent": "portfolio-manager",
            "prompt_template": "You've received two opposing analyses:\n\nBULLISH CASE:\n{result_0}\n\nBEARISH CASE:\n{result_1}\n\nSynthesize these into a balanced investment recommendation for {input}. Include conviction level, position sizing, and risk management.",
        },
    },
    "multi-angle-research": {
        "name": "Multi-Angle Research",
        "description": "Market + Financial + Trends perspectives combined",
        "category": "Data & Research",
        "parallel_agents": [
            {"agent": "market-researcher", "prompt_template": "Research {input} from a MARKET perspective. Focus on market size, trends, growth drivers."},
            {"agent": "financial-analyst", "prompt_template": "Research {input} from a FINANCIAL perspective. Focus on revenue, margins, valuations, financial health."},
            {"agent": "trend-analyst", "prompt_template": "Research {input} from a TRENDS perspective. Focus on emerging signals, social sentiment, search trends, adoption curves."},
        ],
        "synthesizer": {
            "agent": "report-writer",
            "prompt_template": "Synthesize these three research perspectives into a comprehensive report:\n\nMARKET ANALYSIS:\n{result_0}\n\nFINANCIAL ANALYSIS:\n{result_1}\n\nTREND ANALYSIS:\n{result_2}\n\nCreate a structured report on {input} with executive summary, key findings, and recommendations.",
        },
    },
    "code-gauntlet": {
        "name": "Code Gauntlet",
        "description": "Quality + Security + Data layer review combined",
        "category": "Coding & Dev",
        "parallel_agents": [
            {"agent": "code-reviewer", "prompt_template": "Review this code for quality, patterns, and maintainability:\n\n{input}"},
            {"agent": "security-analyst", "prompt_template": "Security audit this code for vulnerabilities:\n\n{input}"},
            {"agent": "database-architect", "prompt_template": "Review the data layer and database interactions in this code:\n\n{input}"},
        ],
        "synthesizer": {
            "agent": "fullstack-dev",
            "prompt_template": "Three experts reviewed this code:\n\nCODE QUALITY:\n{result_0}\n\nSECURITY AUDIT:\n{result_1}\n\nDATA LAYER:\n{result_2}\n\nProvide the final corrected version with all issues addressed:\n\n{input}",
        },
    },
}


# ─── CHAIN EXECUTION ──────────────────────────────────────

async def execute_chain(
    steps: list,
    user_input: str,
    execute_fn,
    db_fn,
    api_key_user: str,
    chain_id: str,
) -> list:
    """Execute a sequential chain of agents."""
    results = []
    previous_result = ""

    for i, step in enumerate(steps):
        agent_type = step["agent"]
        template = step.get("prompt_template", "{input}")

        prompt = template.replace("{input}", user_input)
        prompt = prompt.replace("{previous_result}", previous_result)

        agent_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn = db_fn()
        try:
            conn.execute(
                "INSERT INTO agents (id, user_api_key, agent_type, task_description, status, created_at) VALUES (?, ?, ?, ?, 'running', ?)",
                (agent_id, api_key_user, agent_type, f"[Chain {chain_id[:8]} Step {i+1}] {prompt[:200]}", now),
            )
            conn.commit()
        finally:
            conn.close()

        await execute_fn(agent_id, agent_type, prompt)

        conn = db_fn()
        try:
            row = conn.execute("SELECT result, status FROM agents WHERE id = ?", (agent_id,)).fetchone()
            result_text = row[0] if row else ""
            status = row[1] if row else "failed"
        finally:
            conn.close()

        results.append({
            "step": i + 1,
            "agent_type": agent_type,
            "agent_id": agent_id,
            "status": status,
            "result": result_text,
        })

        previous_result = result_text

        if status == "failed":
            logger.warning(f"Chain {chain_id[:8]} stopped at step {i+1} due to failure")
            break

    return results


# ─── MULTI-AGENT COLLABORATION ────────────────────────────

async def execute_collaboration(
    parallel_agents: list,
    synthesizer: dict,
    user_input: str,
    execute_fn,
    db_fn,
    api_key_user: str,
    collab_id: str,
) -> dict:
    """Execute multiple agents in parallel, then synthesize results."""
    parallel_tasks = []
    agent_ids = []

    for i, agent_spec in enumerate(parallel_agents):
        agent_type = agent_spec["agent"]
        template = agent_spec.get("prompt_template", "{input}")
        prompt = template.replace("{input}", user_input)

        agent_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn = db_fn()
        try:
            conn.execute(
                "INSERT INTO agents (id, user_api_key, agent_type, task_description, status, created_at) VALUES (?, ?, ?, ?, 'running', ?)",
                (agent_id, api_key_user, agent_type, f"[Collab {collab_id[:8]} Agent {i+1}] {prompt[:200]}", now),
            )
            conn.commit()
        finally:
            conn.close()

        agent_ids.append(agent_id)
        parallel_tasks.append(execute_fn(agent_id, agent_type, prompt))

    await asyncio.gather(*parallel_tasks, return_exceptions=True)

    parallel_results = []
    for i, agent_id in enumerate(agent_ids):
        conn = db_fn()
        try:
            row = conn.execute("SELECT result, status FROM agents WHERE id = ?", (agent_id,)).fetchone()
            parallel_results.append({
                "agent_id": agent_id,
                "result": row[0] if row else "No result",
                "status": row[1] if row else "failed",
            })
        finally:
            conn.close()

    # Synthesize
    synth_type = synthesizer["agent"]
    synth_template = synthesizer.get("prompt_template", "{result_0}")
    synth_prompt = synth_template.replace("{input}", user_input)
    for i, pr in enumerate(parallel_results):
        synth_prompt = synth_prompt.replace(f"{{result_{i}}}", pr["result"] or "No result available")

    synth_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = db_fn()
    try:
        conn.execute(
            "INSERT INTO agents (id, user_api_key, agent_type, task_description, status, created_at) VALUES (?, ?, ?, ?, 'running', ?)",
            (synth_id, api_key_user, synth_type, f"[Collab {collab_id[:8]} Synthesis] {synth_prompt[:200]}", now),
        )
        conn.commit()
    finally:
        conn.close()

    await execute_fn(synth_id, synth_type, synth_prompt)

    conn = db_fn()
    try:
        row = conn.execute("SELECT result, status FROM agents WHERE id = ?", (synth_id,)).fetchone()
        synth_result = row[0] if row else "Synthesis failed"
        synth_status = row[1] if row else "failed"
    finally:
        conn.close()

    return {
        "parallel_results": parallel_results,
        "synthesis": {
            "agent_id": synth_id,
            "agent_type": synth_type,
            "result": synth_result,
            "status": synth_status,
        },
    }


# ─── CRON SCHEDULE ────────────────────────────────────────

CRON_PRESETS = {
    "daily": "0 8 * * *",
    "twice-daily": "0 8,20 * * *",
    "weekly": "0 8 * * 1",
    "weekdays": "0 8 * * 1-5",
    "hourly": "0 * * * *",
    "every-6h": "0 */6 * * *",
    "every-12h": "0 */12 * * *",
    "monthly": "0 8 1 * *",
}


def parse_cron(cron_str: str) -> dict:
    """Parse a cron string into components. Supports presets."""
    if cron_str in CRON_PRESETS:
        cron_str = CRON_PRESETS[cron_str]
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron: '{cron_str}'. Expected 5 fields.")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day_of_month": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
        "raw": cron_str,
    }


def cron_matches_now(cron: dict, dt: datetime) -> bool:
    """Check if a cron schedule matches the given datetime."""
    def matches_field(field: str, value: int) -> bool:
        if field == "*":
            return True
        for part in field.split(","):
            if "-" in part:
                lo, hi = part.split("-", 1)
                if int(lo) <= value <= int(hi):
                    return True
            elif "/" in part:
                base, step = part.split("/", 1)
                base_val = 0 if base == "*" else int(base)
                if (value - base_val) % int(step) == 0 and value >= base_val:
                    return True
            else:
                if int(part) == value:
                    return True
        return False

    return (
        matches_field(cron["minute"], dt.minute)
        and matches_field(cron["hour"], dt.hour)
        and matches_field(cron["day_of_month"], dt.day)
        and matches_field(cron["month"], dt.month)
        and matches_field(cron["day_of_week"], dt.isoweekday() % 7)
    )


def describe_schedule(cron_str: str) -> str:
    """Human-readable description of a cron schedule."""
    descriptions = {
        "0 8 * * *": "Daily at 8:00 AM UTC",
        "0 8,20 * * *": "Twice daily at 8:00 AM & 8:00 PM UTC",
        "0 8 * * 1": "Weekly on Monday at 8:00 AM UTC",
        "0 8 * * 1-5": "Weekdays at 8:00 AM UTC",
        "0 * * * *": "Every hour",
        "0 */6 * * *": "Every 6 hours",
        "0 */12 * * *": "Every 12 hours",
        "0 8 1 * *": "Monthly on the 1st at 8:00 AM UTC",
    }
    resolved = CRON_PRESETS.get(cron_str, cron_str)
    return descriptions.get(resolved, f"Custom: {resolved}")
