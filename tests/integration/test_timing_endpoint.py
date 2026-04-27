from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from cptv.main import create_app

V4 = {"X-Forwarded-For": "203.0.113.42"}
TCP_HEADERS = {
    "X-Tcp-Rtt-Us": "24000",
    "X-Tcp-Rttvar-Us": "15800",
    "X-Tcp-Mss": "1448",
}


def _curl(extra: dict[str, str]) -> dict[str, str]:
    return {**extra, "User-Agent": "curl/8.0.0"}


# ---------- /timing/echo basics ----------


def test_timing_echo_text_body(client: TestClient):
    r = client.get("/timing/echo", headers=_curl(V4))
    assert r.status_code == 200
    # Body must be byte-stable across calls (probe consistency).
    assert r.text == "ok\n"


def test_timing_echo_text_body_stable_across_calls(client: TestClient):
    bodies = {client.get("/timing/echo", headers=_curl(V4)).text for _ in range(3)}
    assert bodies == {"ok\n"}


def test_timing_echo_json(client: TestClient):
    r = client.get("/timing/echo", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_timing_echo_api_v1_alias(client: TestClient):
    r = client.get("/api/v1/timing/echo", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ---------- CORS so the home-page JS can read it cross-origin ----------


def test_timing_echo_sets_public_cors(client: TestClient):
    r = client.get("/timing/echo", headers={**V4, "Accept": "application/json"})
    assert r.headers.get("Access-Control-Allow-Origin") == "*"
    assert r.headers.get("Cache-Control") == "no-store"


def test_timing_echo_exposes_timing_headers(client: TestClient):
    r = client.get("/timing/echo", headers={**V4, "Accept": "application/json"})
    expose = r.headers.get("Access-Control-Expose-Headers", "")
    # The home-page JS needs every one of these readable cross-origin.
    for name in (
        "X-Response-Time-Ms",
        "X-Tcp-Rtt-Us",
        "X-Tcp-Rttvar-Us",
        "X-Tcp-Mss",
    ):
        assert name in expose, name


def test_timing_echo_emits_response_time_header(client: TestClient):
    """X-Response-Time-Ms must always be present so JS can subtract server time."""
    r = client.get("/timing/echo", headers={**V4, "Accept": "application/json"})
    assert "X-Response-Time-Ms" in r.headers
    # Value parses as a non-negative float.
    assert float(r.headers["X-Response-Time-Ms"]) >= 0


# ---------- index payload TCP integration ----------
#
# These tests need to control the ASGI scope's "client" tuple so the
# spoof-protection middleware behaves as it would in production. The
# stock TestClient hard-codes ("testclient", 50000), which is NOT in
# our _LOOPBACK_HOSTS set — perfect for "non-loopback peer" tests, but
# we also need a loopback path. We use httpx.ASGITransport(client=…)
# directly to set the peer per test.


def _async_client(peer_host: str) -> httpx.AsyncClient:
    """Async ASGI client whose ``request.client.host`` equals ``peer_host``.

    Stock TestClient hard-codes the peer to ``("testclient", 50000)``;
    we need an explicit ``("127.0.0.1", N)`` to exercise the loopback
    branch of the spoof-protection middleware, and a public IP to
    exercise the strip branch.
    """
    app = create_app()
    transport = httpx.ASGITransport(app=app, client=(peer_host, 12345))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
async def loopback_client():
    async with _async_client("127.0.0.1") as c:
        yield c


@pytest.fixture
async def public_client():
    async with _async_client("198.51.100.7") as c:
        yield c


async def test_aggregated_json_omits_tcp_when_headers_missing(
    loopback_client: httpx.AsyncClient,
):
    r = await loopback_client.get("/", headers={**V4, "Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["timing"]["tcp"] is None


async def test_aggregated_json_includes_tcp_when_loopback_peer_sends_headers(
    loopback_client: httpx.AsyncClient,
):
    """Loopback peer (== nginx in prod) is trusted; headers populate TCP info."""
    r = await loopback_client.get(
        "/",
        headers={**V4, **TCP_HEADERS, "Accept": "application/json"},
    )
    assert r.status_code == 200
    tcp = r.json()["timing"]["tcp"]
    assert tcp == {
        "rtt_ms": 24.0,
        "rttvar_ms": 15.8,
        "mss_bytes": 1448,
        "protocol": "IPv4",
    }


async def test_aggregated_text_includes_tcp_line_when_headers_present(
    loopback_client: httpx.AsyncClient,
):
    r = await loopback_client.get(
        "/",
        headers=_curl({**V4, **TCP_HEADERS}),
    )
    assert r.status_code == 200
    body = r.text
    assert "TCP IPv4" in body
    assert "24.0ms" in body
    assert "1448b" in body


async def test_aggregated_text_omits_tcp_line_when_headers_missing(
    loopback_client: httpx.AsyncClient,
):
    r = await loopback_client.get("/", headers=_curl(V4))
    assert "TCP IPv4" not in r.text
    assert "TCP IPv6" not in r.text


# ---------- spoof protection: non-loopback peers can't inject X-Tcp-* ----------


async def test_index_strips_spoofable_headers_from_non_loopback_peer(
    public_client: httpx.AsyncClient,
):
    """A direct (non-loopback) caller cannot poison the Timing card."""
    r = await public_client.get(
        "/",
        headers={**V4, **TCP_HEADERS, "Accept": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["timing"]["tcp"] is None, (
        "non-loopback peers must not be able to spoof X-Tcp-* timing headers"
    )
