"""
APEX SWARM - Enterprise Hardening Module
==========================================
5 pillars of enterprise readiness:

1. RELIABILITY — retry logic, circuit breakers, graceful degradation
2. PERSISTENCE — conversation continuity, agent memory across runs
3. SECURITY — input sanitization, output filtering, audit trail
4. OBSERVABILITY — structured metrics, latency tracking, cost analytics
5. DOCUMENTATION — auto-generated OpenAPI docs from endpoints

File: enterprise.py
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, Callable

logger = logging.getLogger("apex-swarm")


# ═══════════════════════════════════════════════════════════
# 1. RELIABILITY — Retries, Circuit Breakers, Graceful Degradation
# ═══════════════════════════════════════════════════════════

class RetryConfig:
    """Configurable retry policy."""
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0,
                 max_delay: float = 30.0, exponential: bool = True,
                 retryable_errors: list = None):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential = exponential
        self.retryable_errors = retryable_errors or [
            "timeout", "rate_limit", "server_error", "connection", "502", "503", "529"
        ]


async def retry_with_backoff(fn, config: RetryConfig = None, label: str = "operation"):
    """Execute an async function with exponential backoff retries."""
    config = config or RetryConfig()
    last_error = None

    for attempt in range(config.max_retries + 1):
        try:
            result = await fn()
            if attempt > 0:
                logger.info(f"✅ {label} succeeded on attempt {attempt + 1}")
                metrics_collector.record("retry.success", 1, {"label": label, "attempts": attempt + 1})
            return result
        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # Check if error is retryable
            is_retryable = any(err in error_str for err in config.retryable_errors)
            if not is_retryable or attempt == config.max_retries:
                logger.error(f"❌ {label} failed permanently after {attempt + 1} attempts: {e}")
                metrics_collector.record("retry.exhausted", 1, {"label": label})
                raise

            # Calculate delay
            if config.exponential:
                delay = min(config.base_delay * (2 ** attempt), config.max_delay)
            else:
                delay = config.base_delay

            logger.warning(f"⚠️ {label} attempt {attempt + 1} failed: {e}. Retrying in {delay:.1f}s...")
            metrics_collector.record("retry.attempt", 1, {"label": label, "attempt": attempt + 1})
            await asyncio.sleep(delay)

    raise last_error


class CircuitBreaker:
    """Circuit breaker pattern — stops calling a failing service."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0, name: str = "default"):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "closed"  # closed (normal), open (blocking), half-open (testing)

    def can_proceed(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
                logger.info(f"🔌 Circuit {self.name}: half-open, testing...")
                return True
            return False
        return True  # half-open

    def record_success(self):
        if self.state == "half-open":
            logger.info(f"🔌 Circuit {self.name}: recovered → closed")
        self.failures = 0
        self.state = "closed"

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "open"
            logger.warning(f"🔌 Circuit {self.name}: OPEN after {self.failures} failures. Blocking for {self.recovery_timeout}s")
            metrics_collector.record("circuit.open", 1, {"name": self.name})

    def get_status(self) -> dict:
        return {"name": self.name, "state": self.state, "failures": self.failures,
                "threshold": self.failure_threshold, "recovery_timeout": self.recovery_timeout}


# Global circuit breakers for key services
circuit_breakers = {
    "anthropic": CircuitBreaker(failure_threshold=5, recovery_timeout=60, name="anthropic"),
    "openai": CircuitBreaker(failure_threshold=5, recovery_timeout=60, name="openai"),
    "tools": CircuitBreaker(failure_threshold=10, recovery_timeout=30, name="tools"),
}


# ═══════════════════════════════════════════════════════════
# 2. PERSISTENCE — Conversation Continuity
# ═══════════════════════════════════════════════════════════

class ConversationStore:
    """Persistent conversation history per user+agent for continuity."""

    def __init__(self, db_fn, db_execute_fn, db_fetchall_fn, user_key_col="user_api_key"):
        self._db_fn = db_fn
        self._db_execute = db_execute_fn
        self._db_fetchall = db_fetchall_fn
        self._user_key_col = user_key_col

    def init_tables(self, conn):
        is_pg = hasattr(conn, 'autocommit')
        sql = """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_api_key TEXT NOT NULL,
                agent_type TEXT NOT NULL,
                context_key TEXT NOT NULL,
                messages TEXT DEFAULT '[]',
                summary TEXT DEFAULT '',
                turn_count INTEGER DEFAULT 0,
                last_active TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_api_key);
            CREATE INDEX IF NOT EXISTS idx_conv_context ON conversations(context_key);
        """
        if is_pg:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
        else:
            conn.executescript(sql)
            conn.commit()

    def get_context(self, user_api_key: str, agent_type: str, limit: int = 5) -> str:
        """Get recent conversation context for an agent."""
        context_key = f"{user_api_key}:{agent_type}"
        conn = self._db_fn()
        try:
            rows = self._db_fetchall(conn,
                "SELECT messages, summary FROM conversations WHERE context_key = ? ORDER BY last_active DESC LIMIT 1",
                (context_key,),
            )
        finally:
            conn.close()

        if not rows:
            return ""

        messages = json.loads(rows[0][0]) if rows[0][0] else []
        summary = rows[0][1] or ""

        # Build context string
        parts = []
        if summary:
            parts.append(f"## Previous Session Summary:\n{summary}")
        if messages:
            recent = messages[-limit:]
            parts.append("## Recent Interactions:")
            for msg in recent:
                parts.append(f"- [{msg.get('role', '?')}] {msg.get('content', '')[:300]}")

        return "\n".join(parts) if parts else ""

    def save_turn(self, user_api_key: str, agent_type: str, task: str, result: str):
        """Save a conversation turn."""
        context_key = f"{user_api_key}:{agent_type}"
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db_fn()
        try:
            rows = self._db_fetchall(conn,
                "SELECT id, messages, turn_count FROM conversations WHERE context_key = ? ORDER BY last_active DESC LIMIT 1",
                (context_key,),
            )

            new_msgs = [
                {"role": "user", "content": task[:500], "timestamp": now},
                {"role": "assistant", "content": result[:1000], "timestamp": now},
            ]

            if rows:
                conv_id = rows[0][0]
                existing = json.loads(rows[0][1]) if rows[0][1] else []
                turn_count = (rows[0][2] or 0) + 1
                # Keep last 20 messages
                all_msgs = (existing + new_msgs)[-20:]
                self._db_execute(conn,
                    "UPDATE conversations SET messages = ?, turn_count = ?, last_active = ? WHERE id = ?",
                    (json.dumps(all_msgs), turn_count, now, conv_id),
                )
            else:
                conv_id = str(uuid.uuid4())
                self._db_execute(conn,
                    "INSERT INTO conversations (id, user_api_key, agent_type, context_key, messages, turn_count, last_active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (conv_id, user_api_key, agent_type, context_key, json.dumps(new_msgs), now, now),
                )
            conn.commit()
        except Exception as e:
            logger.error(f"Conversation save failed: {e}")
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════
# 3. SECURITY — Input Sanitization, Audit Trail
# ═══════════════════════════════════════════════════════════

# Patterns that indicate prompt injection attempts
INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|above)\s+(instructions|prompts)",
    r"system\s*:\s*you\s+are\s+now",
    r"pretend\s+you\s+are",
    r"jailbreak",
    r"DAN\s+mode",
    r"ignore\s+your\s+programming",
    r"override\s+your\s+(instructions|rules|system)",
    r"new\s+instruction\s*:",
    r"<\s*system\s*>",
    r"\[\s*SYSTEM\s*\]",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# Sensitive data patterns to filter from outputs
SENSITIVE_PATTERNS = [
    (re.compile(r'(?:sk-|pk-)[a-zA-Z0-9]{20,}'), '[API_KEY_REDACTED]'),
    (re.compile(r'(?:ghp_|gho_)[a-zA-Z0-9]{36}'), '[GITHUB_TOKEN_REDACTED]'),
    (re.compile(r'xox[bsp]-[a-zA-Z0-9\-]+'), '[SLACK_TOKEN_REDACTED]'),
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), '[EMAIL_REDACTED]'),
]


def sanitize_input(text: str) -> dict:
    """Sanitize user input. Returns {clean_text, flags, blocked}."""
    if not text:
        return {"clean_text": "", "flags": [], "blocked": False}

    flags = []

    # Check for injection patterns
    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            flags.append(f"injection_pattern: {pattern.pattern[:40]}")

    # Check for extremely long inputs (token stuffing)
    if len(text) > 50000:
        flags.append("excessive_length")
        text = text[:50000]

    # Check for encoded payloads
    if "\\x" in text or "\\u" in text:
        flags.append("encoded_content")

    blocked = len(flags) >= 3  # Block if 3+ flags

    return {
        "clean_text": text,
        "flags": flags,
        "blocked": blocked,
        "risk_score": min(len(flags) / 5.0, 1.0),
    }


def sanitize_output(text: str) -> str:
    """Remove sensitive data from agent outputs."""
    if not text:
        return ""
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class AuditLog:
    """Security audit trail for all operations."""

    def __init__(self, db_fn, db_execute_fn, db_fetchall_fn):
        self._db_fn = db_fn
        self._db_execute = db_execute_fn
        self._db_fetchall = db_fetchall_fn

    def init_tables(self, conn):
        is_pg = hasattr(conn, 'autocommit')
        sql = """
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                user_api_key TEXT,
                action TEXT NOT NULL,
                resource TEXT,
                details TEXT DEFAULT '{}',
                ip_address TEXT,
                risk_score REAL DEFAULT 0.0,
                flagged INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_api_key);
            CREATE INDEX IF NOT EXISTS idx_audit_flagged ON audit_log(flagged);
        """
        if is_pg:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
        else:
            conn.executescript(sql)
            conn.commit()

    def log(self, action: str, user_api_key: str = None, resource: str = None,
            details: dict = None, ip_address: str = None, risk_score: float = 0.0):
        """Record an audit event."""
        try:
            conn = self._db_fn()
            try:
                self._db_execute(conn,
                    "INSERT INTO audit_log (id, timestamp, user_api_key, action, resource, details, ip_address, risk_score, flagged) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4())[:12], datetime.now(timezone.utc).isoformat(),
                     _hash_key(user_api_key), action, resource,
                     json.dumps(details or {}), ip_address, risk_score,
                     1 if risk_score > 0.3 else 0),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Audit log failed: {e}")

    def get_recent(self, limit: int = 50, flagged_only: bool = False) -> list:
        conn = self._db_fn()
        try:
            if flagged_only:
                rows = self._db_fetchall(conn,
                    "SELECT id, timestamp, user_api_key, action, resource, risk_score FROM audit_log WHERE flagged = 1 ORDER BY timestamp DESC LIMIT ?",
                    (limit,))
            else:
                rows = self._db_fetchall(conn,
                    "SELECT id, timestamp, user_api_key, action, resource, risk_score FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                    (limit,))
        finally:
            conn.close()
        return [{"id": r[0], "timestamp": r[1], "user": r[2], "action": r[3],
                 "resource": r[4], "risk_score": r[5]} for r in rows]


def _hash_key(api_key: str) -> str:
    """Hash API key for audit logs (don't store raw keys)."""
    if not api_key:
        return "anonymous"
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════
# 4. OBSERVABILITY — Metrics, Latency, Cost Analytics
# ═══════════════════════════════════════════════════════════

class MetricsCollector:
    """In-memory metrics collector with aggregation."""

    def __init__(self):
        self._counters: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list] = defaultdict(list)
        self._gauges: dict[str, float] = {}
        self._start_time = time.time()

    def record(self, name: str, value: float = 1.0, tags: dict = None):
        """Record a metric."""
        key = name
        if tags:
            tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
            key = f"{name}[{tag_str}]"
        self._counters[key] += value

    def histogram(self, name: str, value: float, tags: dict = None):
        """Record a latency/distribution metric."""
        key = name
        if tags:
            tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
            key = f"{name}[{tag_str}]"
        self._histograms[key].append(value)
        # Keep last 1000 samples
        if len(self._histograms[key]) > 1000:
            self._histograms[key] = self._histograms[key][-1000:]

    def gauge(self, name: str, value: float):
        """Set a gauge (current value)."""
        self._gauges[name] = value

    def get_summary(self) -> dict:
        """Get full metrics summary."""
        uptime = time.time() - self._start_time

        # Compute histogram stats
        hist_stats = {}
        for name, values in self._histograms.items():
            if values:
                sorted_v = sorted(values)
                hist_stats[name] = {
                    "count": len(values),
                    "mean": sum(values) / len(values),
                    "p50": sorted_v[len(sorted_v) // 2],
                    "p95": sorted_v[int(len(sorted_v) * 0.95)] if len(sorted_v) > 1 else sorted_v[0],
                    "p99": sorted_v[int(len(sorted_v) * 0.99)] if len(sorted_v) > 1 else sorted_v[0],
                    "min": sorted_v[0],
                    "max": sorted_v[-1],
                }

        return {
            "uptime_seconds": round(uptime),
            "uptime_human": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m",
            "counters": dict(self._counters),
            "histograms": hist_stats,
            "gauges": dict(self._gauges),
        }

    def get_agent_metrics(self) -> dict:
        """Get agent-specific metrics."""
        agent_counts = {}
        agent_latencies = {}
        for key, val in self._counters.items():
            if key.startswith("agent."):
                agent_counts[key] = val
        for key, vals in self._histograms.items():
            if key.startswith("agent."):
                if vals:
                    agent_latencies[key] = {"mean": round(sum(vals)/len(vals), 2), "count": len(vals)}
        return {"counts": agent_counts, "latencies": agent_latencies}


# Global metrics instance
metrics_collector = MetricsCollector()


class LatencyTracker:
    """Context manager for tracking operation latency."""

    def __init__(self, operation: str, tags: dict = None):
        self.operation = operation
        self.tags = tags or {}
        self.start = 0.0

    async def __aenter__(self):
        self.start = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start
        metrics_collector.histogram(f"latency.{self.operation}", duration, self.tags)
        if exc_type:
            metrics_collector.record(f"error.{self.operation}", 1, self.tags)
        else:
            metrics_collector.record(f"success.{self.operation}", 1, self.tags)


# ═══════════════════════════════════════════════════════════
# 5. API DOCUMENTATION
# ═══════════════════════════════════════════════════════════

API_DOCS = {
    "openapi": "3.0.3",
    "info": {
        "title": "APEX SWARM API",
        "version": "4.0.0",
        "description": "Autonomous AI Agent Platform — 66+ agents, multi-model, marketplace, voice, A2A protocol, autonomous goals.",
        "contact": {"name": "APEX SWARM", "url": "https://apex-swarm.com"},
    },
    "servers": [{"url": "/", "description": "Current server"}],
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key", "description": "Your Gumroad license key or API key"},
        },
    },
    "security": [{"ApiKeyAuth": []}],
    "paths": {
        "/api/v1/health": {
            "get": {
                "summary": "System health check",
                "tags": ["System"],
                "security": [],
                "responses": {"200": {"description": "System status with all module states"}},
            },
        },
        "/api/v1/agents": {
            "get": {
                "summary": "List all 66+ agent types",
                "tags": ["Agents"],
                "security": [],
                "responses": {"200": {"description": "Agent types grouped by category"}},
            },
        },
        "/api/v1/deploy": {
            "post": {
                "summary": "Deploy an agent",
                "tags": ["Agents"],
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["agent_type", "task_description"],
                    "properties": {
                        "agent_type": {"type": "string", "description": "Agent type ID (e.g. 'research', 'crypto-research')", "example": "research"},
                        "task_description": {"type": "string", "description": "What the agent should do", "example": "Analyze the current state of the DeFi market"},
                        "model": {"type": "string", "description": "Optional: LLM model to use (e.g. 'gpt-4o', 'gemini-2.5-flash')", "example": "claude-haiku-4-5-20241022"},
                    },
                }}}},
                "responses": {
                    "200": {"description": "Agent deployed, returns agent_id to poll for results"},
                    "400": {"description": "Unknown agent type"},
                    "401": {"description": "API key required"},
                    "429": {"description": "Rate limit exceeded"},
                },
            },
        },
        "/api/v1/status/{agent_id}": {
            "get": {
                "summary": "Get agent result",
                "tags": ["Agents"],
                "parameters": [{"name": "agent_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Agent status and result"}},
            },
        },
        "/api/v1/agents/recent": {
            "get": {
                "summary": "List recent agent runs",
                "tags": ["Agents"],
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20}}],
                "responses": {"200": {"description": "Recent agent executions"}},
            },
        },
        "/api/v1/models": {
            "get": {
                "summary": "List all LLM providers and models",
                "tags": ["Models"],
                "security": [],
                "responses": {"200": {"description": "Available providers with model details, pricing, capabilities"}},
            },
        },
        "/api/v1/models/available": {
            "get": {
                "summary": "List only configured (ready-to-use) models",
                "tags": ["Models"],
                "security": [],
                "responses": {"200": {"description": "Models with active API keys"}},
            },
        },
        "/api/v1/marketplace/agents": {
            "get": {
                "summary": "Browse marketplace agents",
                "tags": ["Marketplace"],
                "security": [],
                "parameters": [
                    {"name": "category", "in": "query", "schema": {"type": "string"}},
                    {"name": "search", "in": "query", "schema": {"type": "string"}},
                    {"name": "sort", "in": "query", "schema": {"type": "string", "enum": ["popular", "newest", "rating", "free"]}},
                ],
                "responses": {"200": {"description": "Published marketplace agents"}},
            },
            "post": {
                "summary": "Create a custom marketplace agent",
                "tags": ["Marketplace"],
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["name", "description", "system_prompt"],
                    "properties": {
                        "name": {"type": "string", "example": "Alpha Scanner"},
                        "description": {"type": "string"},
                        "system_prompt": {"type": "string", "description": "The agent's personality and instructions"},
                        "category": {"type": "string", "example": "Crypto & DeFi"},
                        "price_usd": {"type": "number", "default": 0},
                        "icon": {"type": "string", "default": "🤖"},
                    },
                }}}},
                "responses": {"200": {"description": "Agent created (draft). Publish to make visible."}},
            },
        },
        "/api/v1/a2a/delegate": {
            "post": {
                "summary": "Delegate a complex task to multiple agents",
                "tags": ["A2A Protocol"],
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {"type": "string", "example": "Research AI startups and write an investment report"},
                        "lead_agent": {"type": "string", "default": "research"},
                        "max_subtasks": {"type": "integer", "default": 5},
                    },
                }}}},
                "responses": {"200": {"description": "Delegation plan with subtask results"}},
            },
        },
        "/api/v1/goals": {
            "post": {
                "summary": "Create an autonomous goal",
                "tags": ["Autonomous Goals"],
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["title", "description"],
                    "properties": {
                        "title": {"type": "string", "example": "Launch a DeFi newsletter"},
                        "description": {"type": "string"},
                        "org_roles": {"type": "array", "items": {"type": "string"}, "default": ["ceo", "researcher", "writer", "analyst"]},
                        "auto_execute": {"type": "boolean", "default": True},
                    },
                }}}},
                "responses": {"200": {"description": "Goal with projects, tasks, and results"}},
            },
        },
        "/api/v1/roles": {
            "get": {
                "summary": "List org chart roles and permissions",
                "tags": ["Autonomous Goals"],
                "security": [],
                "responses": {"200": {"description": "10 roles with tool access and email permissions"}},
            },
        },
        "/api/v1/voice/deploy": {
            "post": {
                "summary": "Voice-in, voice-out agent deployment",
                "tags": ["Voice"],
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["audio_base64"],
                    "properties": {
                        "audio_base64": {"type": "string", "description": "Base64 encoded audio"},
                        "agent_type": {"type": "string", "default": "research"},
                        "voice_response": {"type": "boolean", "default": True},
                        "voice": {"type": "string", "example": "nova"},
                    },
                }}}},
                "responses": {"200": {"description": "Transcript, agent result, and optional audio response"}},
            },
        },
        "/api/v1/channels": {
            "get": {
                "summary": "List messaging channel status",
                "tags": ["Channels"],
                "security": [],
                "responses": {"200": {"description": "Telegram, Discord, Slack connection status"}},
            },
        },
        "/api/v1/metrics": {
            "get": {
                "summary": "System metrics and observability data",
                "tags": ["System"],
                "responses": {"200": {"description": "Uptime, counters, latency histograms, gauges"}},
            },
        },
        "/api/v1/audit": {
            "get": {
                "summary": "Security audit trail",
                "tags": ["System"],
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}},
                    {"name": "flagged_only", "in": "query", "schema": {"type": "boolean", "default": False}},
                ],
                "responses": {"200": {"description": "Recent audit events"}},
            },
        },
    },
}


def get_api_docs() -> dict:
    """Return OpenAPI 3.0 spec."""
    return API_DOCS
