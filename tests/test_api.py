"""Tests for the core API router (contexts CRUD + legacy)."""

import pytest


@pytest.mark.anyio
async def test_list_contexts_empty(client):
    resp = await client.get("/api/contexts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_create_context(client):
    resp = await client.post("/api/contexts", json={"title": "Test", "description": "Desc"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test"
    assert data["description"] == "Desc"
    assert data["done"] is False
    assert "id" in data


@pytest.mark.anyio
async def test_create_and_get_context(client):
    create = await client.post("/api/contexts", json={"title": "CTX1"})
    ctx_id = create.json()["id"]
    resp = await client.get(f"/api/contexts/{ctx_id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "CTX1"


@pytest.mark.anyio
async def test_get_context_not_found(client):
    resp = await client.get("/api/contexts/nonexistent")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_update_context(client):
    create = await client.post("/api/contexts", json={"title": "Old"})
    ctx_id = create.json()["id"]
    resp = await client.put(f"/api/contexts/{ctx_id}", json={"title": "New", "done": True})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New"
    assert resp.json()["done"] is True


@pytest.mark.anyio
async def test_update_context_not_found(client):
    resp = await client.put("/api/contexts/nonexistent", json={"title": "X"})
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_delete_context(client):
    create = await client.post("/api/contexts", json={"title": "Del"})
    ctx_id = create.json()["id"]
    resp = await client.delete(f"/api/contexts/{ctx_id}")
    assert resp.status_code == 200
    assert resp.json()["result"] is True
    # Verify it's gone
    resp2 = await client.get(f"/api/contexts/{ctx_id}")
    assert resp2.status_code == 404


@pytest.mark.anyio
async def test_delete_context_not_found(client):
    resp = await client.delete("/api/contexts/nonexistent")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_fibonacci(client):
    resp = await client.get("/api/fib/10")
    assert resp.status_code == 200
    assert resp.json()["result"] == "55"


@pytest.mark.anyio
async def test_fibonacci_too_large(client):
    resp = await client.get("/api/fib/30000")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_sleep_endpoint(client):
    resp = await client.get("/api/sleep/1")
    assert resp.status_code == 200
    assert "Delayed" in resp.json()["message"]


@pytest.mark.anyio
async def test_sleep_too_long(client):
    resp = await client.get("/api/sleep/11")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_count_without_redis(client):
    resp = await client.get("/api/count")
    assert resp.status_code == 200
    assert resp.json()["counter"] is None
