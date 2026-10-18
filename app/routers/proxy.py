"""Internal web-proxy endpoint (hidden from the public API schema).

Proxies outbound web requests through the configured upstream worker via
``https://<worker>/?url=<destination>``. The worker endpoint enforces its own
HTTP Basic Auth and is responsible for its own credentials — the application
never stores, embeds, or forwards any credentials. The route is marked
``include_in_schema=False`` so it does not appear in the public OpenAPI docs.
"""

from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from loguru import logger

from app.config import get_settings

router = APIRouter(prefix="/api/internal", tags=["internal"])

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOST_SUFFIXES = (".internal", ".local")
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _validate_target(target: str) -> None:
    """Reject non-http(s) targets and, when SSRF protection is on, private hosts."""
    parsed = urlparse(target)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise HTTPException(status_code=400, detail="Only http/https targets are allowed")

    settings = get_settings()
    if settings.ssrf_protection_enabled and parsed.hostname:
        host = parsed.hostname.lower()
        if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
            raise HTTPException(status_code=400, detail="Target host is not allowed")


@router.get("/web-proxy", include_in_schema=False, summary="Internal web proxy (hidden)")
async def web_proxy(
    url: str = Query(..., description="Destination URL to fetch via the upstream worker"),
    timeout: int = Query(15, ge=1, le=60, description="Upstream request timeout in seconds"),
):
    """Forward ``url`` to the configured upstream worker.

    The worker owns its own authentication; the application does not handle any
    credentials. Intended for internal app use, not end-user traffic.
    """
    _validate_target(url)

    settings = get_settings()
    upstream = f"{settings.proxy_worker_url.rstrip('/')}/?url={url}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(upstream, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.warning("web-proxy upstream error: {}", exc)
        raise HTTPException(status_code=502, detail=f"Upstream proxy error: {exc}")

    excluded = {"content-length", "content-encoding", "transfer-encoding", "connection"}
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
    return Response(content=resp.content, status_code=resp.status_code, headers=headers)
