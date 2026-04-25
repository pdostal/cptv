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

router = APIRouter()


def _collect(request: Request) -> dict:
    address = ip_service.client_ip(request)
    classified = ip_service.classify(address) if address is not None else None
    geo = geoip_service.lookup(address)
    asn = asn_service.lookup(address)

    current = classified.text if classified else None
    protocol = classified.protocol if classified else None
    ipv4 = current if classified and isinstance(address, ipaddress.IPv4Address) else None
    ipv6 = current if classified and isinstance(address, ipaddress.IPv6Address) else None

    domain = get_base_domain(request)
    resolver_q = request.query_params.get("resolver")
    resolver = dns_service.classify_resolver(resolver_q)

    http_version = request.scope.get("http_version") or "1.1"
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)

    elapsed = elapsed_ms_so_far(request)
    rtt_ms = round(elapsed, 1) if elapsed is not None else None

    return {
        "ip": {
            "current": current,
            "protocol": protocol,
            "preferred": protocol,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "is_private": classified.is_private if classified else None,
            "is_cgnat": classified.is_cgnat if classified else None,
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
        "meta": {
            "server": domain,
            "repo": "https://github.com/pdostal/cptv",
        },
        "quick_links": [link.model_dump() for link in get_settings().quick_links],
    }


def _text_aggregated(data: dict) -> str:
    ip = data["ip"]
    geo = data["geoip"]
    asn = data["asn"]
    dns = data["dns"]
    http = data["http"]
    meta = data["meta"]

    lines: list[str] = []

    current = ip["current"] or "unknown"
    proto = ip["protocol"] or ""
    suffix = f"  ({proto}, preferred)" if proto else ""
    lines.append(f"🌐 IP:        {current}{suffix}")
    if ip["ipv4"] and ip["protocol"] != "IPv4":
        lines.append(f"   IPv4:      {ip['ipv4']}")
    if ip["ipv6"] and ip["protocol"] != "IPv6":
        lines.append(f"   IPv6:      {ip['ipv6']}")
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

    if dns["resolver_ip"]:
        lines.append(
            f"🔎 Resolver:  {dns['resolver_ip']}  ({dns['resolver_name'] or 'unknown operator'})"
        )
    else:
        lines.append("🔎 Resolver:  unknown (DNS probe not configured)")
    lines.append("🔐 DNSSEC:    unable to determine (requires browser)")
    lines.append("")

    lines.append(f"⏱️  Time:      {data['timing']['server_timestamp']}")
    rtt = data["timing"].get("rtt_ms")
    if rtt is not None:
        lines.append(f"   RTT:       {rtt}ms (server-side handling)")
    lines.append(f"   HTTP:      {http['version']}")
    lines.append(f"   Server:    {meta['server']}  ({meta['repo']})")

    return "\n".join(lines)


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/")
    @router.get("/api/v1/")
    def aggregated(request: Request) -> Response:
        data = _collect(request)
        return respond(
            request,
            templates=templates,
            html_template="index.html",
            html_context={"data": data},
            json_data=data,
            text=_text_aggregated(data),
        )

    @router.get("/details")
    @router.get("/more")
    @router.get("/api/v1/details")
    def details(request: Request) -> Response:
        data = _collect(request)
        data["request"] = {
            "headers": {
                k: v
                for k, v in request.headers.items()
                if k.lower() in {"accept", "accept-language", "host", "x-forwarded-proto"}
            },
            "client": request.client.host if request.client else None,
            "method": request.method,
            "path": request.url.path,
        }
        return respond(
            request,
            templates=templates,
            html_template="details.html",
            html_context={"data": data},
            json_data=data,
            text=_text_aggregated(data)
            + "\n\n(see /api/v1/details for JSON with extended request fields)",
        )

    return router
