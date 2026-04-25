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
    assert r.text.rstrip("\n") == "—"


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
    # Must surface the streaming + suffix-format variants per item-7 review.
    assert "/traceroute.json" in r.text
    assert "/traceroute.txt" in r.text
    assert "/traceroute/stream" in r.text
    # ?format=json should be advertised so plain-text users discover it.
    assert "?format=json" in r.text
    # The dropped /details endpoint must NOT appear anymore.
    assert "/details" not in r.text


def test_help_html_lists_traceroute_streaming(client: TestClient):
    r = client.get("/help", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "/traceroute/stream" in r.text
    assert "/traceroute.json" in r.text
    assert "?format=json" in r.text
    # The removed /details endpoint must not be linked anywhere in the
    # body; the substring would also match the </details> closing tag,
    # so look for the URL form specifically.
    assert 'href="/details"' not in r.text
    assert "<code>/details</code>" not in r.text


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


# ---------- aggregated home page (was the /details split, now folded in) ----------


def test_aggregated_json_has_all_sections(client: TestClient):
    r = client.get("/", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    for key in ("ip", "geoip", "asn", "dns", "timing", "http", "meta", "quick_links", "request"):
        assert key in body
    assert body["timing"]["server_timestamp"].endswith("Z")
    assert body["http"]["version"].startswith("HTTP/")
    assert body["meta"]["repo"] == "https://github.com/pdostal/cptv"
    # Request inspection panel data is part of the aggregated payload now.
    assert body["request"]["method"] == "GET"
    assert isinstance(body["request"]["headers"], dict)


def test_aggregated_html_renders_sections(client: TestClient):
    r = client.get("/", headers={**V4, "Accept": "text/html"})
    assert r.status_code == 200
    body = r.text
    assert 'id="ip-section"' in body
    assert 'id="geoip-section"' in body
    assert 'id="asn-section"' in body
    assert 'id="dns-section"' in body
    assert 'id="timing-section"' in body
    assert 'id="history-section"' in body
    assert 'id="traceroute-section"' in body
    assert 'id="anycast-section"' in body
    assert 'id="request-section"' in body  # request inspection (was /details)
    # Per-stack enrichment placeholders for the GeoIP / ASN cards.
    assert 'id="geoip-stacks"' in body
    assert 'id="asn-stacks"' in body
    assert 'id="ip-history"' in body
    assert 'id="anycast-results"' in body
    assert "/static/vendor/pico.min.css" in body
    assert "/static/vendor/htmx.min.js" in body
    assert "/static/app.js" in body
    # Dark Reader opt-out signal (we already implement native dark mode).
    assert '<meta name="darkreader-lock"' in body
    # Inline SVG favicon so the browser doesn't 404 on /favicon.ico.
    assert 'rel="icon"' in body
    assert "data:image/svg+xml" in body


def test_details_endpoints_are_gone(client: TestClient):
    """/details, /more, /api/v1/details were folded into /."""
    for path in ("/details", "/more", "/api/v1/details"):
        r = client.get(path, headers={**V4, "Accept": "text/html"})
        assert r.status_code == 404, path


def test_404_body_ends_with_newline(client: TestClient):
    """Custom 404 handler appends \\n so curl + zsh stay tidy."""
    r = client.get("/totally-bogus-path", headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert r.text.endswith("\n")
    import json

    assert json.loads(r.text)["detail"] == "Not Found"


def test_text_hint_appended_on_aggregated(client: TestClient):
    """Plain-text aggregated output ends with a hint pointing at JSON."""
    r = client.get("/", headers=_headers(V4, CURL))
    assert r.status_code == 200
    assert "tip: append ?format=json" in r.text


def test_text_hint_omitted_on_bare_ip_endpoints(client: TestClient):
    """Bare-IP echoes stay clean for shell scripting."""
    for path in ("/ip", "/ipv4", "/ip4", "/4", "/isp"):
        r = client.get(path, headers=_headers(V4, CURL))
        assert "tip:" not in r.text, path


def test_curl_howto_card_no_longer_on_home(client: TestClient):
    """Removed in v0.1.5 \u2014 the same content lives in /help instead."""
    r = client.get("/", headers={**V4, "Accept": "text/html"})
    assert 'id="curl-howto-section"' not in r.text


def test_ip_card_no_protocol_annotation(client: TestClient):
    """Public IP with no warnings should NOT show the (IPv4)/(IPv6) tag."""
    r = client.get("/", headers={**V4, "Accept": "text/html"})
    body = r.text
    # Pull out the ip-section and check there's no '(IPv4)' or '(IPv6)' in it.
    import re

    m = re.search(r'id="ip-section".*?</article>', body, re.DOTALL)
    assert m, "ip-section not found"
    section = m.group(0)
    assert "(IPv4)" not in section
    assert "(IPv6)" not in section


def test_timing_card_drops_visible_labels_but_keeps_time_for_clock_skew(
    client: TestClient,
):
    """'Server time:' and 'HTTP version:' lines were dropped in v0.1.6,
    but the <time id='server-time'> stays (clock-skew JS reads it)."""
    r = client.get("/", headers={**V4, "Accept": "text/html"})
    body = r.text
    import re

    m = re.search(r'id="timing-section".*?</article>', body, re.DOTALL)
    assert m, "timing-section not found"
    section = m.group(0)
    assert "Server time:" not in section
    assert "HTTP version" not in section
    # JS clock-skew detection still has the data it needs.
    assert 'id="server-time"' in section
    assert "data-server-ts=" in section
    assert "hidden" in section


def test_dnssec_card_mentions_rhybar(client: TestClient):
    """The DNSSEC card explains rhybar.cz is the bogus probe target."""
    r = client.get("/", headers={**V4, "Accept": "text/html"})
    body = r.text
    import re

    m = re.search(r'id="dns-section".*?</article>', body, re.DOTALL)
    assert m, "dns-section not found"
    section = m.group(0)
    assert "rhybar.cz" in section
    assert "CZ.NIC" in section


def test_dnssec_label_and_badge_share_one_line(client: TestClient):
    """The 'DNSSEC validation:' label and the verdict badge live in the
    SAME <p> (a flex row) so they appear side-by-side."""
    r = client.get("/", headers={**V4, "Accept": "text/html"})
    body = r.text
    import re

    m = re.search(r'<p[^>]*class="cptv-dnssec-row".*?</p>', body, re.DOTALL)
    assert m, "cptv-dnssec-row not found"
    row = m.group(0)
    assert "cptv-dnssec-toggle" in row
    assert 'id="dnssec-status"' in row
    # The explainer is a separate hidden <p>, NOT inside the row.
    assert "rhybar.cz" not in row


def test_ip_card_warns_on_private_ip(client: TestClient):
    """RFC1918 private IPs still get the (private) annotation."""
    r = client.get(
        "/",
        headers={"X-Forwarded-For": "192.168.1.1", "Accept": "text/html"},
    )
    body = r.text
    import re

    m = re.search(r'id="ip-section".*?</article>', body, re.DOTALL)
    section = m.group(0)
    assert "(private)" in section


def test_aggregated_json_has_redirect_origin(client: TestClient):
    r = client.get("/", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert "redirect_origin" in body
    # Direct hit, no headers — should not look like a captive portal.
    assert body["redirect_origin"]["looks_like_captive_portal"] is False


def test_redirect_origin_via_referer(client: TestClient):
    r = client.get(
        "/",
        headers={
            **V4,
            "Accept": "application/json",
            "Referer": "http://login.airport-wifi.example/portal?dest=http://google.com",
            "X-Base-Domain": "cptv.example",
        },
    )
    body = r.json()
    assert body["redirect_origin"]["looks_like_captive_portal"] is True
    assert body["redirect_origin"]["referrer_host"] == "login.airport-wifi.example"


def test_redirect_origin_via_x_original_url(client: TestClient):
    r = client.get(
        "/",
        headers={
            **V4,
            "Accept": "application/json",
            "X-Original-URL": "http://example.com/file.html",
        },
    )
    body = r.json()
    assert body["redirect_origin"]["looks_like_captive_portal"] is True
    assert body["redirect_origin"]["via_header"] == "x-original-url"
    assert body["redirect_origin"]["original_url"] == "http://example.com/file.html"


def test_redirect_origin_self_referrer_ignored(client: TestClient):
    r = client.get(
        "/",
        headers={
            **V4,
            "Accept": "application/json",
            "Referer": "http://cptv.example/help",
            "X-Base-Domain": "cptv.example",
        },
    )
    body = r.json()
    assert body["redirect_origin"]["looks_like_captive_portal"] is False


def test_aggregated_json_has_rtt_ms(client: TestClient):
    r = client.get("/", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    rtt = r.json()["timing"]["rtt_ms"]
    assert isinstance(rtt, (int, float))
    assert rtt >= 0


def test_response_time_header_present(client: TestClient):
    r = client.get("/ip", headers=V4)
    assert "X-Response-Time-Ms" in r.headers
    val = float(r.headers["X-Response-Time-Ms"])
    assert val >= 0


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
        # Default title when CPTV_QUICK_LINKS_TITLE is unset.
        assert body["quick_links_title"] == "Quick Links"
    finally:
        config.get_settings.cache_clear()


def test_quick_links_custom_title(monkeypatch, client: TestClient):
    from cptv import config

    config.get_settings.cache_clear()
    monkeypatch.setenv(
        "CPTV_QUICK_LINKS",
        '[{"label":"Wiki","url":"https://wiki.example.net"}]',
    )
    monkeypatch.setenv("CPTV_QUICK_LINKS_TITLE", "Operator Tools")
    try:
        r = client.get("/", headers={**V4, "Accept": "application/json"})
        assert r.json()["quick_links_title"] == "Operator Tools"

        r2 = client.get("/", headers={**V4, "Accept": "text/html"})
        assert "🔗 Operator Tools" in r2.text
    finally:
        config.get_settings.cache_clear()
