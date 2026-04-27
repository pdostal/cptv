from __future__ import annotations

import time

from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from cptv.config import BASE_DOMAIN_HEADER, get_settings

# Prefixes the middleware will tag on request.state.subdomain. ``secure`` is
# included so templates can brand the header even though we never rewrite
# the path for it.
SUBDOMAIN_PREFIXES = ("ipv4", "ipv6", "secure")
# Subset of SUBDOMAIN_PREFIXES whose root path "/" is rewritten to /<prefix>
# so curling ipv4.<domain> returns the bare IPv4 address. ``secure`` does
# NOT rewrite — it's a TLS-only mirror of the apex.
_REWRITE_PREFIXES = ("ipv4", "ipv6")

# Headers the app trusts only from upstream nginx. If anything other
# than a loopback peer sends them, we strip them so a malicious client
# cannot spoof TCP RTT/MSS readings into the page when the app is
# (mis)deployed without a reverse proxy. See cptv/services/timing.py
# for what each header carries; both X-Tcp-Mss and X-Tcp-Mss-Server are
# stripped because the parser accepts either name.
_TRUSTED_UPSTREAM_HEADERS = (
    "x-tcp-rtt-us",
    "x-tcp-rttvar-us",
    "x-tcp-mss",
    "x-tcp-mss-server",
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _strip_untrusted_headers(request: Request) -> None:
    """Remove upstream-only headers when the immediate peer isn't loopback.

    In production nginx is the only thing the app accepts traffic from,
    so any inbound ``X-Tcp-*`` header is legitimate. When the app is
    exposed directly (dev or misconfig), the peer is the visitor and
    those headers must not be trusted.
    """
    peer = request.client.host if request.client else None
    if peer in _LOOPBACK_HOSTS:
        return
    headers = MutableHeaders(scope=request.scope)
    for name in _TRUSTED_UPSTREAM_HEADERS:
        if name in headers:
            del headers[name]


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Stamps the request start time and exposes elapsed handling time.

    ``request.state.request_started_at`` is set as early as possible.
    Handlers can read it to populate ``timing.rtt_ms`` in their payload
    before rendering. After the handler returns, the final elapsed value
    is also written to the ``X-Response-Time-Ms`` response header for
    observability.

    Also strips spoofable upstream-only headers (``X-Tcp-Rtt-Us`` and
    friends) from non-loopback inbound requests; see
    ``_strip_untrusted_headers`` for the rationale.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        _strip_untrusted_headers(request)
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
