from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.negotiation import respond
from cptv.services import dns as dns_service

router = APIRouter()


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/dns")
    @router.get("/api/v1/dns")
    def dns(request: Request) -> Response:
        # Server-side detection of the visitor's resolver requires a DNS-side
        # probe (unique subdomain → authoritative logs). Not implemented here;
        # we expose the classifier so clients can pass a known resolver IP via
        # ?resolver=... for lookup.
        candidate = request.query_params.get("resolver") or None
        info = dns_service.classify_resolver(candidate)

        json_data = {
            "resolver_ip": info.resolver_ip,
            "resolver_name": info.resolver_name,
            "is_known_public": info.is_known_public,
        }
        if info.resolver_ip is None:
            text = "🔎 Resolver:  unknown (requires DNS-side probe)"
        else:
            text = "\n".join(
                [
                    f"🔎 Resolver:  {info.resolver_ip}",
                    f"   Known as:  {info.resolver_name or 'not a well-known public resolver'}",
                ]
            )

        return respond(
            request,
            templates=templates,
            html_template="section_stub.html",
            html_context={"heading": "DNS", "data": json_data, "present": True},
            json_data=json_data,
            text=text,
        )

    return router
