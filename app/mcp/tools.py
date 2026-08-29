"""MCP tools exposing pytbak's capabilities to LLM callers.

Mounted at /api/mcp (see app/main.py), in-process — tools call the same
service/router functions the REST API calls, no HTTP hop back into pytbak
itself. Protected as a whole by MCPBasicAuthMiddleware (app/middleware/mcp_auth.py).

echo_headers has no MCP equivalent (there's no per-call HTTP request to
introspect from inside an in-process tool) and is intentionally omitted.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from app.models.schemas import ContextCreate, ContextUpdate, CpuSpikeRequest
from app.routers.api import count as _count_route
from app.routers.api import fibonacci as _fibonacci_route
from app.routers.api import redis_ping as _redis_ping_route
from app.routers.api import sleep_endpoint as _sleep_route
from app.routers.debug import cpu_spike as _cpu_spike_route
from app.routers.debug import curl as _curl_route
from app.routers.debug import dns_resolve as _dns_resolve_route
from app.routers.debug import network_scan as _network_scan_route
from app.routers.debug import ping_host as _ping_route
from app.routers.debug import random_error as _random_error_route
from app.routers.debug import tcp_check as _tcp_check_route
from app.routers.mgmt import app_env as _app_env_route
from app.routers.mgmt import app_info as _app_info_route
from app.routers.mgmt import app_threaddump as _threaddump_route
from app.services import storage
from app.services.health import get_health

mcp = MCPServer("pytbak")


def _structured_errors(fn: Callable[..., Awaitable[object]]) -> Callable[..., Awaitable[object]]:
    """Turn expected failures into structured results instead of transport-level errors.

    An LLM that passes a bad argument should get a result it can read and retry
    from, not a broken tool call. HTTPException covers the routers' own 4xx
    responses; ValidationError covers arguments that fail the Pydantic request
    models (title too long, duration out of range, ...) — those constraints live
    on the models, not on the tool signatures, so they only fire at call time.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs) -> object:
        try:
            return await fn(*args, **kwargs)
        except HTTPException as e:
            return {"error": True, "status_code": e.status_code, "detail": e.detail}
        except ValidationError as e:
            return {"error": True, "status_code": 422, "detail": e.errors(include_url=False)}

    return wrapper


# ---------- Contexts CRUD ----------


@mcp.tool()
@_structured_errors
async def list_contexts() -> object:
    """List all stored contexts (title/description/done items)."""
    return [c.model_dump(mode="json") for c in await storage.get_all_contexts()]


@mcp.tool()
@_structured_errors
async def get_context(context_id: str) -> object:
    """Get a single context by ID."""
    ctx = await storage.get_context(context_id)
    if ctx is None:
        return {"error": True, "status_code": 404, "detail": "Context not found"}
    return ctx.model_dump(mode="json")


@mcp.tool()
@_structured_errors
async def create_context(title: str, description: str = "") -> object:
    """Create a new context. Title must be 1-255 characters."""
    ctx = await storage.create_context(ContextCreate(title=title, description=description))
    return ctx.model_dump(mode="json")


@mcp.tool()
@_structured_errors
async def update_context(
    context_id: str, title: str | None = None, description: str | None = None, done: bool | None = None
) -> object:
    """Update a context. Only pass the fields you want to change."""
    fields = {k: v for k, v in {"title": title, "description": description, "done": done}.items() if v is not None}
    ctx = await storage.update_context(context_id, ContextUpdate(**fields))
    if ctx is None:
        return {"error": True, "status_code": 404, "detail": "Context not found"}
    return ctx.model_dump(mode="json")


@mcp.tool()
@_structured_errors
async def delete_context(context_id: str) -> object:
    """Delete a context by ID."""
    deleted = await storage.delete_context(context_id)
    if not deleted:
        return {"error": True, "status_code": 404, "detail": "Context not found"}
    return {"result": True}


# ---------- Load / legacy endpoints ----------


@mcp.tool()
@_structured_errors
async def fibonacci(x: int) -> object:
    """Compute the x-th Fibonacci number (max 20000). Useful for CPU load testing."""
    return await _fibonacci_route(x)


@mcp.tool()
@_structured_errors
async def sleep(seconds: int) -> object:
    """Sleep for N seconds server-side (max 10). Useful for latency testing."""
    return await _sleep_route(seconds)


@mcp.tool()
@_structured_errors
async def count() -> object:
    """Increment and return the server's Redis-backed hit counter."""
    return await _count_route()


@mcp.tool()
@_structured_errors
async def redis_ping() -> object:
    """Ping Redis via the legacy webdis path."""
    return await _redis_ping_route()


# ---------- Debug / diagnostics ----------


@mcp.tool()
@_structured_errors
async def network_scan(target: str) -> object:
    """Run ping+dns+tcp+traceroute against target ('host' or 'host:port')."""
    result = await _network_scan_route(target)
    return result.model_dump(mode="json")


@mcp.tool()
@_structured_errors
async def cpu_spike(duration: int = 10, cores: int = 1) -> object:
    """Burn CPU on the server for `duration` seconds across `cores` cores (max 120s / 16 cores)."""
    result = await _cpu_spike_route(CpuSpikeRequest(duration=duration, cores=cores))
    return result.model_dump(mode="json")


@mcp.tool()
@_structured_errors
async def ping(host: str, count: int = 3) -> object:
    """Ping a host from the server (count 1-20)."""
    resp = await _ping_route(host, count)
    return resp.body.decode()


@mcp.tool()
@_structured_errors
async def dns_resolve(name: str) -> object:
    """Resolve a hostname from the server."""
    return await _dns_resolve_route(name)


@mcp.tool()
@_structured_errors
async def curl(url: str) -> object:
    """HTTP GET a URL from the server's network vantage point."""
    resp = await _curl_route(url)
    return {"status_code": resp.status_code, "body": resp.body.decode(errors="replace")}


@mcp.tool()
@_structured_errors
async def tcp_check(host: str, port: int) -> object:
    """Check whether host:port accepts a TCP connection from the server."""
    return await _tcp_check_route(host, port)


@mcp.tool()
@_structured_errors
async def echo_body(body: str) -> object:
    """Echo a string back verbatim. Simple round-trip / connectivity check."""
    return body


@mcp.tool()
@_structured_errors
async def random_error() -> object:
    """Trigger a randomly chosen HTTP error (always errors by design)."""
    return await _random_error_route()


# ---------- Mgmt / observability ----------


@mcp.tool()
@_structured_errors
async def health() -> object:
    """Check pytbak's health (Redis/Postgres backend status)."""
    result = await get_health()
    return {"status": result["overall"], "checks": result["checks"]}


@mcp.tool()
@_structured_errors
async def ready() -> object:
    """Check pytbak's readiness probe."""
    return {"status": "READY"}


@mcp.tool()
@_structured_errors
async def app_info() -> object:
    """Get pytbak's app name/version/environment."""
    return await _app_info_route()


@mcp.tool()
@_structured_errors
async def app_env() -> object:
    """Get pytbak's allowlisted environment variables."""
    return await _app_env_route()


@mcp.tool()
@_structured_errors
async def app_mappings() -> object:
    """List all registered routes on this pytbak instance."""
    from app.main import app as fastapi_app

    routes = []
    for route in fastapi_app.routes:
        routes.append(
            {
                "path": getattr(route, "path", str(route)),
                "methods": sorted(getattr(route, "methods", set())),
                "name": getattr(route, "name", None),
            }
        )
    return {"mappings": routes}


@mcp.tool()
@_structured_errors
async def threaddump() -> object:
    """Get a thread dump from the server."""
    resp = await _threaddump_route()
    return resp.body.decode()
