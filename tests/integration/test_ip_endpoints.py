from __future__ import annotations

from fastapi.testclient import TestClient

V4 = {"X-Forwarded-For": "203.0.113.42"}
V6 = {"X-Forwarded-For": "2001:db8::1"}


def _text_curl(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "User-Agent": "curl/8.0.0"}


def test_ip_returns_current_v4(client: TestClient):
    r = client.get("/ip", headers=_text_curl(V4))
    assert r.text == "203.0.113.42"


def test_ip_returns_current_v6(client: TestClient):
    r = client.get("/ip", headers=_text_curl(V6))
    assert r.text == "2001:db8::1"


def test_ipv4_endpoint_v4_client(client: TestClient):
    r = client.get("/ipv4", headers=_text_curl(V4))
    assert r.text == "203.0.113.42"


def test_ipv4_endpoint_v6_client_is_empty(client: TestClient):
    r = client.get("/ipv4", headers=_text_curl(V6))
    assert r.text == ""


def test_ipv6_endpoint_v6_client(client: TestClient):
    r = client.get("/ipv6", headers=_text_curl(V6))
    assert r.text == "2001:db8::1"


def test_ipv6_endpoint_v4_client_is_empty(client: TestClient):
    r = client.get("/ipv6", headers=_text_curl(V4))
    assert r.text == ""


def test_ip4_alias(client: TestClient):
    r = client.get("/ip4", headers=_text_curl(V4))
    assert r.text == "203.0.113.42"


def test_4_alias(client: TestClient):
    r = client.get("/4", headers=_text_curl(V4))
    assert r.text == "203.0.113.42"


def test_ip6_alias(client: TestClient):
    r = client.get("/ip6", headers=_text_curl(V6))
    assert r.text == "2001:db8::1"


def test_6_alias(client: TestClient):
    r = client.get("/6", headers=_text_curl(V6))
    assert r.text == "2001:db8::1"


def test_api_v1_ipv4_json(client: TestClient):
    r = client.get("/api/v1/ipv4", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"ipv4": "203.0.113.42"}


def test_api_v1_ipv6_json(client: TestClient):
    r = client.get("/api/v1/ipv6", headers={**V6, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"ipv6": "2001:db8::1"}


def test_aggregated_json_v4(client: TestClient):
    r = client.get("/", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["ip"]["current"] == "203.0.113.42"
    assert body["ip"]["protocol"] == "IPv4"
    assert body["ip"]["ipv4"] == "203.0.113.42"
    assert body["ip"]["ipv6"] is None
    assert body["geoip"] is None
    assert body["quick_links"] == []


def test_api_v1_aggregated(client: TestClient):
    r = client.get("/api/v1/", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["ip"]["current"] == "203.0.113.42"
