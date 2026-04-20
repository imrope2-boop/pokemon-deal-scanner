"""
eBay API Scanner — uses the eBay Finding API to search for Pokemon TCG bulk lots.

Requires EBAY_APP_ID environment variable (eBay Developer App ID / Client ID).
If the key is not set, this scanner silently returns [] and is a no-op.

Setup (one-time):
  1. Go to https://developer.ebay.com/
  2. Create an application -> get the App ID (Client ID)
  3. Set EBAY_APP_ID=<your-app-id> in Railway environment variables
  4. Scanning will automatically activate on the next scan

eBay Finding API endpoint (different from main eBay site — not blocked by Railway):
  https://svcs.ebay.com/services/search/FindingService/v1
"""
import os
import re
import hashlib
import time
from datetime import datetime, timezone
from typing import List, Optional
import xml.etree.ElementTree as ET

import requests
from models.deal import Deal, Platform, DealTier, CardCategory
from utils.deal_scorer import score_deal, build_tags, detect_categories
from pricing.price_engine import estimate_lot_value

EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")
EBAY_FINDING_API = "https://svcs.ebay.com/services/search/FindingService/v1"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# Search queries for eBay
EBAY_QUERIES = [
    "pokemon cards bulk lot",
    "pokemon tcg bulk collection",
    "pokemon card collection wholesale",
    "pokemon wotc bulk lot vintage",
    "pokemon cards 100 lot",
    "pokemon binder collection lot",
    "japanese pokemon cards bulk",
    "pokemon ex lot bulk",
]

HEADERS = {
    "User-Agent": "PokemonDealScanner/1.0",
}


def scan_ebay_api(limit: int = 30) -> List[Deal]:
    """
    Scan eBay via the Finding API for Pokemon TCG bulk lots.
    Returns [] silently if EBAY_APP_ID is not configured.
    """
    if not EBAY_APP_ID:
        # Key not yet configured — skip silently
        return []

    deals: List[Deal] = []

    for query in EBAY_QUERIES[:5]:  # cap API calls per scan
        try:
            items = _search_ebay(query)
            for item in items:
                deal = _item_to_deal(item)
                if deal:
                    deals.append(deal)
            time.sleep(0.5)
        except Exception as e:
            print(f"[eBayAPI] Error on query '{query}': {e}")
            continue

    # Deduplicate by deal ID
    seen = set()
    unique = []
    for d in deals:
        if d.id not in seen:
            seen.add(d.id)
            unique.append(d)

    return unique[:limit]


def _search_ebay(query: str, entries_per_page: int = 20) -> List[dict]:
    """Call eBay Finding API and return list of item dicts."""
    params = {
        "OPERATION-NAME": "findItemsByKeywords",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "XML",
        "REST-PAYLOAD": "",
        "keywords": query,
        "paginationInput.entriesPerPage": str(entries_per_page),
        "paginationInput.pageNumber": "1",
        "sortOrder": "StartTimeNewest",
        # Filter: BuyItNow + Auction, USD only
        "itemFilter(0).name": "ListingType",
        "itemFilter(0).value(0)": "FixedPrice",
        "itemFilter(0).value(1)": "AuctionWithBIN",
        "itemFilter(0).value(2)": "Auction",
        "itemFilter(1).name": "Currency",
        "itemFilter(1).value": "USD",
        "itemFilter(2).name": "MinPrice",
        "itemFilter(2).value": "5",
        "itemFilter(2).paramName": "Currency",
        "itemFilter(2).paramValue": "USD",
        "itemFilter(3).name": "MaxPrice",
        "itemFilter(3).value": "2000",
        "itemFilter(3).paramName": "Currency",
        "itemFilter(3).paramValue": "USD",
        # Only show active listings
        "itemFilter(4).name": "HideDescriptionSearch",
        "itemFilter(4).value": "false",
    }

    resp = requests.get(EBAY_FINDING_API, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return []

    return _parse_finding_xml(resp.text)


def _parse_finding_xml(xml_text: str) -> List[dict]:
    """Parse eBay Finding API XML response into item dicts."""
    items = []
    try:
        # Remove namespace prefix for easier parsing
        xml_clean = re.sub(r' xmlns[^"]*"[^"]*"', '', xml_text)
        xml_clean = re.sub(r'<[a-zA-Z]+:', '<', xml_clean)
        xml_clean = re.sub(r'</[a-zA-Z]+:', '</', xml_clean)

        root = ET.fromstring(xml_clean)
        search_result = root.find('.//searchResult')
        if search_result is None:
            return []

        for item_el in search_result.findall('item'):
            try:
                item = {
                    'id': _get_text(item_el, 'itemId'),
                    'title': _get_text(item_el, 'title'),
                    'url': _get_text(item_el, 'viewItemURL'),
                    'price': _get_text(item_el, './/currentPrice'),
                    'currency': _get_text(item_el, './/currentPrice[@currencyId]') or 'USD',
                    'listing_type': _get_text(item_el, './/listingType'),
                    'end_time': _get_text(item_el, './/endTime'),
                    'thumbnail': _get_text(item_el, 'galleryURL'),
                    'location': _get_text(item_el, 'country'),
                }
                if item['id'] and item['title']:
                    items.append(item)
            except Exception:
                continue

    except ET.ParseError as e:
        print(f"[eBayAPI] XML parse error: {e}")

    return items


def _get_text(el, path: str) -> str:
    """Safely get text from an XML element."""
    found = el.find(path)
    return (found.text or '').strip() if found is not None else ''


def _item_to_deal(item: dict) -> Optional[Deal]:
    """Convert an eBay item dict to a Deal object."""
    title = item.get('title', '')
    if not title:
        return None

    # Basic relevance filter
    title_lower = title.lower()
    if not any(kw in title_lower for kw in ['pokemon', 'pokémon', 'tcg']):
        return None

    # Must look like a bulk/lot listing
    if not any(kw in title_lower for kw in [
        'bulk', 'lot', 'collection', 'bundle', 'binder', 'wholesale',
        '100', '200', '500', '1000', 'pounds', 'lbs', 'vintage', 'wotc',
        'mixed', 'japanese', 'japan', 'common', 'uncommon'
    ]):
        return None

    try:
        price_usd = float(item.get('price', 0))
    except (ValueError, TypeError):
        price_usd = 0.0

    if price_usd < 5 or price_usd > 2000:
        return None

    item_id = item.get('id', '')
    url = item.get('url', f"https://www.ebay.com/itm/{item_id}")
    thumbnail = item.get('thumbnail', '')

    estimated = estimate_lot_value(title, price_usd)
    estimated_value = estimated if estimated else price_usd * 1.3

    listing_type = item.get('listing_type', 'FixedPrice')
    listing_label = "Auction" if 'Auction' in listing_type else "Buy It Now"

    return Deal(
        id=hashlib.md5(f"ebay:{item_id}".encode()).hexdigest()[:16],
        title=title,
        platform=Platform.EBAY,
        url=url,
        price=price_usd,
        estimated_value=estimated_value,
        profit_potential=round(estimated_value - price_usd, 2),
        deal_tier=DealTier.GOOD if estimated_value > price_usd * 1.4 else DealTier.FAIR,
        card_categories=detect_categories(title),
        tags=build_tags(title) + ["ebay", listing_label.lower().replace(' ', '-')],
        description=f"eBay listing ({listing_label}). Location: {item.get('location', 'US')}.",
        created_at=datetime.now(timezone.utc),
        score=score_deal(price_usd, estimated_value),
        location=item.get('location', 'US'),
        thumbnail_url=thumbnail,
    )
