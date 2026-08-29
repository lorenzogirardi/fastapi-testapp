"""Integration — management endpoints with real PG + Redis backends."""

import pytest


@pytest.mark.anyio
async def test_ready(client):
    resp = await client.get("/api/mgmt/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"


@pytest.mark.anyio
async def test_health_backends_up(client):
    """Both PostgreSQL and Redis must report UP (not just NOT_CONFIGURED)."""
    resp = await client.get("/api/mgmt/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "UP"
    by_name = {c["name"]: c["status"] for c in data["checks"]}
    assert by_name.get("redis") == "UP", f"Redis not UP: {by_name}"
    assert by_name.get("postgresql") == "UP", f"PostgreSQL not UP: {by_name}"


@pytest.mark.anyio
async def test_info(client):
    resp = await client.get("/api/mgmt/info")
    assert resp.status_code == 200
    app = resp.json()["app"]
    assert app["name"] == "pytbak"
    assert app["version"] != ""
    assert app["environment"] == "development"


@pytest.mark.anyio
async def test_mappings_non_empty(client):
    resp = await client.get("/api/mgmt/mappings")
    assert resp.status_code == 200
    mappings = resp.json()["mappings"]
    paths = [m["path"] for m in mappings]
    assert "/api/contexts" in paths
    assert "/api/mgmt/health" in paths
