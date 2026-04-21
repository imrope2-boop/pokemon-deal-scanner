from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .connectors import fetch_ebay_deals, fetch_email_deals
from .db import get_session
from .repository import update_connector_status, upsert_deal

logger = logging.getLogger(__name__)

_EMAIL_PLATFORMS = ['mercari', 'facebook_marketplace', 'whatnot', 'kijiji', 'buyee']


def run_ebay_poll() -> None:
    logger.info('eBay poll starting')
    with get_session() as session:
        if not settings.ebay_client_id or not settings.ebay_client_secret:
            update_connector_status(
                session, 'ebay',
                enabled=False, state='disabled',
                details='Set EBAY_CLIENT_ID + EBAY_CLIENT_SECRET to enable',
            )
            return
        try:
            deals = fetch_ebay_deals()
            for d in deals:
                upsert_deal(
                    session,
                    platform=d['platform'],
                    source_method=d['source_method'],
                    external_id=d['external_id'],
                    title=d['title'],
                    url=d['url'],
                    image_url=d.get('image_url'),
                    price=d['price'],
                    currency=d['currency'],
                    raw_payload=d.get('raw_payload', {}),
                )
            update_connector_status(
                session, 'ebay',
                enabled=True, state='ok',
                details=f'Fetched {len(deals)} deals',
                success=True,
            )
            logger.info('eBay poll complete: %d deals', len(deals))
        except Exception as exc:
            logger.exception('eBay poll failed')
            update_connector_status(
                session, 'ebay',
                enabled=True, state='error',
                last_error=str(exc)[:500],
            )


def run_email_poll() -> None:
    logger.info('Email poll starting')
    with get_session() as session:
        if not settings.alert_email_username or not settings.alert_email_password:
            for p in _EMAIL_PLATFORMS:
                update_connector_status(
                    session, p,
                    enabled=False, state='disabled',
                    details='Set ALERT_EMAIL_USERNAME + ALERT_EMAIL_PASSWORD to enable',
                )
            return
        try:
            deals = fetch_email_deals()
            for d in deals:
                upsert_deal(
                    session,
                    platform=d['platform'],
                    source_method=d['source_method'],
                    external_id=d['external_id'],
                    title=d['title'],
                    url=d['url'],
                    image_url=d.get('image_url'),
                    price=d['price'],
                    currency=d['currency'],
                    raw_payload=d.get('raw_payload', {}),
                )
            update_connector_status(
                session, 'email_alerts',
                enabled=True, state='ok',
                details=f'Ingested {len(deals)} email deals',
                success=True,
            )
            logger.info('Email poll complete: %d deals', len(deals))
        except Exception as exc:
            logger.exception('Email poll failed')
            update_connector_status(
                session, 'email_alerts',
                enabled=True, state='error',
                last_error=str(exc)[:500],
            )


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone='UTC')
    scheduler.add_job(
        run_ebay_poll,
        'interval',
        seconds=settings.poll_interval_seconds,
        id='ebay',
        next_run_time=datetime.utcnow(),  # run immediately on startup
    )
    scheduler.add_job(
        run_email_poll,
        'interval',
        seconds=settings.email_poll_interval_seconds,
        id='email',
        next_run_time=datetime.utcnow(),
    )
    scheduler.start()
    return scheduler
