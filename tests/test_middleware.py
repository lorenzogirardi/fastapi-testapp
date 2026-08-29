"""Tests for error injection and delay middleware."""

import pytest


@pytest.mark.anyio
async def test_inject_error_500(client):
    resp = await client.get("/api/mgmt/info?inject_error=500")
    assert resp.status_code == 500
    assert resp.json()["injected"] is True


@pytest.mark.anyio
async def test_inject_error_429(client):
    resp = await client.get("/api/mgmt/info?inject_error=429")
    assert resp.status_code == 429


@pytest.mark.anyio
async def test_inject_validation_error(client):
    resp = await client.get("/api/mgmt/info?inject_error=validation_error")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_inject_custom_error(client):
    resp = await client.get("/api/mgmt/info?inject_error=custom:boom")
    assert resp.status_code == 500
    assert resp.json()["error"] == "boom"


@pytest.mark.anyio
async def test_delay_ms(client):
    import time
    start = time.monotonic()
    resp = await client.get("/api/mgmt/ready?delay_ms=100")
    elapsed = time.monotonic() - start
    assert resp.status_code == 200
    assert elapsed >= 0.08  # allow some tolerance


@pytest.mark.anyio
async def test_index_page(client):
    resp = await client.get("/api/")
    assert resp.status_code == 200
    assert "pytbak" in resp.text
