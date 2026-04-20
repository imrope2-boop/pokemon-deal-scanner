"""
Reddit Scanner — scrapes Pokemon TCG trading subreddits for bulk deals
Uses PRAW if credentials set, then app-only OAuth (bypasses IP blocks), then public JSON
"""
import os
import re
import base64
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

SUBREDDITS = [
    "pkmntcgtrades",
    "PokemonCardValue",
    "pokemontrades",
    "PokemonTCG",
    "pokemoncardcollectors",
    "pkmntcg",
    "PokemonCardTrader",
    "Pokemoncard",
]

SALE_FLAIR = [
    "wts", "wts/wtt", "selling", "sale", "for sale", "fs", "fs/ft",
    "selling bulk", "bulk sale", "collection sale", "lot for sale",
    "trade", "wtt", "ft", "swap"
]
SALE_PATTERNS = [
    r'\bWTS\b', r'\bWTT\b', r'\bFS\b', r'\bFT\b', r'\bH:\b', r'\bW:\b',
    r'selling', r'for sale', r'for trade', r'bulk\s*lot',
    r'collection\s+dump', r'price\s*drop', r'clearing',
    r'must\s+sell', r'quick\s+sale', r'moving\s+sale',
    r'\[H\]', r'\[W\]',
]

# Module-level OAuth token cache
_oauth_token = None
_oauth_token_expiry = 0


def _make_reddit_client():
    """Create PRAW Reddit client from env vars"""
    if not PRAW_AVAILABLE:
        return None
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "PokemonDealScanner/1.0")
    if not client_id or not client_secret or client_id == "your_reddit_client_id":
        return None
    try:
        reddit = praw.Reddit(client_id=client_id, client_secret=client_secret, user_agent=user_agent)
        reddit.read_only = True
        return reddit
    except Exception as e:
        print(f"\u26a0\ufe0f  PRAW error: {e}")
        return None


def _get_oauth_token():
    """Get or refresh app-only OAuth token — uses oauth.reddit.com, not IP-blocked"""
    global _oauth_token, _oauth_token_expiry
    if _oauth_token and time.time() < _oauth_token_expiry:
        return _oauth_token
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret or client_id == "your_reddit_client_id":
        return None
    user_agent = os.getenv("REDDIT_USER_AGENT", "PokemonDealScanner/1.0")
    auth_str = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            headers={"Authorization": f"Basic {auth_str}", "User-Agent": user_agent},
            data={"grant_type": "client_credentials"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            _oauth_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            _oauth_token_expiry = time.time() + expires_in - 60
            print(f"\u2705 Reddit OAuth token obtained (valid for {expires_in}s)")
            return _oauth_token
        else:
            print(f"\u26a0\ufe0f  OAuth token failed: HTTP {resp.status_code} {resp.text[:120]}")
    except Exception as e:
        print(f"\u26a0\ufe0f  OAuth token error: {e}")
    return None


def _fetch_subreddit_oauth(subreddit: str, limit: int, token: str) -> List[Dict]:
    """Fetch posts via oauth.reddit.com — bypasses Cloudflare IP blocks on www.reddit.com"""
    posts = []
    user_agent = os.getenv("REDDIT_USER_AGENT", "PokemonDealScanner/1.0")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent}
    after = None
    fetched = 0
    while fetched < limit:
        batch = min(100, limit - fetched)
        params = {"limit": batch, "raw_json": 1}
        if after:
            params["after"] = after
        try:
            resp = requests.get(f"https://oauth.reddit.com/r/{subreddit}/new",
                                headers=headers, params=params, timeout=15)
            print(f"  \U0001f4f6 r/{subreddit} OAuth: HTTP {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                children = data.get("data", {}).get("children", [])
                for child in children:
                    posts.append(child.get("data", {}))
                fetched += len(children)
                after = data.get("data", {}).get("after")
                if not after or not children:
                    break
            elif resp.status_code == 401:
                global _oauth_token
                _oauth_token = None
                print(f"\u26a0\ufe0f  Token expired for r/{subreddit}, will refresh next scan")
                break
            else:
                print(f"\u26a0\ufe0f  OAuth HTTP {resp.status_code} for r/{subreddit}")
                break
        except Exception as e:
            print(f"\u26a0\ufe0f  OAuth error r/{subreddit}: {e}")
            break
    print(f"  \U0001f4c4 Fetched {len(posts)} posts via OAuth from r/{subreddit}")
    return posts


def _fetch_subreddit_json(subreddit: str, limit: int = 100) -> List[Dict]:
    """Fetch posts via public JSON API — often 403 from server IPs"""
    posts = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    after = None
    fetched = 0
    while fetched < limit:
        batch = min(100, limit - fetched)
        params = {"limit": batch, "raw_json": 1}
        if after:
            params["after"] = after
        try:
            resp = requests.get(f"https://www.reddit.com/r/{subreddit}/new.json",
                                headers=headers, params=params, timeout=15)
            print(f"  \U0001f4f6 r/{subreddit} /new: HTTP {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                children = data.get("data", {}).get("children", [])
                for child in children:
                    posts.append(child.get("data", {}))
                fetched += len(children)
                after = data.get("data", {}).get("after")
                if not after or not children:
                    break
            elif resp.status_code == 429:
                print(f"\u26a0\ufe0f  Rate limited on r/{subreddit}, waiting 10s...")
                time.sleep(10)
                break
            else:
                print(f"\u26a0\ufe0f  HTTP {resp.status_code} for r/{subreddit}")
                break
        except Exception as e:
            print(f"\u26a0\ufe0f  Error fetching r/{subreddit}: {e}")
            break
    print(f"  \U0001f4c4 Fetched {len(posts)} posts from r/{subreddit}")
    return posts


def _is_sale_post(title: str, flair: str = "") -> bool:
    """Check if post is a for-sale/trade post"""
    combined = f"{flair} {title}".lower()
    if any(s in combined for s in SALE_FLAIR):
        return True
    for pat in SALE_PATTERNS:
        if re.search(pat, title, re.IGNORECASE):
            return True
    if any(k in combined for k in ["bulk lot", "collection lot", "clearing collection",
                                    "selling my", "selling a", "price drop"]):
        return True
    return False


def _parse_post(post_data: Dict):
    """Parse a Reddit post into a Deal object"""
    title = post_data.get("title", "")
    body = post_data.get("selftext", "")
    permalink = f"https://reddit.com{post_data.get('permalink', '')}"
    flair = post_data.get("link_flair_text") or ""
    author = post_data.get("author", "unknown")
    created_utc = post_data.get("created_utc", time.time())

    if not _is_sale_post(title, flair):
        return None
    if body in ("[removed]", "[deleted]"):
        body = ""

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


def scan_reddit(limit_per_sub: int = 100) -> List[Deal]:
    """
    Main Reddit scanner.
    Priority: PRAW > app-only OAuth (oauth.reddit.com) > public JSON fallback.
    Set REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET in Railway env vars to enable OAuth.
    """
    deals = []
    reddit = _make_reddit_client()

    oauth_token = None
    if not reddit:
        oauth_token = _get_oauth_token()
        if oauth_token:
            print("\u2705 Using Reddit app-only OAuth (oauth.reddit.com — bypasses IP blocks)")
        else:
            print("\u26a0\ufe0f  No Reddit credentials — set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in Railway Variables")

    for subreddit in SUBREDDITS:
        print(f"\U0001f4e1 r/{subreddit}...")
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
                print(f"\u26a0\ufe0f  PRAW error on r/{subreddit}: {e}")
                posts = _fetch_subreddit_oauth(subreddit, limit_per_sub, oauth_token) if oauth_token else _fetch_subreddit_json(subreddit, limit_per_sub)
        elif oauth_token:
            posts = _fetch_subreddit_oauth(subreddit, limit_per_sub, oauth_token)
        else:
            posts = _fetch_subreddit_json(subreddit, limit_per_sub)

        n_sale = n_price = sub_deals = 0
        for post_data in posts:
            try:
                title = post_data.get("title", "")
                flair = post_data.get("link_flair_text") or ""
                body = post_data.get("selftext", "")
                if _is_sale_post(title, flair):
                    n_sale += 1
                    if extract_price(f"{title} {body}") is not None:
                        n_price += 1
                deal = _parse_post(post_data)
                if deal:
                    deals.append(deal)
                    sub_deals += 1
            except Exception as e:
                print(f"\u26a0\ufe0f  Error parsing post: {e}")

        print(f"  \u2197 {sub_deals} deals [fetched={len(posts)} sale={n_sale} priced={n_price}] from r/{subreddit}")
        time.sleep(1)

    print(f"\u2554 Reddit scan complete: {len(deals)} deals total")
    return deals
