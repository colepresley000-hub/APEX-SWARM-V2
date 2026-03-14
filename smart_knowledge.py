"""
APEX SWARM - Smart Knowledge Retrieval Module
Relevance scoring with TF-IDF-like weighting, recency decay, domain matching.
Drop-in replacement for current "get recent patterns" approach.

Integration: Add these functions to main.py and update the /api/v1/agents/deploy endpoint.
"""

import math
import re
import time
from collections import Counter
from typing import Optional


# ============================================================
# 1. RELEVANCE SCORING ENGINE
# ============================================================

# Common stop words to ignore in matching
STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "but", "or",
    "not", "no", "so", "if", "then", "than", "too", "very", "just",
    "about", "up", "out", "this", "that", "it", "its", "my", "your",
}


def tokenize(text: str) -> list[str]:
    """Extract meaningful tokens from text."""
    words = re.findall(r'[a-zA-Z0-9$]+(?:[-/][a-zA-Z0-9$]+)*', text.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 1]


def compute_term_overlap(query_tokens: list[str], pattern_tokens: list[str]) -> float:
    """
    TF-IDF-inspired term overlap score.
    Rewards rare term matches more than common ones.
    """
    if not query_tokens or not pattern_tokens:
        return 0.0

    query_set = set(query_tokens)
    pattern_set = set(pattern_tokens)
    overlap = query_set & pattern_set

    if not overlap:
        return 0.0

    # Weight matches by inverse frequency (rarer terms score higher)
    all_tokens = query_tokens + pattern_tokens
    freq = Counter(all_tokens)
    max_freq = max(freq.values())

    score = sum((1.0 - freq[t] / (max_freq + 1)) + 0.5 for t in overlap)
    max_possible = len(query_set) * 1.5  # normalize

    return min(score / max_possible, 1.0)


def recency_decay(created_at: float, half_life_hours: float = 72.0) -> float:
    """
    Exponential decay based on age. Recent patterns score higher.
    half_life_hours: time for score to drop to 50%.
    Default 72h = patterns lose half their recency bonus after 3 days.
    """
    age_hours = (time.time() - created_at) / 3600.0
    if age_hours < 0:
        age_hours = 0
    return math.exp(-0.693 * age_hours / half_life_hours)


def domain_match_score(query_domain: Optional[str], pattern_domain: str) -> float:
    """
    Domain affinity scoring.
    Exact match = 1.0, related domains = 0.5, unrelated = 0.2.
    Cross-domain patterns still have value (the swarm effect).
    """
    if not query_domain:
        return 0.6  # No domain preference, moderate weight for all

    if query_domain.lower() == pattern_domain.lower():
        return 1.0

    # Domain affinity map - related fields boost each other
    AFFINITIES = {
        ("crypto", "data"): 0.6,
        ("crypto", "business"): 0.5,
        ("coding", "data"): 0.7,
        ("coding", "productivity"): 0.5,
        ("writing", "business"): 0.6,
        ("writing", "productivity"): 0.5,
        ("data", "business"): 0.7,
        ("business", "productivity"): 0.5,
    }

    pair = tuple(sorted([query_domain.lower(), pattern_domain.lower()]))
    return AFFINITIES.get(pair, 0.2)


def confidence_weight(success_count: int, fail_count: int) -> float:
    """
    Bayesian-inspired confidence score.
    More observations = higher confidence. High success rate = higher score.
    Uses Laplace smoothing to handle small sample sizes.
    """
    total = success_count + fail_count
    if total == 0:
        return 0.5  # No data, neutral

    # Wilson score lower bound (simplified)
    p = (success_count + 1) / (total + 2)  # Laplace smoothing
    confidence = p * (1 - 1.0 / (total + 3))  # Penalize small samples

    return min(max(confidence, 0.0), 1.0)


def usage_boost(times_used: int, max_boost: float = 0.15) -> float:
    """
    Logarithmic boost for frequently-used patterns.
    Patterns used by many agents are likely more valuable.
    """
    if times_used <= 0:
        return 0.0
    return min(math.log(times_used + 1) / 10.0, max_boost)


# ============================================================
# 2. COMPOSITE RELEVANCE SCORER
# ============================================================

def compute_relevance(
    query: str,
    pattern: dict,
    query_domain: Optional[str] = None,
    weights: Optional[dict] = None,
) -> float:
    """
    Compute composite relevance score for a knowledge pattern.

    Args:
        query: The user's task/query text
        pattern: Dict with keys: pattern_text, domain, created_at,
                 success_count, fail_count, times_used
        query_domain: The domain of the requesting agent (optional)
        weights: Custom weight overrides

    Returns:
        Float 0.0-1.0 representing relevance
    """
    w = weights or {
        "term_overlap": 0.35,   # How well the pattern matches the query
        "recency": 0.20,        # How recent the pattern is
        "domain": 0.20,         # Domain relevance
        "confidence": 0.15,     # How reliable the pattern is
        "usage": 0.10,          # How many agents have used it
    }

    query_tokens = tokenize(query)
    pattern_tokens = tokenize(pattern.get("pattern_text", ""))

    scores = {
        "term_overlap": compute_term_overlap(query_tokens, pattern_tokens),
        "recency": recency_decay(pattern.get("created_at", time.time())),
        "domain": domain_match_score(query_domain, pattern.get("domain", "")),
        "confidence": confidence_weight(
            pattern.get("success_count", 0),
            pattern.get("fail_count", 0),
        ),
        "usage": usage_boost(pattern.get("times_used", 0)) / 0.15,  # normalize to 0-1
    }

    composite = sum(scores[k] * w[k] for k in w)
    return round(min(max(composite, 0.0), 1.0), 4)


# ============================================================
# 3. SMART RETRIEVAL FUNCTION (Drop-in for main.py)
# ============================================================

def get_relevant_knowledge(
    db_cursor,
    query: str,
    agent_domain: Optional[str] = None,
    limit: int = 10,
    min_relevance: float = 0.3,
) -> list[dict]:
    """
    Smart knowledge retrieval. Replaces the old 'get recent patterns' approach.

    Drop-in usage in main.py:
        # OLD:
        # cursor.execute("SELECT * FROM knowledge ORDER BY created_at DESC LIMIT 10")

        # NEW:
        patterns = get_relevant_knowledge(cursor, task_description, agent_type_domain, limit=10)

    Args:
        db_cursor: SQLite cursor
        query: The task description
        agent_domain: Domain of the agent being deployed
        limit: Max patterns to return
        min_relevance: Minimum score threshold

    Returns:
        List of pattern dicts sorted by relevance score (descending)
    """
    # Fetch candidate patterns (get more than needed, then rank)
    fetch_limit = limit * 5  # Over-fetch to ensure good candidates
    db_cursor.execute("""
        SELECT
            id,
            pattern AS pattern_text,
            domain,
            created_at,
            success_count,
            fail_count,
            source_agent
        FROM knowledge
        ORDER BY created_at DESC
        LIMIT ?
    """, (fetch_limit,))

    rows = db_cursor.fetchall()
    if not rows:
        return []

    # Score each pattern
    scored = []
    for row in rows:
        pattern = {
            "id": row[0],
            "pattern_text": row[1],
            "domain": row[2],
            "created_at": row[3],
            "success_count": row[4] or 0,
            "fail_count": row[5] or 0,
            "times_used": row[6] or 0,
            "source_agent": row[7],
        }

        relevance = compute_relevance(query, pattern, agent_domain)
        if relevance >= min_relevance:
            pattern["relevance_score"] = relevance
            scored.append(pattern)

    # Sort by relevance, return top N
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    return scored[:limit]


# ============================================================
# 4. KNOWLEDGE STORAGE WITH SCORING FIELDS
# ============================================================

KNOWLEDGE_SCHEMA_UPGRADE = """
-- Run this to upgrade your existing knowledge table
-- Adds columns needed for smart relevance scoring

ALTER TABLE knowledge ADD COLUMN success_count INTEGER DEFAULT 0;
ALTER TABLE knowledge ADD COLUMN fail_count INTEGER DEFAULT 0;
ALTER TABLE knowledge ADD COLUMN times_used INTEGER DEFAULT 0;
ALTER TABLE knowledge ADD COLUMN source_agent TEXT DEFAULT '';
ALTER TABLE knowledge ADD COLUMN domain TEXT DEFAULT '';

-- Index for faster retrieval
CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge(domain);
CREATE INDEX IF NOT EXISTS idx_knowledge_created ON knowledge(created_at DESC);

-- If creating fresh:
-- CREATE TABLE knowledge (
--     id INTEGER PRIMARY KEY AUTOINCREMENT,
--     pattern TEXT NOT NULL,
--     domain TEXT DEFAULT '',
--     created_at REAL DEFAULT (strftime('%s', 'now')),
--     success_count INTEGER DEFAULT 0,
--     fail_count INTEGER DEFAULT 0,
--     times_used INTEGER DEFAULT 0,
--     source_agent TEXT DEFAULT ''
-- );
"""


def store_pattern(
    db_cursor,
    pattern_text: str,
    domain: str,
    source_agent: str,
    success: bool = True,
) -> int:
    """
    Store a new knowledge pattern with scoring metadata.

    Returns the pattern ID.
    """
    db_cursor.execute("""
        INSERT INTO knowledge (pattern, domain, created_at, success_count, fail_count, source_agent, times_used)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (
        pattern_text,
        domain,
        time.time(),
        1 if success else 0,
        0 if success else 1,
        source_agent,
    ))
    return db_cursor.lastrowid


def record_pattern_usage(db_cursor, pattern_id: int, was_useful: bool = True):
    """Record that a pattern was used by an agent. Updates scoring metadata."""
    if was_useful:
        db_cursor.execute(
            "UPDATE knowledge SET times_used = times_used + 1, success_count = success_count + 1 WHERE id = ?",
            (pattern_id,),
        )
    else:
        db_cursor.execute(
            "UPDATE knowledge SET times_used = times_used + 1, fail_count = fail_count + 1 WHERE id = ?",
            (pattern_id,),
        )


# ============================================================
# 5. FORMAT KNOWLEDGE FOR CLAUDE PROMPT INJECTION
# ============================================================

def format_knowledge_for_prompt(patterns: list[dict]) -> str:
    """
    Format retrieved knowledge into a string for the Claude API prompt.
    Includes relevance scores so the model can weight accordingly.
    """
    if not patterns:
        return "No relevant collective knowledge found for this task."

    lines = ["## Collective Intelligence (Relevance-Ranked)\n"]
    for i, p in enumerate(patterns, 1):
        score = p.get("relevance_score", 0)
        confidence = "HIGH" if p.get("success_count", 0) > 5 else "MEDIUM" if p.get("success_count", 0) > 1 else "LOW"
        lines.append(
            f"{i}. [{score:.0%} relevant | {confidence} confidence] "
            f"{p['pattern_text']} "
            f"(used by {p.get('times_used', 0)} agents, from {p.get('source_agent', 'unknown')})"
        )

    lines.append(
        f"\n*{len(patterns)} patterns applied from collective knowledge base. "
        f"Highest relevance: {patterns[0].get('relevance_score', 0):.0%}*"
    )
    return "\n".join(lines)


# ============================================================
# 6. INTEGRATION EXAMPLE (paste into main.py deploy endpoint)
# ============================================================

INTEGRATION_EXAMPLE = '''
# In your /api/v1/agents/deploy endpoint, replace:
#
#   cursor.execute("SELECT * FROM knowledge ORDER BY created_at DESC LIMIT 10")
#   patterns = cursor.fetchall()
#   knowledge_context = "\\n".join([p[1] for p in patterns])
#
# With:
#
#   from smart_knowledge import get_relevant_knowledge, format_knowledge_for_prompt
#
#   patterns = get_relevant_knowledge(
#       cursor,
#       query=task_description,
#       agent_domain=agent_type_domain,  # e.g. "Crypto", "Coding"
#       limit=10,
#       min_relevance=0.3,
#   )
#   knowledge_context = format_knowledge_for_prompt(patterns)
#
# Then in your Claude API call, include knowledge_context in the system prompt:
#
#   system_prompt = f"""You are {agent_name}, an APEX SWARM agent.
#   You have access to collective intelligence from the swarm:
#
#   {knowledge_context}
#
#   Use the most relevant patterns to inform your response.
#   Prioritize HIGH confidence, high relevance patterns."""
#
# After the task completes, record what the agent learned:
#
#   store_pattern(cursor, new_discovery, domain, agent_id, success=True)
#   db.commit()
'''


# ============================================================
# QUICK TEST
# ============================================================

if __name__ == "__main__":
    # Simulate scoring without a database
    test_patterns = [
        {
            "pattern_text": "BTC arbitrage profitable Binance to Coinbase 3am UTC",
            "domain": "Crypto",
            "created_at": time.time() - 3600,  # 1 hour ago
            "success_count": 48,
            "fail_count": 3,
            "times_used": 51,
        },
        {
            "pattern_text": "React Server Components reduce bundle size significantly",
            "domain": "Coding",
            "created_at": time.time() - 86400,  # 1 day ago
            "success_count": 112,
            "fail_count": 8,
            "times_used": 120,
        },
        {
            "pattern_text": "Avoid SOL trades during network congestion",
            "domain": "Crypto",
            "created_at": time.time() - 172800,  # 2 days ago
            "success_count": 23,
            "fail_count": 2,
            "times_used": 25,
        },
    ]

    query = "Find BTC arbitrage opportunities between exchanges"
    print(f"Query: {query}\n")
    print(f"{'Pattern':<55} {'Domain':<10} {'Score':<8}")
    print("-" * 75)

    for p in test_patterns:
        score = compute_relevance(query, p, query_domain="Crypto")
        print(f"{p['pattern_text'][:52]:<55} {p['domain']:<10} {score:.4f}")
