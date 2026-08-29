"""Middleware for error injection and delay via query params."""

import asyncio
import math
import random

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings


class ErrorInjectionMiddleware(BaseHTTPMiddleware):
    """Inject errors and delays via query params on ANY endpoint.

    ?inject_error=500          -> returns HTTP 500
    ?inject_error=429          -> returns HTTP 429
    ?inject_error=validation_error -> returns HTTP 422
    ?inject_error=custom:msg   -> returns HTTP 500 with custom message
    ?delay_ms=5000             -> delay 5 seconds
    ?delay_ms=random           -> delay random 0-5s

    Disable entirely with ERROR_INJECTION_ENABLED=false. delay_ms is capped at
    ERROR_INJECTION_MAX_DELAY_MS and rejects NaN/inf/negative values so an
    unauthenticated request cannot hang an event-loop worker indefinitely.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            settings = get_settings()
            if not settings.error_injection_enabled:
                return await call_next(request)

            params = request.query_params

            # --- Delay ---
            delay = params.get("delay_ms")
            if delay:
                if delay.lower() == "random":
                    ms = random.uniform(0, min(5000, settings.error_injection_max_delay_ms))
                else:
                    try:
                        ms = float(delay)
                    except ValueError:
                        ms = 0
                    # Reject NaN/inf/negative/zero: only a bounded positive value can delay.
                    if not math.isfinite(ms) or ms < 0:
                        ms = 0
                    ms = min(ms, settings.error_injection_max_delay_ms)
                if ms > 0:
                    await asyncio.sleep(ms / 1000.0)

            # --- Error injection ---
            error = params.get("inject_error")
            if error:
                if error.isdigit():
                    code = int(error)
                    return JSONResponse(
                        status_code=code,
                        content={"error": f"Injected error {code}", "injected": True},
                    )
                if error == "validation_error":
                    return JSONResponse(
                        status_code=422,
                        content={"error": "Injected validation error", "injected": True},
                    )
                if error.startswith("custom:"):
                    msg = error[7:]
                    return JSONResponse(
                        status_code=500,
                        content={"error": msg, "injected": True},
                    )
                return JSONResponse(
                    status_code=500,
                    content={"error": f"Injected error: {error}", "injected": True},
                )

            return await call_next(request)
        except Exception as e:
            logger.error("Unhandled error in middleware: {}", e)
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error"},
            )
