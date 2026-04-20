"""
Scan Scheduler — coordinates all platform scanners and saves results
Runs on configurable intervals using APScheduler
"""
import json
import time
import asyncio
from datetime import datetime
from typing import List, Callable, Optional

from models.deal import Deal, DealTier
from database.db import save_deal, log_scan, record_price_history
from scanner.reddit_scanner import scan_reddit
from scanner.ebay_scanner import scan_ebay
from scanner.tcgplayer_scanner import scan_tcgplayer

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
    for fn in _new_deal_callbacks:
        try:
            fn(deal)
        except Exception as e:
            print(f"⚠️  Callback error: {e}")


def run_scan(platforms: Optional[List[str]] = None) -> dict:
    """
    Run a full scan across all enabled platforms.
    Returns summary dict with counts per platform.
    """
    start_time = time.time()
    summary = {
        "started_at": datetime.utcnow().isoformat(),
        "platforms": {},
        "total_found": 0,
        "total_new": 0,
        "great_deals": [],
        "good_deals": [],
        "decent_deals": []
    }

    print(f"\n{'='*50}")
    print(f"🚀 Starting scan at {datetime.utcnow().strftime('%H:%M:%S UTC')}")
    print(f"{'='*50}")

    # ── Reddit ────────────────────────────────────────────────────
    if (platforms is None or "reddit" in platforms) and PLATFORMS.get("reddit", {}).get("enabled", True):
        t0 = time.time()
        try:
            reddit_deals = scan_reddit()
            new_count = 0
            for deal in reddit_deals:
                is_new = save_deal(deal.to_dict())
                if is_new:
                    new_count += 1
                    _notify_new_deal(deal)
                    _categorize_deal(summary, deal)

            log_scan("reddit", len(reddit_deals), new_count, duration=time.time()-t0)
            summary["platforms"]["reddit"] = {"found": len(reddit_deals), "new": new_count}
            summary["total_found"] += len(reddit_deals)
            summary["total_new"] += new_count
        except Exception as e:
            print(f"❌ Reddit scan failed: {e}")
            log_scan("reddit", 0, 0, errors=str(e))
            summary["platforms"]["reddit"] = {"error": str(e)}

    # ── eBay ──────────────────────────────────────────────────────
    if (platforms is None or "ebay" in platforms) and PLATFORMS.get("ebay", {}).get("enabled", True):
        t0 = time.time()
        try:
            ebay_deals = scan_ebay()
            new_count = 0
            for deal in ebay_deals:
                is_new = save_deal(deal.to_dict())
                if is_new:
                    new_count += 1
                    _notify_new_deal(deal)
                    _categorize_deal(summary, deal)

            log_scan("ebay", len(ebay_deals), new_count, duration=time.time()-t0)
            summary["platforms"]["ebay"] = {"found": len(ebay_deals), "new": new_count}
            summary["total_found"] += len(ebay_deals)
            summary["total_new"] += new_count
        except Exception as e:
            print(f"❌ eBay scan failed: {e}")
            log_scan("ebay", 0, 0, errors=str(e))
            summary["platforms"]["ebay"] = {"error": str(e)}

    # ── TCGPlayer ─────────────────────────────────────────────────
    if (platforms is None or "tcgplayer" in platforms) and PLATFORMS.get("tcgplayer", {}).get("enabled", True):
        t0 = time.time()
        try:
            tcg_deals = scan_tcgplayer()
            new_count = 0
            for deal in tcg_deals:
                is_new = save_deal(deal.to_dict())
                if is_new:
                    new_count += 1
                    _notify_new_deal(deal)
                    _categorize_deal(summary, deal)

            log_scan("tcgplayer", len(tcg_deals), new_count, duration=time.time()-t0)
            summary["platforms"]["tcgplayer"] = {"found": len(tcg_deals), "new": new_count}
            summary["total_found"] += len(tcg_deals)
            summary["total_new"] += new_count
        except Exception as e:
            print(f"❌ TCGPlayer scan failed: {e}")
            log_scan("tcgplayer", 0, 0, errors=str(e))
            summary["platforms"]["tcgplayer"] = {"error": str(e)}

    duration = round(time.time() - start_time, 1)
    summary["duration_seconds"] = duration
    summary["finished_at"] = datetime.utcnow().isoformat()

    print(f"\n📊 Scan complete in {duration}s")
    print(f"   Total found: {summary['total_found']} | New: {summary['total_new']}")
    print(f"   🟢 Great: {len(summary['great_deals'])} | 🟡 Good: {len(summary['good_deals'])} | 🔴 Decent: {len(summary['decent_deals'])}")
    print(f"{'='*50}\n")

    return summary


def _categorize_deal(summary: dict, deal: Deal):
    """Add a new deal to the appropriate tier bucket in summary"""
    deal_info = {
        "id": deal.id,
        "title": deal.title[:60],
        "platform": deal.platform.value,
        "price": deal.asking_price,
        "score": deal.score,
        "profit": round(deal.price_breakdown.estimated_profit, 2)
    }

    if deal.tier == DealTier.GREAT:
        summary["great_deals"].append(deal_info)
    elif deal.tier == DealTier.GOOD:
        summary["good_deals"].append(deal_info)
    elif deal.tier == DealTier.DECENT:
        summary["decent_deals"].append(deal_info)
