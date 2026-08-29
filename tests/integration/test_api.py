"""Integration — API endpoints with real PostgreSQL and Redis persistence."""

import pytest


@pytest.fixture
async def created_context(client):
    """Create a context and clean it up after the test."""
    resp = await client.post("/api/contexts", json={"title": "integration-test", "description": "teardown-on-exit"})
    assert resp.status_code == 201
    ctx = resp.json()
    yield ctx
    await client.delete(f"/api/contexts/{ctx['id']}")


@pytest.mark.anyio
async def test_create_context_persists_in_pg(client):
    resp = await client.post("/api/contexts", json={"title": "pg-persist", "description": "real pg"})
    assert resp.status_code == 201
    ctx = resp.json()
    assert ctx["id"] != ""
    assert ctx["done"] is False
    assert ctx["created_at"] is not None
    # Verify round-trip from PG via a fresh GET
    get = await client.get(f"/api/contexts/{ctx['id']}")
    assert get.status_code == 200
    assert get.json()["title"] == "pg-persist"
    # Cleanup
    await client.delete(f"/api/contexts/{ctx['id']}")


@pytest.mark.anyio
async def test_update_persists(client, created_context):
    ctx_id = created_context["id"]
    resp = await client.put(f"/api/contexts/{ctx_id}", json={"title": "updated", "done": True})
    assert resp.status_code == 200
    assert resp.json()["title"] == "updated"
    assert resp.json()["done"] is True
    # Verify PG round-trip
    get = await client.get(f"/api/contexts/{ctx_id}")
    assert get.json()["done"] is True


@pytest.mark.anyio
async def test_delete_removes_from_pg(client, created_context):
    ctx_id = created_context["id"]
    del_resp = await client.delete(f"/api/contexts/{ctx_id}")
    assert del_resp.status_code == 200
    get = await client.get(f"/api/contexts/{ctx_id}")
    assert get.status_code == 404


@pytest.mark.anyio
async def test_list_contains_created(client, created_context):
    ctx_id = created_context["id"]
    resp = await client.get("/api/contexts")
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert ctx_id in ids


@pytest.mark.anyio
async def test_redis_counter_increments(client):
    r1 = await client.get("/api/count")
    r2 = await client.get("/api/count")
    assert r1.status_code == 200
    assert r2.status_code == 200
    c1 = r1.json()["counter"]
    c2 = r2.json()["counter"]
    assert c1 is not None, "Redis counter unavailable"
    assert c2 == c1 + 1, f"Counter did not increment: {c1} -> {c2}"


@pytest.mark.anyio
async def test_fibonacci_known_values(client):
    cases = {"0": "0", "1": "1", "10": "55", "20": "6765"}
    for n, expected in cases.items():
        resp = await client.get(f"/api/fib/{n}")
        assert resp.status_code == 200
        assert resp.json()["result"] == expected, f"fib({n}) wrong"
