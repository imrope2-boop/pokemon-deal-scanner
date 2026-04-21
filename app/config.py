from __future__ import annotations

import json

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'Pokemon Bulk Lot Scanner'
    secret_key: str = 'change-me'
    database_url: str = 'sqlite:///./deals.db'
    app_url: str = ''
    poll_interval_seconds: int = 90
    email_poll_interval_seconds: int = 120
    default_currency: str = 'USD'

    # eBay Browse API
    ebay_client_id: str = ''
    ebay_client_secret: str = ''
    ebay_marketplace_id: str = 'EBAY_US'
    search_keywords_json: str = (
        '["pokemon bulk lot","pokemon card lot","pokemon binder collection",'
        '"pokemon 100 cards","pokemon 500 cards","pokemon collection lot"]'
    )

    # IMAP intake
    alert_email_host: str = 'imap.gmail.com'
    alert_email_port: int = 993
    alert_email_username: str = ''
    alert_email_password: str = ''
    alert_email_mailbox: str = 'INBOX'
    alert_email_use_ssl: bool = True
    email_source_filters_json: str = (
        '[{"platform":"mercari","from_contains":"mercari"},'
        '{"platform":"facebook_marketplace","from_contains":"facebook"},'
        '{"platform":"whatnot","from_contains":"whatnot"},'
        '{"platform":"kijiji","from_contains":"kijiji"},'
        '{"platform":"buyee","from_contains":"buyee"}]'
    )

    @property
    def search_keywords(self) -> list[str]:
        return json.loads(self.search_keywords_json)

    @property
    def email_source_filters(self) -> list[dict]:
        return json.loads(self.email_source_filters_json)


settings = Settings()
