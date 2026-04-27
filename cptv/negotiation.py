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


_TEXT_HINT = "\n\n# tip: append ?format=json for JSON, or see /help"


def _with_hint(text: str, *, hint: bool) -> str:
    """Render the plain-text body with a trailing newline.

    Always appends the trailing newline (so zsh doesn't print a reverse-
    video '%' to mark the missing line break). When ``hint=True`` we also
    append the '# tip: …' line that points readers at JSON / /help.

    Shell capture stays clean either way: ``$()`` strips trailing
    newlines automatically, so ``MY_IP=$(curl ipv4.cptv.cz)`` still
    yields a bare IP even though the response now ends with '\\n'.
    """
    body = text.rstrip("\n")
    if hint:
        return f"{body}{_TEXT_HINT}\n"
    return f"{body}\n"


def respond(
    request: Request,
    *,
    templates: Jinja2Templates | None = None,
    html_template: str | None = None,
    html_context: Mapping[str, Any] | None = None,
    json_data: Any = None,
    text: str | None = None,
    text_hint: bool = True,
) -> Response:
    """Return the format the client asked for.

    ``text_hint`` controls whether plain-text responses get a trailing
    '# tip: append ?format=json …' comment line. Bare-IP echo endpoints
    (``/ipv4`` and friends) opt out by passing ``text_hint=False`` so
    ``MY_IP=$(curl -s ipv4.<domain>)`` keeps returning a clean IP.
    """
    html_available = html_template is not None and templates is not None
    fmt = choose_format(request, html_available=html_available)

    if fmt == "html" and html_available:
        context = {"request": request, **(html_context or {})}
        return templates.TemplateResponse(request, html_template, context)  # type: ignore[arg-type, union-attr]

    if fmt == "text" and text is not None:
        return PlainTextResponse(_with_hint(text, hint=text_hint))

    if fmt == "json" and json_data is not None:
        return JSONResponse(json_data)

    # Fallbacks when the requested format has no payload.
    if json_data is not None:
        return JSONResponse(json_data)
    if text is not None:
        return PlainTextResponse(_with_hint(text, hint=text_hint))
    if html_available:
        context = {"request": request, **(html_context or {})}
        return templates.TemplateResponse(request, html_template, context)  # type: ignore[arg-type, union-attr]

    return HTMLResponse("", status_code=204)


def add_public_cors(response: Response) -> Response:
    """Mark a response as safe for cross-origin reads from anywhere.

    Used by the IP echo endpoints (/ip, /ipv4, /ipv6 and aliases), the
    sectional endpoints (/asn, /isp, /geoip, /dns) and the SSE traceroute
    stream so the home page can probe the v4 and v6 stacks side by side
    via ``//ipv4.<base>/…`` and ``//ipv6.<base>/…``. Without this header
    the browser silently withholds cross-origin response bodies.

    These endpoints are public, idempotent, contain no secrets, and
    accept no credentials, so wildcard CORS is safe. We deliberately
    do NOT apply this globally; other endpoints stay browser-same-origin.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "no-store"
    # Expose timing-related headers so the home-page JS can read them
    # cross-origin from ipv4./ipv6. probes:
    #   * X-Response-Time-Ms: subtracted from the resource-timing total
    #     to isolate end-to-end network time per stack.
    #   * X-Tcp-{Rtt,Rttvar}-Us / X-Tcp-Mss: optional, injected by an
    #     nginx Lua/njs snippet from the visitor's TCP socket. See the
    #     "Nginx configuration" section of README.md.
    response.headers["Access-Control-Expose-Headers"] = (
        "X-Response-Time-Ms, X-Tcp-Rtt-Us, X-Tcp-Rttvar-Us, X-Tcp-Mss"
    )
    return response
