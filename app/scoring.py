from __future__ import annotations

import re
from dataclasses import dataclass

COUNT_PATTERNS = [
    re.compile(r'(\d{2,5})\s*\+?\s*(?:cards|card lot|pokemon cards|pkmn cards)', re.I),
    re.compile(r'lot of\s*(\d{2,5})', re.I),
    re.compile(r'collection of\s*(\d{2,5})', re.I),
]

QUALITY_KEYWORDS = {
    'binder': 1.15,
    'collection': 1.1,
    'vintage': 1.25,
    'wotc': 1.35,
    'holo': 1.2,
    'holos': 1.2,
    'reverse holo': 1.15,
    'ex': 1.08,
    'gx': 1.08,
    'v ': 1.08,
    'vmax': 1.12,
    'full art': 1.15,
    'japanese': 1.05,
}

PENALTY_KEYWORDS = {
    'damaged': 0.8,
    'played': 0.9,
    'energy only': 0.65,
    'commons only': 0.75,
    'bulk common': 0.8,
}


@dataclass
class ScoreResult:
    estimated_card_count: int
    price_per_card: float | None
    score: float
    confidence: float


def estimate_card_count(title: str) -> tuple[int, float]:
    lowered = title.lower()
    for pattern in COUNT_PATTERNS:
        match = pattern.search(lowered)
        if match:
            return int(match.group(1)), 0.95

    if 'binder' in lowered and 'collection' in lowered:
        return 250, 0.45
    if 'binder' in lowered:
        return 180, 0.35
    if 'collection' in lowered:
        return 150, 0.30
    if 'bulk' in lowered:
        return 100, 0.25
    if 'lot' in lowered:
        return 75, 0.20
    return 0, 0.05


def score_listing(title: str, price: float) -> ScoreResult:
    estimated_count, confidence = estimate_card_count(title)
    if price <= 0:
        price = 0.01

    price_per_card = round(price / estimated_count, 4) if estimated_count > 0 else None
    base = (estimated_count / price) if estimated_count > 0 else 0.0

    keyword_multiplier = 1.0
    lowered = f' {title.lower()} '
    for keyword, boost in QUALITY_KEYWORDS.items():
        if keyword in lowered:
            keyword_multiplier *= boost
    for keyword, penalty in PENALTY_KEYWORDS.items():
        if keyword in lowered:
            keyword_multiplier *= penalty

    confidence_multiplier = max(0.4, confidence)
    freshness_bonus = 1.02
    score = base * keyword_multiplier * confidence_multiplier * freshness_bonus
    score = round(score, 4)
    return ScoreResult(
        estimated_card_count=estimated_count,
        price_per_card=price_per_card,
        score=score,
        confidence=round(confidence, 3),
    )


def compact_score_label(score: float) -> str:
    if score >= 8:
        return '<!-- 🔵 -->'
    if score >= 4:
        return '<!-- 👋 -->'
    if score >= 2:
        return '<!-- 👀 -->'
    return '.'
