from __future__ import annotations

from types import SimpleNamespace

import pytest

from cptv.services import protocol


def _fake_request(headers: dict[str, str], scope_http_version: str = "1.1", scheme: str = "http"):
    """Build a minimal Request stand-in for protocol.from_request().

    starlette.Request reads `headers`, `scope`, and `url.scheme`. We
    don't go through Starlette here because the service contract is
    just "give me something with those three attributes".
    """
    return SimpleNamespace(
        headers={k.lower(): v for k, v in headers.items()},
        scope={"http_version": scope_http_version},
        url=SimpleNamespace(scheme=scheme),
    )


# ---------- _normalise_version ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HTTP/1.1", "HTTP/1.1"),
        ("HTTP/2.0", "HTTP/2"),  # nginx $server_protocol uses HTTP/2.0
        ("HTTP/3.0", "HTTP/3"),
        ("HTTP/2", "HTTP/2"),
        ("1.1", "HTTP/1.1"),
        ("2", "HTTP/2"),
        ("2.0", "HTTP/2"),
        ("3", "HTTP/3"),
        ("3.0", "HTTP/3"),
        ("http/2.0", "HTTP/2"),  # case-insensitive
        ("", "HTTP/1.1"),  # empty → safe default
    ],
)
def test_normalise_version(raw: str, expected: str) -> None:
    assert protocol._normalise_version(raw) == expected


# ---------- from_request ----------


def test_from_request_full_headers() -> None:
    req = _fake_request(
        {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-HTTP-Version": "HTTP/2.0",
            "X-Forwarded-TLS-Version": "TLSv1.3",
            "X-Forwarded-TLS-Cipher": "TLS_AES_128_GCM_SHA256",
            "X-Forwarded-ALPN": "h2",
        },
        scheme="http",  # uvicorn-side, ignored when X-Forwarded-Proto is set
    )
    info = protocol.from_request(req)
    assert info.http_version == "HTTP/2"
    assert info.tls_version == "TLSv1.3"
    assert info.tls_cipher == "TLS_AES_128_GCM_SHA256"
    assert info.alpn == "h2"
    assert info.is_encrypted is True


def test_from_request_falls_back_to_scope_http_version() -> None:
    """No nginx in front (local dev) — read scope http_version."""
    req = _fake_request({}, scope_http_version="1.1", scheme="http")
    info = protocol.from_request(req)
    assert info.http_version == "HTTP/1.1"
    assert info.tls_version is None
    assert info.alpn is None
    assert info.is_encrypted is False


def test_from_request_plaintext_scheme() -> None:
    req = _fake_request(
        {"X-Forwarded-Proto": "http", "X-Forwarded-HTTP-Version": "HTTP/1.1"},
    )
    assert protocol.from_request(req).is_encrypted is False


def test_from_request_https_scheme_marks_encrypted() -> None:
    req = _fake_request(
        {"X-Forwarded-Proto": "https", "X-Forwarded-HTTP-Version": "HTTP/3.0"},
    )
    info = protocol.from_request(req)
    assert info.is_encrypted is True
    assert info.http_version == "HTTP/3"


def test_from_request_empty_header_values_treated_as_missing() -> None:
    req = _fake_request(
        {
            "X-Forwarded-HTTP-Version": "HTTP/2",
            "X-Forwarded-TLS-Version": "",
            "X-Forwarded-ALPN": "",
        },
    )
    info = protocol.from_request(req)
    assert info.tls_version is None
    assert info.alpn is None


# ---------- endpoints_for ----------


def test_endpoints_for_emits_three_https_urls() -> None:
    from urllib.parse import urlsplit

    eps = protocol.endpoints_for("example.com")
    assert len(eps) == 3
    names = [e.name for e in eps]
    assert names == ["HTTP/1.1", "HTTP/2", "HTTP/3"]
    alpn_tokens = [e.alpn for e in eps]
    assert alpn_tokens == ["http/1.1", "h2", "h3"]
    for ep in eps:
        parsed = urlsplit(ep.url)
        assert parsed.scheme == "https"
        assert parsed.path == "/protocol"
        # Match the exact host suffix, not a substring \u2014 silences
        # CodeQL's "incomplete URL substring sanitization" rule.
        assert parsed.hostname.endswith(".example.com")


def test_endpoints_for_uses_correct_subdomain_prefix() -> None:
    eps = protocol.endpoints_for("cptv.cz")
    urls = {e.alpn: e.url for e in eps}
    assert urls["http/1.1"] == "https://http1.cptv.cz/protocol"
    assert urls["h2"] == "https://http2.cptv.cz/protocol"
    assert urls["h3"] == "https://http3.cptv.cz/protocol"
