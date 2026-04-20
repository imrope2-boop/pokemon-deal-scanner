"""
Price Engine for Pokemon Deal Scanner
Fetches market prices from multiple APIs and estimates deal value
"""
import re
import json
import os
import hashlib
import requests
import time
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from models.deal import CardCategory

# ─────────────────────────────────────────────
# Market price baselines (updated 2026 research)
# Used as fallback when API is unavailable
# ─────────────────────────────────────────────
BASELINE_PRICES = {
    CardCategory.VINTAGE_WOTC: {
        "avg_price_per_card": 0.75,
        "low": 0.35,
        "high": 3.50,
        "resale_rate": "high",
        "volatility": "low",
        "resale_multiplier": 3.5
    },
    CardCategory.HOLO_REVERSE: {
        "avg_price_per_card": 0.35,
        "low": 0.10,
        "high": 2.00,
        "resale_rate": "high",
        "volatility": "medium",
        "resale_multiplier": 4.0
    },
    CardCategory.V_EX_GX: {
        "avg_price_per_card": 1.25,
        "low": 0.40,
        "high": 5.00,
        "resale_rate": "medium",
        "volatility": "medium",
        "resale_multiplier": 2.5
    },
    CardCategory.BULK_COMMONS: {
        "avg_price_per_card": 0.02,
        "low": 0.01,
        "high": 0.05,
        "resale_rate": "medium",
        "volatility": "low",
        "resale_multiplier": 2.0
    },
    CardCategory.MIXED: {
        "avg_price_per_card": 0.08,
        "low": 0.02,
        "high": 0.35,
        "resale_rate": "medium",
        "volatility": "medium",
        "resale_multiplier": 2.2
    },
    CardCategory.UNKNOWN: {
        "avg_price_per_card": 0.05,
        "low": 0.01,
        "high": 0.20,
        "resale_rate": "unknown",
        "volatility": "high",
        "resale_multiplier": 1.8
    }
}

# Simple in-memory price cache (15 min TTL)
_price_cache: Dict[str, Tuple[Dict, float]] = {}
CACHE_TTL = 900  # 15 minutes


def _cache_get(key: str) -> Optional[Dict]:
    if key in _price_cache:
        data, ts = _price_cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _cache_set(key: str, data: Dict):
    _price_cache[key] = (data, time.time())


def get_market_price(category: CardCategory, card_name: str = None) -> Dict:
    """
    Get market price data for a card category.
    Tries Pokemon Price Tracker API first, falls back to baselines.
    Returns: dict with avg_price_per_card, resale_rate, volatility, resale_multiplier
    """
    cache_key = f"{category.value}:{card_name or 'bulk'}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    api_key = os.getenv("POKEMON_PRICE_API_KEY")
    if api_key and card_name:
        try:
            result = _fetch_pokemon_price_api(card_name, api_key)
            if result:
                _cache_set(cache_key, result)
                return result
        except Exception as e:
            print(f"⚠️  Price API error: {e}, using baseline")

    baseline = BASELINE_PRICES.get(category, BASELINE_PRICES[CardCategory.UNKNOWN]).copy()
    _cache_set(cache_key, baseline)
    return baseline


def _fetch_pokemon_price_api(card_name: str, api_key: str) -> Optional[Dict]:
    """Fetch from Pokemon Price Tracker API"""
    url = "https://api.pokemonpricetracker.com/v1/prices"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    params = {"name": card_name, "limit": 1}

    resp = requests.get(url, headers=headers, params=params, timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("results"):
            card = data["results"][0]
            return {
                "avg_price_per_card": card.get("market_price", 0),
                "low": card.get("low_price", 0),
                "high": card.get("high_price", 0),
                "resale_rate": "high",
                "volatility": "medium",
                "resale_multiplier": 2.5
            }
    return None


def estimate_lot_value(
    asking_price: float,
    card_count: Optional[int],
    categories: List[CardCategory],
    description: str = ""
) -> Dict:
    """
    Core valuation engine — estimates a lot's market value and profit potential.
    Returns a full breakdown dict.
    """
    if not categories:
        categories = [CardCategory.UNKNOWN]

    # Build weighted average market price per card based on detected categories
    total_weight = 0
    weighted_price = 0
    weighted_multiplier = 0
    all_volatilities = []
    all_resale_rates = []

    for cat in categories:
        market_data = get_market_price(cat)
        weight = _category_weight(cat, description)
        weighted_price += market_data["avg_price_per_card"] * weight
        weighted_multiplier += market_data["resale_multiplier"] * weight
        total_weight += weight
        all_volatilities.append(market_data["volatility"])
        all_resale_rates.append(market_data["resale_rate"])

    if total_weight > 0:
        avg_price_per_card = weighted_price / total_weight
        avg_multiplier = weighted_multiplier / total_weight
    else:
        avg_price_per_card = BASELINE_PRICES[CardCategory.UNKNOWN]["avg_price_per_card"]
        avg_multiplier = 1.8

    # Estimate card count from description if not provided
    if not card_count:
        card_count = _extract_card_count(description)

    # Estimate total market value
    if card_count:
        estimated_market_value = card_count * avg_price_per_card
        price_per_card = asking_price / card_count if card_count > 0 else None
    else:
        # No card count — estimate conservatively
        estimated_market_value = asking_price * avg_multiplier
        price_per_card = None

    estimated_profit = estimated_market_value - asking_price

    if estimated_market_value > 0:
        profit_margin_pct = (estimated_profit / estimated_market_value) * 100
    else:
        profit_margin_pct = 0

    # Fees estimate
    platform_fees_pct = 13.0
    shipping = _estimate_shipping(card_count)
    fees = estimated_market_value * (platform_fees_pct / 100)
    net_profit = estimated_market_value - asking_price - fees - shipping

    # Determine dominant volatility and resale rate
    volatility = _most_common(all_volatilities)
    resale_rate = _most_common(all_resale_rates)

    return {
        "asking_price": asking_price,
        "card_count": card_count,
        "price_per_card": round(price_per_card, 4) if price_per_card else None,
        "estimated_market_value": round(estimated_market_value, 2),
        "estimated_profit": round(estimated_profit, 2),
        "profit_margin_pct": round(profit_margin_pct, 1),
        "net_profit_after_fees": round(net_profit, 2),
        "platform_fees_pct": platform_fees_pct,
        "shipping_estimate": shipping,
        "avg_price_per_card": round(avg_price_per_card, 4),
        "resale_multiplier": round(avg_multiplier, 2),
        "resale_rate": resale_rate,
        "market_volatility": volatility
    }


def _category_weight(category: CardCategory, description: str) -> float:
    """Weight a category based on how prominently it's mentioned"""
    desc_lower = description.lower()
    weights = {
        CardCategory.VINTAGE_WOTC: 3.0 if any(k in desc_lower for k in ["wotc", "base set", "1st edition", "shadowless", "1999", "2000"]) else 1.0,
        CardCategory.HOLO_REVERSE: 2.5 if any(k in desc_lower for k in ["holo", "foil", "reverse", "shiny"]) else 1.0,
        CardCategory.V_EX_GX: 2.0 if any(k in desc_lower for k in ["vmax", "vstar", "gx", " ex ", "full art", "ultra rare"]) else 1.0,
        CardCategory.BULK_COMMONS: 1.5 if any(k in desc_lower for k in ["bulk", "common", "lot", "1000", "500"]) else 1.0,
        CardCategory.MIXED: 1.2,
        CardCategory.UNKNOWN: 1.0
    }
    return weights.get(category, 1.0)


def _extract_card_count(text: str) -> Optional[int]:
    """Try to extract card count from description text"""
    patterns = [
        r'(\d{2,5})\s*(?:cards?|pokemon\s*cards?|pcs?|pieces?)',
        r'(?:lot|collection)\s*of\s*(\d{2,5})',
        r'(\d{2,5})\s*(?:bulk|mixed)',
        r'over\s*(\d{2,5})',
        r'approximately\s*(\d{2,5})',
        r'~\s*(\d{2,5})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            count = int(match.group(1))
            if 10 <= count <= 100000:
                return count
    return None


def _estimate_shipping(card_count: Optional[int]) -> float:
    """Estimate shipping cost based on card count"""
    if not card_count:
        return 8.0
    if card_count < 100:
        return 5.0
    elif card_count < 500:
        return 8.0
    elif card_count < 1000:
        return 12.0
    elif card_count < 5000:
        return 20.0
    else:
        return 35.0


def _most_common(lst: List[str]) -> str:
    if not lst:
        return "unknown"
    return max(set(lst), key=lst.count)
