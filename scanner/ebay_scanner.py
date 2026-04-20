"""
eBay Scanner — searches eBay for bulk Pokemon card listings
Sorted by newest first, all conditions included (bulk is often listed as Used)
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

# Expanded search terms covering: chase sets, desperation sellers,
# vintage, unsearched, and generic bulk
EBAY_SEARCH_TERMS = [
    # Generic bulk
    "pokemon cards bulk lot",
    "pokemon bulk mixed lot",
    "pokemon card collection lot",
    "pokemon cards job lot",
    "pokemon collection dump",
    # Chase sets
    "hidden fates bulk lot",
    "shining fates bulk lot",
    "evolving skies bulk lot",
    "pokemon 151 bulk lot",
    "crown zenith bulk lot",
    "prismatic evolutions bulk",
    "pokemon celebrations bulk",
    "champions path bulk lot",
    # Vintage / WOTC
    "pokemon base set lot",
    "pokemon wotc collection lot",
    "pokemon 1st edition lot",
    "pokemon vintage cards lot",
    "pokemon neo genesis lot",
    # Desperation / opportunity sellers
    "pokemon cards quick sale",
    "pokemon collection clearing",
    "pokemon cards unsearched lot",
    "pokemon bulk unweighed",
    "pokemon inherited collection",
    "pokemon cards moving sale",
    # Holo focused
    "pokemon holo lot bulk",
    "pokemon reverse holo lot",
    # High card count
    "pokemon 500 cards lot",
    "pokemon 1000 cards lot",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _build_ebay_url(search_term: str, max_price: int = 800, min_price: int = 5) -> str:
    """
    Build eBay search URL for Buy It Now listings, sorted by newest.
    No condition filter — bulk lots are often listed as Used/Acceptable.
    """
    base = "https://www.ebay.com/sch/i.html"
    encoded = search_term.replace(" ", "+")
    return (
        f"{base}?_nkw={encoded}"
        f"&_sop=10"         # Sort: newest first — recency is key
        f"&LH_BIN=1"        # Buy It Now only
        f"&_udlo={min_price}"
        f"&_udhi={max_price}"
        f"&_ipg=60"         # 60 items per page
        # No condition filter — include New, Used, Acceptable, etc.
    )


def _parse_ebay_listing(item) -> Optional[Dict]:
    """Parse a single eBay search result item"""
    try:
        title_el = item.select_one(".s-item__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if title == "Shop on eBay":
            return None

        link_el = item.select_one("a.s-item__link")
        url = link_el["href"].split("?")[0] if link_el else None
        if not url:
            return None

        price_el = item.select_one(".s-item__price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price_match = re.search(r'\$?([\d,]+\.?\d*)', price_text.replace(",", ""))
        if not price_match:
            return None
        price = float(price_match.group(1))
        if price < 1.0:
            return None

        shipping_el = item.select_one(".s-item__shipping")
        shipping_text = shipping_el.get_text(strip=True) if shipping_el else ""
        free_shipping = "free" in shipping_text.lower()

        subtitle_el = item.select_one(".s-item__subtitle")
        subtitle = subtitle_el.get_text(strip=True) if subtitle_el else ""

        seller_el = item.select_one(".s-item__seller-info-text")
        seller = seller_el.get_text(strip=True) if seller_el else "unknown"

        img_el = item.select_one("img.s-item__image-img")
        image = img_el.get("src", "") if img_el else ""

        # Condition
        condition_el = item.select_one(".SECONDARY_INFO")
        condition = condition_el.get_text(strip=True) if condition_el else ""

        return {
            "title": title,
            "url": url,
            "price": price,
            "free_shipping": free_shipping,
            "description": f"{subtitle} {condition}".strip(),
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
        price_breakdown=valuation,
        title=title,
        description=desc
    )

    # Show DECENT and above — cast a wide net
    if tier == DealTier.NO_DEAL and score < 2.0:
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
        resale_multiplier=valuation.get("resale_multiplier", 2.0),
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


def scan_ebay(max_price: int = 800) -> List[Deal]:
    """
    Main eBay scanner. Searches all terms, deduplicates by URL.
    """
    deals = []
    seen_urls = set()

    for search_term in EBAY_SEARCH_TERMS:
        url = _build_ebay_url(search_term, max_price)
        print(f"🔍 eBay: '{search_term}'...")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️  eBay {resp.status_code} for '{search_term}'")
                time.sleep(2)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select(".s-item")
            found_this_term = 0

            for item in items:
                listing = _parse_ebay_listing(item)
                if not listing or listing["url"] in seen_urls:
                    continue
                seen_urls.add(listing["url"])

                deal = _listing_to_deal(listing)
                if deal:
                    deals.append(deal)
                    found_this_term += 1

            print(f"   → {found_this_term} deals from '{search_term}'")

        except Exception as e:
            print(f"⚠️  eBay error for '{search_term}': {e}")

        time.sleep(1.5)  # Polite delay

    print(f"✅ eBay scan complete: {len(deals)} deals from {len(seen_urls)} unique listings")
    return deals
