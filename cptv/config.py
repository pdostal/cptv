from __future__ import annotations

import json
import logging
from functools import lru_cache

from fastapi import Request
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

BASE_DOMAIN_HEADER = "x-base-domain"


class QuickLink(BaseSettings):
    label: str
    url: str
    icon: str | None = None
    description: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CPTV_", case_sensitive=False)

    base_domain_fallback: str = Field(
        default="localhost",
        description="Used when the X-Base-Domain header is absent (local dev).",
    )
    quick_links_raw: str = Field(default="", alias="CPTV_QUICK_LINKS")
    geoip_city_db: str = Field(
        default="/app/vendor/geolite2/GeoLite2-City.mmdb",
        description="Path to the GeoLite2 City MMDB. Missing file disables GeoIP gracefully.",
    )
    geoip_asn_db: str = Field(
        default="/app/vendor/geolite2/GeoLite2-ASN.mmdb",
        description="Path to the GeoLite2 ASN MMDB. Missing file disables ASN gracefully.",
    )

    @property
    def quick_links(self) -> list[QuickLink]:
        raw = self.quick_links_raw.strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [QuickLink(**item) for item in data]
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            log.warning("CPTV_QUICK_LINKS malformed, hiding section: %s", exc)
            return []


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_base_domain(request: Request) -> str:
    header = request.headers.get(BASE_DOMAIN_HEADER)
    if header:
        return header.strip().lower()
    return get_settings().base_domain_fallback
