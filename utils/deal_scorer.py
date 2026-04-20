"""
Deal Scorer — assigns a 1-10 score and tier to each deal
Based on: price per card, % below market, lot size, category quality
"""
import json
import re
from typing import List, Optional, Dict, Tuple
from models.deal import CardCategory, DealTier

# Load config
try:
    with open("config.json") as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    CONFIG = {}

THRESHOLDS = CONFIG.get("deal_thresholds", {}).get("tiers", {})
EXCLUDED_KEYWORDS = CONFIG.get("excluded_keywords", [])
SCAN_KEYWORDS = CONFIG.get("scan_keywords", [])


def detect_categories(title: str, description: str) -> List[CardCategory]:
    """
    Detect card categories from post text.
    Returns a list of detected categories (can be multiple).
    """
    combined = f"{title} {description}".lower()
    categories = []

    category_signals = CONFIG.get("card_categories", {})

    for cat_key, cat_data in category_signals.items():
        keywords = cat_data.get("keywords", [])
        if any(kw.lower() in combined for kw in keywords):
            try:
                categories.append(CardCategory(cat_key))
            except ValueError:
                pass

    # Fallback: if bulk keywords found, add bulk
    if any(k in combined for k in ["bulk", "lot", "collection", "dump"]) and not categories:
        categories.append(CardCategory.BULK_COMMONS)

    if not categories:
        categories.append(CardCategory.UNKNOWN)

    # If multiple, add MIXED
    if len(categories) > 1 and CardCategory.MIXED not in categories:
        categories.append(CardCategory.MIXED)

    return categories


def is_relevant_post(title: str, description: str, price: Optional[float]) -> bool:
    """
    Filter: Is this post worth analyzing?
    Returns False for irrelevant, graded-only, or single card posts.
    """
    combined = f"{title} {description}".lower()

    # Must have at least one scan keyword
    has_keyword = any(kw.lower() in combined for kw in SCAN_KEYWORDS)
    if not has_keyword:
        # Check for pokemon + cards combo
        has_keyword = ("pokemon" in combined and ("card" in combined or "lot" in combined))

    if not has_keyword:
        return False

    # Exclude if only excluded keywords
    excluded_count = sum(1 for kw in EXCLUDED_KEYWORDS if kw.lower() in combined)
    if excluded_count > 0 and not any(k in combined for k in ["bulk", "lot"]):
        return False

    # Must have a parseable price or "make offer"
    if price is None and not any(k in combined for k in ["obo", "offer", "make offer", "mo"]):
        return False

    return True


def extract_price(text: str) -> Optional[float]:
    """Extract price from post text"""
    patterns = [
        r'\$\s*(\d+(?:\.\d{1,2})?)',
        r'(\d+(?:\.\d{1,2})?)\s*(?:usd|dollars?)',
        r'(?:asking|price|selling for|listed at)\s*\$?\s*(\d+(?:\.\d{1,2})?)',
        r'(?:^|\s)\$(\d+(?:\.\d{1,2})?)(?:\s|$)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            # Return the first reasonable price (filter out clearly wrong ones)
            for m in matches:
                price = float(m)
                if 0.50 <= price <= 10000:
                    return price
    return None


def score_deal(
    asking_price: float,
    estimated_market_value: float,
    card_count: Optional[int],
    categories: List[CardCategory],
    price_breakdown: Dict
) -> Tuple[float, DealTier]:
    """
    Score a deal from 1-10 and assign a tier.

    Scoring factors:
    - % below market value (40% weight)
    - Price per card vs baseline (25% weight)
    - Lot size (15% weight)
    - Category quality (20% weight)
    """
    score = 0.0

    # ── Factor 1: % Below Market (40% of score) ──────────────────
    if estimated_market_value > 0:
        below_pct = ((estimated_market_value - asking_price) / estimated_market_value) * 100
    else:
        below_pct = 0

    if below_pct >= 60:
        market_score = 10.0
    elif below_pct >= 40:
        market_score = 8.0 + (below_pct - 40) / 20 * 2
    elif below_pct >= 25:
        market_score = 5.0 + (below_pct - 25) / 15 * 3
    elif below_pct >= 15:
        market_score = 3.0 + (below_pct - 15) / 10 * 2
    elif below_pct >= 0:
        market_score = below_pct / 15 * 3
    else:
        market_score = 0.0  # Above market — not a deal

    score += market_score * 0.40

    # ── Factor 2: Price Per Card vs Baseline (25% of score) ──────
    price_per_card = price_breakdown.get("price_per_card")
    avg_baseline = price_breakdown.get("avg_price_per_card", 0.05)

    if price_per_card and avg_baseline > 0:
        ratio = price_per_card / avg_baseline
        if ratio <= 0.3:
            ppc_score = 10.0
        elif ratio <= 0.5:
            ppc_score = 8.0
        elif ratio <= 0.7:
            ppc_score = 6.0
        elif ratio <= 1.0:
            ppc_score = 4.0
        else:
            ppc_score = max(0, 4.0 - (ratio - 1.0) * 3)
    else:
        ppc_score = 5.0  # Neutral if no card count

    score += ppc_score * 0.25

    # ── Factor 3: Lot Size (15% of score) ────────────────────────
    if card_count:
        if card_count >= 5000:
            size_score = 10.0
        elif card_count >= 2000:
            size_score = 8.0
        elif card_count >= 1000:
            size_score = 7.0
        elif card_count >= 500:
            size_score = 6.0
        elif card_count >= 200:
            size_score = 5.0
        elif card_count >= 100:
            size_score = 4.0
        elif card_count >= 50:
            size_score = 3.0
        else:
            size_score = 1.0
    else:
        size_score = 4.0  # Unknown count — neutral

    score += size_score * 0.15

    # ── Factor 4: Category Quality (20% of score) ─────────────────
    category_scores = {
        CardCategory.VINTAGE_WOTC: 10.0,
        CardCategory.HOLO_REVERSE: 8.5,
        CardCategory.V_EX_GX: 7.5,
        CardCategory.MIXED: 6.0,
        CardCategory.BULK_COMMONS: 4.0,
        CardCategory.UNKNOWN: 3.0
    }

    if categories:
        # Use best category score
        cat_score = max(category_scores.get(c, 3.0) for c in categories)
    else:
        cat_score = 3.0

    score += cat_score * 0.20

    # Clamp to 1-10
    score = max(1.0, min(10.0, score))

    # ── Assign Tier ───────────────────────────────────────────────
    net_profit = price_breakdown.get("net_profit_after_fees", 0)

    if score >= 8 or below_pct >= 40 or (price_per_card and price_per_card <= 0.02 and card_count and card_count >= 200):
        tier = DealTier.GREAT
    elif score >= 5 or below_pct >= 25:
        tier = DealTier.GOOD
    elif score >= 3 or below_pct >= 15:
        tier = DealTier.DECENT
    else:
        tier = DealTier.NO_DEAL

    # Downgrade if net profit is negative
    if net_profit < 0 and tier == DealTier.GREAT:
        tier = DealTier.GOOD
    elif net_profit < -20 and tier in (DealTier.GREAT, DealTier.GOOD):
        tier = DealTier.DECENT

    return round(score, 1), tier


def build_tags(title: str, description: str, categories: List[CardCategory], tier: DealTier) -> List[str]:
    """Build searchable/display tags for a deal"""
    combined = f"{title} {description}".lower()
    tags = []

    tag_signals = {
        "vintage": ["wotc", "base set", "1st edition", "shadowless", "1999", "2000", "jungle", "fossil"],
        "holo": ["holo", "foil", "reverse holo"],
        "bulk": ["bulk", "lot", "1000", "500", "collection"],
        "v-cards": ["vmax", "vstar", "gx", " ex "],
        "price-drop": ["price drop", "lowered", "reduced", "must sell"],
        "obo": ["obo", "best offer", "make offer"],
        "local": ["local", "pickup", "in person"],
        "shipping": ["shipping included", "free shipping", "ships"],
        "near-mint": ["nm", "near mint", "pack fresh"],
        "collection-dump": ["collection dump", "bulk dump", "dumping"]
    }

    for tag, signals in tag_signals.items():
        if any(s in combined for s in signals):
            tags.append(tag)

    for cat in categories:
        if cat != CardCategory.UNKNOWN:
            tags.append(cat.value.replace("_", "-"))

    tags.append(tier.value)

    return list(set(tags))
