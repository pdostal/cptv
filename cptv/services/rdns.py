from __future__ import annotations

import logging

import dns.asyncresolver
import dns.exception
import dns.resolver
import dns.reversename

from cptv.config import get_settings
from cptv.services.ip import IPAddress
from cptv.services.valkey import get_valkey

log = logging.getLogger(__name__)

# Namespaced cache key prefix; mirrors cptv:mtr: in traceroute.py.
_CACHE_PREFIX = "cptv:rdns:"
# Empty string in cache means "looked up, no PTR" \u2014 negative caching so
# we don't hammer DNS for known-empty addresses.
_NEGATIVE_MARKER = ""
# Tight per-request timeout. PTR lookups happen on the request hot path
# for the current connection's IP, so we accept "no PTR" rather than
# stalling page render. 0.3 s catches healthy resolvers; misbehaving
# upstreams produce silent None.
_LOOKUP_TIMEOUT_S = 0.3


async def lookup(addr: IPAddress | None) -> str | None:
    """Reverse DNS (PTR) for an address. Cached in Valkey when available.

    Returns ``None`` for ``None`` input, failed lookups, NXDOMAIN, and
    timeouts. The caller should treat ``None`` uniformly as "no useful
    hostname".

    Private (RFC1918 / ULA) addresses are NOT skipped: self-hosted
    deployments routinely run a local resolver with PTR zones for the
    LAN, and the 0.3 s timeout caps damage from misbehaving upstream
    resolvers that might forward such queries.
    """
    if addr is None:
        return None

    key = f"{_CACHE_PREFIX}{addr}"
    r = await get_valkey()
    if r is not None:
        try:
            cached = await r.get(key)
        except Exception:  # noqa: BLE001 - any redis error => fall through to live lookup
            cached = None
        if cached is not None:
            # Negative cache hit (we deliberately stored "" earlier) maps
            # back to None; positive hit returns the cached hostname.
            return cached or None

    result = await _do_lookup(addr)

    if r is not None:
        ttl = get_settings().rdns_cache_ttl
        try:
            await r.set(key, result or _NEGATIVE_MARKER, ex=ttl)
        except Exception:  # noqa: BLE001 - cache write failures are non-fatal
            log.debug("rdns cache write failed for %s", addr, exc_info=True)

    return result


async def _do_lookup(addr: IPAddress) -> str | None:
    """Issue an async PTR query honouring /etc/resolv.conf.

    ``dns.asyncresolver.Resolver()`` reads the system resolver config by
    default, so this picks up systemd-resolved, /etc/resolv.conf, etc.
    """
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = _LOOKUP_TIMEOUT_S
    resolver.lifetime = _LOOKUP_TIMEOUT_S
    qname = dns.reversename.from_address(str(addr))
    try:
        answer = await resolver.resolve(qname, "PTR")
    except (
        dns.resolver.NXDOMAIN,
        dns.resolver.NoAnswer,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
        dns.exception.DNSException,
    ):
        return None
    except Exception:  # noqa: BLE001 - any unexpected resolver error => no PTR
        log.debug("rdns lookup raised unexpected error for %s", addr, exc_info=True)
        return None

    if not answer:
        return None
    # Trailing dot on the canonical name reads weirdly in HTML/text;
    # strip it so the rendered hostname looks like 'host.example.com'.
    return str(answer[0].target).rstrip(".")
