from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

V4 = {"X-Forwarded-For": "203.0.113.42"}
CURL = {"User-Agent": "curl/8.0.0"}


# ---------- /rdns/{ip} ----------


def test_rdns_json_returns_hostname_when_resolver_succeeds(client: TestClient) -> None:
    with patch(
        "cptv.routes.rdns.rdns_service.lookup",
        new=AsyncMock(return_value="host.example.com"),
    ):
        r = client.get("/rdns/1.1.1.1", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"ip": "1.1.1.1", "hostname": "host.example.com"}


def test_rdns_json_returns_null_when_no_ptr(client: TestClient) -> None:
    with patch("cptv.routes.rdns.rdns_service.lookup", new=AsyncMock(return_value=None)):
        r = client.get("/rdns/1.1.1.1", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"ip": "1.1.1.1", "hostname": None}


def test_rdns_text_emits_em_dash_when_no_ptr(client: TestClient) -> None:
    with patch("cptv.routes.rdns.rdns_service.lookup", new=AsyncMock(return_value=None)):
        r = client.get("/rdns/1.1.1.1", headers={**V4, **CURL})
    assert r.status_code == 200
    body = r.text.rstrip("\n").splitlines()[0]
    assert body == "\u2014"


def test_rdns_text_emits_hostname_on_success(client: TestClient) -> None:
    with patch(
        "cptv.routes.rdns.rdns_service.lookup", new=AsyncMock(return_value="one.one.one.one")
    ):
        r = client.get("/rdns/1.1.1.1", headers={**V4, **CURL})
    assert r.status_code == 200
    assert r.text.rstrip("\n").splitlines()[0] == "one.one.one.one"


def test_rdns_400_on_invalid_ip(client: TestClient) -> None:
    r = client.get("/rdns/not-an-ip", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 400


def test_rdns_handles_ipv6(client: TestClient) -> None:
    with patch("cptv.routes.rdns.rdns_service.lookup", new=AsyncMock(return_value="v6.example")):
        r = client.get("/rdns/2001:db8::1", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["hostname"] == "v6.example"
    # Address normalised by ipaddress.ip_address (collapses zeros).
    assert body["ip"] == "2001:db8::1"


def test_rdns_has_public_cors(client: TestClient) -> None:
    """The dual-stack JS probe fetches /rdns cross-origin from
    ipv4./ipv6.<base>; without wildcard CORS the browser silently
    drops the response body."""
    with patch("cptv.routes.rdns.rdns_service.lookup", new=AsyncMock(return_value=None)):
        r = client.get("/rdns/1.1.1.1", headers={**V4, "Accept": "application/json"})
    assert r.headers.get("access-control-allow-origin") == "*"


def test_rdns_api_v1_alias(client: TestClient) -> None:
    with patch("cptv.routes.rdns.rdns_service.lookup", new=AsyncMock(return_value="ok.example")):
        r = client.get("/api/v1/rdns/1.1.1.1", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["hostname"] == "ok.example"
