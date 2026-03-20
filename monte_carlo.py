"""
monte_carlo.py — APEX SWARM Monte Carlo Trading Engine
Runs probabilistic simulations to find mispriced prediction markets.
"""

import random
import math
import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


# ─── SIMULATION ENGINE ────────────────────────────────────

def gbm_simulate(
    current_price: float,
    mu: float,           # annualized drift (e.g. 0.20 = 20%)
    sigma: float,        # annualized volatility (e.g. 0.80 = 80% for crypto)
    days: float,         # time horizon in days
    simulations: int = 10000,
    seed: int = None,
) -> list[float]:
    """
    Geometric Brownian Motion simulation.
    Returns list of final prices after `days`.
    """
    if seed is not None:
        random.seed(seed)

    dt = days / 365.0
    sqrt_dt = math.sqrt(dt)
    results = []

    for _ in range(simulations):
        z = random.gauss(0, 1)
        final = current_price * math.exp((mu - 0.5 * sigma ** 2) * dt + sigma * sqrt_dt * z)
        results.append(final)

    return results


def multi_path_simulate(
    current_price: float,
    sigma: float,
    days: float,
    mu: float = 0.0,
    simulations: int = 10000,
    steps: int = None,
) -> list[list[float]]:
    """
    Multi-step path simulation. Returns full price paths.
    Used for barrier/touch events (e.g. 'will it ever hit $X?').
    """
    if steps is None:
        steps = max(1, int(days))

    dt = (days / 365.0) / steps
    sqrt_dt = math.sqrt(dt)
    paths = []

    for _ in range(simulations):
        path = [current_price]
        price = current_price
        for _ in range(steps):
            z = random.gauss(0, 1)
            price = price * math.exp((mu - 0.5 * sigma ** 2) * dt + sigma * sqrt_dt * z)
            path.append(price)
        paths.append(path)

    return paths


def estimate_probability_above(
    simulations: list[float],
    target: float,
) -> float:
    """P(price > target) from simulation results."""
    hits = sum(1 for p in simulations if p >= target)
    return hits / len(simulations)


def estimate_probability_below(
    simulations: list[float],
    target: float,
) -> float:
    """P(price < target) from simulation results."""
    hits = sum(1 for p in simulations if p <= target)
    return hits / len(simulations)


def estimate_touch_probability(
    paths: list[list[float]],
    target: float,
    direction: str = "above",  # "above" or "below"
) -> float:
    """P(price ever touches target) across full paths."""
    hits = 0
    for path in paths:
        if direction == "above":
            if any(p >= target for p in path):
                hits += 1
        else:
            if any(p <= target for p in path):
                hits += 1
    return hits / len(paths)


def compute_edge(our_prob: float, market_prob: float) -> dict:
    """Kelly criterion edge computation."""
    edge = our_prob - market_prob
    # Kelly fraction: f = (bp - q) / b where b = (1-market_prob)/market_prob
    if market_prob <= 0 or market_prob >= 1:
        kelly = 0.0
    else:
        b = (1 - market_prob) / market_prob  # payout odds
        q = 1 - our_prob
        kelly = (b * our_prob - q) / b
    half_kelly = max(0.0, kelly / 2)  # use half-Kelly for safety

    return {
        "edge_pct": round(edge * 100, 1),
        "kelly_fraction": round(kelly, 3),
        "half_kelly_fraction": round(half_kelly, 3),
        "suggested_position_pct": round(min(half_kelly * 100, 25), 1),  # cap at 25%
        "trade_direction": "YES" if edge > 0 else "NO",
        "mispricing": abs(edge) > 0.10,  # significant if >10% edge
    }


# ─── MARKET DATA FETCHERS ─────────────────────────────────

async def fetch_crypto_price(symbol: str) -> Optional[dict]:
    """Fetch current price and 30d volatility from CoinGecko (free tier)."""
    symbol_map = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "AVAX": "avalanche-2", "MATIC": "matic-network",
        "LINK": "chainlink",
    }
    coin_id = symbol_map.get(symbol.upper(), symbol.lower())

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Current price
            r = await client.get(
                f"https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true"}
            )
            data = r.json()
            if coin_id not in data:
                return None

            price = data[coin_id]["usd"]
            change_24h = data[coin_id].get("usd_24h_change", 0) / 100

            # 30d history for volatility
            hist = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
                params={"vs_currency": "usd", "days": "30", "interval": "daily"}
            )
            hist_data = hist.json()
            prices = [p[1] for p in hist_data.get("prices", [])]

            if len(prices) > 5:
                returns = [math.log(prices[i] / prices[i-1]) for i in range(1, len(prices))]
                daily_vol = (sum(r**2 for r in returns) / len(returns)) ** 0.5
                annual_vol = daily_vol * math.sqrt(365)
            else:
                annual_vol = 0.80  # fallback for crypto

            return {
                "symbol": symbol.upper(),
                "price": price,
                "change_24h": change_24h,
                "annualized_volatility": round(annual_vol, 3),
                "source": "coingecko",
            }
    except Exception as e:
        logger.warning(f"Price fetch failed for {symbol}: {e}")
        return None


async def fetch_gold_price() -> Optional[dict]:
    """Fetch gold spot price."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.metals.live/v1/spot/gold",
                headers={"Accept": "application/json"}
            )
            data = r.json()
            price = float(data.get("price", 0))
            if price > 0:
                return {"symbol": "GOLD", "price": price, "annualized_volatility": 0.15, "source": "metals.live"}
    except Exception:
        pass
    # Fallback: approximate
    return {"symbol": "GOLD", "price": 2650.0, "annualized_volatility": 0.15, "source": "fallback"}


# ─── SIMULATION RUNNER ────────────────────────────────────

async def run_monte_carlo_analysis(
    question: str,
    asset: str,
    target_price: float,
    deadline_days: float,
    market_probability: float,      # e.g. 0.18 for 18 cents on polymarket
    direction: str = "above",       # "above" or "below"
    simulations: int = 10000,
    event_type: str = "close",      # "close" (at expiry) or "touch" (ever hits)
    macro_adjustment: float = 0.0,  # manual drift adjustment, e.g. -0.05 for bearish macro
) -> dict:
    """
    Full Monte Carlo analysis pipeline.
    Returns structured trade recommendation.
    """
    # 1. Fetch market data
    price_data = None
    if asset.upper() in ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "MATIC", "LINK"]:
        price_data = await fetch_crypto_price(asset)
    elif asset.upper() in ["GOLD", "XAU"]:
        price_data = await fetch_gold_price()

    if not price_data:
        # Use reasonable defaults
        price_data = {
            "price": target_price * 0.95,  # assume near target
            "annualized_volatility": 0.60,
            "source": "estimated",
        }

    current_price = price_data["price"]
    sigma = price_data["annualized_volatility"]
    mu = macro_adjustment  # net drift (we're neutral by default, let vol do the work)

    # 2. Run simulations
    if event_type == "touch":
        paths = multi_path_simulate(
            current_price=current_price,
            sigma=sigma,
            days=deadline_days,
            mu=mu,
            simulations=simulations,
        )
        our_prob = estimate_touch_probability(paths, target_price, direction)
    else:
        final_prices = gbm_simulate(
            current_price=current_price,
            mu=mu,
            sigma=sigma,
            days=deadline_days,
            simulations=simulations,
        )
        if direction == "above":
            our_prob = estimate_probability_above(final_prices, target_price)
        else:
            our_prob = estimate_probability_below(final_prices, target_price)

    # 3. Compute edge
    edge = compute_edge(our_prob, market_probability)

    # 4. Build percentiles
    if event_type != "touch":
        sorted_prices = sorted(final_prices)
        n = len(sorted_prices)
        percentiles = {
            "p10": round(sorted_prices[int(n * 0.10)], 2),
            "p25": round(sorted_prices[int(n * 0.25)], 2),
            "p50": round(sorted_prices[int(n * 0.50)], 2),
            "p75": round(sorted_prices[int(n * 0.75)], 2),
            "p90": round(sorted_prices[int(n * 0.90)], 2),
        }
    else:
        percentiles = {}

    # 5. Build recommendation
    confidence = "HIGH" if abs(edge["edge_pct"]) > 20 else "MEDIUM" if abs(edge["edge_pct"]) > 10 else "LOW"
    actionable = edge["mispricing"] and confidence in ["HIGH", "MEDIUM"]

    result = {
        "question": question,
        "asset": asset.upper(),
        "current_price": current_price,
        "target_price": target_price,
        "direction": direction,
        "deadline_days": deadline_days,
        "simulations_run": simulations,
        "event_type": event_type,
        "our_probability": round(our_prob, 3),
        "market_probability": round(market_probability, 3),
        "edge": edge,
        "percentiles": percentiles,
        "confidence": confidence,
        "actionable": actionable,
        "recommendation": {
            "action": f"BET {edge['trade_direction']}" if actionable else "SKIP",
            "reason": (
                f"Model shows {our_prob*100:.1f}% vs market's {market_probability*100:.1f}% — "
                f"{edge['edge_pct']:+.1f}% edge. "
                f"Suggest {edge['suggested_position_pct']}% of bankroll (half-Kelly)."
            ) if actionable else (
                f"Edge too small ({edge['edge_pct']:+.1f}%) or low confidence. Pass."
            ),
            "position_size_pct": edge["suggested_position_pct"] if actionable else 0,
        },
        "market_data": price_data,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    return result


# ─── POLYMARKET PARSER ────────────────────────────────────

def parse_polymarket_question(question: str) -> dict:
    """
    Parse a Polymarket-style question into simulation parameters.
    Returns best-guess asset, target, direction, deadline, and question_type.

    question_type is one of:
      "price"  — a numeric price target was extracted; Monte Carlo applies
      "event"  — binary outcome (election, approval, etc.); no price target
    """
    import re
    q = question.lower()

    # Asset detection
    asset = "BTC"
    for sym in ["btc", "bitcoin", "eth", "ethereum", "sol", "solana", "gold", "xau", "spy", "nasdaq"]:
        if sym in q:
            asset_map = {"btc": "BTC", "bitcoin": "BTC", "eth": "ETH", "ethereum": "ETH",
                        "sol": "SOL", "solana": "SOL", "gold": "GOLD", "xau": "GOLD"}
            asset = asset_map.get(sym, sym.upper())
            break

    # Direction detection
    direction = "above"
    if any(w in q for w in ["below", "under", "drop", "fall", "lose", "decline"]):
        direction = "below"

    # ── Price extraction (multiple strategies, in priority order) ──────────
    target = None

    # 1. Dollar-prefixed amounts: $100, $50k, $1,000, $100.50
    prices = re.findall(r'\$[\d,]+(?:\.\d+)?[kK]?', question)
    if prices:
        p = prices[0].replace('$', '').replace(',', '')
        if p.lower().endswith('k'):
            target = float(p[:-1]) * 1000
        else:
            target = float(p)

    # 2. Bare number + k/K suffix: "reach 100k", "above 50K", "hit 200k"
    if target is None:
        k_matches = re.findall(r'\b(\d+(?:\.\d+)?)\s*[kK]\b', question)
        if k_matches:
            target = float(k_matches[0]) * 1000

    # 3. Large bare integers (≥4 digits) likely to be prices:
    #    "reach 100000", "above 50000", "exceed 3500"
    #    Exclude year-like numbers (2020–2030) used as dates.
    if target is None:
        bare = re.findall(r'\b(\d[\d,]*(?:\.\d+)?)\b', question)
        for raw in bare:
            val = float(raw.replace(',', ''))
            # Skip 4-digit values in the year range (2000-2040) unless question
            # clearly refers to a price (has "above/below/reach/hit/exceed/over")
            is_price_context = any(w in q for w in ["above", "below", "reach", "hit", "exceed", "over", "under", "price"])
            if val >= 1000 and not (2000 <= val <= 2040 and not is_price_context):
                target = val
                break
            # Also accept 3-digit values for assets like gold or mid-cap prices
            if val >= 100 and asset in ("GOLD",) and is_price_context:
                target = val
                break

    # ── Classify question type ──────────────────────────────────────────────
    question_type = "price" if target is not None else "event"

    # ── Deadline extraction ─────────────────────────────────────────────────
    deadline_days = 30  # default
    if "week" in q:
        deadline_days = 7
    elif "month" in q:
        deadline_days = 30
    elif "year" in q:
        deadline_days = 365
    elif "quarter" in q:
        deadline_days = 90

    # Date-specific (e.g. "by march 15")
    date_patterns = re.findall(r'by\s+(\w+\s+\d+)', q)
    if date_patterns:
        try:
            from dateutil import parser as dateutil_parser
            target_date = dateutil_parser.parse(date_patterns[0], default=datetime.now())
            deadline_days = max(1, (target_date - datetime.now()).days)
        except Exception:
            pass

    return {
        "asset": asset,
        "target": target,
        "direction": direction,
        "deadline_days": deadline_days,
        "question_type": question_type,
    }


# ─── DAEMON INTEGRATION ───────────────────────────────────

async def scan_for_opportunities(
    questions: list[dict],  # list of {question, market_probability}
    simulations: int = 5000,
) -> list[dict]:
    """
    Batch scan a list of prediction market questions for mispricings.
    Returns sorted list of opportunities by edge.
    """
    opportunities = []

    event_markets = []  # binary events with no price target

    for item in questions:
        q = item.get("question", "")
        market_prob = item.get("market_probability", 0.5)

        params = parse_polymarket_question(q)

        if params["question_type"] == "event":
            # Binary event — can't run Monte Carlo, but don't silently drop it.
            logger.info(f"Auto-scan: binary event market (no price target): '{q[:80]}'")
            event_markets.append({
                "question": q,
                "asset": params["asset"],
                "question_type": "event",
                "market_probability": round(market_prob, 3),
                "actionable": False,
                "confidence": "N/A",
                "edge": {"edge_pct": 0, "trade_direction": "MONITOR"},
                "recommendation": {
                    "action": "MONITOR",
                    "reason": "Binary event market — no price target to model quantitatively.",
                    "position_size_pct": 0,
                },
                "computed_at": datetime.now(timezone.utc).isoformat(),
            })
            continue

        try:
            result = await run_monte_carlo_analysis(
                question=q,
                asset=params["asset"],
                target_price=params["target"],
                deadline_days=params["deadline_days"],
                market_probability=market_prob,
                direction=params["direction"],
                simulations=simulations,
            )
            result["question_type"] = "price"
            if result["actionable"]:
                opportunities.append(result)
        except Exception as e:
            logger.error(f"Scan error for '{q}': {e}")

    # Sort price-target opportunities by absolute edge; append event markets at end
    opportunities.sort(key=lambda x: abs(x["edge"]["edge_pct"]), reverse=True)
    return opportunities + event_markets


# ─── POLYMARKET API HELPERS ────────────────────────────────
# These functions call the Polymarket Gamma REST API.
# They are used by the /api/v1/polymarket/* endpoints in main.py.

GAMMA_BASE = "https://gamma-api.polymarket.com"


async def search_polymarket_markets(q: str, limit: int = 20) -> list[dict]:
    """Search active Polymarket markets by keyword."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GAMMA_BASE}/markets",
                params={"q": q, "active": "true", "closed": "false", "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            return markets[:limit]
    except Exception as e:
        logger.warning(f"search_polymarket_markets error: {e}")
        return []


async def get_trending_markets(limit: int = 50) -> list[dict]:
    """Fetch trending active Polymarket markets sorted by 24h volume."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GAMMA_BASE}/markets",
                params={"active": "true", "closed": "false", "order": "volume24hr", "ascending": "false", "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            # Filter out near-certain outcomes (price < 2% or > 98%)
            filtered = []
            for m in markets:
                try:
                    price = float(m.get("lastTradePrice") or m.get("midpoint") or 0.5)
                    if 0.02 <= price <= 0.98:
                        filtered.append(m)
                except Exception:
                    filtered.append(m)
            return filtered[:limit]
    except Exception as e:
        logger.warning(f"get_trending_markets error: {e}")
        return []


async def get_polymarket_market(market_id: str) -> Optional[dict]:
    """Fetch a single Polymarket market by condition ID or slug."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{GAMMA_BASE}/markets/{market_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"get_polymarket_market({market_id}) error: {e}")
        return None


async def auto_scan_polymarket(
    simulations: int = 5000,
    min_edge_pct: float = 7.0,
    limit: int = 30,
) -> list[dict]:
    """
    Autonomous end-to-end scan: fetch trending markets, run Monte Carlo on each,
    return opportunities whose edge exceeds min_edge_pct, plus binary event markets
    that couldn't be quantitatively scored.
    """
    markets = await get_trending_markets(limit=limit)
    if not markets:
        return []

    questions = []
    for m in markets:
        question = m.get("question") or m.get("title") or ""
        if not question:
            continue
        # Polymarket prices are 0-1 representing probability
        try:
            prob = float(m.get("lastTradePrice") or m.get("midpoint") or 0.5)
        except Exception:
            prob = 0.5
        questions.append({"question": question, "market_probability": prob})

    logger.info(f"Auto-scan: fetched {len(markets)} trending markets, extracted {len(questions)} questions")
    all_results = await scan_for_opportunities(questions, simulations=simulations)

    # Keep price-target results that clear the edge bar, plus all event markets
    filtered = [
        o for o in all_results
        if o.get("question_type") == "event"
        or abs(o.get("edge", {}).get("edge_pct", 0)) >= min_edge_pct
    ]
    logger.info(f"Auto-scan: {len(filtered)} results returned (threshold={min_edge_pct}%)")
    return filtered
