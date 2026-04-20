"""
International Scanner — Yahoo Auctions Japan (Buyee), Mercari Japan (Buyee),
Surugaya Japan, Carousell Singapore/HK, Kijiji Canada

Purchase routes:
  - Yahoo Auctions Japan  -> Buyee proxy (buyee.jp) — bid/buy for overseas
  - Mercari Japan         -> Buyee proxy (buyee.jp) — buy for overseas
  - Surugaya (Suruga-ya)  -> direct purchase, ships internationally
  - Carousell SG/HK       -> direct contact with seller
  - Kijiji Canada         -> direct contact with seller

All prices converted to USD at scan time. Japanese titles translated via
JP_TRANSLATION_MAP — no API calls needed.
"""
import re
import hashlib
import time
import json
from datetime import datetime, timezone
from typing import List, Optional, Dict

import requests

from models.deal import Deal, Platform, DealTier, CardCategory, PriceBreakdown
from utils.deal_scorer import score_deal, build_tags, detect_categories
from pricing.price_engine import estimate_lot_value

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Currency conversion rates
JPY_TO_USD = 0.0067
CAD_TO_USD = 0.74
SGD_TO_USD = 0.74

# ---------------------------------------------------------------------------
# Japanese search terms (ポケモンカード = Pokemon card)
# ---------------------------------------------------------------------------
JP_SEARCH_TERMS = [
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9 \u307e\u3068\u3081",      # ポケモンカード まとめ
    "\u30dd\u30b1\u30e2\u30f3 \u30ab\u30fc\u30c9 \u5927\u91cf",            # ポケモン カード 大量
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9 \u30bb\u30c3\u30c8",       # ポケモンカード セット
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9 \u30b3\u30ec\u30af\u30b7\u30e7\u30f3",  # コレクション
]

JP_TRANSLATION_MAP = {
    "\u30dd\u30b1\u30e2\u30f3\u30ab\u30fc\u30c9": "Pokemon card",
    "\u30dd\u30b1\u30e2\u30f3": "Pokemon",
    "\u307e\u3068\u3081": "bulk lot",
    "\u5927\u91cf": "large quantity",
    "\u30ab\u30fc\u30c9": "card",
    "\u30bb\u30c3\u30c8": "set",
    "\u30b3\u30ec\u30af\u30b7\u30e7\u30f3": "collection",
    "\u30d3\u30f3\u30c0\u30fc": "binder",
    "\u30d9\u30fc\u30b9\u30bb\u30c3\u30c8": "Base Set",
    "\u521d\u671f": "1st edition",
    "\u30ec\u30a2": "rare",
    "\u30db\u30ed": "holo",
    "\u30ad\u30e9": "foil/holo",
    "\u30b5\u30a4\u30f3": "signed",
    "\u30c8\u30ec\u30fc\u30ca\u30fc": "trainer",
    "\u30a8\u30cd\u30eb\u30ae\u30fc": "energy",
    "\u5c71\u5206": "large lot",
    "\u30d0\u30e9": "individual",
    "\u6d77\u5916": "international",
    "\u672a\u4f7f\u7528": "unused/sealed",
    "\u672a\u958b\u5c01": "sealed",
    "\u30d1\u30c3\u30af": "pack",
    "\u30dc\u30c3\u30af\u30b9": "box",
    "\u30b9\u30fc\u30d1\u30fc\u30ec\u30a2": "Super Rare",
    "\u30a6\u30eb\u30c8\u30e9\u30ec\u30a2": "Ultra Rare",
    "\u30cf\u30a4\u30d1\u30fc\u30ec\u30a2": "Hyper Rare",
    "\u30d5\u30eb\u30a2\u30fc\u30c8": "Full Art",
    "\u30d7\u30ed\u30e2": "promo",
    "\u683c\u5b89": "bargain",
    "\u51e6\u5206": "clearance",
    "\u304a\u5f97": "great deal",
    "PSA": "PSA graded",
    "BGS": "BGS graded",
    "\u9451\u5b9a": "graded",
}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _translate(title: str) -> str:
    """Translate known Japanese Pokemon TCG terms to English."""
    for jp, en in JP_TRANSLATION_MAP.items():
        title = title.replace(jp, en)
    return title


def _deal_id(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.md5(text.encode()).hexdigest()[:10]}"


def _jpy_to_usd(jpy: float) -> float:
    return round(jpy * JPY_TO_USD, 2)


def _cad_to_usd(cad: float) -> float:
    return round(cad * CAD_TO_USD, 2)


def _sgd_to_usd(sgd: float) -> float:
    return round(sgd * SGD_TO_USD, 2)


def _is_pokemon_bulk(text: str) -> bool:
    """Return True if text looks like a Pokemon card bulk/lot listing."""
    t = text.lower()
    has_poke = any(k in t for k in [
        "pokemon", "poke", "\u30dd\u30b1", "pikachu", "charizard", "tcg"
    ])
    has_bulk = any(k in t for k in [
        "lot", "bulk", "bundle", "set", "collection", "binder", "card",
        "\u307e\u3068\u3081", "\u5927\u91cf", "\u30bb\u30c3\u30c8",
        "\u30b3\u30ec\u30af\u30b7\u30e7\u30f3", "\u30ab\u30fc\u30c9",
    ])
    return has_poke and has_bulk


def _build_deal(
    deal_id: str,
    title: str,
    url: str,
    price_usd: float,
    platform: Platform,
    desc: str = "",
) -> Optional[Deal]:
    """Build a scored Deal from basic listing info."""
    cats = detect_categories(title, desc)
    val = estimate_lot_value(title, desc, price_usd if price_usd > 1.0 else 0)
    asking = max(price_usd, 0.0)
    score, tier = score_deal(
        asking_price=asking,
        estimated_market_value=val.estimated_market_value,
        card_count=val.card_count,
        categories=cats,
        price_breakdown=vars(val),
        title=title,
        description=desc,
    )
    if tier == DealTier.NO_DEAL and score < 0.5:
        return None
    tags = build_tags(title, desc, cats, tier)
    return Deal(
        id=deal_id,
        title=title,
        description=desc,
        url=url,
        platform=platform,
        asking_price=asking,
        card_count=val.card_count,
        card_categories=cats,
        tier=tier,
        score=score,
        price_breakdown=val,
        seller_info="",
        posted_at=datetime.now(timezone.utc),
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Buyee HTML parser (shared by Yahoo Japan + Mercari Japan)
# ---------------------------------------------------------------------------

def _parse_buyee_html(html: str, source: str) -> list:
    """
    Parse Buyee search result pages.
    Tries embedded JSON first (more robust), falls back to regex on HTML.
    source: 'yahoo' or 'mercari'
    """
    items = []

    # --- Strategy 1: Next.js __NEXT_DATA__ JSON ---
    nd_match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.+?\})\s*</script>',
        html, re.DOTALL
    )
    if nd_match:
        try:
            data = json.loads(nd_match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})
            # Buyee stores items under various keys depending on page version
            item_list = (
                page_props.get("items") or
                page_props.get("itemList") or
                page_props.get("searchResults", {}).get("items") or
                []
            )
            for item in item_list[:25]:
                if not isinstance(item, dict):
                    continue
                title_raw = (
                    item.get("title") or item.get("name") or
                    item.get("itemTitle") or ""
                )
                title_en = _translate(title_raw.strip())
                if not _is_pokemon_bulk(title_en + " " + title_raw):
                    continue
                price_raw = item.get("price") or item.get("currentPrice") or item.get("buyItNowPrice") or 0
                try:
                    price_jpy = float(str(price_raw).replace(",", "").replace("\u00a5", "").strip() or 0)
                except Exception:
                    price_jpy = 0.0
                item_url = item.get("url") or item.get("link") or item.get("itemUrl") or ""
                if item_url and not item_url.startswith("http"):
                    item_url = "https://buyee.jp" + item_url
                if item_url:
                    items.append({
                        "title": title_en or title_raw,
                        "url": item_url,
                        "price_jpy": price_jpy,
                        "price_usd": _jpy_to_usd(price_jpy),
                    })
            if items:
                return items
        except Exception as e:
            print(f"[Buyee JSON parse] {e}")

    # --- Strategy 2: Regex on rendered HTML ---
    if source == "yahoo":
        url_pat = re.compile(r'href="(https://buyee\.jp/item/yahoo/auction/[a-z0-9]+)"', re.I)
    else:
        url_pat = re.compile(r'href="(https://buyee\.jp/mercari/item/[^"?\s]{5,80})"', re.I)

    price_pat = re.compile(r'[\u00a5\xa5]\s*([0-9][0-9,]{2,})')
    seen_urls: set = set()

    for m in url_pat.finditer(html):
        item_url = m.group(1)
        if item_url in seen_urls:
            continue
        seen_urls.add(item_url)

        pos = m.start()
        # Search a window around the URL for title and price
        window = html[max(0, pos - 600):pos + 600]
        clean = re.sub(r'<[^>]+>', ' ', window)
        clean = re.sub(r'&[a-z]+;', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        # Try aria-label first (most reliable title source on Buyee)
        aria = re.search(r'aria-label="([^"]{10,200})"', window)
        alt = re.search(r'alt="([^"]{10,200})"', window)

        title_raw = ""
        if aria:
            title_raw = aria.group(1)
        elif alt:
            title_raw = alt.group(1)
        else:
            # Take first substantial text chunk from the window
            for chunk in clean.split("  "):
                chunk = chunk.strip()
                if len(chunk) >= 10 and not chunk.startswith("http"):
                    title_raw = chunk[:200]
                    break

        if not title_raw:
            continue

        title_en = _translate(title_raw)
        if not _is_pokemon_bulk(title_en + " " + title_raw):
            continue

        price_match = price_pat.search(window)
        price_jpy = float(price_match.group(1).replace(",", "")) if price_match else 0.0

        items.append({
            "title": title_en,
            "url": item_url,
            "price_jpy": price_jpy,
            "price_usd": _jpy_to_usd(price_jpy),
        })

        if len(items) >= 20:
            break

    return items


# ---------------------------------------------------------------------------
# Yahoo Auctions Japan (via Buyee)
# ---------------------------------------------------------------------------

def scan_yahoo_japan(limit: int = 30) -> List[Deal]:
    """
    Search Yahoo Auctions Japan via Buyee proxy.
    Buyee (buyee.jp) is the leading English-language purchasing proxy for
    Yahoo Japan Auctions — overseas buyers can bid and have items shipped.
    """
    deals: List[Deal] = []
    seen: set = set()

    for query in JP_SEARCH_TERMS[:3]:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://buyee.jp/item/search/query/{encoded}"
            params = {
                "translationType": "1",
                "lang": "en",
            }
            resp = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                print(f"[Yahoo Japan] HTTP {resp.status_code}")
                time.sleep(2)
                continue

            items = _parse_buyee_html(resp.text, "yahoo")
            print(f"[Yahoo Japan] query returned {len(items)} raw items")

            for item in items:
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                deal = _build_deal(
                    _deal_id("yahoo_jp", item["url"]),
                    f"\U0001f1ef\U0001f1f5 JP: {item['title'][:180]}",
                    item["url"],
                    item["price_usd"],
                    Platform.YAHOO_JAPAN,
                    f"Yahoo Auctions Japan — bid via Buyee proxy. "
                    f"Original price: \u00a5{item['price_jpy']:,.0f}",
                )
                if deal:
                    deals.append(deal)

            time.sleep(2)

        except Exception as e:
            print(f"[Yahoo Japan] Error: {e}")

    print(f"[Yahoo Japan] {len(deals)} deals saved")
    return deals


# ---------------------------------------------------------------------------
# Mercari Japan (via Buyee)
# ---------------------------------------------------------------------------

def scan_mercari_japan(limit: int = 30) -> List[Deal]:
    """
    Search Mercari Japan via Buyee proxy search page.
    Buyee proxies Mercari Japan at buyee.jp/mercari — no Japanese account needed.
    """
    deals: List[Deal] = []
    seen: set = set()

    for query in JP_SEARCH_TERMS[:3]:
        try:
            url = "https://buyee.jp/mercari/search"
            params = {
                "keyword": query,
                "translationType": "1",
                "status": "on_sale",
            }
            resp = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                print(f"[Mercari Japan] HTTP {resp.status_code}")
                time.sleep(2)
                continue

            items = _parse_buyee_html(resp.text, "mercari")
            print(f"[Mercari Japan] query returned {len(items)} raw items")

            for item in items:
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                deal = _build_deal(
                    _deal_id("mercari_jp", item["url"]),
                    f"\U0001f1ef\U0001f1f5 JP: {item['title'][:180]}",
                    item["url"],
                    item["price_usd"],
                    Platform.MERCARI_JAPAN,
                    f"Mercari Japan — buy via Buyee proxy. "
                    f"Original price: \u00a5{item['price_jpy']:,.0f}",
                )
                if deal:
                    deals.append(deal)

            time.sleep(2)

        except Exception as e:
            print(f"[Mercari Japan] Error: {e}")

    print(f"[Mercari Japan] {len(deals)} deals saved")
    return deals


# ---------------------------------------------------------------------------
# Surugaya (駿河屋) — major Japanese TCG retailer, ships internationally
# ---------------------------------------------------------------------------

SURUGAYA_QUERIES = [
    "pokemon card lot",
    "pokemon card bulk",
    "pokemon card set",
]


def scan_surugaya(limit: int = 30) -> List[Deal]:
    """
    Surugaya (suruga-ya.jp) is one of Japan's largest used TCG retailers.
    They regularly list bulk Pokemon lots at 20-50% below market. They have an
    English-language storefront and ship internationally via Buyee/tenso.
    """
    deals: List[Deal] = []
    seen: set = set()

    for query in SURUGAYA_QUERIES[:2]:
        try:
            url = "https://www.suruga-ya.jp/en/search"
            params = {
                "search_word": query,
                "category": "5",    # Card Games category
                "in_stock": "1",
            }
            resp = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                print(f"[Surugaya] HTTP {resp.status_code}")
                time.sleep(2)
                continue

            items = _parse_surugaya_html(resp.text)
            print(f"[Surugaya] query '{query}' returned {len(items)} raw items")

            for item in items:
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                deal = _build_deal(
                    _deal_id("surugaya", item["url"]),
                    f"\U0001f1ef\U0001f1f5 Surugaya: {item['title'][:175]}",
                    item["url"],
                    item["price_usd"],
                    Platform.YAHOO_JAPAN,
                    f"Surugaya Japan — fixed-price retail. "
                    f"Price: \u00a5{item['price_jpy']:,.0f} (~${item['price_usd']:.2f} USD). "
                    f"Ships internationally via Buyee/Tenso.",
                )
                if deal:
                    deals.append(deal)

            time.sleep(1.5)

        except Exception as e:
            print(f"[Surugaya] Error: {e}")

    print(f"[Surugaya] {len(deals)} deals saved")
    return deals


def _parse_surugaya_html(html: str) -> list:
    """Parse Surugaya English search result pages."""
    items = []

    # Surugaya product URL pattern: /en/product/XXXXXXXX
    url_pat = re.compile(r'href="(/en/product/\d+[^"?#]*)"', re.I)
    price_pat = re.compile(r'[\u00a5\xa5]\s*([0-9][0-9,]+)')
    yen_pat = re.compile(r'([0-9][0-9,]+)\s*(?:JPY|yen|\u5186)')

    seen_paths: set = set()
    for m in url_pat.finditer(html):
        path = m.group(1)
        if path in seen_paths:
            continue
        seen_paths.add(path)

        item_url = "https://www.suruga-ya.jp" + path
        pos = m.start()
        window = html[max(0, pos - 400):pos + 800]
        clean = re.sub(r'<[^>]+>', ' ', window)
        clean = re.sub(r'&[a-z#0-9]+;', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        # Extract title
        title_match = (
            re.search(r'alt="([^"]{8,200})"', window) or
            re.search(r'title="([^"]{8,200})"', window) or
            re.search(r'aria-label="([^"]{8,200})"', window)
        )
        if not title_match:
            # Take longest meaningful text chunk
            chunks = [c.strip() for c in clean.split("  ") if len(c.strip()) > 12]
            title_raw = chunks[0][:200] if chunks else ""
        else:
            title_raw = title_match.group(1).strip()

        if not title_raw or not _is_pokemon_bulk(title_raw):
            continue

        # Extract price
        price_match = price_pat.search(window) or yen_pat.search(window)
        price_jpy = float(price_match.group(1).replace(",", "")) if price_match else 0.0

        items.append({
            "title": title_raw,
            "url": item_url,
            "price_jpy": price_jpy,
            "price_usd": _jpy_to_usd(price_jpy),
        })

        if len(items) >= 20:
            break

    return items


# ---------------------------------------------------------------------------
# Carousell Singapore / Hong Kong
# ---------------------------------------------------------------------------

CAROUSELL_QUERIES = [
    "pokemon card lot",
    "pokemon bulk cards",
    "pokemon card collection",
]


def scan_carousell(limit: int = 20) -> List[Deal]:
    """
    Carousell is the dominant C2C marketplace across Singapore, HK, Malaysia,
    and Taiwan. Pokemon bulk lots from Asian collectors often appear 30-50%
    below TCGPlayer due to lower local demand for English/vintage sets.
    Try the JSON search API first; fall back to scraping the search page.
    """
    deals: List[Deal] = []
    seen: set = set()

    for query in CAROUSELL_QUERIES[:2]:
        try:
            # Carousell search API (Singapore)
            api_url = "https://search.carousell.com/api/v4/listings"
            params = {
                "query": query,
                "country_code": "SG",
                "count": 30,
                "sort_by": "recent",
            }
            resp = requests.get(api_url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    listings = (
                        data.get("data", {}).get("results") or
                        data.get("results") or
                        data.get("listings") or
                        []
                    )
                    for item in listings:
                        _carousell_item_to_deal(item, seen, deals)
                except Exception:
                    pass

            # Also try the web search page (Next.js JSON embedded)
            web_url = f"https://www.carousell.sg/search/{requests.utils.quote(query)}/"
            resp2 = requests.get(web_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp2.status_code == 200:
                _parse_carousell_html(resp2.text, seen, deals)

            time.sleep(2)

        except Exception as e:
            print(f"[Carousell] Error: {e}")

    print(f"[Carousell] {len(deals)} deals saved")
    return deals


def _carousell_item_to_deal(item: dict, seen: set, deals: list):
    """Convert one Carousell API item dict to a Deal."""
    try:
        listing = item.get("listing") or item
        title = listing.get("title") or listing.get("name") or ""
        if not title or not _is_pokemon_bulk(title):
            return

        # Price can live in several shapes
        price_raw = listing.get("price") or {}
        if isinstance(price_raw, dict):
            price_sgd = float(price_raw.get("amount") or price_raw.get("value") or 0)
        else:
            price_sgd = float(price_raw or 0)

        price_usd = _sgd_to_usd(price_sgd)

        listing_id = listing.get("id") or listing.get("listingId") or ""
        slug = listing.get("slug") or re.sub(r"[^a-z0-9]+", "-", title.lower())[:50]
        item_url = listing.get("url") or f"https://www.carousell.sg/p/{slug}-{listing_id}/"
        if not item_url.startswith("http"):
            item_url = "https://www.carousell.sg" + item_url

        if item_url in seen:
            return
        seen.add(item_url)

        deal = _build_deal(
            _deal_id("carousell", item_url),
            f"\U0001f1f8\U0001f1ec SG: {title[:180]}",
            item_url,
            price_usd,
            Platform.CAROUSELL,
            f"Carousell Singapore — S${price_sgd:.2f} (~${price_usd:.2f} USD). "
            f"Direct pickup/shipping negotiated with seller.",
        )
        if deal:
            deals.append(deal)
    except Exception as e:
        print(f"[Carousell] item parse error: {e}")


def _parse_carousell_html(html: str, seen: set, deals: list):
    """Extract Carousell listings from embedded Next.js page JSON."""
    nd_match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.+?\})\s*</script>',
        html, re.DOTALL
    )
    if not nd_match:
        return
    try:
        data = json.loads(nd_match.group(1))
        page_props = data.get("props", {}).get("pageProps", {})
        listings = (
            page_props.get("listings") or
            page_props.get("searchResults", {}).get("listings") or
            page_props.get("data", {}).get("results") or
            []
        )
        for item in listings[:20]:
            _carousell_item_to_deal(item, seen, deals)
    except Exception as e:
        print(f"[Carousell HTML parse] {e}")


# ---------------------------------------------------------------------------
# Kijiji Canada (improved)
# ---------------------------------------------------------------------------

KIJIJI_QUERIES = [
    "pokemon cards lot",
    "pokemon card bulk",
    "pokemon card collection",
]


def scan_kijiji(limit: int = 20) -> List[Deal]:
    """
    Kijiji is Canada's largest classifieds marketplace (eBay-owned).
    Canadian Pokemon bulk lots in CAD convert favorably for US buyers
    (~25% discount vs equivalent USD listings). Direct seller contact.
    """
    deals: List[Deal] = []
    seen: set = set()

    for query in KIJIJI_QUERIES[:2]:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://www.kijiji.ca/b-toys-games/{encoded}/k0c108l0"
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                print(f"[Kijiji] HTTP {resp.status_code}")
                time.sleep(2)
                continue

            items = _parse_kijiji_html(resp.text)
            print(f"[Kijiji] query '{query}' returned {len(items)} raw items")

            for item in items:
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                deal = _build_deal(
                    _deal_id("kijiji", item["url"]),
                    f"\U0001f341 CA: {item['title'][:180]}",
                    item["url"],
                    item["price_usd"],
                    Platform.KIJIJI,
                    f"Kijiji Canada — CA${item['price_cad']:.2f} (~${item['price_usd']:.2f} USD). "
                    f"Direct seller contact.",
                )
   2           if deal:
                    deals.append(deal)

            time.sleep(1)

        except Exception as e:
            print(f"[Kijiji] Error: {e}")

    print(f"[Kijiji] {len(deals)} deals saved")
    return deals


def _parse_kijiji_html(html: str) -> list:
    """
    Parse Kijiji search results.
    Tries __NEXT_DATA__ JSON first, falls back to HTML regex.
    """
    items = []

    # --- Strategy 1: __NEXT_DATA__ embedded JSON ---
    nd_match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.+?\})\s*</script>',
        html, re.DOTALL
    )
    if nd_match:
        try:
            data = json.loads(nd_match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})
            ads = (
                page_props.get("listings") or
                page_props.get("searchResults", {}).get("ads") or
                page_props.get("ads") or
                []
            )
            for ad in ads[:30]:
                title = ad.get("title") or ad.get("heading") or ""
                if not title or not _is_pokemon_bulk(title):
                    continue
                price_raw = ad.get("price") or {}
                if isinstance(price_raw, dict):
                    price_cad = float(price_raw.get("amount") or price_raw.get("value") or 0)
                else:
                    price_cad = float(str(price_raw).replace("$", "").replace(",", "").strip() or 0)
                ad_id = ad.get("id") or ad.get("adId") or ""
                item_url = ad.get("url") or f"https://www.kijiji.ca/v-toys-games/{ad_id}"
                if not item_url.startswith("http"):
                    item_url = "https://www.kijiji.ca" + item_url
                items.append({
                    "title": title,
                    "url": item_url,
                    "price_cad": price_cad,
                    "price_usd": _cad_to_usd(price_cad),
                })
            if items:
                return items
        except Exception as e:
            print(f"[Kijiji JSON parse] {e}")

    # --- Strategy 2: Regex fallback ---
    url_pat = re.compile(r'href="(https://www\.kijiji\.ca/v-[^"]{10,150})"', re.I)
    price_pat = re.compile(r'\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)')

    seen_urls: set = set()
    for m in url_pat.finditer(html):
        item_url = m.group(1)
        if item_url in seen_urls or "kijiji.ca/b-" in item_url:
            continue
        seen_urls.add(item_url)

        pos = m.start()
        window = html[max(0, pos - 300):pos + 700]

        title_match = (
            re.search(r'data-testid="[^"]*title[^"]*"[^>]*>\s*([^<]{8,200})', window, re.I) or
            re.search(r'"heading"\s*:\s*"([^"]{8,200})"', window) or
            re.search(r'<a[^>]+href="[^"]*kijiji[^"]*"[^>]*>([^<]{8,200})</a>', window, re.I)
        )
        if not title_match:
            continue

        title = title_match.group(1).strip()
        if not _is_pokemon_bulk(title):
            continue

        price_match = price_pat.search(window)
        price_cad = float(price_match.group(1).replace(",", "")) if price_match else 0.0

        items.append({
            "title": title,
            "url": item_url,
            "price_cad": price_cad,
            "price_usd": _cad_to_usd(price_cad),
        })

        if len(items) >= 20:
            break

    return items
