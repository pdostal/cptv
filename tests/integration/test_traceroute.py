from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from cptv.services.traceroute import (
    CachedMeta,
    Hop,
    TracerouteError,
    TracerouteRateLimitedError,
    TracerouteResult,
)

V4 = {"X-Forwarded-For": "203.0.113.42"}

_MOCK_RESULT = TracerouteResult(
    target="203.0.113.42",
    ran_at="2024-01-01T00:00:00+00:00",
    hops=[
        Hop(
            hop=1,
            ip="10.0.0.1",
            rdns="gw.local",
            loss_pct=0.0,
            avg_ms=1.0,
            best_ms=0.8,
            worst_ms=1.2,
        ),
        Hop(hop=2, ip=None, loss_pct=100.0),
        Hop(
            hop=3,
            ip="203.0.113.42",
            asn=1234,
            asn_name="Example",
            loss_pct=0.0,
            avg_ms=5.0,
            best_ms=4.8,
            worst_ms=5.3,
        ),
    ],
)

_MOCK_META_LIVE = CachedMeta(cached=False, cache_age=0, refreshes_in=3600)
_MOCK_META_CACHED = CachedMeta(cached=True, cache_age=120, refreshes_in=3480)


def _patch_mtr():
    return patch(
        "cptv.routes.traceroute.run_mtr_cached",
        new_callable=AsyncMock,
        return_value=(_MOCK_RESULT, _MOCK_META_LIVE),
    )


def _patch_mtr_cached():
    cached_result = TracerouteResult(
        target="203.0.113.42",
        ran_at="2024-01-01T00:00:00+00:00",
        hops=_MOCK_RESULT.hops,
        cached=True,
    )
    return patch(
        "cptv.routes.traceroute.run_mtr_cached",
        new_callable=AsyncMock,
        return_value=(cached_result, _MOCK_META_CACHED),
    )


def _patch_mtr_error():
    return patch(
        "cptv.routes.traceroute.run_mtr_cached",
        new_callable=AsyncMock,
        side_effect=TracerouteError("mtr not found"),
    )


def _patch_mtr_rate_limited():
    return patch(
        "cptv.routes.traceroute.run_mtr_cached",
        new_callable=AsyncMock,
        side_effect=TracerouteRateLimitedError("already in progress"),
    )


class TestTracerouteHTML:
    def test_returns_html(self, client: TestClient):
        with _patch_mtr():
            r = client.get(
                "/traceroute",
                headers={**V4, "Accept": "text/html"},
            )
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "203.0.113.42" in r.text
        assert "X-Traceroute-Cached" in r.headers

    def test_shows_hop_table(self, client: TestClient):
        with _patch_mtr():
            r = client.get(
                "/traceroute",
                headers={**V4, "Accept": "text/html"},
            )
        assert "AS1234" in r.text
        assert "* * *" in r.text


class TestTracerouteJSON:
    def test_json_endpoint(self, client: TestClient):
        with _patch_mtr():
            r = client.get("/traceroute.json", headers=V4)
        assert r.status_code == 200
        data = r.json()
        assert data["target"] == "203.0.113.42"
        assert len(data["hops"]) == 3

    def test_api_v1_endpoint(self, client: TestClient):
        with _patch_mtr():
            r = client.get(
                "/api/v1/traceroute",
                headers={**V4, "Accept": "application/json"},
            )
        assert r.status_code == 200
        data = r.json()
        assert "hops" in data

    def test_live_headers(self, client: TestClient):
        with _patch_mtr():
            r = client.get("/traceroute.json", headers=V4)
        assert r.headers["X-Traceroute-Cached"] == "false"
        assert r.headers["X-Traceroute-Cache-Age"] == "0"
        assert r.headers["X-Traceroute-Refreshes-In"] == "3600"

    def test_cached_headers(self, client: TestClient):
        with _patch_mtr_cached():
            r = client.get("/traceroute.json", headers=V4)
        assert r.headers["X-Traceroute-Cached"] == "true"
        assert r.headers["X-Traceroute-Cache-Age"] == "120"
        assert r.headers["X-Traceroute-Refreshes-In"] == "3480"


class TestTracerouteText:
    def test_text_endpoint(self, client: TestClient):
        with _patch_mtr():
            r = client.get("/traceroute.txt", headers=V4)
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "203.0.113.42" in r.text
        assert "AS1234" in r.text


class TestTracerouteApiV1Suffixed:
    def test_api_v1_json_suffix(self, client: TestClient):
        with _patch_mtr():
            r = client.get("/api/v1/traceroute.json", headers=V4)
        assert r.status_code == 200
        assert "application/json" in r.headers["content-type"]
        data = r.json()
        assert data["target"] == "203.0.113.42"
        assert len(data["hops"]) == 3
        assert r.headers["X-Traceroute-Cached"] == "false"

    def test_api_v1_txt_suffix(self, client: TestClient):
        with _patch_mtr():
            r = client.get("/api/v1/traceroute.txt", headers=V4)
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "203.0.113.42" in r.text


class TestTracerouteErrors:
    def test_mtr_failure(self, client: TestClient):
        with _patch_mtr_error():
            r = client.get("/traceroute.json", headers=V4)
        assert r.status_code == 200
        data = r.json()
        assert "error" in data

    def test_no_client_ip(self, client: TestClient):
        r = client.get("/traceroute.json")
        assert r.status_code == 200
        data = r.json()
        assert "error" in data

    def test_rate_limited(self, client: TestClient):
        with _patch_mtr_rate_limited():
            r = client.get("/traceroute.json", headers=V4)
        assert r.status_code == 429
        data = r.json()
        assert "error" in data
