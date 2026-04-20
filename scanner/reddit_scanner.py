"""
Reddit Scanner — scrapes Pokemon TCG trading subreddits for bulk deals
Uses PRAW if credentials are set, otherwise falls back to public JSON API
"""
import os
import re
import hashlib
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict

try:
    import praw
    PRAW_AVAILABLE = True
except ImportError:
    PRAW_AVAILABLE = False

import requests
from models.deal import Deal, Platform, DealTier, CardCategory, PriceBreakdown
from utils.deal_scorer import detect_categories, is_relevant_post, extract_price, score_deal, build_tags
from pricing.price_engine import estimate_lot_value, _extract_card_count

# Expanded subreddit list
SUBREDDITS = [
    "pkmntcgtrades",        # Main trading sub — huge volume
    "PokemonCardValue",     # People asking value of collections
    "pokemontrades",        # General Pokemon trades
    "PokemonTCG",           # General TCG discussion (occasional sales)
    "pokemoncardcollectors",
    "pkmntcg",
    "PokemonCardTrader",
    "Pokemoncard",
]

# Sale/WTS flair and title patterns — broad to catch more posts
SALE_FLAIR = [
    "wts", "wts/wtt", "wtt", "selling", "sale", "for sale", "fs", "fs/ft",
    "selling bulk", "bulk sale", "collection sale", "lot for sale"
]
SALE_PATTERNS = [
    r'\bWTS\b', r'\bWTT\b', r'\bFS\b', r'\bFT\b',
    r'selling', r'for sale', r'for trade', r'bulk\s+lot',
    r'collection\s+dump', r'price\s+drop', r'clearing',
    r'must\s+sell', r'quick\s+sale', r'moving\s+sale',
]


def _make_reddit_client() -> Optional[object]:
    """Create PRAW Reddit client from env vars"""
    if not PRAW_AVAILABLE:
        return None

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "PokemonDealScanner/1.0")

    if not client_id or not client_secret or client_id == "your_reddit_client_id":
        print("⚠️  Reddit credentials not set — using public JSON fallback")
        return None

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent
        )
        reddit.read_only = True
        return reddit
    except Exception as e:
        print(f"⚠️  Reddit client error: {e}")
        return None


def _fetch_subreddit_json(subreddit: str, limit: int = 50) -> List[Dict]:
    """Fetch posts via Reddit's public JSON API (no auth required)"""
    posts = []
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    headers = {"User-Agent": "PokemonDealScanner/1.0 (deal research bot)"}
    params = {"limit": limit}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for child in data.get("data", {}).get("children", []):
                posts.append(child.get("data", {}))
        elif resp.status_code == 429:
            print(f"⚠️  Reddit rate limited on r/{subreddit}, waiting...")
            time.sleep(5)
    except Exception as e:
        print(f"⚠️  Reddit JSON error for r/{subreddit}: {e}")

    return posts


def _is_sale_post(title: str, flair: str = "") -> bool:
    """Check if post is a for-sale/trade post — broad matching"""
    combined = f"{flair} {title}".lower()
    if any(s in combined for s in SALE_FLAIR):
        return True
    for pat in SALE_PATTERNS:
        if re.search(pat, title, re.IGNORECASE):
            return True
    # Also catch posts that mention bulk/lot selling even without explicit WTS
    if any(k in combined for k in ["bulk lot", "collection lot", "clearing collection",
                                    "selling my", "selling a", "price drop"]):
        return True
    return False


def _parse_post(post_data: Dict) -> Optional[Deal]:
    """Parse a Reddit post into a Deal object"""
    title = post_data.get("title", "")
    body = post_data.get("selftext", "")
    permalink = f"https://reddit.com{post_data.get('permalink', '')}"
    flair = post_data.get("link_flair_text") or ""
    author = post_data.get("author", "unknown")
    created_utc = post_data.get("created_utc", time.time())

    if not _is_sale_post(title, flair):
        return None

    combined_text = f"{title} {body}"
    price = extract_price(combined_text)

    if not is_relevant_post(title, body, price):
        return None

    if price is None:
        return None

    categories = detect_categories(title, body)
    card_count = _extract_card_count(combined_text)

    valuation = estimate_lot_value(price, card_count, categories, combined_text)

    score, tier = score_deal(
        asking_price=price,
        estimated_market_value=valuation["estimated_market_value"],
        card_count=card_count,
        categories=categories,
        price_breakdown=valuation,
        title=title,
        description=body
    )

    # Show DECENT and above
    if tier == DealTier.NO_DEAL and score < 2.0:
        return None

    tags = build_tags(title, body, categories, tier)

    images = []
    post_url = post_data.get("url", "")
    if post_url.lower().endswith((".jpg", ".png", ".gif")):
        images.append(post_url)
    if post_data.get("preview"):
        for img in post_data["preview"].get("images", [])[:2]:
            src = img.get("source", {}).get("url", "")
            if src:
                images.append(src.replace("&amp;", "&"))

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

    deal_id = hashlib.md5(permalink.encode()).hexdigest()[:12]
    posted_at = datetime.fromtimestamp(created_utc, tz=timezone.utc)

    return Deal(
        id=f"reddit_{deal_id}",
        title=title[:200],
        description=body[:1000],
        url=permalink,
        platform=Platform.REDDIT,
        asking_price=price,
        card_count=card_count,
        card_categories=categories,
        tier=tier,
        score=score,
        price_breakdown=pb,
        seller_info=f"u/{author}",
        posted_at=posted_at,
        images=images,
        tags=tags
    )


def scan_reddit(limit_per_sub: int = 50) -> List[Deal]:
    """
    Main Reddit scanner. Returns list of Deal objects found.
    """
    deals = []
    reddit = _make_reddit_client()

    for subreddit in SUBREDDITS:
        print(f"🔍 r/{subreddit}...")
        posts = []

        if reddit:
            try:
                sub = reddit.subreddit(subreddit)
                for post in sub.new(limit=limit_per_sub):
                    posts.append({
                        "title": post.title,
                        "selftext": post.selftext,
                        "url": post.url,
                        "permalink": post.permalink,
                        "link_flair_text": post.link_flair_text or "",
                        "author": str(post.author) if post.author else "deleted",
                        "created_utc": post.created_utc,
                        "preview": getattr(post, "preview", None)
                    })
            except Exception as e:
                print(f"⚠️  PRAW error on r/{subreddit}: {e}, falling back")
                posts = _fetch_subreddit_json(subreddit, limit_per_sub)
        else:
            posts = _fetch_subreddit_json(subreddit, limit_per_sub)

        sub_deals = 0
        for post_data in posts:
            try:
                deal = _parse_post(post_data)
                if deal:
                    deals.append(deal)
                    sub_deals += 1
            except Exception as e:
                print(f"⚠️  Error parsing post: {e}")

        print(f"   → {sub_deals} deals from r/{subreddit}")
        time.sleep(1)

    print(f"✅ Reddit scan complete: {len(deals)} deals total")
    return deals
