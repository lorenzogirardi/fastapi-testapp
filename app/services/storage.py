"""Unified storage service with fallback chain: PostgreSQL -> Redis -> In-Memory.

Architecture:
  StorageBackend  — Protocol defining the interface each backend implements
  PostgreSQLBackend / RedisBackend / InMemoryBackend — concrete implementations
  StorageService  — composes the three backends with fallback logic
  Public functions — unchanged API consumed by routers
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from loguru import logger

from app.models.database import ContextDB, get_session_factory
from app.models.schemas import ContextCreate, ContextResponse, ContextUpdate
from app.services.redis_client import get_redis, is_redis_available, redis_op_with_retry


# ── Exceptions ─────────────────────────────────────────────────────────────────

class BackendError(Exception):
    """Raised when a backend operation fails. StorageService catches this to fall through."""


# ── Protocol ───────────────────────────────────────────────────────────────────

@runtime_checkable
class StorageBackend(Protocol):
    def is_available(self) -> bool: ...
    async def get_all(self) -> list[ContextResponse]: ...
    async def get(self, context_id: str) -> ContextResponse | None: ...
    async def store(self, context_id: str, ctx: dict) -> None: ...
    async def delete(self, context_id: str) -> None: ...


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dict_to_response(data: dict) -> ContextResponse:
    return ContextResponse(**data)


def _db_row_to_response(row: ContextDB) -> ContextResponse:
    return ContextResponse(
        id=row.id,
        title=row.title,
        description=row.description,
        done=row.done,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ── InMemoryBackend ────────────────────────────────────────────────────────────

class InMemoryBackend:
    """Dict-backed store. Always available, always last resort.
    Instance state (not module-level global) eliminates test pollution."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    def is_available(self) -> bool:
        return True

    async def get_all(self) -> list[ContextResponse]:
        return [_dict_to_response(v) for v in self._store.values()]

    async def get(self, context_id: str) -> ContextResponse | None:
        item = self._store.get(context_id)
        return _dict_to_response(item) if item else None

    async def store(self, context_id: str, ctx: dict) -> None:
        self._store[context_id] = ctx

    async def delete(self, context_id: str) -> None:
        self._store.pop(context_id, None)

    def clear(self) -> None:
        """Clear all stored contexts. For tests only."""
        self._store.clear()


# ── PostgreSQLBackend ──────────────────────────────────────────────────────────

class PostgreSQLBackend:
    """SQLAlchemy async backend. Uses merge() for upsert (handles both create and update)."""

    def is_available(self) -> bool:
        return get_session_factory() is not None

    async def get_all(self) -> list[ContextResponse]:
        from sqlalchemy import select
        sf = get_session_factory()
        try:
            async with sf() as session:
                result = await session.execute(select(ContextDB))
                return [_db_row_to_response(r) for r in result.scalars().all()]
        except Exception as e:
            raise BackendError(f"PG get_all failed: {e}") from e

    async def get(self, context_id: str) -> ContextResponse | None:
        sf = get_session_factory()
        try:
            async with sf() as session:
                row = await session.get(ContextDB, context_id)
                return _db_row_to_response(row) if row else None
        except Exception as e:
            raise BackendError(f"PG get failed: {e}") from e

    async def store(self, context_id: str, ctx: dict) -> None:
        sf = get_session_factory()
        try:
            async with sf() as session:
                row = ContextDB(
                    id=context_id,
                    title=ctx["title"],
                    description=ctx["description"],
                    done=ctx["done"],
                    created_at=datetime.fromisoformat(ctx["created_at"]),
                    updated_at=datetime.fromisoformat(ctx["updated_at"]),
                )
                await session.merge(row)
                await session.commit()
        except Exception as e:
            raise BackendError(f"PG store failed: {e}") from e

    async def delete(self, context_id: str) -> None:
        sf = get_session_factory()
        try:
            async with sf() as session:
                row = await session.get(ContextDB, context_id)
                if row:
                    await session.delete(row)
                    await session.commit()
        except Exception as e:
            raise BackendError(f"PG delete failed: {e}") from e


# ── RedisBackend ───────────────────────────────────────────────────────────────

class RedisBackend:
    """Redis backend. JSON-serialised context dicts under key 'context:{id}'."""

    def is_available(self) -> bool:
        return is_redis_available()

    async def get_all(self) -> list[ContextResponse]:
        r = get_redis()
        try:
            keys = await r.keys("context:*")
            contexts: list[ContextResponse] = []
            for key in keys:
                try:
                    data = await r.get(key)
                    if data:
                        contexts.append(ContextResponse(**json.loads(data)))
                except Exception as e:
                    logger.warning("Skipping corrupt Redis key {}: {}", key, e)
            return contexts
        except Exception as e:
            raise BackendError(f"Redis get_all failed: {e}") from e

    async def get(self, context_id: str) -> ContextResponse | None:
        r = get_redis()
        try:
            data = await r.get(f"context:{context_id}")
            return ContextResponse(**json.loads(data)) if data else None
        except Exception as e:
            raise BackendError(f"Redis get failed: {e}") from e

    async def store(self, context_id: str, ctx: dict) -> None:
        try:
            await redis_op_with_retry(lambda: get_redis().set(f"context:{context_id}", json.dumps(ctx)))
        except Exception as e:
            raise BackendError(f"Redis store failed: {e}") from e

    async def delete(self, context_id: str) -> None:
        try:
            await redis_op_with_retry(lambda: get_redis().delete(f"context:{context_id}"))
        except Exception as e:
            raise BackendError(f"Redis delete failed: {e}") from e

    async def incr(self, key: str) -> int | None:
        r = get_redis()
        if r:
            return await redis_op_with_retry(lambda: r.incr(key))
        return None


# ── StorageService ─────────────────────────────────────────────────────────────

class StorageService:
    """Composes PG + Redis + Memory with the fallback chain.

    Read path:  local cache → PG → Redis → Memory
    Write path: PG (best-effort) → Redis (best-effort) → Memory (always)
    """

    def __init__(self, pg: PostgreSQLBackend, redis: RedisBackend, memory: InMemoryBackend) -> None:
        self._pg = pg
        self._redis = redis
        self._memory = memory

    def _cache(self):
        from app.services.cache import get_cache
        return get_cache()

    async def get_all(self) -> list[ContextResponse]:
        for backend in (self._pg, self._redis):
            if backend.is_available():
                try:
                    return await backend.get_all()
                except BackendError as e:
                    logger.warning("{}", e)
        return await self._memory.get_all()

    async def get(self, context_id: str) -> ContextResponse | None:
        cached = self._cache().get(f"ctx:{context_id}")
        if cached:
            return _dict_to_response(cached)

        for backend in (self._pg, self._redis):
            if backend.is_available():
                try:
                    result = await backend.get(context_id)
                    # Backend responded authoritatively (found or not found) — trust it.
                    if result is not None:
                        self._cache().set(f"ctx:{context_id}", result.model_dump(mode="json"))
                    return result
                except BackendError as e:
                    logger.warning("{}", e)
                    # Backend errored — fall through to next.

        return await self._memory.get(context_id)

    async def create(self, data: ContextCreate) -> ContextResponse:
        context_id = str(uuid.uuid4())
        now = _now()
        ctx = {
            "id": context_id,
            "title": data.title,
            "description": data.description,
            "done": False,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        self._cache().set(f"ctx:{context_id}", ctx)

        for backend in (self._pg, self._redis):
            if backend.is_available():
                try:
                    await backend.store(context_id, ctx)
                except BackendError as e:
                    logger.warning("{}", e)

        await self._memory.store(context_id, ctx)
        return _dict_to_response(ctx)

    async def update(self, context_id: str, data: ContextUpdate) -> ContextResponse | None:
        existing = await self.get(context_id)
        if not existing:
            return None

        updates = data.model_dump(exclude_unset=True)
        updated = existing.model_dump(mode="json")
        updated.update(updates)
        updated["updated_at"] = _now().isoformat()

        self._cache().set(f"ctx:{context_id}", updated)

        for backend in (self._pg, self._redis):
            if backend.is_available():
                try:
                    await backend.store(context_id, updated)
                except BackendError as e:
                    logger.warning("{}", e)

        await self._memory.store(context_id, updated)
        return _dict_to_response(updated)

    async def delete(self, context_id: str) -> bool:
        existing = await self.get(context_id)
        if not existing:
            return False

        self._cache().delete(f"ctx:{context_id}")

        for backend in (self._pg, self._redis):
            if backend.is_available():
                try:
                    await backend.delete(context_id)
                except BackendError as e:
                    logger.warning("{}", e)

        await self._memory.delete(context_id)
        return True


# ── Singletons ─────────────────────────────────────────────────────────────────

_memory_backend = InMemoryBackend()
_service = StorageService(
    pg=PostgreSQLBackend(),
    redis=RedisBackend(),
    memory=_memory_backend,
)


# ── Public API (interface unchanged — routers require no changes) ───────────────

async def get_all_contexts() -> list[ContextResponse]:
    return await _service.get_all()


async def get_context(context_id: str) -> ContextResponse | None:
    return await _service.get(context_id)


async def create_context(data: ContextCreate) -> ContextResponse:
    return await _service.create(data)


async def update_context(context_id: str, data: ContextUpdate) -> ContextResponse | None:
    return await _service.update(context_id, data)


async def delete_context(context_id: str) -> bool:
    return await _service.delete(context_id)


async def redis_incr(key: str) -> int | None:
    """Increment a Redis counter. Returns None if Redis unavailable."""
    if not _service._redis.is_available():
        return None
    return await _service._redis.incr(key)


def reset_memory_store() -> None:
    """Clear in-memory fallback store. For tests only."""
    _memory_backend.clear()
