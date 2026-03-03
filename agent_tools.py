"""
APEX SWARM - Agent Tools Module v2
====================================
12 built-in tools + MCP dynamic tool routing.

Built-in Tools:
  - web_search: Search via DuckDuckGo HTML
  - fetch_url: Read any webpage content
  - crypto_prices: Live prices from CoinGecko
  - run_code: Python sandbox (math, json, statistics, datetime, re, collections)
  - send_email: Send emails via SMTP
  - screenshot_url: Capture webpage screenshot via external API
  - json_api: Call any JSON REST API (GET/POST/PUT/DELETE)
  - data_transform: Transform, filter, aggregate JSON data
  - sentiment_analysis: Analyze text sentiment and extract entities
  - generate_chart: Generate chart data for visualization
  - rss_feed: Parse RSS/Atom feeds for monitoring
  - send_webhook: Send webhook notifications to Slack/Discord/custom

File: agent_tools.py
"""

import asyncio
import html
import json
import logging
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("apex-swarm")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "swarm@apex-swarm.com")
SCREENSHOT_API = os.getenv("SCREENSHOT_API", "")

TOOL_DEFINITIONS = [
    {
        "name": "web_search",
        "description": "Search the web for current information. Returns top results with titles, URLs, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": "Fetch and read the text content of any webpage URL.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full URL (https://...)"}},
            "required": ["url"],
        },
    },
    {
        "name": "crypto_prices",
        "description": "Get live cryptocurrency prices, market cap, 24h change, and volume from CoinGecko.",
        "input_schema": {
            "type": "object",
            "properties": {
                "coins": {"type": "string", "description": "Comma-separated CoinGecko IDs"},
                "vs_currency": {"type": "string", "description": "Currency (default: usd)", "default": "usd"},
            },
            "required": ["coins"],
        },
    },
    {
        "name": "run_code",
        "description": "Execute Python code in a sandbox with math, json, statistics, datetime, re, collections.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python code to execute"}},
            "required": ["code"],
        },
    },
    {
        "name": "json_api",
        "description": "Call any JSON REST API. Supports GET, POST, PUT, DELETE with custom headers and body.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "API endpoint URL"},
                "method": {"type": "string", "description": "HTTP method", "default": "GET"},
                "headers": {"type": "object", "description": "HTTP headers", "default": {}},
                "body": {"type": "object", "description": "JSON body for POST/PUT", "default": {}},
                "params": {"type": "object", "description": "URL query parameters", "default": {}},
            },
            "required": ["url"],
        },
    },
    {
        "name": "data_transform",
        "description": "Transform, filter, sort, aggregate JSON data. Operations: filter, sort, group_by, aggregate, select, limit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {"type": "array", "description": "Array of objects to transform"},
                "operations": {"type": "array", "description": "List of operations"},
            },
            "required": ["data", "operations"],
        },
    },
    {
        "name": "sentiment_analysis",
        "description": "Analyze text for sentiment, extract key topics, compute readability metrics. No API needed.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to analyze"}},
            "required": ["text"],
        },
    },
    {
        "name": "rss_feed",
        "description": "Parse an RSS or Atom feed URL. Returns titles, links, dates, summaries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "RSS/Atom feed URL"},
                "limit": {"type": "integer", "description": "Max entries (default: 10)", "default": 10},
            },
            "required": ["url"],
        },
    },
    {
        "name": "send_webhook",
        "description": "Send webhook notification to Slack, Discord, or custom URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Webhook URL"},
                "message": {"type": "string", "description": "Message text"},
                "platform": {"type": "string", "description": "slack, discord, or custom", "default": "custom"},
                "extra_data": {"type": "object", "description": "Additional payload", "default": {}},
            },
            "required": ["url", "message"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email. Requires SMTP config. Use for alerts, reports, notifications.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email"},
                "subject": {"type": "string", "description": "Subject"},
                "body": {"type": "string", "description": "Email body"},
                "is_html": {"type": "boolean", "description": "HTML body?", "default": False},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "generate_chart",
        "description": "Generate chart data config. Supports: line, bar, pie, scatter, area.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string", "description": "line, bar, pie, scatter, area"},
                "title": {"type": "string", "description": "Chart title"},
                "data": {"type": "array", "description": "Data points"},
                "x_field": {"type": "string", "description": "X-axis field"},
                "y_field": {"type": "string", "description": "Y-axis field"},
                "group_field": {"type": "string", "description": "Grouping field", "default": ""},
            },
            "required": ["chart_type", "data", "x_field", "y_field"],
        },
    },
    {
        "name": "screenshot_url",
        "description": "Capture a webpage screenshot. Requires SCREENSHOT_API config.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to screenshot"},
                "width": {"type": "integer", "description": "Width (default: 1280)", "default": 1280},
                "height": {"type": "integer", "description": "Height (default: 800)", "default": 800},
            },
            "required": ["url"],
        },
    },
]

CATEGORY_TOOLS = {
    "Crypto & DeFi": ["web_search", "fetch_url", "crypto_prices", "run_code", "json_api", "data_transform", "rss_feed", "send_webhook", "sentiment_analysis", "generate_chart"],
    "Coding & Dev": ["web_search", "fetch_url", "run_code", "json_api", "data_transform", "send_webhook", "screenshot_url"],
    "Writing & Content": ["web_search", "fetch_url", "sentiment_analysis", "rss_feed", "send_email"],
    "Data & Research": ["web_search", "fetch_url", "run_code", "json_api", "data_transform", "rss_feed", "sentiment_analysis", "generate_chart"],
    "Business & Strategy": ["web_search", "fetch_url", "run_code", "json_api", "data_transform", "rss_feed", "sentiment_analysis", "send_email", "generate_chart"],
    "Productivity": ["web_search", "fetch_url", "json_api", "send_webhook", "send_email", "rss_feed"],
    "DevOps & Monitoring": ["web_search", "fetch_url", "json_api", "run_code", "data_transform", "rss_feed", "send_webhook", "screenshot_url", "generate_chart"],
    "Intelligence & OSINT": ["web_search", "fetch_url", "json_api", "rss_feed", "sentiment_analysis", "data_transform", "send_webhook"],
    "Sales & Growth": ["web_search", "fetch_url", "json_api", "data_transform", "sentiment_analysis", "send_email", "generate_chart", "run_code"],
}


def get_tools_for_agent(category: str) -> list[dict]:
    allowed = CATEGORY_TOOLS.get(category, ["web_search", "fetch_url"])
    return [t for t in TOOL_DEFINITIONS if t["name"] in allowed]


# ─── TOOL IMPLEMENTATIONS ────────────────────────────────

async def tool_web_search(query: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get("https://html.duckduckgo.com/html/", params={"q": query},
                                    headers={"User-Agent": "Mozilla/5.0 (compatible; ApexSwarm/3.4)"})
        if resp.status_code != 200:
            return f"Search failed (HTTP {resp.status_code})"
        text = resp.text
        results = []
        snippets = re.findall(r'<a rel="nofollow" class="result__a" href="([^"]*)"[^>]*>(.*?)</a>.*?<a class="result__snippet"[^>]*>(.*?)</a>', text, re.DOTALL)
        if not snippets:
            snippets_simple = re.findall(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', text, re.DOTALL)
            for url, title in snippets_simple[:5]:
                results.append(f"- {html.unescape(re.sub(r'<[^>]+>', '', title).strip())}\n  {url}")
        else:
            for url, title, snippet in snippets[:5]:
                results.append(f"- {html.unescape(re.sub(r'<[^>]+>', '', title).strip())}\n  {url}\n  {html.unescape(re.sub(r'<[^>]+>', '', snippet).strip())}")
        return f"Search results for '{query}':\n\n" + "\n\n".join(results) if results else f"No results for: {query}"
    except Exception as e:
        return f"Search error: {str(e)}"


async def tool_fetch_url(url: str) -> str:
    try:
        if not url.startswith(("http://", "https://")): return "Invalid URL"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ApexSwarm/3.4)"})
        if resp.status_code != 200: return f"Failed (HTTP {resp.status_code})"
        ct = resp.headers.get("content-type", "")
        if "json" in ct:
            try: return json.dumps(resp.json(), indent=2)[:8000]
            except: pass
        text = resp.text
        for tag in ["script", "style", "nav", "footer", "header"]:
            text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL)
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip())
        return text[:6000] + "\n\n[Truncated]" if len(text) > 6000 else (text or "No readable content.")
    except Exception as e:
        return f"Fetch error: {str(e)}"


async def tool_crypto_prices(coins: str, vs_currency: str = "usd") -> str:
    try:
        ids = ",".join(c.strip().lower() for c in coins.split(",") if c.strip())[:10]
        if not ids: return "No coins specified"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.coingecko.com/api/v3/simple/price",
                                    params={"ids": ids, "vs_currencies": vs_currency, "include_market_cap": "true", "include_24hr_vol": "true", "include_24hr_change": "true"})
        if resp.status_code != 200: return f"CoinGecko error (HTTP {resp.status_code})"
        data = resp.json()
        if not data: return f"No data for: {ids}"
        def fmt(n):
            if not n: return "N/A"
            return f"${n/1e9:.2f}B" if n >= 1e9 else f"${n/1e6:.2f}M" if n >= 1e6 else f"${n:,.0f}"
        lines = [f"Live Prices ({vs_currency.upper()}):\n"]
        for cid, info in data.items():
            p = info.get(vs_currency, "N/A")
            c = info.get(f"{vs_currency}_24h_change", 0)
            cs = f"+{c:.2f}%" if c and c > 0 else f"{c:.2f}%" if c else "N/A"
            ps = f"${p:,.2f}" if isinstance(p, (int, float)) else str(p)
            lines.append(f"**{cid.upper()}**: {ps} ({cs})\n  MCap: {fmt(info.get(f'{vs_currency}_market_cap', 0))} | Vol: {fmt(info.get(f'{vs_currency}_24h_vol', 0))}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"Price error: {str(e)}"


def tool_run_code(code: str) -> str:
    BLOCKED = ["import os", "import sys", "import subprocess", "import shutil", "__import__", "open(",
               "import socket", "import requests", "import httpx", "import urllib", "import ctypes",
               "import pickle", "import shelve", "breakpoint(", "import signal", "import threading"]
    for b in BLOCKED:
        if b in code: return f"Blocked: '{b}'"
    old_out, old_err = sys.stdout, sys.stderr
    cout, cerr = StringIO(), StringIO()
    import math as _m; import json as _j; import re as _r
    from collections import Counter as _C, defaultdict as _dd
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    sg = {
        "__builtins__": {
            "print": print, "range": range, "len": len, "int": int, "float": float, "str": str,
            "list": list, "dict": dict, "tuple": tuple, "set": set, "bool": bool, "type": type,
            "abs": abs, "round": round, "min": min, "max": max, "sum": sum, "sorted": sorted,
            "enumerate": enumerate, "zip": zip, "map": map, "filter": filter, "reversed": reversed,
            "isinstance": isinstance, "any": any, "all": all, "chr": chr, "ord": ord, "hex": hex,
            "bin": bin, "format": format, "hash": hash, "hasattr": hasattr, "getattr": getattr,
            "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
            "IndexError": IndexError, "Exception": Exception, "True": True, "False": False, "None": None,
        },
        "math": _m, "json": _j, "re": _r, "Counter": _C, "defaultdict": _dd,
        "datetime": _dt, "timedelta": _td, "timezone": _tz,
    }
    try:
        import statistics; sg["statistics"] = statistics
    except: pass
    try:
        sys.stdout, sys.stderr = cout, cerr
        exec(code, sg)
        out = cout.getvalue() + (("\nSTDERR:\n" + cerr.getvalue()) if cerr.getvalue() else "")
        return (out.strip() or "(No output)")[:4000]
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)}"
    finally:
        sys.stdout, sys.stderr = old_out, old_err


async def tool_json_api(url: str, method: str = "GET", headers: dict = None, body: dict = None, params: dict = None) -> str:
    try:
        if not url.startswith(("http://", "https://")): return "Invalid URL"
        h = headers or {}
        if "User-Agent" not in h: h["User-Agent"] = "ApexSwarm/3.4"
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            m = method.upper()
            if m == "GET": resp = await client.get(url, headers=h, params=params or {})
            elif m == "POST": resp = await client.post(url, headers=h, json=body or {}, params=params or {})
            elif m == "PUT": resp = await client.put(url, headers=h, json=body or {})
            elif m == "DELETE": resp = await client.delete(url, headers=h)
            else: return f"Unsupported: {m}"
        try:
            r = json.dumps(resp.json(), indent=2)
            return f"HTTP {resp.status_code}\n{r[:6000]}"
        except:
            return f"HTTP {resp.status_code}\n{resp.text[:4000]}"
    except Exception as e:
        return f"API error: {str(e)}"


def tool_data_transform(data: list, operations: list) -> str:
    try:
        if not isinstance(data, list): return "Error: data must be array"
        result = list(data)
        for od in operations:
            op = od.get("op", "")
            if op == "filter":
                f, c, v = od.get("field",""), od.get("cond","eq"), od.get("value")
                def match(item):
                    iv = item.get(f)
                    if c == "eq": return iv == v
                    if c == "neq": return iv != v
                    if c == "gt": return iv is not None and iv > v
                    if c == "lt": return iv is not None and iv < v
                    if c == "gte": return iv is not None and iv >= v
                    if c == "lte": return iv is not None and iv <= v
                    if c == "contains": return isinstance(iv, str) and str(v).lower() in iv.lower()
                    return False
                result = [i for i in result if match(i)]
            elif op == "sort":
                result.sort(key=lambda x: x.get(od.get("field",""), ""), reverse=(od.get("order","asc") == "desc"))
            elif op == "select":
                fs = od.get("fields", [])
                result = [{ff: i.get(ff) for ff in fs} for i in result]
            elif op == "limit":
                result = result[:od.get("count", 10)]
            elif op == "group_by":
                f, agg, af = od.get("field",""), od.get("agg","count"), od.get("agg_field","")
                groups = {}
                for i in result:
                    k = str(i.get(f, "?"))
                    groups.setdefault(k, []).append(i)
                gr = []
                for k, items in groups.items():
                    e = {"group": k, "count": len(items)}
                    if agg == "sum" and af: e["sum"] = sum(i.get(af, 0) or 0 for i in items)
                    elif agg == "avg" and af: vals = [i.get(af, 0) or 0 for i in items]; e["avg"] = sum(vals)/len(vals) if vals else 0
                    gr.append(e)
                result = gr
        o = json.dumps(result, indent=2)
        return f"Result ({len(result)} items):\n{o[:4000]}"
    except Exception as e:
        return f"Transform error: {str(e)}"


def tool_sentiment_analysis(text: str) -> str:
    try:
        words = text.lower().split()
        wc = len(words)
        if wc == 0: return "Empty text"
        pos_w = {"good","great","excellent","amazing","wonderful","fantastic","love","best","happy","bullish","growth","profit","gain","success","win","strong","positive","improve","innovative","opportunity","surge","rally","boom","breakthrough","upgrade"}
        neg_w = {"bad","terrible","awful","horrible","hate","worst","sad","bearish","loss","decline","fail","weak","negative","crash","dump","scam","fraud","risk","warning","concern","fear","drop","plunge","recession","crisis","bankruptcy","collapse"}
        pc = sum(1 for w in words if w.strip(".,!?;:") in pos_w)
        nc = sum(1 for w in words if w.strip(".,!?;:") in neg_w)
        ts = pc - nc
        s = "strongly positive" if ts > 2 else "positive" if ts > 0 else "strongly negative" if ts < -2 else "negative" if ts < 0 else "neutral"
        stop = {"the","a","an","is","are","was","were","be","been","have","has","had","do","does","did","will","would","to","of","in","for","on","with","at","by","from","as","not","but","and","or","if","this","that","it","its","i","we","they"}
        topics = Counter(w.strip(".,!?;:()[]") for w in words if w.strip(".,!?;:()[]") not in stop and len(w) > 2)
        sents = len(re.split(r'[.!?]+', text))
        return json.dumps({"sentiment": s, "score": round(max(-1, min(1, ts/max(wc*0.1, 1))), 3), "positive": pc, "negative": nc, "topics": [w for w, _ in topics.most_common(10)], "words": wc, "sentences": sents}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


async def tool_rss_feed(url: str, limit: int = 10) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "ApexSwarm/3.4"})
        if resp.status_code != 200: return f"RSS failed (HTTP {resp.status_code})"
        text = resp.text
        items = re.findall(r'<item[^>]*>(.*?)</item>', text, re.DOTALL) or re.findall(r'<entry[^>]*>(.*?)</entry>', text, re.DOTALL)
        entries = []
        for item in items[:limit]:
            title = re.search(r'<title[^>]*>(.*?)</title>', item, re.DOTALL)
            link = re.search(r'<link[^>]*(?:href="([^"]*)"[^>]*/?>|>(.*?)</link>)', item, re.DOTALL)
            date = re.search(r'<(?:pubDate|published|updated)[^>]*>(.*?)</', item, re.DOTALL)
            desc = re.search(r'<(?:description|summary|content)[^>]*>(.*?)</', item, re.DOTALL)
            e = {}
            if title: e["title"] = html.unescape(re.sub(r'<!\[CDATA\[|\]\]>|<[^>]+>', '', title.group(1)).strip())
            if link: e["link"] = (link.group(1) or link.group(2) or "").strip()
            if date: e["date"] = date.group(1).strip()
            if desc: e["summary"] = html.unescape(re.sub(r'<!\[CDATA\[|\]\]>|<[^>]+>', '', desc.group(1)).strip())[:200]
            entries.append(e)
        if not entries: return "No entries found."
        lines = [f"RSS ({len(entries)} entries):\n"]
        for e in entries:
            lines.append(f"- **{e.get('title','Untitled')}**")
            if e.get("link"): lines.append(f"  {e['link']}")
            if e.get("date"): lines.append(f"  {e['date']}")
            if e.get("summary"): lines.append(f"  {e['summary']}")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"RSS error: {str(e)}"


async def tool_send_webhook(url: str, message: str, platform: str = "custom", extra_data: dict = None) -> str:
    try:
        if platform == "slack": payload = {"text": message}
        elif platform == "discord": payload = {"content": message}
        else: payload = {"message": message, "timestamp": datetime.now(timezone.utc).isoformat()}
        if extra_data: payload.update(extra_data)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        return f"Webhook sent (HTTP {resp.status_code})"
    except Exception as e:
        return f"Webhook error: {str(e)}"


async def tool_send_email(to: str, subject: str, body: str, is_html: bool = False) -> str:
    if not SMTP_HOST: return "Email not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASS env vars."
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart()
        msg["From"], msg["To"], msg["Subject"] = SMTP_FROM, to, subject
        msg.attach(MIMEText(body, "html" if is_html else "plain"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_USER: server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return f"Email sent to {to}"
    except Exception as e:
        return f"Email error: {str(e)}"


def tool_generate_chart(chart_type: str, data: list, x_field: str, y_field: str, title: str = "", group_field: str = "") -> str:
    try:
        if not data: return "No data"
        chart = {"type": chart_type, "title": title or f"{y_field} by {x_field}", "x_axis": x_field, "y_axis": y_field}
        if group_field:
            series = {}
            for i in data:
                g = str(i.get(group_field, "default"))
                series.setdefault(g, []).append({"x": i.get(x_field), "y": i.get(y_field)})
            chart["series"] = series
        elif chart_type == "pie":
            chart["data_points"] = [{"label": i.get(x_field), "value": i.get(y_field)} for i in data]
        else:
            chart["data_points"] = [{"x": i.get(x_field), "y": i.get(y_field)} for i in data]
        return json.dumps(chart, indent=2)
    except Exception as e:
        return f"Chart error: {str(e)}"


async def tool_screenshot_url(url: str, width: int = 1280, height: int = 800) -> str:
    if not SCREENSHOT_API: return "Screenshot API not configured. Set SCREENSHOT_API env var."
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(SCREENSHOT_API, params={"url": url, "width": width, "height": height})
        return f"Screenshot captured for {url}" if resp.status_code == 200 else f"Failed (HTTP {resp.status_code})"
    except Exception as e:
        return f"Screenshot error: {str(e)}"


# ─── MCP TOOL ROUTING ────────────────────────────────────

_mcp_registry = None

def set_mcp_registry(registry, user_key_col="user_api_key"):
    global _mcp_registry
    _mcp_registry = registry


async def execute_tool(tool_name: str, tool_input: dict, user_api_key: str = None) -> str:
    try:
        dispatch = {
            "web_search": lambda: tool_web_search(tool_input.get("query", "")),
            "fetch_url": lambda: tool_fetch_url(tool_input.get("url", "")),
            "crypto_prices": lambda: tool_crypto_prices(tool_input.get("coins", ""), tool_input.get("vs_currency", "usd")),
            "json_api": lambda: tool_json_api(tool_input.get("url", ""), tool_input.get("method", "GET"), tool_input.get("headers"), tool_input.get("body"), tool_input.get("params")),
            "rss_feed": lambda: tool_rss_feed(tool_input.get("url", ""), tool_input.get("limit", 10)),
            "send_webhook": lambda: tool_send_webhook(tool_input.get("url", ""), tool_input.get("message", ""), tool_input.get("platform", "custom"), tool_input.get("extra_data")),
            "send_email": lambda: tool_send_email(tool_input.get("to", ""), tool_input.get("subject", ""), tool_input.get("body", ""), tool_input.get("is_html", False)),
            "screenshot_url": lambda: tool_screenshot_url(tool_input.get("url", ""), tool_input.get("width", 1280), tool_input.get("height", 800)),
        }
        sync_dispatch = {
            "run_code": lambda: tool_run_code(tool_input.get("code", "")),
            "data_transform": lambda: tool_data_transform(tool_input.get("data", []), tool_input.get("operations", [])),
            "sentiment_analysis": lambda: tool_sentiment_analysis(tool_input.get("text", "")),
            "generate_chart": lambda: tool_generate_chart(tool_input.get("chart_type", "bar"), tool_input.get("data", []), tool_input.get("x_field", ""), tool_input.get("y_field", ""), tool_input.get("title", ""), tool_input.get("group_field", "")),
        }
        if tool_name in dispatch:
            return await dispatch[tool_name]()
        elif tool_name in sync_dispatch:
            return sync_dispatch[tool_name]()
        elif _mcp_registry and user_api_key:
            return await _execute_mcp_tool(tool_name, tool_input, user_api_key)
        return f"Unknown tool: {tool_name}"
    except Exception as e:
        logger.error(f"Tool error ({tool_name}): {e}")
        return f"Tool error: {str(e)}"


async def _execute_mcp_tool(tool_name: str, tool_input: dict, user_api_key: str) -> str:
    try:
        tools = await _mcp_registry.get_tools(user_api_key)
        match = next((t for t in tools if t["name"].lower().replace(" ", "_") == tool_name.lower() or t["tool_id"] == tool_name), None)
        if not match: return f"MCP tool '{tool_name}' not found"
        result = await _mcp_registry.execute_tool(match["tool_id"], user_api_key, input_data=tool_input)
        if "error" in result: return f"MCP error: {result['error']}"
        return f"MCP '{match['name']}' (HTTP {result.get('status_code','?')}):\n{json.dumps(result.get('result', {}), indent=2)[:4000]}"
    except Exception as e:
        return f"MCP error: {str(e)}"


def get_mcp_tool_definitions(mcp_tools: list[dict]) -> list[dict]:
    return [{
        "name": t["name"].lower().replace(" ", "_").replace("-", "_"),
        "description": f"[MCP] {t['description']}. {t['method']} {t['endpoint_url'][:80]}",
        "input_schema": {"type": "object", "properties": {"input_data": {"type": "object", "description": "Key-value pairs for API"}}, "required": []},
    } for t in mcp_tools]


# ─── AGENTIC EXECUTION ───────────────────────────────────

# Multi-model router reference (set by main.py)
_model_router = None


def set_model_router(router):
    """Called by main.py to wire multi-model router into tool execution."""
    global _model_router
    _model_router = router


async def execute_with_tools(
    api_key: str, model: str, system_prompt: str, user_message: str,
    tools: list[dict], max_turns: int = 5, user_api_key: str = None, mcp_tools: list[dict] = None,
    image_data: str = None, image_media_type: str = "image/jpeg",
) -> str:
    """Run any LLM with tool use in a loop. Supports all providers via model_router."""
    messages = [{"role": "user", "content": user_message}]
    final_text = ""
    all_tools = list(tools) + (get_mcp_tool_definitions(mcp_tools) if mcp_tools else [])

    for turn in range(max_turns):
        if _model_router:
            # Use multi-model router
            result = await _model_router.call(
                model_id=model,
                system_prompt=system_prompt,
                messages=messages,
                tools=all_tools if all_tools else None,
                max_tokens=4096,
                image_data=image_data if turn == 0 else None,
                image_media_type=image_media_type,
            )

            if result.get("error"):
                logger.error(f"LLM error: {result['error']}")
                return f"Agent error: {result['error']}"

            text = result.get("text", "")
            tool_calls = result.get("tool_calls", [])
            stop_reason = result.get("stop_reason", "")
            raw_content = result.get("raw_content", [])

            if text:
                final_text = text

            if stop_reason != "tool_use" or not tool_calls:
                break

            # Build assistant message with raw content for conversation continuity
            messages.append({"role": "assistant", "content": raw_content})

            # Execute tools and add results
            results = []
            for tc in tool_calls:
                logger.info(f"Tool: {tc['name']}({json.dumps(tc.get('input', {}))[:100]})")
                r = await execute_tool(tc["name"], tc.get("input", {}), user_api_key=user_api_key)
                results.append({"type": "tool_result", "tool_use_id": tc["id"], "content": r})
            messages.append({"role": "user", "content": results})

        else:
            # Fallback: direct Anthropic call (no multi-model router)
            async with httpx.AsyncClient(timeout=45.0) as client:
                payload = {"model": model, "max_tokens": 4096, "system": system_prompt, "messages": messages}
                if all_tools:
                    payload["tools"] = all_tools
                resp = await client.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=payload)

            if resp.status_code != 200:
                logger.error(f"Claude API error ({resp.status_code}): {resp.text[:200]}")
                return f"Agent error: Claude API returned {resp.status_code}"

            data = resp.json()
            stop_reason = data.get("stop_reason", "")
            blocks = data.get("content", [])
            text_parts = [b["text"] for b in blocks if b.get("type") == "text"]
            tool_calls = [b for b in blocks if b.get("type") == "tool_use"]
            if text_parts:
                final_text = "\n".join(text_parts)
            if stop_reason != "tool_use" or not tool_calls:
                break

            messages.append({"role": "assistant", "content": blocks})
            results = []
            for tc in tool_calls:
                logger.info(f"Tool: {tc['name']}({json.dumps(tc.get('input', {}))[:100]})")
                r = await execute_tool(tc["name"], tc.get("input", {}), user_api_key=user_api_key)
                results.append({"type": "tool_result", "tool_use_id": tc["id"], "content": r})
            messages.append({"role": "user", "content": results})

    return final_text
