from __future__ import annotations

from fastapi.testclient import TestClient

V4 = {"X-Forwarded-For": "203.0.113.42"}
CURL = {"User-Agent": "curl/8.0.0"}


# ---------- /protocol ----------


def test_protocol_json_default_local() -> None:
    """No nginx headers => fallback to scope http_version (HTTP/1.1).

    TestClient simulates HTTP/1.1, no TLS, no ALPN.
    """
    from fastapi.testclient import TestClient

    from cptv.main import create_app

    client = TestClient(create_app())
    r = client.get("/protocol", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["http_version"] == "HTTP/1.1"
    assert body["tls_version"] is None
    assert body["alpn"] is None
    assert body["is_encrypted"] is False
    # No 'endpoints' key \u2014 the per-protocol probe was removed in v0.3.0.
    assert "endpoints" not in body


def test_protocol_reads_nginx_headers(client: TestClient) -> None:
    r = client.get(
        "/protocol",
        headers={
            **V4,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-HTTP-Version": "HTTP/2.0",
            "X-Forwarded-TLS-Version": "TLSv1.3",
            "X-Forwarded-TLS-Cipher": "TLS_AES_128_GCM_SHA256",
            "X-Forwarded-ALPN": "h2",
            "Accept": "application/json",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["http_version"] == "HTTP/2"  # normalised
    assert body["tls_version"] == "TLSv1.3"
    assert body["tls_cipher"] == "TLS_AES_128_GCM_SHA256"
    assert body["alpn"] == "h2"
    assert body["is_encrypted"] is True


def test_protocol_text_is_tab_separated(client: TestClient) -> None:
    r = client.get(
        "/protocol",
        headers={
            **V4,
            **CURL,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-HTTP-Version": "HTTP/3.0",
            "X-Forwarded-TLS-Version": "TLSv1.3",
            "X-Forwarded-ALPN": "h3",
        },
    )
    assert r.status_code == 200
    # First line is the data; trailing newline added by text_hint logic
    # so we strip and split tabs.
    body = r.text.rstrip("\n").splitlines()[0]
    fields = body.split("\t")
    assert fields[0] == "HTTP/3"
    assert fields[1] == "TLSv1.3"
    assert fields[2] == "h3"
    assert fields[3] == "encrypted"


def test_protocol_text_uses_dashes_for_missing_fields(client: TestClient) -> None:
    """Local dev / no nginx => TLS info is missing; emit '-' to keep
    column count stable for shell `cut`."""
    r = client.get("/protocol", headers={**V4, **CURL})
    assert r.status_code == 200
    body = r.text.rstrip("\n").splitlines()[0]
    fields = body.split("\t")
    assert fields[0] == "HTTP/1.1"
    assert fields[1] == "-"
    assert fields[2] == "-"
    assert fields[3] == "plain"


def test_protocol_html_renders_via_section_stub(client: TestClient) -> None:
    r = client.get("/protocol", headers={**V4, "Accept": "text/html"})
    assert r.status_code == 200
    # section_stub.html dumps a <dl> of every key
    assert "Connection Protocol" in r.text or "http_version" in r.text


def test_protocol_has_public_cors(client: TestClient) -> None:
    """Wildcard CORS is kept even though no JS probe consumes it now \u2014
    the endpoint may still be hit cross-origin by user scripts and
    the response is public, idempotent, contains no secrets."""
    r = client.get("/protocol", headers={**V4, "Accept": "application/json"})
    assert r.headers.get("access-control-allow-origin") == "*"


def test_protocol_api_v1_alias(client: TestClient) -> None:
    r = client.get("/api/v1/protocol", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["http_version"].startswith("HTTP/")


# ---------- aggregated payload integration ----------


def test_aggregated_json_includes_protocol_block(client: TestClient) -> None:
    r = client.get("/", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert "protocol" in body
    assert "http_version" in body["protocol"]
    # No 'endpoints' key \u2014 dropped in v0.3.0 along with httpN. probes.
    assert "endpoints" not in body["protocol"]
    # Existing data["http"].version still present (compat alias).
    assert body["http"]["version"].startswith("HTTP/")


def test_aggregated_text_includes_protocol_line(client: TestClient) -> None:
    """Curl users see the negotiated protocol on every page."""
    r = client.get("/", headers={**V4, **CURL})
    assert r.status_code == 200
    assert "🔗 Protocol:" in r.text
    assert "HTTP/" in r.text


def test_aggregated_html_includes_protocol_section(client: TestClient) -> None:
    r = client.get("/", headers={**V4, "Accept": "text/html"})
    assert r.status_code == 200
    assert 'id="protocol-section"' in r.text
    # SSR 'Connected via ...' line is the entire body of the section now.
    assert "Connected via" in r.text
    # The per-protocol probe was removed in v0.3.0; the probe markers
    # MUST be gone so app.js (which doesn't query for them anymore)
    # stays consistent with the DOM.
    assert "protocol-list" not in r.text
    assert "protocol-https-note" not in r.text
    assert "data-protocol-probe" not in r.text
