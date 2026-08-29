"""FastAPI application entry point."""

import pathlib
import sys
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import get_settings
from app.middleware.error_injection import ErrorInjectionMiddleware
from app.models.database import close_db, init_db
from app.services.redis_client import close_redis, init_redis


# --- Structured logging (loguru + JSON) ---
def _setup_logging() -> None:
    settings = get_settings()
    logger.remove()
    if settings.app_env == "production":
        logger.add(sys.stderr, level=settings.log_level, serialize=True)
    else:
        logger.add(
            sys.stderr,
            level=settings.log_level,
            format="{time:YYYY-MM-DDTHH:mm:ss.SSS}Z | {level:<8} | {name}:{function}:{line} | {message}",
        )


def _make_lifespan(mcp_server):
    """Build the lifespan context manager. mcp_server is None when MCP is disabled.

    FastAPI's app.mount() does not forward lifespan events to mounted sub-apps,
    so the MCP session manager (which streamable_http_app() normally starts via
    its own lifespan) has to be started here explicitly — otherwise requests to
    /api/mcp hang forever waiting on a session manager that never started.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _setup_logging()
        settings = get_settings()

        # Init backends (graceful: failures are logged, not fatal)
        db_ok = await init_db()
        redis_ok = await init_redis()
        logger.info(
            "Startup complete | pg={} redis={} env={}",
            "connected" if db_ok else "fallback",
            "connected" if redis_ok else "fallback",
            settings.app_env,
        )

        async with AsyncExitStack() as stack:
            if mcp_server is not None:
                await stack.enter_async_context(mcp_server.session_manager.run())
            yield

        await close_redis()
        await close_db()
        logger.info("Shutdown complete")

    return lifespan


def create_app() -> FastAPI:
    settings = get_settings()

    # Built before FastAPI() so the lifespan closure can start its session manager.
    mcp_server = None
    mcp_asgi_app = None
    if settings.mcp_enabled:
        from app.mcp.tools import mcp as mcp_server

        mcp_asgi_app = mcp_server.streamable_http_app(streamable_http_path="/", stateless_http=True)  # lazily creates session_manager

    app = FastAPI(
        title="pytbak API",
        description="Python REST API Test & Debug Application (FastAPI)",
        version=settings.app_version,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=_make_lifespan(mcp_server),
    )

    # --- Middleware ---
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(ErrorInjectionMiddleware)

    # --- CORS ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Request logging middleware ---
    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        start = time.monotonic()
        response = await call_next(request)
        elapsed = (time.monotonic() - start) * 1000
        logger.info(
            "{} {} {} {:.1f}ms [{}]",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
            request_id,
        )
        response.headers["X-Request-ID"] = request_id
        return response

    # --- Rate limiting ---
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.util import get_remote_address

        limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    except ImportError:
        logger.warning("slowapi not installed, rate limiting disabled")

    # --- Prometheus metrics ---
    if settings.prometheus_enabled:
        try:
            from prometheus_fastapi_instrumentator import Instrumentator

            Instrumentator().instrument(app).expose(app, endpoint="/metrics")
        except ImportError:
            logger.warning("prometheus-fastapi-instrumentator not installed, /metrics disabled")

    # --- OpenTelemetry ---
    if settings.otel_enabled:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({"service.name": settings.otel_service_name})
            provider = TracerProvider(resource=resource)
            exporter = OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                timeout=settings.otel_exporter_timeout,
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            FastAPIInstrumentor.instrument_app(app)
            logger.info("OpenTelemetry tracing enabled -> {}", settings.otel_exporter_otlp_endpoint)
        except ImportError:
            logger.warning("OpenTelemetry packages not installed, tracing disabled")

    # --- Static files ---
    static_dir = pathlib.Path(__file__).resolve().parent / "static"
    app.mount("/api/static", StaticFiles(directory=str(static_dir)), name="static")

    # --- Routers ---
    from app.routers.api import router as api_router
    from app.routers.home import router as home_router
    from app.routers.mgmt import router as mgmt_router

    app.include_router(api_router)
    app.include_router(mgmt_router)
    app.include_router(home_router)

    # Debug endpoints (can be disabled in production)
    if settings.debug_endpoints_enabled:
        from app.routers.debug import router as debug_router
        app.include_router(debug_router)
    else:
        logger.info("Debug endpoints disabled (DEBUG_ENDPOINTS_ENABLED=false)")

    # --- MCP server (in-process, Basic Auth protected) ---
    if settings.mcp_enabled:
        from app.middleware.mcp_auth import MCPBasicAuthMiddleware

        app.mount("/api/mcp", MCPBasicAuthMiddleware(mcp_asgi_app))
        logger.info("MCP server mounted at /api/mcp")
    else:
        logger.info("MCP server disabled (MCP_ENABLED=false)")

    # --- Error handlers ---
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):
        return JSONResponse(status_code=404, content={"error": "Not found"})

    @app.exception_handler(400)
    async def bad_request_handler(request: Request, exc):
        return JSONResponse(status_code=400, content={"error": "Bad request"})

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=settings.shutdown_timeout,
    )
