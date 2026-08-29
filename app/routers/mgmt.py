"""Management router — health, ready, info, env, mappings, threaddump."""

import os
import sys
import traceback

from fastapi import APIRouter, Depends, Request
from loguru import logger

from app.auth import verify_credentials
from app.config import get_settings
from app.models.schemas import AppInfoResponse, HealthCheck, HealthResponse
from app.services.health import get_health

router = APIRouter(prefix="/api/mgmt", tags=["Management"])


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health_check():
    result = await get_health()
    from fastapi.responses import JSONResponse

    checks = [HealthCheck(**c) for c in result["checks"]]
    return JSONResponse(
        content=HealthResponse(status=result["overall"], checks=checks).model_dump(),
        status_code=result["status_code"],
    )


@router.get("/ready", summary="Readiness probe")
async def readiness():
    """Returns 200 when app is ready to serve traffic (always true once started)."""
    return {"status": "READY"}


@router.get("/info", response_model=AppInfoResponse, summary="Application info")
async def app_info():
    settings = get_settings()
    return {
        "app": {
            "name": settings.app_name,
            "description": "Python REST API Test Application (FastAPI)",
            "version": settings.app_version,
            "environment": settings.app_env,
        }
    }


@router.get("/env", summary="Whitelisted environment variables")
async def app_env():
    safe_vars = [
        "PATH", "HOSTNAME", "REDIS_HOST", "REDIS_PORT", "REDIS_DB",
        "OTEL_ENABLED", "APP_ENV",
        "DD_PROFILING_ENABLED", "DD_LOGS_INJECTION",
    ]
    return {k: v for k, v in os.environ.items() if k in safe_vars}


@router.get("/mappings", summary="All URL route mappings")
async def app_mappings(request: Request):
    routes = []
    for route in request.app.routes:
        info = {
            "path": getattr(route, "path", str(route)),
            "methods": sorted(getattr(route, "methods", set())),
            "name": getattr(route, "name", None),
        }
        routes.append(info)
    return {"mappings": routes}


@router.get(
    "/threaddump",
    summary="Thread dump",
    dependencies=[Depends(verify_credentials)],
)
async def app_threaddump():
    dump = []
    for thread_id, stack in sys._current_frames().items():
        dump.append(f"\n# Thread: {thread_id}")
        for filename, lineno, name, line in traceback.extract_stack(stack):
            dump.append(f'  File: "{filename}", line {lineno}, in {name}')
            if line:
                dump.append(f"    {line.strip()}")
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("\n".join(dump))
