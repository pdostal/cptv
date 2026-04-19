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
class ASN:
    number: int
    name: str | None
    prefix: str | None

    @property
    def looking_glass(self) -> str:
        return f"https://lg.he.net/cgi-bin/bgplookingglass?asn={self.number}"


@lru_cache(maxsize=1)
def _asn_reader() -> Any | None:
    path = get_settings().geoip_asn_db
    if not path or not Path(path).exists():
        log.info("GeoLite2-ASN DB not found at %s — ASN disabled", path)
        return None
    try:
        import geoip2.database

        return geoip2.database.Reader(path)
    except Exception:  # noqa: BLE001
        log.exception("failed to open GeoLite2-ASN DB at %s", path)
        return None


def lookup(address: IPAddress | None) -> ASN | None:
    if address is None or address.is_private:
        return None
    reader = _asn_reader()
    if reader is None:
        return None

    try:
        response = reader.asn(str(address))
    except Exception:  # noqa: BLE001
        return None

    number = response.autonomous_system_number
    if number is None:
        return None

    prefix = getattr(response, "network", None)
    return ASN(
        number=int(number),
        name=str(response.autonomous_system_organization)
        if response.autonomous_system_organization
        else None,
        prefix=str(prefix) if prefix else None,
    )
