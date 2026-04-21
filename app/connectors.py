from __future__ import annotations

import email
import hashlib
import imaplib
import re
import ssl
from base64 import b64encode

import httpx
from bs4 import BeautifulSoup

from .config import settings

EBAY_AUTH_URL = 'https://api.ebay.com/identity/v1/oauth2/token'
EBAY_BROWSE_URL = 'https://api.ebay.com/buy/browse/v1/item_summary/search'


# ─── eBay ───────────────────────────────────────────────────────────────────

def _get_ebay_token() -> str:
    creds = b64encode(
        f'{settings.ebay_client_id}:{settings.ebay_client_secret}'.encode()
    ).decode()
    resp = httpx.post(
        EBAY_AUTH_URL,
        headers={
            'Authorization': f'Basic {creds}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        data={
            'grant_type': 'client_credentials',
            'scope': 'https://api.ebay.com/oauth/api_scope',
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()['access_token']


def fetch_ebay_deals() -> list[dict]:
    if not settings.ebay_client_id or not settings.ebay_client_secret:
        raise RuntimeError('eBay credentials not configured — set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET')

    token = _get_ebay_token()
    seen: set[str] = set()
    results: list[dict] = []

    for keyword in settings.search_keywords:
        try:
            resp = httpx.get(
                EBAY_BROWSE_URL,
                headers={
                    'Authorization': f'Bearer {token}',
                    'X-EBAY-C-MARKETPLACE-ID': settings.ebay_marketplace_id,
                },
                params={
                    'q': keyword,
                    'limit': 50,
                    'filter': 'buyingOptions:{FIXED_PRICE|AUCTION}',
                },
                timeout=20,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f'eBay API error for "{keyword}": {exc.response.status_code}') from exc

        for item in resp.json().get('itemSummaries', []):
            item_id = item.get('itemId', '')
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)

            price_value = float(item.get('price', {}).get('value', 0))
            if price_value <= 1:
                continue

            results.append({
                'platform': 'ebay',
                'source_method': 'ebay_api',
                'external_id': item_id,
                'title': item.get('title', ''),
                'url': item.get('itemWebUrl', ''),
                'image_url': item.get('image', {}).get('imageUrl'),
                'price': price_value,
                'currency': item.get('price', {}).get('currency', settings.default_currency),
                'raw_payload': item,
            })

    return results


# ─── IMAP / email alerts ─────────────────────────────────────────────────────

_PRICE_RE = re.compile(r'\$\s*(\d[\d,]*(?:\.\d{1,2})?)')
_URL_INTERESTING = re.compile(r'/item/|/listing/|/product/|/l/|/i/|/ad/', re.I)


def _body_text(msg: email.message.Message) -> tuple[str, str]:
    """Return (html_body, plain_body)."""
    html, plain = '', ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/html' and not html:
                html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
            elif ct == 'text/plain' and not plain:
                plain = part.get_payload(decode=True).decode('utf-8', errors='ignore')
    else:
        ct = msg.get_content_type()
        payload = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
        if ct == 'text/html':
            html = payload
        else:
            plain = payload
    return html, plain


def _parse_email_deals(msg: email.message.Message, platform: str) -> list[dict]:
    subject = str(msg.get('Subject', ''))
    html, plain = _body_text(msg)

    soup = BeautifulSoup(html or plain, 'html.parser')
    text = soup.get_text(' ', strip=True)

    # Collect candidate links
    links: list[tuple[str, str]] = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if _URL_INTERESTING.search(href):
            label = a.get_text(strip=True) or subject
            links.append((label, href))

    # Collect prices
    prices = [
        float(p.replace(',', ''))
        for p in _PRICE_RE.findall(text)
        if float(p.replace(',', '')) > 1
    ]

    deals: list[dict] = []

    if links:
        for i, (label, href) in enumerate(links[:10]):
            price = prices[i] if i < len(prices) else (prices[0] if prices else 0)
            if price <= 1:
                continue
            ext_id = hashlib.md5(href.encode()).hexdigest()[:20]
            deals.append({
                'platform': platform,
                'source_method': 'email_alert',
                'external_id': ext_id,
                'title': label[:400],
                'url': href,
                'image_url': None,
                'price': price,
                'currency': settings.default_currency,
                'raw_payload': {'subject': subject, 'url': href},
            })
    elif subject and prices:
        ext_id = hashlib.md5((subject + str(prices[0])).encode()).hexdigest()[:20]
        deals.append({
            'platform': platform,
            'source_method': 'email_alert',
            'external_id': ext_id,
            'title': subject[:400],
            'url': '',
            'image_url': None,
            'price': prices[0],
            'currency': settings.default_currency,
            'raw_payload': {'subject': subject},
        })

    return deals


def fetch_email_deals() -> list[dict]:
    if not settings.alert_email_username or not settings.alert_email_password:
        raise RuntimeError('Email credentials not configured — set ALERT_EMAIL_USERNAME and ALERT_EMAIL_PASSWORD')

    filters = settings.email_source_filters
    if not filters:
        return []

    if settings.alert_email_use_ssl:
        ctx = ssl.create_default_context()
        imap = imaplib.IMAP4_SSL(settings.alert_email_host, settings.alert_email_port, ssl_context=ctx)
    else:
        imap = imaplib.IMAP4(settings.alert_email_host, settings.alert_email_port)

    try:
        imap.login(settings.alert_email_username, settings.alert_email_password)
        imap.select(settings.alert_email_mailbox)

        deals: list[dict] = []

        for f in filters:
            platform = f.get('platform', 'unknown')
            from_contains = f.get('from_contains', '')
            if not from_contains:
                continue

            _, data = imap.search(None, f'(FROM "{from_contains}" UNSEEN)')
            msg_ids = data[0].split()

            for msg_id in msg_ids[-20:]:  # cap at 20 per platform per run
                _, msg_data = imap.fetch(msg_id, '(RFC822)')
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                deals.extend(_parse_email_deals(msg, platform))
                imap.store(msg_id, '+FLAGS', '\\Seen')

        return deals
    finally:
        try:
            imap.logout()
        except Exception:
            pass
