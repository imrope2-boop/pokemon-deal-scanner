from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import ConnectorStatus, Deal
from .scoring import score_listing


def upsert_deal(
    session: Session,
    *,
    platform: str,
    source_method: str,
    external_id: str,
    title: str,
    url: str,
    image_url: str | None,
    price: float,
    currency: str,
    raw_payload: dict,
) -> Deal:
    result = session.execute(
        select(Deal).where(Deal.platform == platform, Deal.external_id == external_id)
    )
    deal = result.scalar_one_or_none()
    scored = score_listing(title, price)
    now = datetime.utcnow()

    if deal is None:
        deal = Deal(
            platform=platform,
            source_method=source_method,
            external_id=external_id,
            title=title,
            url=url,
            image_url=image_url,
            price=price,
            currency=currency,
            estimated_card_count=scored.estimated_card_count,
            price_per_card=scored.price_per_card,
            score=scored.score,
            confidence=scored.confidence,
            raw_payload=json.dumps(raw_payload),
            first_seen_at=now,
            last_seen_at=now,
            is_active=True,
        )
        session.add(deal)
    else:
        deal.title = title
        deal.url = url
        deal.image_url = image_url
        deal.price = price
        deal.currency = currency
        deal.estimated_card_count = scored.estimated_card_count
        deal.price_per_card = scored.price_per_card
        deal.score = scored.score
        deal.confidence = scored.confidence
        deal.raw_payload = json.dumps(raw_payload)
        deal.last_seen_at = now
        deal.is_active = True
    session.flush()
    return deal


def list_deals(
    session: Session,
    *,
    platform: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    sort_by: str = 'score',
    limit: int = 100,
) -> list[Deal]:
    stmt = select(Deal).where(Deal.is_active.is_(True))
    if platform:
        stmt = stmt.where(Deal.platform == platform)
    if min_price is not None:
        stmt = stmt.where(Deal.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Deal.price <= max_price)

    sort_map = {
        'score': desc(Deal.score),
        'price_asc': Deal.price.asc(),
        'price_desc': Deal.price.desc(),
        'newest': desc(Deal.last_seen_at),
        'count_desc': desc(Deal.estimated_card_count),
    }
    stmt = stmt.order_by(sort_map.get(sort_by, desc(Deal.score))).limit(limit)
    return list(session.execute(stmt).scalars().all())


def update_connector_status(
    session: Session,
    connector_name: str,
    *,
    enabled: bool,
    state: str,
    details: str | None = None,
    last_error: str | None = None,
    success: bool = False,
) -> ConnectorStatus:
    result = session.execute(
        select(ConnectorStatus).where(ConnectorStatus.connector_name == connector_name)
    )
    status = result.scalar_one_or_none()
    now = datetime.utcnow()
    if status is None:
        status = ConnectorStatus(connector_name=connector_name)
        session.add(status)

    status.enabled = enabled
    status.state = state
    status.last_attempt_at = now
    status.details = details
    status.last_error = last_error
    if success:
        status.last_success_at = now
    session.flush()
    return status


def list_connector_statuses(session: Session) -> list[ConnectorStatus]:
    stmt = select(ConnectorStatus).order_by(ConnectorStatus.connector_name.asc())
    return list(session.execute(stmt).scalars().all())
