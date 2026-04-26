from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.negotiation import add_public_cors, respond
from cptv.services import ip as ip_service
from cptv.services import rdns as rdns_service

router = APIRouter()


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/rdns")
    @router.get("/rdns/")
    @router.get("/rdns/{ip}")
    @router.get("/api/v1/rdns")
    @router.get("/api/v1/rdns/")
    @router.get("/api/v1/rdns/{ip}")
    async def rdns(request: Request, ip: str | None = None) -> Response:
        # No path arg => resolve the caller's own IP. This matches the
        # convention of /ip, /geoip, /asn, /protocol, etc.
        if ip is None:
            addr = ip_service.client_ip(request)
            if addr is None:
                raise HTTPException(status_code=400, detail="cannot determine client ip")
        else:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid ip") from exc

        hostname = await rdns_service.lookup(addr)

        json_data: dict[str, Any] = {"ip": str(addr), "hostname": hostname}
        # Em dash for "no PTR" so shell users get a stable single token
        # they can detect (mirrors the /isp text shape).
        text = hostname if hostname else "\u2014"

        return add_public_cors(
            respond(
                request,
                templates=templates,
                html_template="section_stub.html",
                html_context={
                    "heading": "Reverse DNS",
                    "data": json_data,
                    "present": True,
                },
                json_data=json_data,
                text=text,
            )
        )

    return router
