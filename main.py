"""
Pokemon Bulk Deal Scanner — Main FastAPI Application
Serves the dashboard and API endpoints, manages background scanning
"""
import os
import json
import asyncio
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from dotenv import load_dotenv
load_dotenv()

from database.db import init_db, get_deals, get_deal_stats, get_price_history
from scanner.scheduler import run_scan, register_new_deal_callback
from models.deal import Deal

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))  # Used for display only — scanning is manual
scheduler = AsyncIOScheduler()
connected_websockets: List[WebSocket] = []
last_scan_summary = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    def on_new_deal(deal: Deal):
        asyncio.create_task(_broadcast({"type": "new_deal", "deal": deal.to_dict()}))
    register_new_deal_callback(on_new_deal)
    scheduler.start()
    yield
    scheduler.shutdown()

async def _delayed_startup_scan():
    await asyncio.sleep(3)
    await _run_scan_job()

async def _run_scan_job():
    global last_scan_summary
    await _broadcast({"type": "scan_started", "time": datetime.utcnow().isoformat()})
    summary = await run_scan()
    last_scan_summary = summary
    await _broadcast({"type": "scan_complete", "summary": summary})

async def _broadcast(message: dict):
    if not connected_websockets: return
    dead = []
    msg = json.dumps(message)
    for ws in connected_websockets:
        try: await ws.send_text(msg)
        except Exception: dead.append(ws)
    for ws in dead: connected_websockets.remove(ws)

app = FastAPI(title="Pokemon Bulk Deal Scanner", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
if os.path.exists("dashboard"):
    app.mount("/static", StaticFiles(directory="dashboard"), name="static")

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    if os.path.exists("dashboard/index.html"):
        with open("dashboard/index.html") as f: return HTMLResponse(f.read())
    return HTMLResponse("<h1>Dashboard not found</h1>")

@app.get("/api/deals")
async def api_get_deals(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), tier: Optional[str] = Query(None), platform: Optional[str] = Query(None), category: Optional[str] = Query(None), min_score: float = Query(0, ge=0, le=10), active_only: bool = Query(True)):
    deals = get_deals(limit=limit, offset=offset, tier=tier, platform=platform, category=category, min_score=min_score, active_only=active_only)
    return {"deals": deals, "count": len(deals), "offset": offset}

@app.get("/api/stats")
async def api_get_stats():
    stats = get_deal_stats()
    stats["last_scan"] = last_scan_summary
    stats["next_scan_in_minutes"] = _time_to_next_scan()
    return stats

@app.get("/api/price-history/{category}")
async def api_price_history(category: str, days: int = Query(30, ge=1, le=90)):
    return {"category": category, "days": days, "data": get_price_history(category, days)}

@app.post("/api/scan")
async def api_trigger_scan(background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_scan_job)
    return {"status": "scan_started"}

@app.get("/api/config")
async def api_get_config():
    try:
        with open("config.json") as f: return json.load(f)
    except: return {}

@app.post("/api/deals/import")
async def api_import_deals(payload: dict):
    """
    Accept deals posted by the browser-based scanner.
    Expects: { "deals": [{ "title", "url", "price", "platform", "description", "card_count", "image_url" }] }
    """
    import hashlib
    from models.deal import Deal, Platform, DealTier, CardCategory
    from utils.deal_scorer import score_deal, build_tags, detect_categories
    from pricing.price_engine import estimate_lot_value
    from database.db import save_deal

    raw_deals = payload.get("deals", [])
    saved = 0
    skipped = 0

    for raw in raw_deals:
        try:
            title = str(raw.get("title", "")).strip()
            url   = str(raw.get("url", "")).strip()
            price = float(raw.get("price") or 0)
            platform_str = str(raw.get("platform", "other")).lower()
            description  = str(raw.get("description", ""))
            card_count   = int(raw.get("card_count") or 0) or None
            image_url    = str(raw.get("image_url", ""))

            if not title or not url or price <= 1.0:
                skipped += 1
                continue

            try:
                platform = Platform(platform_str)
            except ValueError:
                platform = Platform.OTHER

            deal_id = "bro_" + hashlib.md5(url.encode()).hexdigest()[:12]
            combined = f"{title} {description}"
            categories = detect_categories(title, description)
            valuation  = estimate_lot_value(price, card_count, categories, combined)
            score, tier = score_deal(
                asking_price=price,
                estimated_market_value=valuation["estimated_market_value"],
                card_count=card_count,
                categories=categories,
                price_breakdown=valuation,
                title=title,
                description=description,
            )

            if tier == DealTier.NO_DEAL and score < 2.0:
                skipped += 1
                continue

            tags = build_tags(title, description, categories, tier)

            deal = Deal(
                id=deal_id,
                title=title,
                url=url,
                asking_price=price,
                estimated_market_value=valuation["estimated_market_value"],
                platform=platform,
                score=score,
                tier=tier,
                card_count=card_count,
                categories=categories,
                tags=tags,
                description=description[:500],
                image_url=image_url or None,
            )
            if save_deal(deal):
                saved += 1
                for cb in connected_websockets:
                    try:
                        import json as _json
                        import dataclasses as _dc
                        await cb.send_text(_json.dumps({"type": "new_deal", "deal": _dc.asdict(deal)}))
                    except Exception:
                        pass
        except Exception as e:
            print(f"[Import] Error processing deal: {e}")
            skipped += 1

    return {"saved": saved, "skipped": skipped, "total": len(raw_deals)}

@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat(), "scan_interval_minutes": SCAN_INTERVAL}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.append(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "connected", "stats": get_deal_stats()}))
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect: connected_websockets.remove(websocket)
    except Exception:
        if websocket in connected_websockets: connected_websockets.remove(websocket)

def _time_to_next_scan():
    try:
        job = scheduler.get_job("main_scan")
        if job and job.next_run_time:
            delta = (job.next_run_time.replace(tzinfo=None) - datetime.utcnow()).total_seconds()
            return round(delta / 60, 1)
    except: pass
    return None

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080")))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
