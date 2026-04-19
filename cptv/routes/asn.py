from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.negotiation import respond
from cptv.services import asn as asn_service
from cptv.services import ip as ip_service

router = APIRouter()


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/asn")
    @router.get("/api/v1/asn")
    def asn(request: Request) -> Response:
        address = ip_service.client_ip(request)
        result = asn_service.lookup(address)

        json_data: dict[str, Any]
        if result is None:
            json_data = {
                "asn": None,
                "name": None,
                "prefix": None,
                "looking_glass": None,
            }
            text = "ASN information unavailable (private IP or GeoLite2 ASN DB missing)"
        else:
            json_data = {
                "asn": result.number,
                "name": result.name,
                "prefix": result.prefix,
                "looking_glass": result.looking_glass,
            }
            text = "\n".join(
                [
                    f"🔌 ASN:       AS{result.number}",
                    f"   Name:      {result.name or '—'}",
                    f"   Prefix:    {result.prefix or '—'}",
                    f"   Looking glass: {result.looking_glass}",
                ]
            )

        return respond(
            request,
            templates=templates,
            html_template="section_stub.html",
            html_context={"heading": "ASN", "data": json_data, "present": result is not None},
            json_data=json_data,
            text=text,
        )

    @router.get("/isp")
    @router.get("/api/v1/isp")
    def isp(request: Request) -> Response:
        address = ip_service.client_ip(request)
        result = asn_service.lookup(address)

        json_data: dict[str, Any]
        if result is None:
            json_data = {"isp": None, "asn": None}
            text = "—"
        else:
            json_data = {"isp": result.name, "asn": result.number}
            text = f"{result.name or '?'} (AS{result.number})"

        return respond(
            request,
            templates=templates,
            html_template="section_stub.html",
            html_context={"heading": "ISP", "data": json_data, "present": result is not None},
            json_data=json_data,
            text=text,
        )

    return router
