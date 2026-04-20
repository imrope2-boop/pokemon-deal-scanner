"""
Deal data models for the Pokemon Bulk Deal Scanner
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict
from enum import Enum


class DealTier(str, Enum):
    GREAT = "great"
    GOOD = "good"
    DECENT = "decent"
    NO_DEAL = "no_deal"


class Platform(str, Enum):
    REDDIT = "reddit"
    EBAY = "ebay"
    TCGPLAYER = "tcgplayer"
    FACEBOOK = "facebook"
    YAHOO_JAPAN = "yahoo_japan"
    MERCARI_JAPAN = "mercari_japan"
    KIJIJI = "kijiji"
    OTHER = "other"


class CardCategory(str, Enum):
    VINTAGE_WOTC = "vintage_wotc"
    CHASE_SETS = "chase_sets"          # Hidden Fates, Shining Fates, Evolving Skies, 151, Crown Zenith
    HOLO_REVERSE = "holo_reverse_holo"
    V_EX_GX = "v_ex_gx_cards"
    BULK_COMMONS = "bulk_commons"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass
class PriceBreakdown:
    """Detailed price and profit analysis for a deal"""
    asking_price: float
    estimated_market_value: float
    estimated_profit: float
    profit_margin_pct: float
    price_per_card: Optional[float] = None
    card_count: Optional[int] = None
    resale_rate: str = "unknown"
    market_volatility: str = "unknown"
    resale_multiplier: float = 1.0
    platform_fees_pct: float = 13.0  # Default eBay fees
    shipping_estimate: float = 8.0
    net_profit_after_fees: float = 0.0

    def __post_init__(self):
        fees = self.estimated_market_value * (self.platform_fees_pct / 100)
        self.net_profit_after_fees = self.estimated_market_value - self.asking_price - fees - self.shipping_estimate


@dataclass
class Deal:
    """Represents a Pokemon card deal found by the scanner"""
    id: str
    title: str
    description: str
    url: str
    platform: Platform
    asking_price: float
    card_count: Optional[int]
    card_categories: List[CardCategory]
    tier: DealTier
    score: float  # 1-10 deal score
    price_breakdown: PriceBreakdown
    seller_info: str
    posted_at: datetime
    found_at: datetime = field(default_factory=datetime.utcnow)
    images: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    is_active: bool = True
    notes: str = ""

    @property
    def tier_emoji(self) -> str:
        return {"great": "🟢", "good": "🟡", "decent": "🔴", "no_deal": "⚫"}.get(self.tier, "⚫")

    @property
    def tier_label(self) -> str:
        return {"great": "GREAT DEAL", "good": "GOOD DEAL", "decent": "DECENT DEAL", "no_deal": "Not a Deal"}.get(self.tier, "Unknown")

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description[:500],
            "url": self.url,
            "platform": self.platform.value,
            "asking_price": self.asking_price,
            "card_count": self.card_count,
            "card_categories": [c.value for c in self.card_categories],
            "tier": self.tier.value,
            "tier_emoji": self.tier_emoji,
            "tier_label": self.tier_label,
            "score": round(self.score, 1),
            "price_per_card": self.price_breakdown.price_per_card,
            "estimated_market_value": round(self.price_breakdown.estimated_market_value, 2),
            "estimated_profit": round(self.price_breakdown.estimated_profit, 2),
            "profit_margin_pct": round(self.price_breakdown.profit_margin_pct, 1),
            "net_profit_after_fees": round(self.price_breakdown.net_profit_after_fees, 2),
            "resale_rate": self.price_breakdown.resale_rate,
            "market_volatility": self.price_breakdown.market_volatility,
            "seller_info": self.seller_info,
            "posted_at": self.posted_at.isoformat(),
            "found_at": self.found_at.isoformat(),
            "images": self.images[:3],
            "tags": self.tags,
            "is_active": self.is_active,
            "notes": self.notes
        }
