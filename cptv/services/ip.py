from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from fastapi import Request

FORWARDED_FOR_HEADER = "x-forwarded-for"
CGNAT_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class ClassifiedIP:
    address: IPAddress
    protocol: str
    is_private: bool
    is_cgnat: bool

    @property
    def text(self) -> str:
        return str(self.address)


def parse_forwarded_for(raw: str | None) -> str | None:
    if not raw:
        return None
    first = raw.split(",")[0].strip()
    return first or None


def client_ip(request: Request) -> IPAddress | None:
    candidate = parse_forwarded_for(request.headers.get(FORWARDED_FOR_HEADER))
    if candidate is None and request.client is not None:
        candidate = request.client.host
    if not candidate:
        return None
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def classify(address: IPAddress) -> ClassifiedIP:
    protocol = "IPv4" if isinstance(address, ipaddress.IPv4Address) else "IPv6"
    is_cgnat = isinstance(address, ipaddress.IPv4Address) and address in CGNAT_NETWORK
    return ClassifiedIP(
        address=address,
        protocol=protocol,
        is_private=address.is_private,
        is_cgnat=is_cgnat,
    )
