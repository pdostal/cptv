from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

Format = str  # "html" | "json" | "text"

_QUERY_PARAM = "format"
_ALLOWED_QUERY_VALUES = {"json", "text", "html"}


def choose_format(request: Request, *, html_available: bool) -> Format:
    query = request.query_params.get(_QUERY_PARAM, "").lower().strip()
    if query in _ALLOWED_QUERY_VALUES:
        return query

    accept = request.headers.get("accept", "").lower()
    if "application/json" in accept:
        return "json"
    if "text/plain" in accept and "text/html" not in accept:
        return "text"
    # Explicit Accept: text/html beats UA sniffing so `curl -H 'Accept: text/html'` works.
    if html_available and "text/html" in accept:
        return "html"

    user_agent = request.headers.get("user-agent", "").lower()
    if user_agent.startswith("curl/"):
        return "text"

    if html_available and accept in {"", "*/*"}:
        return "html"

    return "json"


def respond(
    request: Request,
    *,
    templates: Jinja2Templates | None = None,
    html_template: str | None = None,
    html_context: Mapping[str, Any] | None = None,
    json_data: Any = None,
    text: str | None = None,
) -> Response:
    html_available = html_template is not None and templates is not None
    fmt = choose_format(request, html_available=html_available)

    if fmt == "html" and html_available:
        context = {"request": request, **(html_context or {})}
        return templates.TemplateResponse(request, html_template, context)  # type: ignore[arg-type, union-attr]

    if fmt == "text" and text is not None:
        return PlainTextResponse(text)

    if fmt == "json" and json_data is not None:
        return JSONResponse(json_data)

    # Fallbacks when the requested format has no payload.
    if json_data is not None:
        return JSONResponse(json_data)
    if text is not None:
        return PlainTextResponse(text)
    if html_available:
        context = {"request": request, **(html_context or {})}
        return templates.TemplateResponse(request, html_template, context)  # type: ignore[arg-type, union-attr]

    return HTMLResponse("", status_code=204)


def add_public_cors(response: Response) -> Response:
    """Mark a response as safe for cross-origin reads from anywhere.

    Used by the IP echo endpoints (/ip, /ipv4, /ipv6 and aliases) so the
    home page's dual-stack probe — which runs at ``cptv.cz`` and fetches
    ``ipv4.cptv.cz`` / ``ipv6.cptv.cz`` — can actually read the body.

    These endpoints are public, idempotent, contain no secrets, and
    accept no credentials, so wildcard CORS is safe. We deliberately
    do NOT apply this globally; other endpoints stay browser-same-origin.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "no-store"
    return response
