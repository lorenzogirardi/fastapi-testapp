"""E2E — full user flows. Pyramid tip: fewest tests, highest confidence."""

import pytest


@pytest.mark.anyio
async def test_full_context_lifecycle(client):
    """Create → read → update → delete — full round-trip through PG."""
    # Create
    r = await client.post("/api/contexts", json={"title": "e2e-lifecycle", "description": "full flow"})
    assert r.status_code == 201
    ctx_id = r.json()["id"]
    assert r.json()["done"] is False

    # Read
    r = await client.get(f"/api/contexts/{ctx_id}")
    assert r.status_code == 200
    assert r.json()["title"] == "e2e-lifecycle"

    # Update
    r = await client.put(f"/api/contexts/{ctx_id}", json={"title": "e2e-done", "done": True})
    assert r.status_code == 200
    assert r.json()["done"] is True

    # Verify update persisted
    r = await client.get(f"/api/contexts/{ctx_id}")
    assert r.json()["title"] == "e2e-done"

    # Delete
    r = await client.delete(f"/api/contexts/{ctx_id}")
    assert r.status_code == 200

    # Verify gone
    r = await client.get(f"/api/contexts/{ctx_id}")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_stack_observability_and_compute(client):
    """Health shows all backends UP, Redis counter works, Fibonacci computes."""
    # Stack health
    health = await client.get("/api/mgmt/health")
    assert health.status_code == 200
    by_name = {c["name"]: c["status"] for c in health.json()["checks"]}
    assert by_name["redis"] == "UP"
    assert by_name["postgresql"] == "UP"

    # Redis counter is live
    r1 = await client.get("/api/count")
    r2 = await client.get("/api/count")
    assert r2.json()["counter"] == r1.json()["counter"] + 1

    # Compute is correct
    fib = await client.get("/api/fib/15")
    assert fib.json()["result"] == "610"

    # Metrics endpoint is serving
    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    assert "python_gc" in metrics.text
