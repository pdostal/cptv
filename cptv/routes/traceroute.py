from __future__ import annotations

import ipaddress
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from cptv.negotiation import respond
from cptv.services import ip as ip_service
from cptv.services.traceroute import (
    TracerouteBusyError,
    TracerouteError,
    TracerouteRateLimitedError,
    TracerouteUnreachableError,
    format_json,
    format_text,
    run_mtr_cached,
    stream_mtr_cached,
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
        except TracerouteBusyError:
            return _error_response(
                request,
                templates,
                "Server is running too many traceroutes right now. Please try again shortly.",
                status_code=503,
            )
        except TracerouteUnreachableError:
            # The host has no route to this IP family. Common on /64-only
            # rootless pods that don't pass IPv6 through to the container.
            # Build the message from the (already-public) target family
            # rather than the exception args so CodeQL is happy that no
            # untrusted info reaches the client.
            family = "IPv6" if isinstance(address, ipaddress.IPv6Address) else "IPv4"
            return _error_response(
                request,
                templates,
                f"no {family} route to your address from this host",
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

    def _sse_html(event: str, html: str) -> str:
        """Frame an SSE event with an HTML data payload, escaping newlines."""
        # Per the SSE spec, newlines split the event into multiple data lines;
        # we collapse them so the htmx-ext-sse swap receives a single string.
        flat = html.replace("\r", "").replace("\n", "")
        return f"event: {event}\ndata: {flat}\n\n"

    def _render_hop(request: Request, hop_dict: dict) -> str:
        ctx = {"request": request, "hop": _HopShim(hop_dict)}
        return templates.get_template("_hop_row.html").render(ctx)

    @router.get("/traceroute/stream")
    @router.get("/api/v1/traceroute/stream")
    async def traceroute_stream(request: Request) -> Response:
        """Server-Sent Events stream of traceroute progress.

        Emits events:
          - ``started`` (once)  payload: HTML status banner
          - ``hop`` (one per hop)  payload: rendered <tr> for the hop
          - ``done`` (once)  payload: HTML status banner
          - ``error`` (on failure)  payload: HTML error message

        The HTML home page consumes this with the htmx-ext-sse extension
        for true progressive rendering. JSON consumers should keep using
        /traceroute or /traceroute.json.
        """
        address = ip_service.client_ip(request)
        log.info("traceroute SSE opened from %s host=%s", address, request.headers.get("host"))
        if address is None:

            async def _err():
                yield _sse_html(
                    "error",
                    "<p><mark>⚠️ Could not determine client IP.</mark></p>",
                )

            return StreamingResponse(
                _err(),
                media_type="text/event-stream",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        async def event_generator():
            async for ev in stream_mtr_cached(address):
                if ev.event == "started":
                    if ev.data.get("cached"):
                        banner = (
                            f"<p><small>🕐 Cached result, age "
                            f"{ev.data.get('cache_age', 0)}s · refreshes in "
                            f"{ev.data.get('refreshes_in', 0)}s</small></p>"
                        )
                    else:
                        banner = "<p><small>⚡ Live result — running mtr…</small></p>"
                    if ev.data.get("nat_warning"):
                        banner += f"<p><mark>⚠️ {ev.data['nat_warning']}</mark></p>"
                    yield _sse_html("status", banner)
                elif ev.event == "hop":
                    yield _sse_html("hop", _render_hop(request, ev.data))
                elif ev.event == "done":
                    yield _sse_html("done", "<p><small>✅ Trace complete.</small></p>")
                elif ev.event == "error":
                    msg = ev.data.get("error", "unknown error")
                    yield _sse_html("error", f"<p><mark>⚠️ {msg}</mark></p>")

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                # Prevent buffering by intermediaries (nginx in particular).
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                # The home page on https://secure.<base> opens this stream
                # against //ipv4.<base> and //ipv6.<base>; the browser
                # blocks cross-origin EventSource bodies without CORS.
                "Access-Control-Allow-Origin": "*",
            },
        )

    return router


class _HopShim:
    """Adapt a hop dict back to attribute access for the Jinja template."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getattr__(self, name: str):
        return self._data.get(name)
