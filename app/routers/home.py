"""Home dashboard router — serves the DebugKnife UI."""

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.models.database import get_session_factory
from app.services.redis_client import is_redis_available

_TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/api", tags=["Dashboard"])


def _status_dot(status: str) -> str:
    """Map a status string to a CSS dot color class."""
    s = status.upper()
    if s in ("UP", "CONNECTED", "ENABLED"):
        return "green"
    if s in ("DEGRADED", "NOT_CONFIGURED", "DISABLED"):
        return "yellow"
    if s in ("DOWN", "ERROR"):
        return "red"
    return "gray"


async def _get_infra_status() -> dict:
    """Collect infrastructure status for dashboard and API."""
    settings = get_settings()

    # PostgreSQL
    sf = get_session_factory()
    if sf:
        try:
            from sqlalchemy import text
            async with sf() as session:
                await session.execute(text("SELECT 1"))
            pg_status = "Connected"
        except Exception:
            pg_status = "Down"
    else:
        pg_status = "Not Configured"

    # Redis
    if is_redis_available():
        from app.services.redis_client import get_redis
        try:
            await get_redis().ping()
            redis_status = "Connected"
        except Exception:
            redis_status = "Down"
    else:
        redis_status = "Not Configured"

    otel_status = "Enabled" if settings.otel_enabled else "Disabled"
    prom_status = "Enabled" if settings.prometheus_enabled else "Disabled"

    return {
        "pg_status": pg_status,
        "redis_status": redis_status,
        "otel_status": otel_status,
        "prom_status": prom_status,
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request):
    settings = get_settings()
    base_path = "/api"
    infra = await _get_infra_status()

    return templates.TemplateResponse(request, "index.html", {
        "base_path": base_path,
        "app_name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "pg_status": infra["pg_status"],
        "pg_color": "green" if infra["pg_status"] == "Connected" else "yellow",
        "pg_dot": _status_dot(infra["pg_status"]),
        "redis_status": infra["redis_status"],
        "redis_color": "green" if infra["redis_status"] == "Connected" else "yellow",
        "redis_dot": _status_dot(infra["redis_status"]),
        "otel_status": infra["otel_status"],
        "otel_dot": _status_dot(infra["otel_status"]),
        "prom_status": infra["prom_status"],
        "prom_dot": _status_dot(infra["prom_status"]),
    })


@router.get("/status", summary="Dashboard status API", tags=["Dashboard"])
async def dashboard_status(request: Request):
    """Returns infrastructure status + route mappings for the real-time dashboard."""
    settings = get_settings()
    infra = await _get_infra_status()

    # Collect routes
    routes = []
    for route in request.app.routes:
        info = {
            "path": getattr(route, "path", str(route)),
            "methods": sorted(getattr(route, "methods", set())),
            "name": getattr(route, "name", None),
        }
        if info["name"]:
            routes.append(info)

    return {
        "app": {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.app_env,
        },
        "infra": infra,
        "endpoints": len(routes),
        "routes": routes,
    }
