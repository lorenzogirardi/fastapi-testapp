"""Basic HTTP authentication with incremental rate limiting after failed attempts."""

import secrets
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from loguru import logger

from app.config import get_settings

security = HTTPBasic()

# Track failed attempts per IP: {ip: [(timestamp, fail_count)]}
_fail_tracker: dict[str, list[float]] = defaultdict(list)
_WINDOW = 600  # 10 minute window
_MAX_FAILURES = 3  # failures before rate limiting kicks in
_BASE_DELAY = 2  # base delay in seconds, doubles each attempt after threshold
_MAX_DELAY = 60  # cap delay at 60 seconds


def _cleanup_old(ip: str) -> None:
    """Remove entries older than the tracking window."""
    cutoff = time.monotonic() - _WINDOW
    _fail_tracker[ip] = [t for t in _fail_tracker[ip] if t > cutoff]
    if not _fail_tracker[ip]:
        _fail_tracker.pop(ip, None)


def check_rate_limit(ip: str) -> None:
    """Check if IP is rate limited due to failed attempts.

    Public: also used by app/middleware/mcp_auth.py so the /api/mcp mount
    shares one backoff budget with the /api/debug/* endpoints.
    """
    _cleanup_old(ip)
    failures = _fail_tracker.get(ip, [])
    if len(failures) < _MAX_FAILURES:
        return

    # Incremental delay: 2s, 4s, 8s, 16s... capped at 60s
    excess = len(failures) - _MAX_FAILURES
    delay = min(_BASE_DELAY * (2 ** excess), _MAX_DELAY)
    last_fail = failures[-1]
    elapsed = time.monotonic() - last_fail

    if elapsed < delay:
        remaining = round(delay - elapsed)
        logger.warning("Rate limited IP {} — {} failures, retry in {}s", ip, len(failures), remaining)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Retry in {remaining}s",
            headers={"Retry-After": str(remaining)},
        )


def record_failure(ip: str) -> None:
    """Record a failed auth attempt. Shared with mcp_auth (see check_rate_limit)."""
    _cleanup_old(ip)
    _fail_tracker[ip].append(time.monotonic())
    count = len(_fail_tracker[ip])
    logger.warning("Auth failure from {} — attempt {}/{} before rate limit", ip, count, _MAX_FAILURES)


def clear_failures(ip: str) -> None:
    """Clear failure history on successful auth. Shared with mcp_auth (see check_rate_limit)."""
    _fail_tracker.pop(ip, None)


def reset_auth_state() -> None:
    """Clear all rate-limiting state. For tests only."""
    _fail_tracker.clear()


def verify_credentials(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(security),
) -> str:
    """Dependency that checks basic auth credentials with incremental rate limiting."""
    ip = request.client.host if request.client else "unknown"

    check_rate_limit(ip)

    settings = get_settings()
    correct_user = secrets.compare_digest(credentials.username, settings.diag_username)
    correct_pass = secrets.compare_digest(credentials.password, settings.diag_password)
    if not (correct_user and correct_pass):
        record_failure(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    clear_failures(ip)
    return credentials.username
