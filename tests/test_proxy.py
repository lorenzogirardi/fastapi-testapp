"""Tests for the internal web-proxy endpoint."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest


@pytest.mark.anyio
async def test_web_proxy_success(client):
    fake_resp = httpx.Response(
        200,
        content=b"hello world",
        headers={"content-type": "text/plain"},
    )
    with patch("app.routers.proxy.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=fake_resp)
        resp = await client.get("/api/internal/web-proxy?url=https://example.com")

    assert resp.status_code == 200
    assert resp.text == "hello world"


@pytest.mark.anyio
async def test_web_proxy_rejects_non_http(client):
    resp = await client.get("/api/internal/web-proxy?url=ftp://example.com")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_web_proxy_upstream_error(client):
    with patch("app.routers.proxy.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        resp = await client.get("/api/internal/web-proxy?url=https://example.com")

    assert resp.status_code == 502
