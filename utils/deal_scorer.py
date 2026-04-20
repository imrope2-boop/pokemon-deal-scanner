"""
Deal Scorer — assigns a 1-10 score and tier to each deal
Scoring factors: price/card, % below market, lot size, category quality,
                 desperation signals, unsearched bonus, negative signal penalty
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
NEGATIVE_SIGNALS = CONFIG.get("negative_signals", [])
DESPERATION_SIGNALS = CONFIG.get("desperation_signals", [])
UNSEARCHED_SIGNALS = CONFIG.get("unsearched_signals", [])


def detect_categories(title: str, description: str) -> List[CardCategory]:
    """
    Detect card categories from post text.
    Returns a list of detected categories.
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

    # Fallback: if generic bulk/lot keywords, add MIXED (not BULK_COMMONS)
    # MIXED has a higher baseline value than BULK_COMMONS
    if any(k in combined for k in ["lot", "collection", "mixed", "bundle"]) and not categories:
        categories.append(CardCategory.MIXED)
    elif any(k in combined for k in ["bulk", "dump"]) and not categories:
        categories.append(CardCategory.BULK_COMMONS)

    if not categories:
        categories.append(CardCategory.UNKNOWN)

    # If multiple categories detected, add MIXED
    if len(categories) > 1 and CardCategory.MIXED not in categories:
        categories.append(CardCategory.MIXED)

    return categories


def detect_desperation_score(title: str, description: str) -> float:
    """
    Detect seller desperation signals.
    Returns a bonus score 0.0 - 2.0
    """
    combined = f"{title} {description}".lower()
    hits = sum(1 for s in DESPERATION_SIGNALS if s in combined)
    if hits >= 3:
        return 2.0
    elif hits == 2:
        return 1.5
    elif hits == 1:
        return 1.0
    return 0.0


def detect_unsearched_score(title: str, description: str) -> float:
    """
    Detect 'unsearched/unweighed' signals — major profit indicator.
    Returns a bonus score 0.0 - 2.0
    """
    combined = f"{title} {description}".lower()
    hits = sum(1 for s in UNSEARCHED_SIGNALS if s in combined)
    if hits >= 2:
        return 2.0
    elif hits == 1:
        return 1.5
    return 0.0


def detect_negative_penalty(title: str, description: str) -> float:
    """
    Detect negative signals (searched, sorted, hits pulled).
    Returns a penalty 0.0 - 3.0 (subtracted from score)
    """
    combined = f"{title} {description}".lower()
    hits = sum(1 for s in NEGATIVE_SIGNALS if s in combined)
    if hits >= 3:
        return 3.0
    elif hits == 2:
        return 2.0
    elif hits == 1:
        return 1.0
    return 0.0


def detect_high_value_set_bonus(title: str, description: str) -> float:
    """
    Bonus for mention of high-demand chase sets.
    Returns a bonus 0.0 - 1.5
    """
    combined = f"{title} {description}".lower()
    chase_keywords = [
        "hidden fates", "shining fates", "evolving skies", "crown zenith",
        "prismatic", "151", "celebrations", "champions path",
        "base set", "shadowless", "1st edition", "wotc"
    ]
    hits = sum(1 for k in chase_keywords if k in combined)
    if hits >= 2:
        return 1.5
    elif hits == 1:
        return 0.75
    return 0.0


def is_relevant_post(title: str, description: str, price: Optional[float]) -> bool:
    """
    Filter: Is this post worth analyzing?
    Broad filter — prefer false negatives over false positives (show more deals).
    """
    combined = f"{title} {description}".lower()

    # Hard exclude: definitively disqualifying
    hard_excludes = ["graded only", "slabs only", "weighed and searched", "sorted and searched",
                     "hits pulled", "rares removed", "holos removed"]
    if any(k in combined for k in hard_excludes):
        return False

    # Must have pokemon reference
    has_pokemon = any(k in combined for k in ["pokemon", "pokémon", "pkmn", "tcg"])
    if not has_pokemon:
        return False

    # Must have card/lot reference
    has_cards = any(k in combined for k in [
        "card", "lot", "bulk", "collection", "bundle", "binder", "holo",
        "pack", "booster", "set", "vintage", "wotc"
    ])
    if not has_cards:
        return False

    # Must have a price or offer language
    if price is None:
        has_offer = any(k in combined for k in ["obo", "offer", "make offer", "mo", "negotiable", "asking"])
        if not has_offer:
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
    price_breakdown: Dict,
    title: str = "",
    description: str = ""
) -> Tuple[float, DealTier]:
    """
    Score a deal from 1-10 and assign a tier.

    Scoring factors:
    - % below market value         (35% weight)
    - Price per card vs baseline   (25% weight)
    - Lot size                     (10% weight)
    - Category quality             (15% weight)
    - Desperation signals          (bonus up to +2.0)
    - Unsearched bonus             (bonus up to +2.0)
    - High-value set bonus         (bonus up to +1.5)
    - Negative signal penalty      (penalty up to -3.0)
    """
    score = 0.0

    # ── Factor 1: % Below Market (35%) ────────────────────────────
    if estimated_market_value > 0:
        below_pct = ((estimated_market_value - asking_price) / estimated_market_value) * 100
    else:
        below_pct = 0

    if below_pct >= 60:
        market_score = 10.0
    elif below_pct >= 40:
        market_score = 8.0 + (below_pct - 40) / 20 * 2
    elif below_pct >= 25:
        market_score = 5.5 + (below_pct - 25) / 15 * 2.5
    elif below_pct >= 10:
        market_score = 3.0 + (below_pct - 10) / 15 * 2.5
    elif below_pct >= 0:
        market_score = below_pct / 10 * 3.0
    else:
        market_score = max(0.0, 3.0 + below_pct / 20)  # Slight negative but not zero

    score += market_score * 0.35

    # ── Factor 2: Price Per Card vs Baseline (25%) ────────────────
    price_per_card = price_breakdown.get("price_per_card")
    avg_baseline = price_breakdown.get("avg_price_per_card", 0.10)

    if price_per_card and avg_baseline > 0:
        ratio = price_per_card / avg_baseline
        if ratio <= 0.2:
            ppc_score = 10.0
        elif ratio <= 0.4:
            ppc_score = 8.5
        elif ratio <= 0.6:
            ppc_score = 7.0
        elif ratio <= 0.8:
            ppc_score = 5.5
        elif ratio <= 1.0:
            ppc_score = 4.0
        elif ratio <= 1.5:
            ppc_score = 2.5
        else:
            ppc_score = max(0.5, 2.5 - (ratio - 1.5) * 1.5)
    else:
        ppc_score = 4.0  # Neutral if no card count info

    score += ppc_score * 0.25

    # ── Factor 3: Lot Size (10%) ──────────────────────────────────
    if card_count:
        if card_count >= 5000:
            size_score = 10.0
        elif card_count >= 2000:
            size_score = 8.5
        elif card_count >= 1000:
            size_score = 7.5
        elif card_count >= 500:
            size_score = 6.5
        elif card_count >= 200:
            size_score = 5.5
        elif card_count >= 100:
            size_score = 4.5
        elif card_count >= 50:
            size_score = 3.5
        elif card_count >= 20:
            size_score = 2.5
        else:
            size_score = 1.5
    else:
        size_score = 4.0

    score += size_score * 0.10

    # ── Factor 4: Category Quality (15%) ─────────────────────────
    category_scores = {
        CardCategory.CHASE_SETS: 10.0,
        CardCategory.VINTAGE_WOTC: 9.5,
        CardCategory.HOLO_REVERSE: 8.0,
        CardCategory.V_EX_GX: 7.0,
        CardCategory.MIXED: 5.5,
        CardCategory.BULK_COMMONS: 3.5,
        CardCategory.UNKNOWN: 3.0
    }

    if categories:
        cat_score = max(category_scores.get(c, 3.0) for c in categories)
    else:
        cat_score = 3.0

    score += cat_score * 0.15

    # ── Bonuses and Penalties ─────────────────────────────────────
    if title or description:
        desperation = detect_desperation_score(title, description)
        unsearched = detect_unsearched_score(title, description)
        high_value = detect_high_value_set_bonus(title, description)
        penalty = detect_negative_penalty(title, description)

        score += desperation * 0.10
        score += unsearched * 0.10
        score += high_value * 0.05
        score -= penalty * 0.15

    # Clamp to 1-10
    score = max(1.0, min(10.0, score))

    # ── Assign Tier ───────────────────────────────────────────────
    net_profit = price_breakdown.get("net_profit_after_fees", 0)

    # GREAT: high score, or big discount, or very low price-per-card with decent size
    if (score >= 7.0 or below_pct >= 35 or
            (price_per_card and price_per_card <= 0.05 and card_count and card_count >= 100)):
        tier = DealTier.GREAT
    elif (score >= 4.5 or below_pct >= 20 or
          (price_per_card and price_per_card <= 0.08 and card_count and card_count >= 50)):
        tier = DealTier.GOOD
    elif score >= 2.5 or below_pct >= 10:
        tier = DealTier.DECENT
    else:
        tier = DealTier.NO_DEAL

    # Downgrade if deeply unprofitable after fees
    if net_profit < -30 and tier == DealTier.GREAT:
        tier = DealTier.GOOD
    elif net_profit < -50 and tier in (DealTier.GREAT, DealTier.GOOD):
        tier = DealTier.DECENT

    return round(score, 1), tier


def build_tags(title: str, description: str, categories: List[CardCategory], tier: DealTier) -> List[str]:
    """Build searchable/display tags for a deal"""
    combined = f"{title} {description}".lower()
    tags = []

    tag_signals = {
        "vintage": ["wotc", "base set", "1st edition", "shadowless", "1999", "2000", "jungle", "fossil", "neo"],
        "chase-set": ["hidden fates", "shining fates", "evolving skies", "crown zenith", "prismatic", "151", "celebrations"],
        "holo": ["holo", "foil", "reverse holo"],
        "bulk": ["bulk", "lot", "1000", "500", "collection"],
        "v-cards": ["vmax", "vstar", "gx", " ex ", "tera ex"],
        "unsearched": ["unsearched", "unweighed", "not searched", "not sorted"],
        "desperation": ["must sell", "need gone", "moving", "quick sale", "clearing", "inherited"],
        "price-drop": ["price drop", "lowered", "reduced", "will negotiate"],
        "obo": ["obo", "best offer", "make offer"],
        "free-shipping": ["free shipping", "shipping included"],
        "near-mint": ["nm", "near mint", "pack fresh", "mint"],
        "collection-dump": ["collection dump", "binder dump", "dumping", "clearing collection"],
        "grading-target": ["grading", "psa", "bgs", "cgc", "grade worthy"],
    }

    for tag, signals in tag_signals.items():
        if any(s in combined for s in signals):
            tags.append(tag)

    for cat in categories:
        if cat not in (CardCategory.UNKNOWN, CardCategory.MIXED):
            tags.append(cat.value.replace("_", "-"))

    tags.append(tier.value)

    return list(set(tags))
