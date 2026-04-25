from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
    assert r.text.rstrip("\n") == "203.0.113.42"


def test_ipv6_subdomain_rewrites_root_to_ipv6(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V6, BASE_DOMAIN, {"Host": "ipv6.example.test"}),
    )
    assert r.status_code == 200
    assert r.text.rstrip("\n") == "2001:db8::1"


def test_ipv4_subdomain_v6_client_returns_empty(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V6, BASE_DOMAIN, {"Host": "ipv4.example.test"}),
    )
    assert r.status_code == 200
    assert r.text.rstrip("\n") == ""


def test_apex_host_returns_aggregated_not_single_stack(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V4, BASE_DOMAIN, {"Host": "example.test"}),
    )
    assert r.status_code == 200
    assert "203.0.113.42" in r.text
    # Aggregated text output includes the IP block + RTT line;
    # single-stack endpoints return just the bare IP.
    assert "RTT:" in r.text
    # DNSSEC + Resolver + Time + Server + HTTP version deliberately
    # absent from curl output (browser-only, or noise in scripts).
    assert "Resolver" not in r.text
    assert "DNSSEC" not in r.text
    assert "Time:" not in r.text
    assert "Server:" not in r.text
    assert "HTTP:" not in r.text


def test_subdomain_detection_ignores_port(client: TestClient):
    r = client.get(
        "/",
        headers=_curl(FWD_V4, BASE_DOMAIN, {"Host": "ipv4.example.test:8080"}),
    )
    assert r.status_code == 200
    assert r.text.rstrip("\n") == "203.0.113.42"


def test_subdomain_does_not_rewrite_other_paths(client: TestClient):
    with patch(
        "cptv.routes.health.valkey_health_check",
        new_callable=AsyncMock,
        return_value="ok",
    ):
        r = client.get(
            "/health",
            headers=_curl(FWD_V4, BASE_DOMAIN, {"Host": "ipv4.example.test"}),
        )
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "degraded")


def test_secure_subdomain_does_not_rewrite_root(client: TestClient):
    """secure.<domain>/ must serve the full home page, not bare IPv4 / IPv6."""
    r = client.get(
        "/",
        headers={
            **FWD_V4,
            **BASE_DOMAIN,
            "Host": "secure.example.test",
            "Accept": "text/html",
        },
    )
    assert r.status_code == 200
    body = r.text
    assert 'id="ip-section"' in body, "should render full home page, not bare IP"


def test_secure_subdomain_brand_prefix_in_header(client: TestClient):
    r = client.get(
        "/",
        headers={
            **FWD_V4,
            **BASE_DOMAIN,
            "Host": "secure.example.test",
            "Accept": "text/html",
        },
    )
    body = r.text
    assert '<span class="cptv-brand-prefix">secure.</span>cptv' in body


def test_apex_does_not_show_brand_prefix(client: TestClient):
    r = client.get(
        "/",
        headers={
            **FWD_V4,
            **BASE_DOMAIN,
            "Host": "example.test",
            "Accept": "text/html",
        },
    )
    body = r.text
    assert "cptv-brand-prefix" not in body


def test_apex_offers_link_to_secure(client: TestClient):
    r = client.get(
        "/",
        headers={
            **FWD_V4,
            **BASE_DOMAIN,
            "Host": "example.test",
            "Accept": "text/html",
        },
    )
    assert 'href="https://secure.example.test/"' in r.text


def test_secure_offers_link_to_insecure(client: TestClient):
    r = client.get(
        "/",
        headers={
            **FWD_V4,
            **BASE_DOMAIN,
            "Host": "secure.example.test",
            "Accept": "text/html",
        },
    )
    assert 'href="http://example.test/"' in r.text
    assert "insecure" in r.text


def test_responsive_header_uses_details_hamburger(client: TestClient):
    r = client.get(
        "/",
        headers={
            **FWD_V4,
            **BASE_DOMAIN,
            "Host": "example.test",
            "Accept": "text/html",
        },
    )
    body = r.text
    # Brand link to / and the tagline class CSS hides on narrow screens.
    assert 'class="cptv-brand"' in body
    assert 'class="cptv-brand-tagline"' in body
    # Hamburger button + always-rendered <ul> menu (JS toggles data-open).
    assert 'class="cptv-nav-toggle"' in body
    assert 'class="cptv-nav-menu"' in body
    assert 'aria-expanded="false"' in body


def test_nav_hides_home_link_on_home(client: TestClient):
    """The 'home' link is redundant when we ARE the home page."""
    r = client.get(
        "/",
        headers={**FWD_V4, **BASE_DOMAIN, "Host": "example.test", "Accept": "text/html"},
    )
    body = r.text
    # Pull just the nav menu so we don't false-match other 'href="/"' bits.
    import re

    m = re.search(r'<ul[^>]*id="cptv-nav-menu".*?</ul>', body, re.DOTALL)
    assert m, "nav menu not found"
    menu = m.group(0)
    assert 'href="/"' not in menu
    assert 'href="/help"' in menu


def test_nav_hides_help_link_on_help(client: TestClient):
    """The 'help' link is redundant when we ARE the help page."""
    r = client.get(
        "/help",
        headers={**FWD_V4, **BASE_DOMAIN, "Host": "example.test", "Accept": "text/html"},
    )
    body = r.text
    import re

    m = re.search(r'<ul[^>]*id="cptv-nav-menu".*?</ul>', body, re.DOTALL)
    assert m, "nav menu not found"
    menu = m.group(0)
    assert 'href="/help"' not in menu
    assert 'href="/"' in menu
