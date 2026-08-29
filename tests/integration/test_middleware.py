"""Integration — error injection and delay middleware over real HTTP."""

import time

import pytest


@pytest.mark.anyio
async def test_inject_http_error(client):
    for code in [400, 500, 503]:
        resp = await client.get(f"/api/mgmt/ready?inject_error={code}")
        assert resp.status_code == code
        body = resp.json()
        assert body["injected"] is True


@pytest.mark.anyio
async def test_inject_custom_message(client):
    resp = await client.get("/api/mgmt/ready?inject_error=custom:oops")
    assert resp.status_code == 500
    assert resp.json()["error"] == "oops"


@pytest.mark.anyio
async def test_delay_ms_timing(client):
    start = time.monotonic()
    resp = await client.get("/api/mgmt/ready?delay_ms=400")
    elapsed_ms = (time.monotonic() - start) * 1000
    assert resp.status_code == 200
    assert elapsed_ms >= 380, f"Delay too short: {elapsed_ms:.0f}ms"
    assert elapsed_ms < 2000, f"Delay too long: {elapsed_ms:.0f}ms"


@pytest.mark.anyio
async def test_x_request_id_header_present(client):
    resp = await client.get("/api/mgmt/ready")
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) == 8
