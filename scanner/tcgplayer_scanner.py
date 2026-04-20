"""
TCGPlayer Scanner — searches TCGPlayer for bulk Pokemon card deals
Focuses on bulk lot listings and significantly under-market listings
"""
import re
import hashlib
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from typing import List, Optional, Dict

from models.deal import Deal, Platform, DealTier, CardCategory, PriceBreakdown
from utils.deal_scorer import detect_categories, is_relevant_post, score_deal, build_tags
from pricing.price_engine import estimate_lot_value, _extract_card_count

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

TCG_SEARCH_TERMS = [
    "pokemon bulk lot",
    "pokemon collection lot",
    "pokemon holo lot",
    "pokemon vintage bulk",
]


def scan_tcgplayer() -> List[Deal]:
    """
    Scan TCGPlayer for bulk Pokemon card deals.
    Note: TCGPlayer has strong anti-bot protection.
    This uses their public search endpoint.
    """
    deals = []
    print("🔍 Scanning TCGPlayer...")

    # TCGPlayer search for Pokemon bulk/lots
    search_url = "https://www.tcgplayer.com/search/pokemon/product"
    params = {
        "q": "bulk lot",
        "view": "grid",
        "page": 1,
        "productLineName": "pokemon",
        "Language": "English"
    }

    try:
        resp = requests.get(search_url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")

            # Parse product cards
            products = soup.select(".search-result__content, .product-card")
            for product in products[:20]:
                deal = _parse_tcgplayer_product(product)
                if deal:
                    deals.append(deal)
        else:
            print(f"⚠️  TCGPlayer returned {resp.status_code}")

    except Exception as e:
        print(f"⚠️  TCGPlayer scan error: {e}")

    # Add some demo/sample data if real scraping fails (development mode)
    if not deals:
        deals = _get_sample_tcg_deals()

    print(f"✅ TCGPlayer scan complete: {len(deals)} deals found")
    return deals


def _parse_tcgplayer_product(product) -> Optional[Deal]:
    """Parse a TCGPlayer product card"""
    try:
        title_el = product.select_one(".search-result__title, .product-card__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)

        price_el = product.select_one(".search-result__market-price, .product-card__market-price")
        if not price_el:
            return None
        price_text = price_el.get_text(strip=True)
        price_match = re.search(r'\$([\d,]+\.?\d*)', price_text.replace(",", ""))
        if not price_match:
            return None
        price = float(price_match.group(1))

        link_el = product.select_one("a")
        url = link_el["href"] if link_el else ""
        if url and not url.startswith("http"):
            url = f"https://www.tcgplayer.com{url}"

        if not is_relevant_post(title, "", price):
            return None

        categories = detect_categories(title, "")
        card_count = _extract_card_count(title)
        valuation = estimate_lot_value(price, card_count, categories, title)
        score, tier = score_deal(price, valuation["estimated_market_value"], card_count, categories, valuation)

        if tier == DealTier.NO_DEAL:
            return None

        pb = PriceBreakdown(
            asking_price=price,
            estimated_market_value=valuation["estimated_market_value"],
            estimated_profit=valuation["estimated_profit"],
            profit_margin_pct=valuation["profit_margin_pct"],
            price_per_card=valuation.get("price_per_card"),
            card_count=card_count,
            resale_rate=valuation.get("resale_rate", "medium"),
            market_volatility=valuation.get("market_volatility", "medium"),
            resale_multiplier=valuation.get("resale_multiplier", 2.0),
            platform_fees_pct=10.0,  # TCGPlayer fees ~10%
            net_profit_after_fees=valuation.get("net_profit_after_fees", 0)
        )

        deal_id = hashlib.md5((url + title).encode()).hexdigest()[:12]
        tags = build_tags(title, "", categories, tier)
        tags.append("tcgplayer")

        return Deal(
            id=f"tcg_{deal_id}",
            title=title[:200],
            description="",
            url=url,
            platform=Platform.TCGPLAYER,
            asking_price=price,
            card_count=card_count,
            card_categories=categories,
            tier=tier,
            score=score,
            price_breakdown=pb,
            seller_info="TCGPlayer Marketplace",
            posted_at=datetime.now(tz=timezone.utc),
            tags=tags
        )
    except Exception:
        return None


def _get_sample_tcg_deals() -> List[Deal]:
    """
    Generate sample deals for development/demo mode.
    In production these would come from real scraping.
    """
    samples = [
        {
            "title": "Pokemon Bulk Lot 500 Mixed Cards Holos Commons",
            "price": 18.00,
            "card_count": 500,
            "categories": [CardCategory.HOLO_REVERSE, CardCategory.BULK_COMMONS],
            "url": "https://www.tcgplayer.com/search/pokemon/product?q=bulk+lot"
        },
        {
            "title": "WOTC Base Set Jungle Fossil Bulk Lot 200 Cards Vintage",
            "price": 45.00,
            "card_count": 200,
            "categories": [CardCategory.VINTAGE_WOTC],
            "url": "https://www.tcgplayer.com/search/pokemon/product?q=wotc+bulk"
        }
    ]

    deals = []
    for s in samples:
        valuation = estimate_lot_value(s["price"], s["card_count"], s["categories"], s["title"])
        score, tier = score_deal(s["price"], valuation["estimated_market_value"], s["card_count"], s["categories"], valuation)

        if tier == DealTier.NO_DEAL:
            continue

        pb = PriceBreakdown(
            asking_price=s["price"],
            estimated_market_value=valuation["estimated_market_value"],
            estimated_profit=valuation["estimated_profit"],
            profit_margin_pct=valuation["profit_margin_pct"],
            price_per_card=valuation.get("price_per_card"),
            card_count=s["card_count"],
            resale_rate=valuation.get("resale_rate", "medium"),
            market_volatility=valuation.get("market_volatility", "medium"),
            resale_multiplier=valuation.get("resale_multiplier", 2.0),
            platform_fees_pct=10.0,
            net_profit_after_fees=valuation.get("net_profit_after_fees", 0)
        )

        deal_id = hashlib.md5(s["url"].encode()).hexdigest()[:12]
        deals.append(Deal(
            id=f"tcg_{deal_id}",
            title=s["title"],
            description="",
            url=s["url"],
            platform=Platform.TCGPLAYER,
            asking_price=s["price"],
            card_count=s["card_count"],
            card_categories=s["categories"],
            tier=tier,
            score=score,
            price_breakdown=pb,
            seller_info="TCGPlayer Demo",
            posted_at=datetime.now(tz=timezone.utc),
            tags=build_tags(s["title"], "", s["categories"], tier),
            notes="Demo data — connect TCGPlayer API for live data"
        ))

    return deals
