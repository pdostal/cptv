from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.config import get_base_domain
from cptv.negotiation import add_public_cors, respond
from cptv.services import protocol as protocol_service

router = APIRouter()


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/protocol")
    @router.get("/api/v1/protocol")
    def protocol(request: Request) -> Response:
        info = protocol_service.from_request(request)
        base = get_base_domain(request)
        endpoints = protocol_service.endpoints_for(base)

        json_data: dict[str, Any] = {
            "http_version": info.http_version,
            "tls_version": info.tls_version,
            "tls_cipher": info.tls_cipher,
            "alpn": info.alpn,
            "is_encrypted": info.is_encrypted,
            "endpoints": [{"name": e.name, "url": e.url, "alpn": e.alpn} for e in endpoints],
        }

        # Tab-separated single line so shell scripts can `cut -f1` etc.
        # Empty fields are emitted as "-" to keep column count stable.
        text = "\t".join(
            [
                info.http_version,
                info.tls_version or "-",
                info.alpn or "-",
                "encrypted" if info.is_encrypted else "plain",
            ]
        )

        return add_public_cors(
            respond(
                request,
                templates=templates,
                html_template="section_stub.html",
                html_context={
                    "heading": "Connection Protocol",
                    "data": json_data,
                    "present": True,
                },
                json_data=json_data,
                text=text,
            )
        )

    return router
