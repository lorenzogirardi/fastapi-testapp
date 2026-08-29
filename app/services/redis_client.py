"""Async Redis client with retry and graceful fallback."""

import asyncio

import redis.asyncio as aioredis
from loguru import logger

from app.config import get_settings

_redis: aioredis.Redis | None = None
_available: bool = False
_lock = asyncio.Lock()


async def init_redis() -> bool:
    """Initialize Redis connection. Returns True if successful."""
    global _redis, _available
    async with _lock:
        settings = get_settings()
        url = settings.effective_redis_url
        if not url:
            logger.warning("No Redis URL configured, running without Redis")
            return False
        try:
            _redis = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=5)
            await _redis.ping()
            _available = True
            logger.info("Redis connected: {}", settings.sanitized_redis_url)
            return True
        except Exception as e:
            logger.warning("Redis unavailable ({}), fallback to local storage", e)
            _redis = None
            _available = False
            return False


async def close_redis() -> None:
    global _redis, _available
    async with _lock:
        if _redis:
            await _redis.aclose()
            _redis = None
            _available = False


def get_redis() -> aioredis.Redis | None:
    return _redis if _available else None


def is_redis_available() -> bool:
    return _available


async def redis_op_with_retry(coro_factory, retries: int | None = None):
    """Execute a Redis coroutine with retry logic. Returns None on failure."""
    settings = get_settings()
    max_retries = retries or settings.redis_retry_attempts
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as e:
            logger.warning("Redis op failed (attempt {}/{}): {}", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                await asyncio.sleep(settings.redis_retry_delay)
    return None
