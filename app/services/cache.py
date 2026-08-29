"""Local in-memory cache with TTL and LRU eviction."""

import time
from threading import Lock
from typing import Any

from app.config import get_settings


class TTLCache:
    """Thread-safe dict-based cache with per-item TTL and LRU eviction."""

    def __init__(self, default_ttl: int | None = None, max_size: int | None = None) -> None:
        settings = get_settings()
        self._ttl = default_ttl or settings.cache_ttl
        self._max_size = max_size or settings.cache_max_size
        # Store: key -> (value, expires_at, last_access)
        self._store: dict[str, tuple[Any, float, float]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at, _ = entry
            now = time.monotonic()
            if now > expires_at:
                del self._store[key]
                return None
            # Update last access time for LRU
            self._store[key] = (value, expires_at, now)
            return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                self._evict_expired()
            if len(self._store) >= self._max_size and key not in self._store:
                # Evict least recently used
                lru_key = min(self._store, key=lambda k: self._store[k][2])
                del self._store[lru_key]
            now = time.monotonic()
            expires_at = now + (ttl or self._ttl)
            self._store[key] = (value, expires_at, now)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, exp, _) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired()
            return len(self._store)


# Singleton cache instance
_cache: TTLCache | None = None


def get_cache() -> TTLCache:
    global _cache
    if _cache is None:
        _cache = TTLCache()
    return _cache
