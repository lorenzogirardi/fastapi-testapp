"""ASGI Basic Auth guard for the mounted MCP server at /api/mcp.

Mounted sub-apps (app.mount(...)) don't go through FastAPI's Depends() system,
so they don't get the same protection Depends(verify_credentials) gives
/api/debug/*. This applies an equivalent check at the ASGI layer, reusing the
same failure-tracking rate limiter as app.auth so brute-forcing either surface
counts against the same backoff.

The whole MCP mount is gated uniformly — unlike the REST API, where some
endpoints (contexts CRUD, /mgmt/info) are unauthenticated. MCP aggregates many
capabilities behind one entry point for an LLM caller, so it gets the
stricter, uniform boundary rather than mirroring each REST endpoint's
individual auth requirement.
"""

from __future__ import annotations

import base64
import json
import secrets

from fastapi import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import check_rate_limit, clear_failures, record_failure
from app.config import get_settings


def _parse_basic(header: str) -> tuple[str | None, str | None]:
    if not header.startswith("Basic "):
        return None, None
    try:
        decoded = base64.b64decode(header[len("Basic ") :]).decode()
    except Exception:
        return None, None
    username, sep, password = decoded.partition(":")
    return (username, password) if sep else (None, None)


class MCPBasicAuthMiddleware:
    """Wraps an ASGI app (the MCP streamable-HTTP app) with HTTP Basic Auth."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        client = scope.get("client")
        ip = client[0] if client else "unknown"

        try:
            check_rate_limit(ip)
        except HTTPException as e:
            await self._deny(send, e.status_code, str(e.detail), retry_after=(e.headers or {}).get("Retry-After"))
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode(errors="ignore")
        username, password = _parse_basic(auth_header)

        settings = get_settings()
        ok = (
            username is not None
            and password is not None
            and secrets.compare_digest(username, settings.diag_username)
            and secrets.compare_digest(password, settings.diag_password)
        )
        if not ok:
            record_failure(ip)
            await self._deny(send, 401, "Invalid credentials")
            return

        clear_failures(ip)
        await self._app(scope, receive, send)

    @staticmethod
    async def _deny(send: Send, status_code: int, message: str, retry_after: str | None = None) -> None:
        headers = [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b'Basic realm="pytbak-mcp"'),
        ]
        if retry_after:
            headers.append((b"retry-after", str(retry_after).encode()))
        body = json.dumps({"error": message}).encode()
        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})
