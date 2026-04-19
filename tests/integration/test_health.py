from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_health_returns_status(client: TestClient):
    with (
        patch(
            "cptv.routes.health._check_mtr_packet",
            new_callable=AsyncMock,
            return_value="error",
        ),
        patch("cptv.routes.health._check_mtr_capability", return_value="unknown"),
        patch(
            "cptv.routes.health.valkey_health_check",
            new_callable=AsyncMock,
            return_value="ok",
        ),
    ):
        r = client.get("/health")
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["status"] == "degraded"
    assert "mtr_packet" in body["checks"]
    assert "mtr_capability" in body["checks"]
    assert "valkey" in body["checks"]
