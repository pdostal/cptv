from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from cptv.config import BASE_DOMAIN_HEADER, get_settings

SUBDOMAIN_PREFIXES = ("ipv4", "ipv6")


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

        if subdomain in SUBDOMAIN_PREFIXES and request.scope["path"] == "/":
            request.scope["path"] = f"/{subdomain}"
            request.scope["raw_path"] = f"/{subdomain}".encode()

        response: Response = await call_next(request)
        return response
