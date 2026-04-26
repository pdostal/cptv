from __future__ import annotations

from dataclasses import dataclass

from starlette.requests import Request

# Headers nginx sets in the upstream proxy block. They carry the protocol
# nginx negotiated with the client, since uvicorn always sees HTTP/1.1
# from the loopback hop and can't observe HTTP/2 / HTTP/3 / TLS directly.
HTTP_VERSION_HEADER = "x-forwarded-http-version"  # $server_protocol
TLS_VERSION_HEADER = "x-forwarded-tls-version"  # $ssl_protocol
TLS_CIPHER_HEADER = "x-forwarded-tls-cipher"  # $ssl_cipher
ALPN_HEADER = "x-forwarded-alpn"  # $ssl_alpn_protocol


@dataclass(frozen=True)
class ProtocolEndpoint:
    """One per-protocol probe endpoint advertised to the client."""

    name: str  # human-readable, e.g. "HTTP/2"
    url: str  # full URL the JS probe / curl hits
    alpn: str  # ALPN token: "http/1.1", "h2", "h3"


@dataclass(frozen=True)
class ConnectionProtocol:
    http_version: str  # normalised: "HTTP/1.1" | "HTTP/2" | "HTTP/3"
    tls_version: str | None  # "TLSv1.3" | "TLSv1.2" | None for plain http
    tls_cipher: str | None  # e.g. "TLS_AES_128_GCM_SHA256"
    alpn: str | None  # "h2" | "h3" | "http/1.1" | None
    is_encrypted: bool


def _normalise_version(value: str) -> str:
    """Normalise nginx's $server_protocol or scope http_version into HTTP/x.

    Inputs we accept:
      * "HTTP/1.1" / "HTTP/2.0" / "HTTP/3.0"  (nginx $server_protocol)
      * "1.1" / "2" / "2.0" / "3" / "3.0"     (ASGI scope http_version)

    Outputs are collapsed to the family: "HTTP/1.1", "HTTP/2", "HTTP/3".
    """
    raw = value.strip()
    if not raw:
        return "HTTP/1.1"
    upper = raw.upper()
    if upper.startswith("HTTP/"):
        upper = upper[len("HTTP/") :]
    # Drop trailing ".0" so HTTP/2.0 and HTTP/3.0 collapse to HTTP/2 / HTTP/3.
    if upper.endswith(".0"):
        upper = upper[:-2]
    return f"HTTP/{upper}"


def from_request(request: Request) -> ConnectionProtocol:
    """Build a ConnectionProtocol from the active request.

    Reads the X-Forwarded-* headers nginx sets in the upstream proxy
    block. Falls back to the ASGI scope's http_version when running
    locally without nginx (in which case TLS info is unavailable).
    """
    headers = request.headers
    fwd_proto = headers.get("x-forwarded-proto", request.url.scheme)
    fwd_ver = headers.get(HTTP_VERSION_HEADER)
    if not fwd_ver:
        scope_ver = request.scope.get("http_version") or "1.1"
        fwd_ver = scope_ver
    return ConnectionProtocol(
        http_version=_normalise_version(fwd_ver),
        tls_version=headers.get(TLS_VERSION_HEADER) or None,
        tls_cipher=headers.get(TLS_CIPHER_HEADER) or None,
        alpn=headers.get(ALPN_HEADER) or None,
        is_encrypted=fwd_proto == "https",
    )


# Canonical list of per-protocol probe subdomains. Order matters: it
# determines column order in the capability table and the listing order
# in /help.
PROTOCOL_SUBDOMAINS: tuple[tuple[str, str, str], ...] = (
    ("http1", "HTTP/1.1", "http/1.1"),
    ("http2", "HTTP/2", "h2"),
    ("http3", "HTTP/3", "h3"),
)


def endpoints_for(base_domain: str) -> list[ProtocolEndpoint]:
    """Build the list of per-protocol probe endpoints for a base domain.

    The URL always uses https:// because nginx pins each httpN.<base> to
    HTTPS — HTTP/2 and HTTP/3 are TLS-only on the wire, and forcing
    HTTPS on http1.<base> too keeps the probe semantics symmetrical.
    """
    return [
        ProtocolEndpoint(
            name=name,
            url=f"https://{prefix}.{base_domain}/protocol",
            alpn=alpn,
        )
        for prefix, name, alpn in PROTOCOL_SUBDOMAINS
    ]
