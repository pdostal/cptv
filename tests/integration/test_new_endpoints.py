from __future__ import annotations

from fastapi.testclient import TestClient

V4 = {"X-Forwarded-For": "203.0.113.42"}
CURL = {"User-Agent": "curl/8.0.0"}


def _headers(*ds):
    out: dict[str, str] = {}
    for d in ds:
        out.update(d)
    return out


# ---------- /geoip ----------


def test_geoip_json_without_db(client: TestClient):
    r = client.get("/geoip", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"country_code", "country", "region", "city", "latitude", "longitude"}
    assert all(v is None for v in body.values())


def test_geoip_text_without_db(client: TestClient):
    r = client.get("/geoip", headers=_headers(V4, CURL))
    assert r.status_code == 200
    assert "unavailable" in r.text.lower()


def test_geoip_api_v1(client: TestClient):
    r = client.get("/api/v1/geoip", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200


# ---------- /asn, /isp ----------


def test_asn_json_without_db(client: TestClient):
    r = client.get("/asn", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["asn"] is None
    assert body["looking_glass"] is None


def test_isp_text_without_db(client: TestClient):
    r = client.get("/isp", headers=_headers(V4, CURL))
    assert r.status_code == 200
    assert r.text == "—"


# ---------- /dns ----------


def test_dns_without_resolver(client: TestClient):
    r = client.get("/dns", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["resolver_ip"] is None
    assert body["is_known_public"] is False


def test_dns_with_known_resolver_query(client: TestClient):
    r = client.get("/dns?resolver=1.1.1.1", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["resolver_ip"] == "1.1.1.1"
    assert body["resolver_name"] == "Cloudflare"
    assert body["is_known_public"] is True


def test_dns_with_unknown_resolver(client: TestClient):
    r = client.get("/dns?resolver=203.0.113.5", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_known_public"] is False


# ---------- /help ----------


def test_help_text_has_endpoints(client: TestClient):
    r = client.get("/help", headers=_headers(V4, CURL))
    assert r.status_code == 200
    assert "ENDPOINTS" in r.text
    assert "/ipv4" in r.text
    assert "/traceroute" in r.text


def test_traceroute_html(client: TestClient):
    r = client.get("/traceroute", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Traceroute" in r.text


def test_help_uses_request_domain(client: TestClient):
    r = client.get(
        "/help",
        headers=_headers(V4, CURL, {"X-Base-Domain": "captive.example"}),
    )
    assert "ipv4.captive.example" in r.text
    assert "curl captive.example" in r.text


def test_help_html(client: TestClient):
    r = client.get("/help", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


# ---------- /details + enriched aggregated ----------


def test_aggregated_json_has_all_sections(client: TestClient):
    r = client.get("/", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    for key in ("ip", "geoip", "asn", "dns", "timing", "http", "meta", "quick_links"):
        assert key in body
    assert body["timing"]["server_timestamp"].endswith("Z")
    assert body["http"]["version"].startswith("HTTP/")
    assert body["meta"]["repo"] == "https://github.com/pdostal/cptv"


def test_aggregated_html_renders_sections(client: TestClient):
    r = client.get("/", headers={**V4, "Accept": "text/html"})
    assert r.status_code == 200
    body = r.text
    assert 'id="ip-section"' in body
    assert 'id="geoip-section"' in body
    assert 'id="asn-section"' in body
    assert 'id="dns-section"' in body
    assert 'id="timing-section"' in body
    assert "/static/vendor/pico.min.css" in body
    assert "/static/vendor/htmx.min.js" in body
    assert "/static/app.js" in body


def test_details_html_adds_request_section(client: TestClient):
    r = client.get("/details", headers={**V4, "Accept": "text/html"})
    assert r.status_code == 200
    assert "request-section" in r.text


def test_details_json_has_request(client: TestClient):
    r = client.get("/api/v1/details", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert "request" in body
    assert body["request"]["method"] == "GET"


def test_more_alias(client: TestClient):
    r = client.get("/more", headers=_headers(V4, CURL))
    assert r.status_code == 200
    assert "Server:" in r.text


def test_aggregated_with_quick_links(monkeypatch, client: TestClient):
    # Push CPTV_QUICK_LINKS into the cached settings by clearing the lru_cache.
    from cptv import config

    config.get_settings.cache_clear()
    monkeypatch.setenv(
        "CPTV_QUICK_LINKS",
        '[{"label":"Looking Glass","url":"https://lg.example.net","icon":"🔭"}]',
    )
    try:
        r = client.get("/", headers={**V4, "Accept": "application/json"})
        assert r.status_code == 200
        body = r.json()
        assert body["quick_links"][0]["label"] == "Looking Glass"
    finally:
        config.get_settings.cache_clear()
