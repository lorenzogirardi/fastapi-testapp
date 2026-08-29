"""Shared fixtures for integration tests against the live Docker stack."""

import httpx
import pytest

_BASE_URL = "http://localhost:8000"
_AUTH = ("admin", "password")


def _service_is_up() -> bool:
    try:
        with httpx.Client(timeout=3) as c:
            return c.get(f"{_BASE_URL}/api/mgmt/ready").status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_service() -> str:
    """Skip the entire integration suite if the Docker stack is not running."""
    if not _service_is_up():
        pytest.skip("Live service not reachable at http://localhost:8000 — start with: docker compose up -d")
    return _BASE_URL


@pytest.fixture
async def client(live_service: str):
    async with httpx.AsyncClient(base_url=live_service, timeout=10) as c:
        yield c


@pytest.fixture
async def auth_client(live_service: str):
    async with httpx.AsyncClient(base_url=live_service, timeout=10, auth=_AUTH) as c:
        yield c
