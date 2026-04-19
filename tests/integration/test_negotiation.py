from __future__ import annotations

from fastapi.testclient import TestClient

HDR_FWD = {"X-Forwarded-For": "203.0.113.42"}


def test_html_by_default_in_browser(client: TestClient):
    r = client.get("/ip", headers={**HDR_FWD, "Accept": "text/html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "203.0.113.42" in r.text


def test_json_when_accept_json(client: TestClient):
    r = client.get("/ip", headers={**HDR_FWD, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"ip": "203.0.113.42", "protocol": "IPv4"}


def test_text_when_curl_ua(client: TestClient):
    r = client.get("/ip", headers={**HDR_FWD, "User-Agent": "curl/8.0.0"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "203.0.113.42"


def test_query_param_overrides_accept(client: TestClient):
    r = client.get("/ip?format=json", headers={**HDR_FWD, "Accept": "text/html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_query_param_overrides_curl_ua(client: TestClient):
    r = client.get(
        "/ip?format=json",
        headers={**HDR_FWD, "User-Agent": "curl/8.0.0"},
    )
    assert r.status_code == 200
    assert r.json()["ip"] == "203.0.113.42"


def test_text_format_param(client: TestClient):
    r = client.get("/ip?format=text", headers={**HDR_FWD, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.text == "203.0.113.42"
