"""MTR wrapper with per-hop enrichment (reverse DNS + ASN) and Valkey caching."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from cptv.config import get_settings
from cptv.services import asn as asn_service
from cptv.services.ip import CGNAT_NETWORK, IPAddress
from cptv.services.valkey import get_valkey

log = logging.getLogger(__name__)

# Redis key prefix for traceroute cache.
_CACHE_PREFIX = "cptv:mtr:"
# Redis key prefix for in-progress lock.
_LOCK_PREFIX = "cptv:mtr:lock:"

# Maximum allowed hops (safety bound).
_MAX_HOPS = 30
# Subprocess timeout in seconds.
_SUBPROCESS_TIMEOUT = 60


@dataclass(frozen=True)
class MplsLabel:
    label: int
    tc: int
    s: int
    ttl: int


@dataclass(frozen=True)
class Hop:
    hop: int
    ip: str | None
    rdns: str | None = None
    asn: int | None = None
    asn_name: str | None = None
    loss_pct: float = 0.0
    avg_ms: float | None = None
    best_ms: float | None = None
    worst_ms: float | None = None
    mpls: list[MplsLabel] = field(default_factory=list)


@dataclass
class TracerouteResult:
    target: str
    ran_at: str
    hops: list[Hop]
    cached: bool = False
    nat_warning: str | None = None


class TracerouteError(Exception):
    """Raised when mtr execution fails."""


class TracerouteBusyError(Exception):
    """Raised when the global concurrency cap is saturated and the wait timed out."""


# Lazily-initialised process-wide semaphore. Created on first use so the
# cap value can be read from settings (which may be overridden in tests).
_global_semaphore: asyncio.Semaphore | None = None
_global_semaphore_size: int = 0


def _get_semaphore() -> asyncio.Semaphore:
    global _global_semaphore, _global_semaphore_size
    cap = get_settings().traceroute_max_concurrency
    if _global_semaphore is None or _global_semaphore_size != cap:
        _global_semaphore = asyncio.Semaphore(cap)
        _global_semaphore_size = cap
    return _global_semaphore


def reset_concurrency_cap() -> None:
    """Drop the cached semaphore. Used by tests after settings overrides."""
    global _global_semaphore, _global_semaphore_size
    _global_semaphore = None
    _global_semaphore_size = 0


def _reverse_dns(ip_str: str) -> str | None:
    """Best-effort reverse DNS lookup (PTR)."""
    try:
        hostname, _, _ = socket.gethostbyaddr(ip_str)
        return hostname
    except (socket.herror, socket.gaierror, OSError):
        return None


def _enrich_hop(hop_num: int, hub: dict) -> Hop:
    """Convert a single mtr JSON hub entry into an enriched Hop."""
    ip_str: str | None = hub.get("host")
    if ip_str == "???" or not ip_str:
        return Hop(hop=hop_num, ip=None, loss_pct=hub.get("Loss%", 100.0))

    # Reverse DNS
    rdns = _reverse_dns(ip_str)

    # ASN enrichment
    asn_num: int | None = None
    asn_name: str | None = None
    try:
        addr = ipaddress.ip_address(ip_str)
        asn_result = asn_service.lookup(addr)
        if asn_result:
            asn_num = asn_result.number
            asn_name = asn_result.name
    except ValueError:
        pass

    # MPLS labels
    mpls_labels: list[MplsLabel] = []
    for m in hub.get("Mpls", []):
        mpls_labels.append(
            MplsLabel(
                label=m.get("label", 0),
                tc=m.get("tc", 0),
                s=m.get("s", 0),
                ttl=m.get("ttl", 0),
            )
        )

    return Hop(
        hop=hop_num,
        ip=ip_str,
        rdns=rdns,
        asn=asn_num,
        asn_name=asn_name,
        loss_pct=hub.get("Loss%", 0.0),
        avg_ms=hub.get("Avg"),
        best_ms=hub.get("Best"),
        worst_ms=hub.get("Wrst"),
        mpls=mpls_labels,
    )


def _nat_warning(address: IPAddress) -> str | None:
    """Return a warning string if the target is RFC1918 or CGNAT."""
    if address.is_private:
        return (
            "Your IP is in a private (RFC 1918) range. "
            "The trace runs but may not reach the public internet."
        )
    if isinstance(address, ipaddress.IPv4Address) and address in CGNAT_NETWORK:
        return (
            "Your IP is in the CGNAT range (100.64.0.0/10). "
            "The trace runs but intermediate hops belong to your ISP's NAT."
        )
    return None


async def run_mtr(target: IPAddress) -> TracerouteResult:
    """Execute mtr and return enriched results.

    Runs ``mtr --json --report --no-dns --mpls -c <count> <target>``
    as a subprocess, then enriches each hop with reverse DNS and ASN data.

    Subject to the process-wide concurrency cap (see ``_get_semaphore``).
    Raises :class:`TracerouteBusyError` if no slot becomes available before
    the configured wait window.
    """
    settings = get_settings()
    target_str = str(target)

    sem = _get_semaphore()
    wait = settings.traceroute_concurrency_wait_seconds
    try:
        await asyncio.wait_for(sem.acquire(), timeout=wait if wait > 0 else None)
    except TimeoutError as exc:
        msg = (
            f"too many concurrent traceroutes (cap={settings.traceroute_max_concurrency}); "
            f"waited {wait}s with no slot"
        )
        raise TracerouteBusyError(msg) from exc

    try:
        cmd = [
            settings.mtr_path,
            "--json",
            "--report",
            "--no-dns",
            "--mpls",
            "-c",
            str(settings.mtr_count),
            target_str,
        ]

        log.info("running mtr: %s", " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SUBPROCESS_TIMEOUT)
        except TimeoutError as exc:
            msg = f"mtr timed out after {_SUBPROCESS_TIMEOUT}s for {target_str}"
            raise TracerouteError(msg) from exc
        except FileNotFoundError as exc:
            msg = f"mtr binary not found at '{settings.mtr_path}'"
            raise TracerouteError(msg) from exc
        except OSError as exc:
            msg = f"failed to run mtr: {exc}"
            raise TracerouteError(msg) from exc

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            msg = f"mtr exited {proc.returncode}: {err}"
            raise TracerouteError(msg)

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            msg = "mtr produced invalid JSON"
            raise TracerouteError(msg) from exc

        # mtr JSON structure: { "report": { "hubs": [...] } }
        hubs = data.get("report", {}).get("hubs", [])

        hops = [_enrich_hop(i + 1, hub) for i, hub in enumerate(hubs[:_MAX_HOPS])]

        return TracerouteResult(
            target=target_str,
            ran_at=datetime.now(tz=UTC).isoformat(),
            hops=hops,
            nat_warning=_nat_warning(target),
        )
    finally:
        sem.release()


def cache_key(address: IPAddress) -> str:
    """Build the Redis cache key for a client address.

    IPv4: keyed on the full address.
    IPv6: keyed on the /64 prefix.
    """
    if isinstance(address, ipaddress.IPv6Address):
        network = ipaddress.IPv6Network(f"{address}/64", strict=False)
        return f"{_CACHE_PREFIX}{network.network_address}"
    return f"{_CACHE_PREFIX}{address}"


@dataclass
class CachedMeta:
    """Metadata about a traceroute result's cache status."""

    cached: bool
    cache_age: int = 0
    refreshes_in: int = 0


async def run_mtr_cached(address: IPAddress) -> tuple[TracerouteResult, CachedMeta]:
    """Run mtr with Redis caching and rate limiting.

    Returns the result and cache metadata.  Falls back to a direct
    ``run_mtr`` call when Redis is unavailable.
    """
    settings = get_settings()
    ttl = settings.traceroute_cache_ttl
    key = cache_key(address)
    lock_key = f"{_LOCK_PREFIX}{key}"

    r = await get_valkey()
    if r is None:
        result = await run_mtr(address)
        return result, CachedMeta(cached=False, cache_age=0, refreshes_in=ttl)

    # Check cache first.
    cached_raw = await r.get(key)
    if cached_raw is not None:
        remaining = await r.ttl(key)
        remaining = max(remaining, 0)
        age = ttl - remaining
        data = json.loads(cached_raw)
        result = _deserialize_result(data)
        result.cached = True
        return result, CachedMeta(cached=True, cache_age=age, refreshes_in=remaining)

    # Check if another request is already running for this key.
    if await r.exists(lock_key):
        msg = "Traceroute already in progress for this IP. Please try again shortly."
        raise TracerouteRateLimitedError(msg)

    # Set lock (short TTL to auto-expire if the process crashes).
    await r.set(lock_key, "1", ex=_SUBPROCESS_TIMEOUT + 10)

    try:
        result = await run_mtr(address)
    except Exception:
        await r.delete(lock_key)
        raise

    # Store in cache.
    serialized = json.dumps(asdict(result))
    await r.set(key, serialized, ex=ttl)
    await r.delete(lock_key)

    return result, CachedMeta(cached=False, cache_age=0, refreshes_in=ttl)


def _deserialize_result(data: dict) -> TracerouteResult:
    """Reconstruct a TracerouteResult from a JSON-serialized dict."""
    hops = []
    for h in data.get("hops", []):
        mpls = [MplsLabel(**m) for m in h.pop("mpls", [])]
        hops.append(Hop(**h, mpls=mpls))
    return TracerouteResult(
        target=data["target"],
        ran_at=data["ran_at"],
        hops=hops,
        cached=data.get("cached", False),
        nat_warning=data.get("nat_warning"),
    )


class TracerouteRateLimitedError(Exception):
    """Raised when a traceroute is already in progress for this key."""


@dataclass(frozen=True)
class StreamEvent:
    """A single SSE event emitted by ``stream_mtr_cached``."""

    event: str  # "started" | "hop" | "done" | "error"
    data: dict


# Delay between progressive hop emissions in seconds. Small enough to feel
# live, large enough to avoid hammering the client.
_STREAM_HOP_GAP = 0.12


async def stream_mtr_cached(
    address: IPAddress,
) -> AsyncIterator[StreamEvent]:
    """Yield per-hop SSE events for a traceroute, using cache when possible.

    Emits, in order:
      * one ``started`` event with target + cache metadata
      * one ``hop`` event per hop with the enriched per-hop dict
      * one ``done`` event with cache metadata + final hop count

    On a cache hit we replay the cached hops with a small inter-hop delay
    so the UI still feels live. On miss we run mtr (blocking call), cache
    the result, then stream the hops out of the in-memory list.

    On rate-limit collision we emit an ``error`` event and stop.
    """
    settings = get_settings()
    ttl = settings.traceroute_cache_ttl
    key = cache_key(address)
    lock_key = f"{_LOCK_PREFIX}{key}"

    r = await get_valkey()

    # ---- cache hit: replay ----
    if r is not None:
        cached_raw = await r.get(key)
        if cached_raw is not None:
            remaining = await r.ttl(key)
            remaining = max(remaining, 0)
            age = ttl - remaining
            data = json.loads(cached_raw)
            result = _deserialize_result(data)
            result.cached = True
            yield StreamEvent(
                event="started",
                data={
                    "target": result.target,
                    "ran_at": result.ran_at,
                    "cached": True,
                    "cache_age": age,
                    "refreshes_in": remaining,
                    "nat_warning": result.nat_warning,
                },
            )
            for hop in result.hops:
                yield StreamEvent(event="hop", data=asdict(hop))
                await asyncio.sleep(_STREAM_HOP_GAP)
            yield StreamEvent(
                event="done",
                data={"cached": True, "cache_age": age, "refreshes_in": remaining},
            )
            return

        # ---- rate limit ----
        if await r.exists(lock_key):
            yield StreamEvent(
                event="error",
                data={"error": "Traceroute already in progress for this IP."},
            )
            return

        await r.set(lock_key, "1", ex=_SUBPROCESS_TIMEOUT + 10)

    # ---- live run ----
    try:
        # Announce start before the (potentially slow) mtr call so the UI
        # can flip into the loading state immediately.
        yield StreamEvent(
            event="started",
            data={
                "target": str(address),
                "cached": False,
                "cache_age": 0,
                "refreshes_in": ttl,
            },
        )
        try:
            result = await run_mtr(address)
        except Exception as exc:
            if r is not None:
                await r.delete(lock_key)
            log.exception("traceroute stream failed for %s", address)
            yield StreamEvent(event="error", data={"error": str(exc)})
            return

        # Persist to cache before streaming hops so subsequent requests hit it.
        if r is not None:
            serialized = json.dumps(asdict(result))
            await r.set(key, serialized, ex=ttl)
            await r.delete(lock_key)

        for hop in result.hops:
            yield StreamEvent(event="hop", data=asdict(hop))
            await asyncio.sleep(_STREAM_HOP_GAP)

        yield StreamEvent(
            event="done",
            data={"cached": False, "cache_age": 0, "refreshes_in": ttl},
        )
    finally:
        if r is not None:
            # Best-effort lock cleanup if the caller disconnects mid-stream.
            try:
                await r.delete(lock_key)
            except Exception:  # noqa: BLE001, S110
                # Lock will auto-expire via its TTL — losing it here is harmless.
                log.debug("stream_mtr_cached: lock cleanup failed", exc_info=True)


def format_text(result: TracerouteResult) -> str:
    """Render a TracerouteResult as plain text."""
    lines = [f"traceroute to {result.target} at {result.ran_at}", ""]
    if result.nat_warning:
        lines.append(f"WARNING: {result.nat_warning}")
        lines.append("")

    for hop in result.hops:
        if hop.ip is None:
            lines.append(f"{hop.hop:>3}.  * * *")
            continue

        parts = [f"{hop.hop:>3}.  {hop.ip}"]
        if hop.rdns:
            parts.append(f" {hop.rdns}")
        if hop.asn:
            name = f" {hop.asn_name}" if hop.asn_name else ""
            parts.append(f"  AS{hop.asn}{name}")
        parts.append(f"  {hop.loss_pct:.1f}% loss")
        if hop.avg_ms is not None:
            parts.append(f"  {hop.avg_ms:.1f}ms")
        for m in hop.mpls:
            parts.append(f"  MPLS:{m.label}/{m.tc}/{m.s}")
        lines.append("".join(parts))

    return "\n".join(lines) + "\n"


def format_json(result: TracerouteResult) -> dict:
    """Render a TracerouteResult as a JSON-serializable dict."""
    return asdict(result)
