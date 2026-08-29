"""Shared fixtures for pytest."""

import os

# Force test settings BEFORE any import
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = ""
os.environ["REDIS_URL"] = ""
os.environ["OTEL_ENABLED"] = "false"
os.environ["PROMETHEUS_ENABLED"] = "false"
os.environ["DIAG_USERNAME"] = "admin"
os.environ["DIAG_PASSWORD"] = "test-password-only"
os.environ["DEBUG_ENDPOINTS_ENABLED"] = "true"
os.environ["MCP_ENABLED"] = "true"
os.environ["ERROR_INJECTION_ENABLED"] = "true"

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param

# Clear cached settings so test env vars take effect
from app.config.settings import get_settings
get_settings.cache_clear()

from app.main import create_app


@pytest.fixture
def app():
    get_settings.cache_clear()
    application = create_app()
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_headers():
    import base64
    creds = base64.b64encode(b"admin:test-password-only").decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture(autouse=True)
def reset_auth():
    """Wipe auth rate-limit state before/after every test."""
    from app.auth import reset_auth_state
    reset_auth_state()
    yield
    reset_auth_state()


@pytest.fixture(autouse=True)
def reset_storage():
    """Wipe in-memory store and local cache before/after every test."""
    from app.services.cache import get_cache
    from app.services.storage import reset_memory_store
    reset_memory_store()
    get_cache().clear()
    yield
    reset_memory_store()
    get_cache().clear()
