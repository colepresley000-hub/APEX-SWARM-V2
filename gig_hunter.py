"""
gig_hunter.py — Firecrawl-powered freelance job scraper
Searches Upwork RSS, Fiverr, and Selar for landing page / website jobs.
"""

import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

logger = logging.getLogger("apex-swarm")

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"

UPWORK_RSS_QUERIES = [
    "landing page",
    "website design",
    "saas frontend",
    "react developer",
]

UPWORK_RSS_URL = "https://www.upwork.com/ab/feed/jobs/rss?q={query}&sort=recency&paging=0%3B10"

FIVERR_SEARCH_URL = "https://www.fiverr.com/search/gigs?query={query}&filter=rating"


# ─── UPWORK ───────────────────────────────────────────────

async def fetch_upwork_jobs(queries: list[str], limit: int = 10) -> list[dict]:
    """Fetch jobs from Upwork public RSS feeds."""
    jobs = []
    async with httpx.AsyncClient(timeout=15) as client:
        for query in queries:
            url = UPWORK_RSS_URL.format(query=query.replace(" ", "+"))
            try:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    logger.warning(f"Upwork RSS returned {resp.status_code} for {query}")
                    continue
                root = ET.fromstring(resp.text)
                channel = root.find("channel")
                if channel is None:
                    continue
                for item in channel.findall("item")[:limit]:
                    title = item.findtext("title", "").strip()
                    link = item.findtext("link", "").strip()
                    description = item.findtext("description", "").strip()
                    pub_date = item.findtext("pubDate", "").strip()
                    # Extract budget from description if present
                    budget = ""
                    if "Budget:" in description:
                        start = description.index("Budget:") + 7
                        end = description.find("<", start)
                        budget = description[start:end].strip() if end > start else ""
                    jobs.append({
                        "platform": "Upwork",
                        "title": title,
                        "link": link,
                        "description": description[:500],
                        "budget": budget,
                        "posted": pub_date,
                        "query": query,
                    })
            except Exception as e:
                logger.error(f"Upwork RSS fetch failed for {query}: {e}")
    return jobs[:limit]


# ─── FIRECRAWL ────────────────────────────────────────────

async def firecrawl_scrape(url: str) -> Optional[dict]:
    """Scrape a URL using Firecrawl. Returns markdown content."""
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set — scrape skipped")
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{FIRECRAWL_BASE}/scrape",
                headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}", "Content-Type": "application/json"},
                json={"url": url, "formats": ["markdown"]},
            )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "url": url,
                "markdown": data.get("data", {}).get("markdown", ""),
                "title": data.get("data", {}).get("metadata", {}).get("title", ""),
            }
        logger.warning(f"Firecrawl scrape returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Firecrawl scrape error: {e}")
    return None


async def firecrawl_search(query: str, limit: int = 5) -> list[dict]:
    """Search the web using Firecrawl search endpoint."""
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set — search skipped")
        return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{FIRECRAWL_BASE}/search",
                headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}", "Content-Type": "application/json"},
                json={"query": query, "limit": limit},
            )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", [])
        logger.warning(f"Firecrawl search returned {resp.status_code}")
    except Exception as e:
        logger.error(f"Firecrawl search error: {e}")
    return []


# ─── PROPOSAL WRITER ─────────────────────────────────────

async def generate_proposal(job: dict, anthropic_api_key: str, model: str) -> str:
    """Write a short tailored proposal for a gig."""
    system = (
        "You are a senior freelance web developer specializing in React, Tailwind, and conversion-focused landing pages. "
        "Your portfolio: swarmsfall.com. Write a concise 80-100 word proposal that shows you understand the job, "
        "mentions 1 relevant past result, and ends with a clear call to action. Plain text only, no markdown."
    )
    user_msg = (
        f"Write a proposal for this job:\n\nTitle: {job.get('title', '')}\n"
        f"Budget: {job.get('budget', 'Not specified')}\n"
        f"Description: {job.get('description', '')[:400]}"
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 256,
                    "system": system,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )
        if resp.status_code == 200:
            return resp.json().get("content", [{}])[0].get("text", "").strip()
    except Exception as e:
        logger.error(f"Proposal generation failed: {e}")
    return ""


# ─── MAIN RUNNER ──────────────────────────────────────────

async def run_gig_hunter(
    anthropic_api_key: str,
    model: str = "claude-haiku-4-5",
    generate_proposals: bool = True,
    max_results: int = 5,
) -> list[dict]:
    """Main entry: scrape jobs, optionally generate proposals, return results."""
    jobs = await fetch_upwork_jobs(UPWORK_RSS_QUERIES, limit=max_results * 2)

    # Filter for higher budget jobs
    filtered = []
    for j in jobs:
        budget_str = j.get("budget", "").replace("$", "").replace(",", "").strip()
        try:
            budget_val = float(budget_str.split("-")[0].strip())
            if budget_val >= 100:
                filtered.append(j)
        except ValueError:
            filtered.append(j)  # Include if budget can't be parsed

    results = filtered[:max_results] if filtered else jobs[:max_results]

    if generate_proposals and anthropic_api_key:
        tasks = [generate_proposal(j, anthropic_api_key, model) for j in results]
        proposals = await asyncio.gather(*tasks)
        for j, proposal in zip(results, proposals):
            j["proposal"] = proposal

    return results


def format_gig_results(results: list[dict]) -> str:
    """Format gig results into a readable string for agent output."""
    if not results:
        return "No gig opportunities found at this time."
    lines = [f"ALERT: Found {len(results)} freelance opportunity/opportunities\n"]
    for i, job in enumerate(results, 1):
        lines.append(f"{i}. [{job.get('platform', 'Unknown')}] {job.get('title', 'Untitled')}")
        if job.get("budget"):
            lines.append(f"   Budget: {job['budget']}")
        if job.get("link"):
            lines.append(f"   Link: {job['link']}")
        if job.get("proposal"):
            lines.append(f"   Proposal: {job['proposal']}")
        lines.append("")
    return "\n".join(lines)
