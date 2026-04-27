"""Visitor TCP timing & MSS extraction from upstream nginx headers.

This app sits behind nginx, which terminates TCP. The Python ASGI handler
therefore never sees the visitor's real TCP socket — calling
``getsockopt(TCP_INFO)`` / ``getsockopt(TCP_MAXSEG)`` on the request
socket would return useless loopback values.

To surface real visitor-side TCP RTT, RTT variance, and Maximum Segment
Size on the page, nginx is expected to inject the following response
headers (microseconds for the time fields, bytes for MSS)::

    X-Tcp-Rtt-Us:     smoothed RTT estimate (tcpi_rtt)
    X-Tcp-Rttvar-Us:  RTT variance estimate (tcpi_rttvar)
    X-Tcp-Mss-Server: server-side advertised MSS (TCP_MAXSEG via Lua FFI)
    X-Tcp-Mss:        legacy alias for the same MSS value

RTT and RTTvar work on stock distro nginx via two ``proxy_set_header``
lines pointing at the built-in ``$tcpinfo_rtt`` / ``$tcpinfo_rttvar``
variables. MSS requires OpenResty (or another lua-nginx-module nginx)
because no built-in variable exposes ``tcpi_advmss`` — see ``README.md``
for the OpenResty FFI snippet that calls ``getsockopt(TCP_MAXSEG)``.

When MSS is missing, RTT/RTTvar are still surfaced; the MSS row in the
Timing card simply renders ``—``. When *all* timing headers are absent
(local dev without nginx), the parser returns ``None`` and no per-stack
TCP rows render.

Trust note: these headers are stripped from non-loopback inbound
requests by ``RequestTimingMiddleware`` so a malicious client can't
spoof them when uvicorn is exposed directly without a reverse proxy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

HEADER_RTT_US = "X-Tcp-Rtt-Us"
HEADER_RTTVAR_US = "X-Tcp-Rttvar-Us"
# Two header names accepted for MSS. Server-side is preferred when both
# are present because that's what the README's OpenResty snippet sets;
# X-Tcp-Mss is kept as the legacy alias from the original Lua draft.
HEADER_MSS_SERVER = "X-Tcp-Mss-Server"
HEADER_MSS = "X-Tcp-Mss"

# Headers stripped from non-loopback inbound requests (see middleware).
TRUSTED_TIMING_HEADERS = (
    HEADER_RTT_US,
    HEADER_RTTVAR_US,
    HEADER_MSS_SERVER,
    HEADER_MSS,
)


@dataclass(frozen=True)
class TcpInfo:
    """Visitor-side TCP statistics derived from nginx-injected headers.

    ``mss_bytes`` is ``None`` when the upstream did not provide MSS
    (RTT/RTTvar work on stock nginx; MSS needs OpenResty + Lua FFI).
    """

    rtt_ms: float
    rttvar_ms: float
    mss_bytes: int | None


def _parse_positive_int(raw: str | None) -> int | None:
    """Parse a non-negative integer from an upstream-supplied header.

    Returns ``None`` for missing, blank, malformed, or negative values.
    Caps at 60_000_000 (60 s in microseconds, ~57 MiB in bytes) to keep
    obviously bogus numbers from leaking into the JSON / template.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    if value < 0 or value > 60_000_000:
        return None
    return value


def parse_tcp_info_headers(headers: Mapping[str, str]) -> TcpInfo | None:
    """Build a :class:`TcpInfo` from the upstream timing headers.

    RTT and RTTvar are required; MSS is optional. When MSS is absent
    or malformed the resulting :class:`TcpInfo` has ``mss_bytes=None``
    and the UI renders ``—`` for the MSS row. When RTT or RTTvar are
    missing the function returns ``None`` and no per-stack TCP rows
    render at all.
    """
    rtt_us = _parse_positive_int(headers.get(HEADER_RTT_US))
    rttvar_us = _parse_positive_int(headers.get(HEADER_RTTVAR_US))
    if rtt_us is None or rttvar_us is None:
        return None

    # Prefer the server-side MSS header when both are present; fall back
    # to the legacy alias. mss=0 is meaningless and treated as missing.
    mss = _parse_positive_int(headers.get(HEADER_MSS_SERVER) or headers.get(HEADER_MSS))
    if mss == 0:
        mss = None

    return TcpInfo(
        rtt_ms=round(rtt_us / 1000.0, 1),
        rttvar_ms=round(rttvar_us / 1000.0, 1),
        mss_bytes=mss,
    )
