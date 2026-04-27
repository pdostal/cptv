"""Visitor TCP timing & MSS extraction from upstream nginx headers.

This app sits behind nginx, which terminates TCP. The Python ASGI handler
therefore never sees the visitor's real TCP socket — calling
``getsockopt(TCP_INFO)`` / ``getsockopt(TCP_MAXSEG)`` on the request
socket would return useless loopback values.

To surface real visitor-side TCP RTT, RTT variance, and Maximum Segment
Size on the page, nginx is expected to read ``TCP_INFO`` /
``TCP_MAXSEG`` on the visitor's socket and inject the following response
headers (microseconds for the time fields, bytes for MSS)::

    X-Tcp-Rtt-Us:    smoothed RTT estimate (tcpi_rtt)
    X-Tcp-Rttvar-Us: RTT variance estimate (tcpi_rttvar)
    X-Tcp-Mss:       advertised MSS (tcpi_advmss / TCP_MAXSEG)

When any of those headers are missing or malformed (e.g. local dev
without nginx, or nginx without the Lua/njs snippet), this module
returns ``None`` and the UI degrades gracefully — no TCP/MSS rows are
shown. See ``README.md`` for the nginx configuration.

Trust note: these headers are accepted unconditionally because the
``RequestTimingMiddleware`` and ``SubdomainMiddleware`` rely on the
deployment putting nginx in front. If the app is exposed directly to
the public internet, ``cptv/middleware.py`` strips the headers from
non-loopback inbound requests so a malicious client cannot spoof them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

HEADER_RTT_US = "X-Tcp-Rtt-Us"
HEADER_RTTVAR_US = "X-Tcp-Rttvar-Us"
HEADER_MSS = "X-Tcp-Mss"


@dataclass(frozen=True)
class TcpInfo:
    """Visitor-side TCP statistics derived from nginx-injected headers."""

    rtt_ms: float
    rttvar_ms: float
    mss_bytes: int


def _parse_positive_int(raw: str | None) -> int | None:
    """Parse a non-negative integer from an upstream-supplied header.

    Returns ``None`` for missing, blank, malformed, or negative values.
    Caps at 60_000_000 (60 s in microseconds, ~64 KiB in bytes) to keep
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
    """Build a :class:`TcpInfo` from the three upstream timing headers.

    All three headers (``X-Tcp-Rtt-Us``, ``X-Tcp-Rttvar-Us``,
    ``X-Tcp-Mss``) must be present and parse to non-negative integers.
    A missing or malformed value yields ``None`` so the UI hides the
    rows entirely rather than showing a partial / misleading reading.
    """
    rtt_us = _parse_positive_int(headers.get(HEADER_RTT_US))
    rttvar_us = _parse_positive_int(headers.get(HEADER_RTTVAR_US))
    mss = _parse_positive_int(headers.get(HEADER_MSS))
    if rtt_us is None or rttvar_us is None or mss is None:
        return None
    if mss == 0:
        # MSS of zero is meaningless; treat as missing data.
        return None
    return TcpInfo(
        rtt_ms=round(rtt_us / 1000.0, 1),
        rttvar_ms=round(rttvar_us / 1000.0, 1),
        mss_bytes=mss,
    )
