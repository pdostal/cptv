from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.config import get_base_domain
from cptv.negotiation import respond

router = APIRouter()


HELP_TEMPLATE = """\
cptv — CaPTiVe network diagnostics
https://github.com/pdostal/cptv

ENDPOINTS
    /                      Full aggregated info
                           (auto-detects HTML / JSON / text)
    /ip                    Current connection IP (bare value, scriptable)
    /ipv4 /ip4 /4          IPv4 only (bare value, scriptable)
    /ipv6 /ip6 /6          IPv6 only (bare value, scriptable)
    /geoip                 Country + city + coordinates
    /asn                   ASN, operator name, prefix, looking-glass URL
    /isp                   "<name> (AS<n>)" (bare value, scriptable)
    /dns                   DNS resolver classifier (?resolver=… optional)
    /protocol              Negotiated HTTP/TLS/ALPN for the current connection
    /traceroute            Full traceroute, blocking
    /traceroute.json       Same, JSON only
    /traceroute.txt        Same, plain text only
    /traceroute/stream     Server-Sent Events of hops as they arrive
    /help                  This text
    /health                Service health (always JSON)
    /docs /redoc           Auto-generated OpenAPI explorers
    /openapi.json          Raw OpenAPI schema

SUBDOMAINS
    {domain}                Apex (HTTP only — captive-portal-friendly)
    www.{domain}            Same as apex
    ipv4.{domain}           DNS A only — forces IPv4 (HTTP + HTTPS)
    ipv6.{domain}           DNS AAAA only — forces IPv6 (HTTP + HTTPS)
    secure.{domain}         TLS-enforced mirror of the apex (HTTPS only)
    http1.{domain}          HTTPS pinned to HTTP/1.1 only (ALPN http/1.1)
    http2.{domain}          HTTPS pinned to HTTP/2 (ALPN h2)
    http3.{domain}          HTTPS + HTTP/3 / QUIC (ALPN h3, UDP/443)

FORMAT NEGOTIATION
    Each endpoint speaks three formats. Pick one:
      ?format=text                       force plain text
      ?format=json                       force JSON
      ?format=html                       force HTML
      Accept: application/json header    JSON
      Accept: text/html header           HTML
      User-Agent: curl/*                 plain text (auto-detected)

EXAMPLES
    curl {domain}                        # aggregated text
    curl {domain}/?format=json           # aggregated JSON
    curl ipv4.{domain}                   # bare IPv4, no decoration
    curl ipv6.{domain}                   # bare IPv6, no decoration
    MY_IP=$(curl -s ipv4.{domain})       # capture in shell
    curl {domain}/geoip                  # one section, plain text
    curl {domain}/asn?format=json        # one section, JSON
    curl {domain}/traceroute.txt         # blocking traceroute
    curl -I {domain}/ip                  # response headers

    curl --http1.1 https://http1.{domain}/protocol   # verify HTTP/1.1
    curl --http2   https://http2.{domain}/protocol   # verify HTTP/2
    curl --http3   https://http3.{domain}/protocol   # verify HTTP/3 (QUIC)
    # /protocol prints: <HTTP/x>\\t<TLSvX.Y>\\t<alpn>\\t<encrypted|plain>
"""


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/help")
    @router.get("/api/v1/help")
    def help_page(request: Request) -> Response:
        domain = get_base_domain(request)
        text = HELP_TEMPLATE.format(domain=domain)
        return respond(
            request,
            templates=templates,
            html_template="help.html",
            html_context={"domain": domain},
            json_data={"help": text},
            text=text,
        )

    return router
