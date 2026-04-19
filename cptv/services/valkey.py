"""Valkey connection manager for traceroute cache and rate limiting.

Valkey is wire-compatible with Redis; the ``redis`` Python package is used
as the protocol client.
"""

from __future__ import annotations

import logging

import redis.asyncio as redis

from cptv.config import get_settings

log = logging.getLogger(__name__)

_pool: redis.Redis | None = None


def _build_url() -> str:
    """Build a redis:// URL from the configured host and port."""
    settings = get_settings()
    return f"redis://{settings.valkey_host}:{settings.valkey_port}/0"


async def get_valkey() -> redis.Redis | None:
    """Return a shared async Valkey client, or None if unavailable."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        return _pool
    url = _build_url()
    try:
        _pool = redis.from_url(url, decode_responses=True)
        await _pool.ping()
        log.info("connected to Valkey at %s", url)
        return _pool
    except (redis.ConnectionError, redis.RedisError, OSError) as exc:
        log.warning("Valkey unavailable, running without cache: %s", exc)
        _pool = None
        return None


async def close_valkey() -> None:
    """Close the Valkey connection pool."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def health_check() -> str:
    """Return 'ok' if Valkey responds to PING, 'error' otherwise."""
    try:
        r = await get_valkey()
        if r is None:
            return "error"
        await r.ping()
        return "ok"
    except (redis.RedisError, OSError):
        return "error"
