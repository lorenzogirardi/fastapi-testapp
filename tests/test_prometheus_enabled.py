"""The app must actually serve requests with Prometheus instrumentation on.

conftest.py forces PROMETHEUS_ENABLED=false for the rest of the suite, so
nothing else here exercises the instrumentator middleware — while production
(and the Helm chart, and the k8s probe in CI) runs with it enabled by default.

That gap let a real outage through: bumping fastapi to 0.141.1 put
`_IncludedRouter` objects in `app.routes`, which
prometheus-fastapi-instrumentator 7.0.2 could not handle
(`AttributeError: '_IncludedRouter' object has no attribute 'path'`). Every
request 500'd in the container while all 62 tests stayed green, because the
suite never loaded that middleware. These tests build the app the way
production does.
"""

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app


@pytest.fixture
def prometheus_app(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield create_app()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("path", ["/api/mgmt/ready", "/api/mgmt/health"])
def test_requests_succeed_with_instrumentation_enabled(prometheus_app, path):
    # raise_server_exceptions=False so a middleware crash shows up as the 500
    # a real client would get, instead of propagating and masking the assert.
    client = TestClient(prometheus_app, raise_server_exceptions=False)
    assert client.get(path).status_code == 200


def test_metrics_endpoint_is_exposed(prometheus_app):
    client = TestClient(prometheus_app, raise_server_exceptions=False)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "python_info" in response.text


def test_request_is_actually_counted(prometheus_app):
    """Instrumentation is wired up, not merely importable without crashing."""
    client = TestClient(prometheus_app, raise_server_exceptions=False)
    client.get("/api/mgmt/ready")
    assert "http_requests_total" in client.get("/metrics").text
