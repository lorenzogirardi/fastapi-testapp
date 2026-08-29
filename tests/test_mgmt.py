"""Tests for the management router."""

import pytest


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/api/mgmt/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ["UP", "DEGRADED"]
    assert "checks" in data


@pytest.mark.anyio
async def test_ready(client):
    resp = await client.get("/api/mgmt/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"


@pytest.mark.anyio
async def test_info(client):
    resp = await client.get("/api/mgmt/info")
    assert resp.status_code == 200
    assert resp.json()["app"]["name"] == "pytbak"


@pytest.mark.anyio
async def test_env(client):
    resp = await client.get("/api/mgmt/env")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


@pytest.mark.anyio
async def test_mappings(client):
    resp = await client.get("/api/mgmt/mappings")
    assert resp.status_code == 200
    assert "mappings" in resp.json()


@pytest.mark.anyio
async def test_threaddump_requires_auth(client):
    resp = await client.get("/api/mgmt/threaddump")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_threaddump(client, auth_headers):
    resp = await client.get("/api/mgmt/threaddump", headers=auth_headers)
    assert resp.status_code == 200
    assert "Thread" in resp.text
