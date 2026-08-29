"""Integration — debug endpoints: auth enforcement, DNS, echo."""

import pytest


@pytest.mark.anyio
async def test_debug_requires_auth(client):
    for path in ["/api/debug/headers", "/api/debug/dns?name=localhost", "/api/debug/tcp-check?host=localhost&port=80"]:
        resp = await client.get(path)
        assert resp.status_code == 401, f"{path} should require auth"


@pytest.mark.anyio
async def test_echo_headers(auth_client):
    resp = await auth_client.get("/api/debug/headers", headers={"X-Custom": "hello"})
    assert resp.status_code == 200
    assert resp.json()["x-custom"] == "hello"


@pytest.mark.anyio
async def test_echo_body(auth_client):
    resp = await auth_client.post(
        "/api/debug/echo",
        content=b"integration-test-payload",
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 200
    assert resp.text == "integration-test-payload"


@pytest.mark.anyio
async def test_dns_resolve_localhost(auth_client):
    resp = await auth_client.get("/api/debug/dns?name=localhost")
    assert resp.status_code == 200
    addresses = resp.json()["addresses"]
    assert isinstance(addresses, list)
    assert len(addresses) > 0


@pytest.mark.anyio
async def test_dns_invalid_host_rejected(auth_client):
    resp = await auth_client.get("/api/debug/dns?name=not;valid")
    assert resp.status_code == 400
