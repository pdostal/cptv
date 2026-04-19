from __future__ import annotations

from fastapi.testclient import TestClient

FWD_V4 = {"X-Forwarded-For": "203.0.113.42"}
FWD_V6 = {"X-Forwarded-For": "2001:db8::1"}
BASE_DOMAIN = {"X-Base-Domain": "example.test"}


def _curl(*dicts: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {"User-Agent": "curl/8.0.0"}
    for d in dicts:
        merged.update(d)
    return merged


def test_ipv4_subdomain_rewrites_root_to_ipv4(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V4, BASE_DOMAIN, {"Host": "ipv4.example.test"}),
    )
    assert r.status_code == 200
    assert r.text == "203.0.113.42"


def test_ipv6_subdomain_rewrites_root_to_ipv6(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V6, BASE_DOMAIN, {"Host": "ipv6.example.test"}),
    )
    assert r.status_code == 200
    assert r.text == "2001:db8::1"


def test_ipv4_subdomain_v6_client_returns_empty(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V6, BASE_DOMAIN, {"Host": "ipv4.example.test"}),
    )
    assert r.status_code == 200
    assert r.text == ""


def test_apex_host_returns_aggregated_not_single_stack(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V4, BASE_DOMAIN, {"Host": "example.test"}),
    )
    assert r.status_code == 200
    assert "203.0.113.42" in r.text
    # Aggregated text output includes the IP block + resolver/DNSSEC sections;
    # single-stack endpoints return just the bare IP.
    assert "Resolver" in r.text
    assert "Server:" in r.text


def test_subdomain_detection_ignores_port(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V4, BASE_DOMAIN, {"Host": "ipv4.example.test:8080"}),
    )
    assert r.status_code == 200
    assert r.text == "203.0.113.42"


def test_subdomain_does_not_rewrite_other_paths(client: TestClient):
    r = client.get(
        "/health",
        headers=_curl(FWD_V4, BASE_DOMAIN, {"Host": "ipv4.example.test"}),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
