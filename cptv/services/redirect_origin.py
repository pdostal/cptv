"""Captive-portal redirect origin detection (PLAN.md §4.8).

Captive portals typically redirect the visitor's browser to our site after
intercepting an outbound HTTP request. They do this either with a 302
redirect (browser carries a Referer) or by transparently proxying and
adding hints like ``X-Original-URL`` or ``X-Original-Host``.

This module inspects an incoming request's headers and returns a small
shape describing what the captive portal told us, if anything. It does
not store anything server-side.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Request

# Headers known to leak the original URL the visitor was trying to reach.
# Order matters — first match wins.
_ORIGIN_HEADERS: tuple[str, ...] = (
    "x-original-url",
    "x-original-host",
    "x-forwarded-host",
    "x-original-uri",
    "x-rewrite-url",
)


@dataclass(frozen=True)
class RedirectOrigin:
    """Information about how the visitor likely arrived at this page."""

    referrer: str | None
    referrer_host: str | None
    via_header: str | None  # name of the X-* header that revealed the origin
    original_url: str | None  # value of that header, if any
    looks_like_captive_portal: bool


def _safe_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url if "//" in url else f"//{url}")
    except ValueError:
        return None
    host = parsed.hostname
    return host.lower() if host else None


def detect(request: Request, own_host: str | None = None) -> RedirectOrigin:
    """Inspect headers and decide whether the visit looks portal-driven.

    ``own_host`` is the base domain the request landed on (so we can
    ignore self-referrers). When the referrer's host is not us *and*
    not empty, we treat the visit as portal-redirected.
    """
    headers = request.headers
    referrer = headers.get("referer") or headers.get("referrer")
    referrer_host = _safe_host(referrer)

    via_header: str | None = None
    original_url: str | None = None
    for name in _ORIGIN_HEADERS:
        value = headers.get(name)
        if value:
            via_header = name
            original_url = value
            break

    # Heuristic: a captive portal usually leaves a referrer that's not us
    # *or* drops one of the X-Original-* hints. Self-referrers (clicking
    # a link in the same site) are not portals.
    own = (own_host or "").lower().lstrip(".") or None
    referrer_external = bool(
        referrer_host and own and referrer_host != own and not referrer_host.endswith(f".{own}")
    )
    looks_like_portal = bool(via_header) or referrer_external

    return RedirectOrigin(
        referrer=referrer,
        referrer_host=referrer_host,
        via_header=via_header,
        original_url=original_url,
        looks_like_captive_portal=looks_like_portal,
    )
