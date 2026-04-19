from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from cptv.config import get_settings
from cptv.services.ip import IPAddress

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeoIP:
    country_code: str | None
    country: str | None
    region: str | None
    city: str | None
    latitude: float | None
    longitude: float | None


@lru_cache(maxsize=1)
def _city_reader() -> Any | None:
    path = get_settings().geoip_city_db
    if not path or not Path(path).exists():
        log.info("GeoLite2-City DB not found at %s — GeoIP disabled", path)
        return None
    try:
        import geoip2.database

        return geoip2.database.Reader(path)
    except Exception:  # noqa: BLE001 — readonly MMDB; any failure → disable
        log.exception("failed to open GeoLite2-City DB at %s", path)
        return None


def lookup(address: IPAddress | None) -> GeoIP | None:
    if address is None or address.is_private:
        return None
    reader = _city_reader()
    if reader is None:
        return None

    try:
        response = reader.city(str(address))
    except Exception:  # noqa: BLE001 — unknown IP / bad record
        return None

    subdivision = response.subdivisions.most_specific
    return GeoIP(
        country_code=str(response.country.iso_code) if response.country.iso_code else None,
        country=str(response.country.name) if response.country.name else None,
        region=subdivision.name if subdivision else None,
        city=str(response.city.name) if response.city.name else None,
        latitude=float(response.location.latitude)
        if response.location.latitude is not None
        else None,
        longitude=float(response.location.longitude)
        if response.location.longitude is not None
        else None,
    )
