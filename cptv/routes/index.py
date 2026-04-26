from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.config import get_base_domain, get_settings
from cptv.middleware import elapsed_ms_so_far
from cptv.negotiation import respond
from cptv.services import asn as asn_service
from cptv.services import clock as clock_service
from cptv.services import dns as dns_service
from cptv.services import geoip as geoip_service
from cptv.services import ip as ip_service
from cptv.services import protocol as protocol_service
from cptv.services import rdns as rdns_service
from cptv.services import redirect_origin as redirect_origin_service

router = APIRouter()


def _request_inspection(request: Request) -> dict:
    """Snapshot of the request for the 'request inspection' panel.

    No filtering: surface every header the browser sent so the panel
    is genuinely useful for debugging captive portals, proxies, and
    misconfigured nginx forwarding. The headers belong to the user's
    own request; there is no privacy concern in showing them back.
    """
    return {
        "method": request.method,
        "path": request.url.path,
        "scheme": request.url.scheme,
        "client": request.client.host if request.client else None,
        "headers": dict(request.headers),
    }


async def _collect(request: Request) -> dict:
    address = ip_service.client_ip(request)
    classified = ip_service.classify(address) if address is not None else None
    geo = geoip_service.lookup(address)
    asn = asn_service.lookup(address)
    # PTR for the *current* connection's IP only. The other-stack IP is
    # not known server-side; the JS dual-stack probe fetches it client-
    # side via /rdns/<ip>. Bounded at 0.3 s in the service layer.
    rdns = await rdns_service.lookup(address)

    current = classified.text if classified else None
    protocol = classified.protocol if classified else None
    ipv4 = current if classified and isinstance(address, ipaddress.IPv4Address) else None
    ipv6 = current if classified and isinstance(address, ipaddress.IPv6Address) else None

    domain = get_base_domain(request)
    resolver_q = request.query_params.get("resolver")
    resolver = dns_service.classify_resolver(resolver_q)

    http_version = request.scope.get("http_version") or "1.1"
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)

    proto_info = protocol_service.from_request(request)

    elapsed = elapsed_ms_so_far(request)
    rtt_ms = round(elapsed, 1) if elapsed is not None else None

    redirect = redirect_origin_service.detect(request, own_host=domain)

    return {
        "ip": {
            "current": current,
            "protocol": protocol,
            "preferred": protocol,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "is_private": classified.is_private if classified else None,
            "is_cgnat": classified.is_cgnat if classified else None,
            # PTR for the current IP only. None when private, no PTR
            # configured upstream, or the lookup timed out (>0.3 s).
            "rdns": rdns,
        },
        "geoip": None
        if geo is None
        else {
            "country_code": geo.country_code,
            "country": geo.country,
            "region": geo.region,
            "city": geo.city,
            "latitude": geo.latitude,
            "longitude": geo.longitude,
        },
        "asn": None
        if asn is None
        else {
            "number": asn.number,
            "name": asn.name,
            "prefix": asn.prefix,
            "looking_glass": asn.looking_glass,
        },
        "dns": {
            "resolver_ip": resolver.resolver_ip,
            "resolver_name": resolver.resolver_name,
            "is_known_public": resolver.is_known_public,
        },
        "dnssec": None,  # client-side only (§4.5)
        "timing": {
            "server_timestamp": clock_service.iso_now(),
            "rtt_ms": rtt_ms,
        },
        "http": {
            "version": f"HTTP/{http_version}",
            "protocol": forwarded_proto,
            "referrer": request.headers.get("referer"),
        },
        # Richer view of the connection protocol; data["http"] stays as
        # a short alias for shell scripts. See cptv/services/protocol.py.
        "protocol": {
            "http_version": proto_info.http_version,
            "tls_version": proto_info.tls_version,
            "tls_cipher": proto_info.tls_cipher,
            "alpn": proto_info.alpn,
            "is_encrypted": proto_info.is_encrypted,
        },
        "redirect_origin": {
            "referrer": redirect.referrer,
            "referrer_host": redirect.referrer_host,
            "via_header": redirect.via_header,
            "original_url": redirect.original_url,
            "looks_like_captive_portal": redirect.looks_like_captive_portal,
        },
        "meta": {
            "server": domain,
            "repo": "https://github.com/pdostal/cptv",
        },
        "quick_links": [link.model_dump() for link in get_settings().quick_links],
        "quick_links_title": get_settings().quick_links_title,
        "request": _request_inspection(request),
    }


def _text_aggregated(data: dict) -> str:
    """Plain-text aggregated output for curl users.

    Skips DNSSEC, Resolver and the server-side timestamp (browser-only
    or not useful in scripts) and the Server identity line (visible to
    anyone who already knows where they sent the request). Keeps IP,
    GeoIP, ASN, the negotiated connection protocol, and the RTT
    diagnostic.

    The per-protocol probe URLs (https://http{1,2,3}.<base>/protocol)
    are NOT listed here \u2014 curl users learn about them from /help.
    """
    ip = data["ip"]
    geo = data["geoip"]
    asn = data["asn"]
    conn = data["protocol"]

    lines: list[str] = []

    current = ip["current"] or "unknown"
    ip_proto = ip["protocol"] or ""
    suffix = f"  ({ip_proto}, preferred)" if ip_proto else ""
    lines.append(f"🌐 IP:        {current}{suffix}")
    if ip.get("rdns"):
        lines.append(f"   rDNS:      {ip['rdns']}")
    if ip["ipv4"] and ip["protocol"] != "IPv4":
        lines.append(f"   IPv4:      {ip['ipv4']}")
    if ip["ipv6"] and ip["protocol"] != "IPv6":
        lines.append(f"   IPv6:      {ip['ipv6']}")
    lines.append("")

    # Connection protocol \u2014 always emitted; users can compare
    # by hitting `curl --http2 https://http2.<base>/protocol` etc.
    parts: list[str] = [conn["http_version"]]
    extras: list[str] = []
    if conn["tls_version"]:
        extras.append(conn["tls_version"])
    if conn["alpn"]:
        extras.append(f"ALPN {conn['alpn']}")
    if extras:
        parts.append(f"({', '.join(extras)})")
    lines.append(f"🔗 Protocol:  {' '.join(parts)}")
    lines.append("")

    if geo:
        coords = (
            f"{geo['latitude']:.4f}, {geo['longitude']:.4f}"
            if geo["latitude"] is not None and geo["longitude"] is not None
            else "—"
        )
        lines.append(f"🌍 Country:   {geo['country_code'] or '?'}  {geo['country'] or ''}".rstrip())
        lines.append(f"   City:      {geo['city'] or '—'}")
        lines.append(f"   Coords:    {coords}")
        lines.append("")

    if asn:
        lines.append(f"🔌 ASN:       AS{asn['number']}  {asn['name'] or ''}".rstrip())
        lines.append(f"   Prefix:    {asn['prefix'] or '—'}")
        lines.append("")

    rtt = data["timing"].get("rtt_ms")
    if rtt is not None:
        # One leading space (not two) after the ⏱️ emoji so 'RTT' aligns
        # vertically with 'IP', 'Country', 'ASN' on their lines.
        lines.append(f"⏱️ RTT:       {rtt}ms (server-side handling)")

    return "\n".join(lines)


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/")
    @router.get("/api/v1/")
    async def aggregated(request: Request) -> Response:
        data = await _collect(request)
        return respond(
            request,
            templates=templates,
            html_template="index.html",
            html_context={"data": data},
            json_data=data,
            text=_text_aggregated(data),
        )

    return router
