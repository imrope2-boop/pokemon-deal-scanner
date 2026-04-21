from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DealOut(BaseModel):
    id: int
    platform: str
    source_method: str
    title: str
    url: str
    image_url: str | None
    price: float
    currency: str
    estimated_card_count: int
    price_per_card: float | None
    score: float
    confidence: float
    first_seen_at: datetime
    last_seen_at: datetime

    class Config:
        from_attributes = True


class ConnectorStatusOut(BaseModel):
    connector_name: str
    enabled: bool
    state: str
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    details: str | None

    class Config:
        from_attributes = True
