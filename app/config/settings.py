"""Application configuration via Pydantic Settings (env-based)."""

import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All config is driven by environment variables with sane defaults."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # --- App ---
    app_name: str = "pytbak"
    app_version: str = "2.0.0"
    app_env: Literal["development", "production", "test"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    shutdown_timeout: int = Field(default=30, description="Graceful shutdown timeout in seconds")

    # --- Auth (basic auth for diag endpoints) ---
    diag_username: str = "admin"
    diag_password: str = "password"

    # --- Debug endpoints & error injection ---
    debug_endpoints_enabled: bool = Field(
        default=False,
        description="Mount /api/debug/* (opt-in; defaults off so production never exposes network tools)",
    )
    error_injection_enabled: bool = Field(default=True, description="Enable ?inject_error=/?delay_ms= middleware")
    error_injection_max_delay_ms: int = Field(default=10000, description="Cap for ?delay_ms= to stop unbounded sleeps")

    # --- MCP server ---
    mcp_enabled: bool = Field(
        default=False,
        description="Mount the MCP server at /api/mcp (opt-in; defaults off in production)",
    )

    # --- SSRF guard for /api/debug and MCP network tools ---
    ssrf_protection_enabled: bool = Field(
        default=False,
        description="Block loopback/private/link-local/metadata targets in curl/network_scan/ping/dns/tcp",
    )

    # --- Redis ---
    redis_url: str | None = Field(default=None, description="redis://host:port/db or http(s) endpoint in prod")
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_retry_attempts: int = 3
    redis_retry_delay: float = 0.5

    # --- PostgreSQL ---
    database_url: str | None = Field(default=None, description="postgresql+asyncpg://user:pass@host/db")
    db_pool_size: int = Field(default=5, description="SQLAlchemy connection pool size")
    db_max_overflow: int = Field(default=10, description="SQLAlchemy max overflow connections")

    # --- Cache ---
    cache_ttl: int = Field(default=300, description="Local cache TTL in seconds")
    cache_max_size: int = Field(default=1000, description="Max items in local cache")

    # --- Rate limiting ---
    rate_limit: str = "100/minute"
    debug_rate_limit: str = "10/minute"

    # --- OTEL ---
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_exporter_timeout: int = Field(default=10, description="OTLP exporter timeout in seconds")
    otel_service_name: str = "pytbak"

    # --- Prometheus ---
    prometheus_enabled: bool = True

    # --- Webdis (legacy compat) ---
    webdis_url: str = "http://webdis-svc.webdis:7379"

    @model_validator(mode="after")
    def _reject_production_defaults(self) -> "Settings":
        """Fail fast in production when the default credentials are still set.

        The report: default `admin` / `password` means an attacker gets full
        access to network diagnostics, SSRF, CPU-spike tools, and MCP. Refuse
        to boot in production until real credentials are provided.
        """
        if self.app_env == "production" and self.diag_username == "admin" and self.diag_password == "password":
            raise ValueError(
                "APP_ENV=production requires real DIAG_USERNAME/DIAG_PASSWORD "
                "(defaults admin/password are not allowed)"
            )
        return self

    @property
    def effective_redis_url(self) -> str:
        """Return redis_url if set, otherwise build from host/port/db."""
        if self.redis_url:
            return self.redis_url
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def sanitized_redis_url(self) -> str:
        """Return Redis URL with password masked for logging."""
        return re.sub(r"://[^@]*@", "://***@", self.effective_redis_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
