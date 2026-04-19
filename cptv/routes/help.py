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
    /              Full info (auto-detects format)
    /ip            Current IP address
    /ipv4 /ip4 /4  IPv4 address only
    /ipv6 /ip6 /6  IPv6 address only
    /geoip         Geolocation
    /asn           ASN and network info
    /isp           ISP name
    /dns           DNS resolver info
    /details       Extended output
    /traceroute    Traceroute status
    /help          This help text
    /health        Service health check (JSON)

SUBDOMAINS
    ipv4.{domain}   Force IPv4  (also: curl ipv4.{domain})
    ipv6.{domain}   Force IPv6  (also: curl ipv6.{domain})
    secure.{domain} HTTPS only

FORMAT
    Append ?format=json or ?format=text to any endpoint.
    Or set Accept: application/json header.
    curl auto-detected — plain text returned by default.

EXAMPLES
    curl {domain}
    curl ipv4.{domain}
    curl {domain}/geoip
    curl {domain}/asn?format=json
    MY_IP=$(curl -s ipv4.{domain})
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
