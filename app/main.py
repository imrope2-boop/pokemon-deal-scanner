from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .db import Base, engine
from .jobs import start_scheduler
from .repository import list_connector_statuses, list_deals
from .schemas import ConnectorStatusOut, DealOut

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    scheduler = start_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')


@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        'index.html', {'request': request, 'app_name': settings.app_name}
    )


@app.get('/api/deals', response_model=list[DealOut])
async def api_deals(
    platform: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    sort_by: str = 'score',
    limit: int = 100,
):
    from .db import get_session
    with get_session() as session:
        deals = list_deals(
            session,
            platform=platform,
            min_price=min_price,
            max_price=max_price,
            sort_by=sort_by,
            limit=min(limit, 200),
        )
        return [DealOut.model_validate(d) for d in deals]


@app.get('/api/status', response_model=list[ConnectorStatusOut])
async def api_status():
    from .db import get_session
    with get_session() as session:
        statuses = list_connector_statuses(session)
        return [ConnectorStatusOut.model_validate(s) for s in statuses]


@app.get('/health')
async def health():
    return {'status': 'ok'}
