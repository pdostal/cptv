from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from cptv.negotiation import respond
from cptv.services import ip as ip_service
from cptv.services.traceroute import (
    TracerouteError,
    TracerouteRateLimitedError,
    format_json,
    format_text,
    run_mtr_cached,
)

log = logging.getLogger(__name__)

router = APIRouter()


def _error_response(
    request: Request,
    templates: Jinja2Templates,
    message: str,
    status_code: int = 200,
) -> Response:
    """Return an error in the format appropriate for the request path."""
    path = request.url.path
    if path.endswith(".json"):
        return JSONResponse({"error": message}, status_code=status_code)
    if path.endswith(".txt"):
        return PlainTextResponse(f"error: {message}\n", status_code=status_code)
    return respond(
        request,
        templates=templates,
        html_template="section_stub.html",
        html_context={"heading": "Traceroute", "present": False, "data": {}},
        json_data={"error": message},
        text=f"error: {message}\n",
    )


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/traceroute")
    @router.get("/traceroute.json")
    @router.get("/traceroute.txt")
    @router.get("/api/v1/traceroute")
    @router.get("/api/v1/traceroute.json")
    @router.get("/api/v1/traceroute.txt")
    async def traceroute(request: Request) -> Response:
        address = ip_service.client_ip(request)
        if address is None:
            return _error_response(request, templates, "Could not determine client IP.")

        try:
            result, meta = await run_mtr_cached(address)
        except TracerouteRateLimitedError:
            return _error_response(
                request,
                templates,
                "Traceroute already in progress for this IP. Please try again shortly.",
                status_code=429,
            )
        except TracerouteError:
            log.exception("traceroute failed for %s", address)
            return _error_response(
                request,
                templates,
                "Traceroute failed. The mtr binary may be missing or lack capabilities.",
            )

        json_data = format_json(result)
        text = format_text(result)

        # Force format based on URL suffix.
        path = request.url.path
        if path.endswith(".json"):
            resp = JSONResponse(json_data)
        elif path.endswith(".txt"):
            resp = PlainTextResponse(text)
        else:
            resp = respond(
                request,
                templates=templates,
                html_template="traceroute.html",
                html_context={
                    "heading": "Traceroute",
                    "result": result,
                },
                json_data=json_data,
                text=text,
            )

        # Custom headers per spec.
        resp.headers["X-Traceroute-Cached"] = str(meta.cached).lower()
        resp.headers["X-Traceroute-Cache-Age"] = str(meta.cache_age)
        resp.headers["X-Traceroute-Refreshes-In"] = str(meta.refreshes_in)
        return resp

    return router
