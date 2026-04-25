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


class TracerouteUnreachableError(TracerouteError):
    """Raised when the host has no route to the target IP family."""


def _family_flag(target: IPAddress) -> str:
    """Return ``-4`` or ``-6`` so mtr commits to one family.

    Without this, mtr-packet does an internal UDP ``connect()`` to discover
    the source address, which fails with ``Network is unreachable`` (and a
    very confusing error message) when the kernel has no route to the
    target's family. Forcing ``-4`` / ``-6`` lets mtr fail cleanly and lets
    us recognise the failure shape in :func:`_translate_mtr_error`.
    """
    return "-6" if isinstance(target, ipaddress.IPv6Address) else "-4"


def _translate_mtr_error(stderr: str, target: IPAddress) -> Exception:
    """Convert an mtr stderr blob into the most informative exception.

    Specifically, ``Network is unreachable`` from the kernel via mtr-packet
    means there is no route to ``target``'s family from this host. Surface
    that as :class:`TracerouteUnreachableError` so callers (and the SSE
    stream) can render a friendly message instead of a stack trace.
    """
    lower = stderr.lower()
    family = "IPv6" if isinstance(target, ipaddress.IPv6Address) else "IPv4"
    if "network is unreachable" in lower or "no route to host" in lower:
        return TracerouteUnreachableError(
            f"no {family} route to {target} from this host (check container networking)"
        )
    return TracerouteError(f"mtr failed: {stderr.strip()}")


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
        # mtr defaults to ICMP echo (no flag exposes that; the absence of
        # -u / -T is the way). The explicit -4 / -6 flag stops mtr-packet
        # from probing the wrong family during source-address discovery,
        # which is the failure mode behind "udp socket connect failed:
        # Network is unreachable" on hosts missing a route for one stack.
        cmd = [
            settings.mtr_path,
            "--json",
            "--report",
            "--no-dns",
            "--mpls",
            _family_flag(target),
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
            raise _translate_mtr_error(err, target)

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


# Delay between progressive hop emissions when *replaying* from cache.
# Small enough to feel live, large enough to avoid hammering the client.
_STREAM_HOP_GAP = 0.12


async def _stream_mtr_raw_lines(target: IPAddress) -> AsyncIterator[bytes]:
    """Spawn ``mtr --raw`` and yield each stdout line as it arrives.

    Subject to the global concurrency cap (acquires a slot up-front and
    releases when the subprocess exits or the consumer stops iterating).
    """
    settings = get_settings()
    target_str = str(target)
    cmd = [
        settings.mtr_path,
        "--raw",
        "--no-dns",
        "--mpls",
        _family_flag(target),
        "-c",
        str(settings.mtr_count),
        target_str,
    ]

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

    proc: asyncio.subprocess.Process | None = None
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            msg = f"mtr binary not found at '{settings.mtr_path}'"
            raise TracerouteError(msg) from exc
        except OSError as exc:
            msg = f"failed to run mtr: {exc}"
            raise TracerouteError(msg) from exc

        assert proc.stdout is not None  # noqa: S101
        try:
            async with asyncio.timeout(_SUBPROCESS_TIMEOUT):
                async for line in proc.stdout:
                    yield line
        except TimeoutError as exc:
            msg = f"mtr timed out after {_SUBPROCESS_TIMEOUT}s for {target_str}"
            raise TracerouteError(msg) from exc

        await proc.wait()
        if proc.returncode not in (0, None):
            err = b""
            if proc.stderr is not None:
                err = await proc.stderr.read()
            raise _translate_mtr_error(err.decode(errors="replace"), target)
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        sem.release()


@dataclass
class _LiveHopState:
    """Mutable per-hop accumulator while parsing raw mtr output."""

    pos: int
    ip: str | None = None
    sent: int = 0
    received: int = 0
    rtt_us_samples: list[int] = field(default_factory=list)
    mpls: list[MplsLabel] = field(default_factory=list)
    rdns: str | None = None
    asn: int | None = None
    asn_name: str | None = None
    asn_resolved: bool = False
    rdns_resolved: bool = False

    def to_hop(self) -> Hop:
        if not self.rtt_us_samples:
            avg = best = worst = None
        else:
            samples_ms = [v / 1000.0 for v in self.rtt_us_samples]
            avg = round(sum(samples_ms) / len(samples_ms), 2)
            best = round(min(samples_ms), 2)
            worst = round(max(samples_ms), 2)
        loss = 0.0 if self.sent == 0 else round(100.0 * (1 - self.received / self.sent), 1)
        return Hop(
            hop=self.pos,
            ip=self.ip,
            rdns=self.rdns,
            asn=self.asn,
            asn_name=self.asn_name,
            loss_pct=loss,
            avg_ms=avg,
            best_ms=best,
            worst_ms=worst,
            mpls=list(self.mpls),
        )


def _enrich_live_hop(state: _LiveHopState) -> None:
    """Populate rDNS + ASN on a hop state once an IP is known."""
    if state.ip is None:
        return
    if not state.rdns_resolved:
        state.rdns = _reverse_dns(state.ip)
        state.rdns_resolved = True
    if not state.asn_resolved:
        try:
            asn_result = asn_service.lookup(ipaddress.ip_address(state.ip))
        except ValueError:
            asn_result = None
        if asn_result is not None:
            state.asn = asn_result.number
            state.asn_name = asn_result.name
        state.asn_resolved = True


async def stream_mtr_live(target: IPAddress) -> AsyncIterator[StreamEvent]:
    """Stream true hop-by-hop traceroute progress from ``mtr --raw``.

    Emits a ``hop`` event the first time a hop is observed and every time
    its measurements update, so the UI can render rows the moment hops
    answer instead of waiting for the full report. Uses out-of-band swaps
    keyed by ``hop-{n}`` so duplicate emissions update the existing row.

    Caller is responsible for cache lookup / locking. The blocking
    :func:`run_mtr` path remains for JSON / text consumers.
    """
    states: dict[int, _LiveHopState] = {}

    async for raw_line in _stream_mtr_raw_lines(target):
        line = raw_line.decode(errors="replace").strip()
        if not line:
            continue
        parts = line.split()
        kind = parts[0]
        try:
            if kind == "h" and len(parts) >= 3:
                pos = int(parts[1]) + 1  # mtr raw is 0-indexed
                state = states.setdefault(pos, _LiveHopState(pos=pos))
                state.ip = parts[2]
                _enrich_live_hop(state)
                yield StreamEvent(event="hop", data=asdict(state.to_hop()))
            elif kind == "x" and len(parts) >= 3:
                pos = int(parts[1]) + 1
                state = states.setdefault(pos, _LiveHopState(pos=pos))
                state.sent += 1
                # No emission — sent count alone changes loss but is volatile.
            elif kind == "p" and len(parts) >= 4:
                pos = int(parts[1]) + 1
                state = states.setdefault(pos, _LiveHopState(pos=pos))
                state.received += 1
                state.rtt_us_samples.append(int(parts[2]))
                _enrich_live_hop(state)
                yield StreamEvent(event="hop", data=asdict(state.to_hop()))
            elif kind == "m" and len(parts) >= 6:
                pos = int(parts[1]) + 1
                state = states.setdefault(pos, _LiveHopState(pos=pos))
                state.mpls.append(
                    MplsLabel(
                        label=int(parts[2]),
                        tc=int(parts[3]),
                        s=int(parts[4]),
                        ttl=int(parts[5]),
                    )
                )
                if state.ip is not None:
                    yield StreamEvent(event="hop", data=asdict(state.to_hop()))
            # Other line types (d, t) ignored.
        except (ValueError, IndexError):
            log.debug("ignoring malformed mtr line: %r", line)
            continue

    # Final flush: emit any sent-but-no-reply hops with their final loss%.
    for pos in sorted(states):
        state = states[pos]
        if state.ip is None and state.sent > 0:
            yield StreamEvent(event="hop", data=asdict(state.to_hop()))


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
    nat = _nat_warning(address)
    yield StreamEvent(
        event="started",
        data={
            "target": str(address),
            "cached": False,
            "cache_age": 0,
            "refreshes_in": ttl,
            "nat_warning": nat,
        },
    )
    try:
        latest_hops: dict[int, Hop] = {}
        try:
            async for ev in stream_mtr_live(address):
                if ev.event == "hop":
                    latest_hops[ev.data["hop"]] = Hop(
                        hop=ev.data["hop"],
                        ip=ev.data.get("ip"),
                        rdns=ev.data.get("rdns"),
                        asn=ev.data.get("asn"),
                        asn_name=ev.data.get("asn_name"),
                        loss_pct=ev.data.get("loss_pct", 0.0),
                        avg_ms=ev.data.get("avg_ms"),
                        best_ms=ev.data.get("best_ms"),
                        worst_ms=ev.data.get("worst_ms"),
                        mpls=[MplsLabel(**m) for m in ev.data.get("mpls", [])],
                    )
                yield ev
        except Exception as exc:
            log.exception("traceroute stream failed for %s", address)
            yield StreamEvent(event="error", data={"error": str(exc)})
            return

        # Persist the final aggregated result to cache.
        result = TracerouteResult(
            target=str(address),
            ran_at=datetime.now(tz=UTC).isoformat(),
            hops=[latest_hops[pos] for pos in sorted(latest_hops)],
            nat_warning=nat,
        )
        if r is not None:
            try:
                serialized = json.dumps(asdict(result))
                await r.set(key, serialized, ex=ttl)
            except Exception:  # noqa: BLE001
                log.exception("failed to cache traceroute result for %s", address)

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
