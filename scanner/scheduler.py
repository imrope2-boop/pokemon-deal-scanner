"""
Scan Scheduler — coordinates all platform scanners and saves results
Scanning is manual-only (triggered via POST /api/scan).
"""
import json
import dataclasses
import time
import asyncio
from datetime import datetime
from typing import List, Callable, Optional

from models.deal import Deal, DealTier
from database.db import save_deal, log_scan, record_price_history
from scanner.reddit_scanner import scan_reddit
from scanner.ebay_scanner import scan_ebay
from scanner.tcgplayer_scanner import scan_tcgplayer
from scanner.ebay_api_scanner import scan_ebay_api
from scanner.international_scanner import scan_yahoo_japan, scan_mercari_japan, scan_kijiji

# Load config
try:
    with open("config.json") as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    CONFIG = {}

PLATFORMS = CONFIG.get("platforms", {})

# Global: new deal callbacks (for WebSocket broadcast)
_new_deal_callbacks: List[Callable] = []


def register_new_deal_callback(fn: Callable):
    """Register a callback to be called when a new deal is found"""
    _new_deal_callbacks.append(fn)


def _notify_new_deal(deal: Deal):
    """Notify all registered callbacks about a new deal"""
    for cb in _new_deal_callbacks:
        try:
            cb(deal)
        except Exception as e:
            print(f"[Scheduler] Callback error: {e}")


async def run_scan() -> dict:
    """
    Run all enabled platform scanners and save results.
    Returns a summary dict with counts per platform.
    """
    start_time = time.time()
    all_deals: List[Deal] = []
    summary = {}

    scanners = [
        # North America — Reddit
        ("reddit",         scan_reddit,        PLATFORMS.get("reddit",  {}).get("enabled", True)),
        # North America — eBay HTML scraping (legacy, may be blocked)
        ("ebay_html",      scan_ebay,           PLATFORMS.get("ebay",    {}).get("enabled", False)),
        # North America — eBay Finding API (activates automatically when EBAY_APP_ID is set)
        ("ebay_api",       scan_ebay_api,       True),
        # North America — TCGPlayer
        ("tcgplayer",      scan_tcgplayer,      PLATFORMS.get("tcgplayer", {}).get("enabled", True)),
        # International — Yahoo Auctions Japan (via Buyee proxy)
        ("yahoo_japan",    scan_yahoo_japan,    PLATFORMS.get("yahoo_japan",    {}).get("enabled", True)),
        # International — Mercari Japan (via Buyee proxy)
        ("mercari_japan",  scan_mercari_japan,  PLATFORMS.get("mercari_japan",  {}).get("enabled", True)),
        # Canada — Kijiji
        ("kijiji",         scan_kijiji,         PLATFORMS.get("kijiji",         {}).get("enabled", True)),
    ]

    for name, scanner_fn, enabled in scanners:
        if not enabled:
            print(f"[Scheduler] Skipping {name} (disabled in config)")
            summary[name] = {"status": "disabled", "new_deals": 0}
            continue

        try:
            print(f"[Scheduler] Running {name} scanner...")
            t0 = time.time()

            # Run synchronous scanners in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            deals = await loop.run_in_executor(None, scanner_fn)

            elapsed = round(time.time() - t0, 1)
            new_count = 0

            for deal in deals:
                try:
                    # Convert Deal dataclass to flat dict (PriceBreakdown inlined)
                    _d = dataclasses.asdict(deal)
                    _pb = _d.pop("price_breakdown", {})
                    _pb.pop("asking_price", None)
                    _pb.pop("card_count", None)
                    _d.update(_pb)
                    is_new = save_deal(_d)
                    if is_new:
                        new_count += 1
                        all_deals.append(deal)
                        _notify_new_deal(deal)
                    # Always try to record price history
                    try:
                        record_price_history(deal)
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[Scheduler] Error saving deal {deal.id}: {e}")

            print(f"[Scheduler] {name}: {len(deals)} found, {new_count} new ({elapsed}s)")
            summary[name] = {"status": "ok", "found": len(deals), "new_deals": new_count, "elapsed_s": elapsed}

        except Exception as e:
            print(f"[Scheduler] {name} scanner failed: {e}")
            summary[name] = {"status": "error", "error": str(e), "new_deals": 0}

    total_elapsed = round(time.time() - start_time, 1)
    total_new = sum(v.get("new_deals", 0) for v in summary.values())

    # Log the scan
    try:
        log_scan(
            platforms=list(summary.keys()),
            deals_found=total_new,
            duration_seconds=total_elapsed,
        )
    except Exception as e:
        print(f"[Scheduler] log_scan error: {e}")

    print(f"[Scheduler] Scan complete: {total_new} new deals in {total_elapsed}s")

    return {
        "status": "ok",
        "total_new_deals": total_new,
        "total_elapsed_s": total_elapsed,
        "platforms": summary,
        "timestamp": datetime.utcnow().isoformat(),
    }
