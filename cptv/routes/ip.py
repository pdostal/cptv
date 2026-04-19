from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.negotiation import respond
from cptv.services import ip as ip_service

router = APIRouter()


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/ip")
    @router.get("/api/v1/ip")
    def current_ip(request: Request) -> Response:
        address = ip_service.client_ip(request)
        if address is None:
            return respond(
                request,
                templates=templates,
                html_template="ip.html",
                html_context={"heading": "Your IP", "value": None},
                json_data={"ip": None, "protocol": None},
                text="",
            )
        classified = ip_service.classify(address)
        return respond(
            request,
            templates=templates,
            html_template="ip.html",
            html_context={"heading": "Your IP", "value": classified.text},
            json_data={"ip": classified.text, "protocol": classified.protocol},
            text=classified.text,
        )

    def _single_stack(request: Request, version: int) -> Response:
        address = ip_service.client_ip(request)
        ip_cls = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address
        key = f"ipv{version}"
        value = str(address) if isinstance(address, ip_cls) else None
        heading = f"Your IPv{version}"
        return respond(
            request,
            templates=templates,
            html_template="ip.html",
            html_context={"heading": heading, "value": value},
            json_data={key: value},
            text=value or "",
        )

    @router.get("/ipv4")
    @router.get("/ip4")
    @router.get("/4")
    @router.get("/api/v1/ipv4")
    def ipv4(request: Request) -> Response:
        return _single_stack(request, 4)

    @router.get("/ipv6")
    @router.get("/ip6")
    @router.get("/6")
    @router.get("/api/v1/ipv6")
    def ipv6(request: Request) -> Response:
        return _single_stack(request, 6)

    return router
