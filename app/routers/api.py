"""Core API router — contexts CRUD + legacy endpoints (fib, sleep, count, redis)."""

import asyncio
import time

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.config import get_settings
from app.models.schemas import ContextCreate, ContextResponse, ContextUpdate, ErrorResponse
from app.services import storage

router = APIRouter(prefix="/api", tags=["API"])


# ========== CONTEXTS ==========
@router.get("/contexts", response_model=list[ContextResponse], summary="List all contexts")
async def list_contexts():
    return await storage.get_all_contexts()


@router.get(
    "/contexts/{context_id}",
    response_model=ContextResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get context by ID",
)
async def get_context(context_id: str):
    ctx = await storage.get_context(context_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")
    return ctx


@router.post(
    "/contexts",
    response_model=ContextResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new context",
)
async def create_context(data: ContextCreate):
    return await storage.create_context(data)


@router.put(
    "/contexts/{context_id}",
    response_model=ContextResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Update a context",
)
async def update_context(context_id: str, data: ContextUpdate):
    ctx = await storage.update_context(context_id, data)
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")
    return ctx


@router.delete(
    "/contexts/{context_id}",
    responses={404: {"model": ErrorResponse}},
    summary="Delete a context",
)
async def delete_context(context_id: str):
    if not await storage.delete_context(context_id):
        raise HTTPException(status_code=404, detail="Context not found")
    return {"result": True}


# ========== LEGACY ENDPOINTS ==========
@router.get("/fib/{x}", summary="Calculate Fibonacci number")
async def fibonacci(x: int):
    if x < 0:
        raise HTTPException(status_code=400, detail="Input must be non-negative")
    if x > 20000:
        raise HTTPException(status_code=400, detail="Input too large, max 20000")

    def _compute(n: int) -> str:
        a, b = 0, 1
        for _ in range(n):
            a, b = b, a + b
        return str(a)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _compute, x)
    return {"result": result}


@router.get("/sleep/{seconds}", summary="Sleep for N seconds")
async def sleep_endpoint(seconds: int):
    if seconds < 0:
        raise HTTPException(status_code=400, detail="Sleep time must be non-negative")
    if seconds > 10:
        raise HTTPException(status_code=400, detail="Sleep time too long, max 10 seconds")
    await asyncio.sleep(seconds)
    return {"message": f"Delayed by {seconds} seconds"}


@router.get("/count", summary="Increment Redis counter")
async def count():
    value = await storage.redis_incr("hits")
    if value is None:
        return {"error": "Redis unavailable", "counter": None}
    return {"counter": value}


@router.get("/redisping", summary="Ping Redis via webdis (legacy)")
async def redis_ping():
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.webdis_url}/ping")
            return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    except Exception as e:
        return {"error": str(e)}
