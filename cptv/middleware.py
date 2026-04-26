from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from cptv.config import BASE_DOMAIN_HEADER, get_settings

# Prefixes the middleware will tag on request.state.subdomain. ``secure`` and
# the ``httpN`` family are included so templates can brand the header even
# though we never rewrite the path for them.
#
# The ``http1`` / ``http2`` / ``http3`` subdomains are dedicated probes for
# specific HTTP versions: nginx pins each to its protocol via ALPN /
# listener directives so curl --http2/--http3 can verify negotiation.
SUBDOMAIN_PREFIXES = ("ipv4", "ipv6", "secure", "http1", "http2", "http3")
# Subset of SUBDOMAIN_PREFIXES whose root path "/" is rewritten to /<prefix>
# so curling ipv4.<domain> returns the bare IPv4 address. ``secure`` and
# the ``httpN`` family do NOT rewrite — they serve the full home page.
_REWRITE_PREFIXES = ("ipv4", "ipv6")


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Stamps the request start time and exposes elapsed handling time.

    ``request.state.request_started_at`` is set as early as possible.
    Handlers can read it to populate ``timing.rtt_ms`` in their payload
    before rendering. After the handler returns, the final elapsed value
    is also written to the ``X-Response-Time-Ms`` response header for
    observability.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        start = time.perf_counter()
        request.state.request_started_at = start
        response: Response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
        return response


def elapsed_ms_so_far(request: Request) -> float | None:
    """Return ms elapsed since the request entered the timing middleware."""
    started = getattr(request.state, "request_started_at", None)
    if started is None:
        return None
    return (time.perf_counter() - started) * 1000.0


def detect_subdomain(host: str | None, base_domain: str) -> str | None:
    if not host:
        return None
    hostname = host.split(":", 1)[0].lower().rstrip(".")
    base = base_domain.lower().rstrip(".")
    for prefix in SUBDOMAIN_PREFIXES:
        expected = f"{prefix}.{base}"
        if hostname == expected:
            return prefix
    return None


class SubdomainMiddleware(BaseHTTPMiddleware):
    """Rewrites `/` to `/ipv4` or `/ipv6` when the request hits those subdomains.

    Keeps the subdomain tag on `request.state.subdomain` so route handlers
    can short-circuit to bare plain-text output when appropriate.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        base_domain = (
            (request.headers.get(BASE_DOMAIN_HEADER) or get_settings().base_domain_fallback)
            .strip()
            .lower()
        )
        subdomain = detect_subdomain(request.headers.get("host"), base_domain)
        request.state.subdomain = subdomain
        request.state.base_domain = base_domain

        if subdomain in _REWRITE_PREFIXES and request.scope["path"] == "/":
            request.scope["path"] = f"/{subdomain}"
            request.scope["raw_path"] = f"/{subdomain}".encode()

        response: Response = await call_next(request)
        return response
