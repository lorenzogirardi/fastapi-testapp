"""Shared health-check logic — used by REST /api/mgmt/health and the MCP health tool."""

from __future__ import annotations

from sqlalchemy import text

from app.models.database import get_session_factory
from app.services.redis_client import get_redis, is_redis_available


async def get_health() -> dict:
    """Check Redis and PostgreSQL. Returns overall status, per-backend checks, and the HTTP status code that should accompany them."""
    checks: list[dict] = []
    overall = "UP"

    if is_redis_available():
        r = get_redis()
        try:
            await r.ping()
            checks.append({"name": "redis", "status": "UP", "error": None})
        except Exception as e:
            checks.append({"name": "redis", "status": "DOWN", "error": str(e)})
            overall = "DEGRADED"
    else:
        checks.append({"name": "redis", "status": "NOT_CONFIGURED", "error": None})

    sf = get_session_factory()
    if sf:
        try:
            async with sf() as session:
                await session.execute(text("SELECT 1"))
            checks.append({"name": "postgresql", "status": "UP", "error": None})
        except Exception as e:
            checks.append({"name": "postgresql", "status": "DOWN", "error": str(e)})
            overall = "DEGRADED"
    else:
        checks.append({"name": "postgresql", "status": "NOT_CONFIGURED", "error": None})

    configured = [c for c in checks if c["status"] != "NOT_CONFIGURED"]
    all_configured_down = bool(configured) and all(c["status"] == "DOWN" for c in configured)
    status_code = 503 if all_configured_down else 200

    return {"overall": overall, "checks": checks, "status_code": status_code}
