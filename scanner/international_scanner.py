"""
International Scanner — Yahoo Auctions Japan, Mercari Japan, Kijiji Canada
Searches Japanese-language marketplaces for Pokemon TCG bulk lots and translates
relevant listings to English for display on the dashboard.

Purchase routes:
  - Yahoo Auctions Japan  -> buy via Buyee proxy (buyee.jp)
  - Mercari Japan         -> buy via Buyee proxy (buyee.jp)
  - Kijiji Canada         -> direct contact with seller
"""
import os
import re
import hashlib
import time
from datetime import datetime, timezone
from typing import List, Optional

import requests
from models.deal import Deal, Platform, DealTier, CardCategory, PriceBreakdown
from utils.deal_scorer import score_deal, build_tags, detect_categories
from pricing.price_engine import estimate_lot_value

# ---------------------------------------------------------------------------
# Japanese keyword -> English translation map
# ---------------------------------------------------------------------------
JP_TRANSLATION_MAP = {
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9": "Pokemon Card",
    "\u30dd\u30b1\u30ab": "Pokemon Card",
    "\u307e\u3068\u3081": "Bulk Lot",
    "\u5927\u91cf": "Large Quantity",
    "\u65e7\u88cf": "Vintage Old-Back",
    "\u521d\u671f": "Early/Vintage",
    "\u521d\u671f\u7248": "Early Edition Vintage",
    "\u30b3\u30ec\u30af\u30b7\u30e7\u30f3": "Collection",
    "\u5f15\u9000": "Quitting / Collection Sale",
    "\u51e6\u5206": "Clearance Sale",
    "\u683c\u5b89": "Bargain",
    "\u304a\u5f97": "Great Deal",
    "\u30bb\u30c3\u30c8": "Set/Bundle",
    "\u30ec\u30a2": "Rare",
    "\u30b9\u30fc\u30d1\u30fc\u30ec\u30a2": "Super Rare (SR)",
    "\u30a6\u30eb\u30c8\u30e9\u30ec\u30a2": "Ultra Rare (UR)",
    "\u30a2\u30fc\u30c8\u30ec\u30a2": "Art Rare (AR)",
    "\u30b9\u30da\u30b7\u30e3\u30eb\u30a2\u30fc\u30c8\u30ec\u30a2": "Special Art Rare (SAR)",
    "\u30cf\u30a4\u30d1\u30fc\u30ec\u30a2": "Hyper Rare (HR)",
    "\u30ad\u30e9": "Holo/Foil",
    "\u30d5\u30eb\u30a2\u30fc\u30c8": "Full Art",
    "PSA": "PSA Graded",
    "BGS": "BGS Graded",
    "\u9451\u5b9a\u6e08\u307f": "Professionally Graded",
    "\u672a\u958b\u5c01": "Sealed/Unopened",
    "BOX": "Booster Box",
    "\u30b9\u30bf\u30fc\u30bf\u30fc\u30bb\u30c3\u30c8": "Starter Set",
    "\u62e1\u5f35\u30d1\u30c3\u30af": "Expansion Pack",
    "\u30d7\u30ed\u30e2": "Promo Card",
    "\u30c6\u30ad\u30b9\u30c8\u50b7": "Text Scratch (minor damage)",
    "\u7f8e\u54c1": "Near Mint",
    "\u826f\u54c1": "Good Condition",
    "\u8a33\u3042\u308a": "Imperfect / Defect",
}

# Japanese search terms for Pokemon TCG bulk lots
JP_SEARCH_TERMS = [
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9 \u307e\u3068\u3081",
    "\u30dd\u30b1\u30ab \u5927\u91cf",
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9 \u65e7\u88cf",
    "\u30dd\u30b1\u30ab \u5f15\u9000",
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9 \u521d\u671f \u307e\u3068\u3081",
    "\u30dd\u30b1\u30ab \u30b3\u30ec\u30af\u30b7\u30e7\u30f3 \u683c\u5b89",
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9 \u51e6\u5206",
]

# Kijiji Canada search terms
KIJIJI_SEARCH_TERMS = [
    "pokemon cards bulk",
    "pokemon card collection lot",
    "pokemon tcg bulk",
    "pokemon cards wholesale",
    "pokemon binder collection",
]

JPY_TO_USD = float(os.getenv("JPY_TO_USD_RATE", "0.0067"))
CAD_TO_USD = float(os.getenv("CAD_TO_USD_RATE", "0.74"))
MIN_DEAL_SCORE = float(os.getenv("MIN_DEAL_SCORE", "0.3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "ja,en;q=0.9",
}


# ------------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _translate_jp_title(title: str) -> str:
    """Replace known Japanese TCG terms with English equivalents."""
    result = title
    for jp, en in JP_TRANSLATION_MAP.items():
        result = result.replace(jp, en)
    return result.strip()


def _make_deal_id(platform: str, item_id: str) -> str:
    return hashlib.md5(f"{platform}:{item_id}".encode()).hexdigest()[:16]


def _jpy_to_usd(jpy: float) -> float:
    return round(jpy * JPY_TO_USD, 2)


def _cad_to_usd(cad: float) -> float:
    return round(cad * CAD_TO_USD, 2)


def _is_pokemon_bulk(title_en: str, title_jp: str) -> bool:
    """Check if a listing is relevant Pokemon TCG bulk."""
    pokemon_en = any(kw in title_en.lower() for kw in [
        "pokemon", "pikachu", "charizard", "bulk", "lot", "collection",
        "vintage", "old-back", "tcg", "booster", "holo", "rare"
    ])
    pokemon_jp = any(kw in title_jp for kw in [
        "\u30dd\u30b1\u30e2\u30f3", "\u30dd\u30b1\u30ab",
        "\u30d4\u30ab\u30c1\u30e5\u30a6", "\u30ea\u30b6\u30fc\u30c9\u30f3"
    ])
    return pokemon_en or pokemon_jp


# ---------------------------------------------------------------------------
# Yahoo Auctions Japan  (via Buyee search proxy)
# ---------------------------------------------------------------------------

def scan_yahoo_japan(limit: int = 20) -> List[Deal]:
    """
    Search Yahoo Auctions Japan for Pokemon bulk lots via the Buyee proxy.
    Buyee (buyee.jp) is a legitimate purchasing proxy service for Japanese
    auctions -- NA buyers can place proxy bids and have items shipped overseas.
    """
    deals: List[Deal] = []

    for query in JP_SEARCH_TERMS[:4]:  # cap requests per scan
        try:
            encoded_query = requests.utils.quote(query)
            url = f"https://buyee.jp/item/search/query/{encoded_query}"
            params = {
                "translationType": "1",
                "lang": "en",
                "order": "cbids",
            }
            resp = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue

            items = _parse_buyee_html(resp.text, query)
            deals.extend(items)
            time.sleep(1.5)

        except Exception as e:
            print(f"[YahooJapan] Error on query '{query}': {e}")
            continue

    return deals[:limit]


def _parse_buyee_html(html: str, query: str) -> List[Deal]:
    """Parse Buyee search results HTML for auction listings."""
    deals = []

    # Buyee structures listings with data attributes and class-based titles
    # We parse title text, price, and item URLs
    price_pattern = re.compile(r'[\u00a5\xa5]([0-9,]+)')
    link_pattern = re.compile(
        r'href="(https://buyee\.jp/item/yahoo/auction/[a-z0-9]+)"',
        re.IGNORECASE
    )
    # Title text appears inside elements with class containing "itemTitle"
    title_pattern = re.compile(
        r'class="[^"]*itemTitle[^"]*"[^>]*>\s*([^<]{5,200})',
        re.IGNORECASE
    )

    titles = title_pattern.findall(html)
    links = link_pattern.findall(html)
    prices_raw = price_pattern.findall(html)

    for i, title_raw in enumerate(titles):
        try:
            if i >= len(links):
                break

            title_en = _translate_jp_title(title_raw.strip())
            if not _is_pokemon_bulk(title_en, title_raw):
                continue

            price_jpy = 0.0
            if i < len(prices_raw):
                price_jpy = float(prices_raw[i].replace(",", ""))

            price_usd = _jpy_to_usd(price_jpy)
            if price_usd < 5 or price_usd > 2000:
                continue

            link = links[i]
            item_id = link.split("/")[-1]
            estimated = estimate_lot_value(title_en, price_usd)
            estimated_value = estimated if estimated else price_usd * 1.5

            deal = Deal(
                id=_make_deal_id("yahoo_japan", item_id),
                title=f"\U0001f1ef\U0001f1f5 JP: {title_en}",
                platform=Platform.YAHOO_JAPAN,
                url=link,
                price=price_usd,
                estimated_value=estimated_value,
                profit_potential=round(estimated_value - price_usd, 2),
                deal_tier=DealTier.GOOD if estimated_value > price_usd * 1.3 else DealTier.FAIR,
                card_categories=detect_categories(title_en),
                tags=build_tags(title_en) + ["yahoo-japan", "auction"],
                description=f"Yahoo Auctions Japan listing. Buy via Buyee proxy. Original JP title: {title_raw.strip()[:120]}",
                created_at=datetime.now(timezone.utc),
                score=score_deal(price_usd, estimated_value),
                location="Japan (ships via Buyee proxy)",
            )
            deals.append(deal)

        except Exception:
            continue

    return deals


# ---------------------------------------------------------------------------
# Mercari Japan  (official JSON API)
# ---------------------------------------------------------------------------

def scan_mercari_japan(limit: int = 20) -> List[Deal]:
    """
    Search Mercari Japan via their public search API.
    Results are fixed-price listings (not auctions).
    """
    deals: List[Deal] = []
    api_url = "https://api.mercari.jp/v2/entities:search"

    for query in JP_SEARCH_TERMS[:3]:
        try:
            payload = {
                "pageSize": 20,
                "pageToken": "",
                "searchSessionId": hashlib.md5(query.encode()).hexdigest(),
                "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
                "searchCondition": {
                    "keyword": query,
                    "excludeKeyword": "",
                    "sort": "SORT_CREATED_TIME",
                    "order": "ORDER_DESC",
                    "status": ["STATUS_ON_SALE"],
                    "categoryId": ["5"],
                },
                "defaultDatasets": ["DATASET_TYPE_MERCARI"],
            }
            headers = {
                **HEADERS,
                "X-Platform": "web",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            }
            resp = requests.post(api_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)

            if resp.status_code != 200:
                continue

            data = resp.json()
            items = data.get("items", [])

            for item in items:
                try:
                    deal = _mercari_item_to_deal(item)
                    if deal:
                        deals.append(deal)
                except Exception:
                    continue

            time.sleep(1.5)

        except Exception as e:
            print(f"[MercariJapan] Error on query '{query}': {e}")
            continue

    return deals[:limit]


def _mercari_item_to_deal(item: dict) -> Optional[Deal]:
    """Convert a Mercari JP API item dict to a Deal object."""
    item_id = item.get("id", "")
    title_jp = item.get("name", "")
    price_jpy = float(item.get("price", 0))
    thumbnails = item.get("thumbnails", [])
    thumbnail = thumbnails[0] if thumbnails else ""

    if not title_jp or price_jpy < 500:
        return None

    title_en = _translate_jp_title(title_jp)
    if not _is_pokemon_bulk(title_en, title_jp):
        return None

    price_usd = _jpy_to_usd(price_jpy)
    if price_usd > 2000:
        return None

    buyee_url = f"https://buyee.jp/mercari/item/{item_id}"
    estimated = estimate_lot_value(title_en, price_usd)
    estimated_value = estimated if estimated else price_usd * 1.4

    return Deal(
        id=_make_deal_id("mercari_japan", item_id),
        title=f"\U0001f1ef\U0001f1f5 JP: {title_en}",
        platform=Platform.MERCARI_JAPAN,
        url=buyee_url,
        price=price_usd,
        estimated_value=estimated_value,
        profit_potential=round(estimated_value - price_usd, 2),
        deal_tier=DealTier.GOOD if estimated_value > price_usd * 1.3 else DealTier.FAIR,
        card_categories=detect_categories(title_en),
        tags=build_tags(title_en) + ["mercari-jp"],
        description=f"Fixed-price listing on Mercari Japan. Buy via Buyee proxy. Original: {title_jp[:120]}",
        created_at=datetime.now(timezone.utc),
        score=score_deal(price_usd, estimated_value),
        location="Japan (ships via Buyee proxy)",
        thumbnail_url=thumbnail,
    )


# ---------------------------------------------------------------------------
# Kijiji Canada
# ---------------------------------------------------------------------------

def scan_kijiji(limit: int = 20) -> List[Deal]:
    """
    Search Kijiji Canada for Pokemon card deals.
    Kijiji is Canada's largest classifieds site, popular for local card sales.
    """
    deals: List[Deal] = []

    for query in KIJIJI_SEARCH_TERMS[:3]:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://www.kijiji.ca/b-toys-games/{encoded}/k0c108"
            resp = requests.get(
                url,
                headers={**HEADERS, "Accept-Language": "en-CA,en;q=0.9"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                continue

            items = _parse_kijiji_html(resp.text)
            deals.extend(items)
            time.sleep(2.0)

        except Exception as e:
            print(f"[Kijiji] Error on query '{query}': {e}")
            continue

    return deals[:limit]


def _parse_kijiji_html(html: str) -> List[Deal]:
    """Parse Kijiji search results for relevant listings."""
    deals = []

    listing_ids = re.findall(r'data-listing-id="([0-9]+)"', html)
    titles = re.findall(r'class="title"[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*<', html)
    prices_raw = re.findall(r'\$\s*([0-9,]+(?:\.[0-9]{2})?)', html)
    locations = re.findall(r'class="[^"]*location[^"]*"[^>]*>\s*([^<]{3,60}?)\s*<', html)

    for i, (lid, title) in enumerate(zip(listing_ids, titles)):
        try:
            title = title.strip()
            if not any(kw in title.lower() for kw in ["pokemon", "pok\u00e9mon", "pok\u00e9", "tcg", "card"]):
                continue

            price_cad = 0.0
            if i < len(prices_raw):
                try:
                    price_cad = float(prices_raw[i].replace(",", ""))
                except ValueError:
                    pass

            price_usd = _cad_to_usd(price_cad) if price_cad else 0.0
            if price_usd > 3000:
                continue

            location = locations[i].strip() if i < len(locations) else "Canada"
            listing_url = f"https://www.kijiji.ca/v-toys-games/{lid}"

            estimated = estimate_lot_value(title, price_usd)
            estimated_value = estimated if estimated else price_usd * 1.25

            deal = Deal(
                id=_make_deal_id("kijiji", lid),
                title=f"\U0001f341 CA: {title}",
                platform=Platform.KIJIJI,
                url=listing_url,
                price=price_usd,
                estimated_value=estimated_value,
                profit_potential=round(estimated_value - price_usd, 2),
                deal_tier=DealTier.GOOD if estimated_value > price_usd * 1.2 else DealTier.FAIR,
                card_categories=detect_categories(title),
                tags=build_tags(title) + ["canada", "local", "kijiji"],
                description=f"Local classifieds listing on Kijiji Canada -- {location}. Listed price in CAD: ${price_cad:.2f}.",
                created_at=datetime.now(timezone.utc),
                score=score_deal(price_usd, estimated_value),
                location=location,
            )
            deals.append(deal)

        except Exception:
            continue

    return deals
