"""Tiny CORS-enabled probe endpoint used for client-side end-to-end timing.

The browser fires a handful of GETs against ``//ipv4.<base>/timing/echo``
and ``//ipv6.<base>/timing/echo`` and uses the
``PerformanceResourceTiming`` entries (minus the server-reported
``X-Response-Time-Ms``) to compute end-to-end network time per stack.

Body is intentionally minimal and byte-stable so probe sizes don't
fluctuate between samples. The endpoint is excluded from any heavy
work (no DB hits, no rDNS, no GeoIP) and is safe to hammer.

Visitor-side TCP RTT/RTTvar/MSS are read from upstream nginx headers
on the *probe* response itself (see :mod:`cptv.services.timing` and the
nginx configuration section in ``README.md``).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.negotiation import add_public_cors, respond

router = APIRouter()


def _register(_templates: Jinja2Templates) -> APIRouter:
    @router.get("/timing/echo")
    @router.get("/api/v1/timing/echo")
    def timing_echo(request: Request) -> Response:
        # Body is fixed bytes so consecutive probes are byte-stable.
        # No HTML — this endpoint is JS-/curl-only.
        return add_public_cors(
            respond(
                request,
                json_data={"ok": True},
                text="ok",
                text_hint=False,
            )
        )

    return router
