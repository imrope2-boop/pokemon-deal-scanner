"""
eBay Scanner — searches eBay for bulk Pokemon card listings
Uses eBay's public search pages (no API key required for basic scraping)
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

EBAY_SEARCH_TERMS = [
    "pokemon cards bulk lot",
    "pokemon card collection dump",
    "pokemon bulk holo lot",
    "pokemon vintage wotc bulk",
    "pokemon cards mixed lot 500",
    "pokemon cards mixed lot 1000",
    "pokemon bulk reverse holo",
    "pokemon base set lot bulk",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _build_ebay_url(search_term: str, max_price: int = 500, min_price: int = 5) -> str:
    """Build eBay search URL for Buy It Now listings, sorted by newest"""
    base = "https://www.ebay.com/sch/i.html"
    encoded = search_term.replace(" ", "+")
    return (
        f"{base}?_nkw={encoded}"
        f"&_sop=10"         # Sort: newest first
        f"&LH_BIN=1"        # Buy It Now only
        f"&LH_ItemCondition=1000|2500|3000"  # New, Like New, Very Good
        f"&_udlo={min_price}"
        f"&_udhi={max_price}"
        f"&_ipg=48"         # 48 items per page
    )


def _parse_ebay_listing(item) -> Optional[Dict]:
    """Parse a single eBay search result item (BeautifulSoup tag)"""
    try:
        # Title
        title_el = item.select_one(".s-item__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if title == "Shop on eBay":  # Sponsored placeholder
            return None

        # URL
        link_el = item.select_one("a.s-item__link")
        url = link_el["href"].split("?")[0] if link_el else None
        if not url:
            return None

        # Price
        price_el = item.select_one(".s-item__price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        # Handle price ranges — take the lower bound
        price_match = re.search(r'\$?([\d,]+\.?\d*)', price_text.replace(",", ""))
        if not price_match:
            return None
        price = float(price_match.group(1))

        # Shipping
        shipping_el = item.select_one(".s-item__shipping")
        shipping_text = shipping_el.get_text(strip=True) if shipping_el else ""
        free_shipping = "free" in shipping_text.lower()

        # Subtitle / description snippet
        subtitle_el = item.select_one(".s-item__subtitle")
        subtitle = subtitle_el.get_text(strip=True) if subtitle_el else ""

        # Seller info
        seller_el = item.select_one(".s-item__seller-info-text")
        seller = seller_el.get_text(strip=True) if seller_el else "unknown"

        # Image
        img_el = item.select_one("img.s-item__image-img")
        image = img_el.get("src", "") if img_el else ""

        return {
            "title": title,
            "url": url,
            "price": price,
            "free_shipping": free_shipping,
            "description": subtitle,
            "seller": seller,
            "image": image
        }
    except Exception:
        return None


def _listing_to_deal(listing: Dict) -> Optional[Deal]:
    """Convert a parsed eBay listing to a Deal object"""
    title = listing["title"]
    desc = listing.get("description", "")
    combined = f"{title} {desc}"
    price = listing["price"]

    if not is_relevant_post(title, desc, price):
        return None

    categories = detect_categories(title, desc)
    card_count = _extract_card_count(combined)

    valuation = estimate_lot_value(price, card_count, categories, combined)

    score, tier = score_deal(
        asking_price=price,
        estimated_market_value=valuation["estimated_market_value"],
        card_count=card_count,
        categories=categories,
        price_breakdown=valuation
    )

    if tier == DealTier.NO_DEAL and score < 3:
        return None

    tags = build_tags(title, desc, categories, tier)
    if listing.get("free_shipping"):
        tags.append("free-shipping")

    pb = PriceBreakdown(
        asking_price=price,
        estimated_market_value=valuation["estimated_market_value"],
        estimated_profit=valuation["estimated_profit"],
        profit_margin_pct=valuation["profit_margin_pct"],
        price_per_card=valuation.get("price_per_card"),
        card_count=card_count,
        resale_rate=valuation.get("resale_rate", "unknown"),
        market_volatility=valuation.get("market_volatility", "unknown"),
        resale_multiplier=valuation.get("resale_multiplier", 1.8),
        net_profit_after_fees=valuation.get("net_profit_after_fees", 0)
    )

    deal_id = hashlib.md5(listing["url"].encode()).hexdigest()[:12]

    return Deal(
        id=f"ebay_{deal_id}",
        title=title[:200],
        description=desc[:500],
        url=listing["url"],
        platform=Platform.EBAY,
        asking_price=price,
        card_count=card_count,
        card_categories=categories,
        tier=tier,
        score=score,
        price_breakdown=pb,
        seller_info=listing.get("seller", "unknown"),
        posted_at=datetime.now(tz=timezone.utc),
        images=[listing["image"]] if listing.get("image") else [],
        tags=tags
    )


def scan_ebay(max_price: int = 500) -> List[Deal]:
    """
    Main eBay scanner. Returns list of Deal objects.
    """
    deals = []
    seen_urls = set()

    for search_term in EBAY_SEARCH_TERMS:
        url = _build_ebay_url(search_term, max_price)
        print(f"🔍 eBay: searching '{search_term}'...")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️  eBay returned {resp.status_code} for '{search_term}'")
                time.sleep(2)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select(".s-item")

            for item in items:
                listing = _parse_ebay_listing(item)
                if not listing:
                    continue

                if listing["url"] in seen_urls:
                    continue
                seen_urls.add(listing["url"])

                deal = _listing_to_deal(listing)
                if deal:
                    deals.append(deal)

        except Exception as e:
            print(f"⚠️  eBay scan error for '{search_term}': {e}")

        time.sleep(2)  # Polite delay between searches

    print(f"✅ eBay scan complete: {len(deals)} deals found")
    return deals
