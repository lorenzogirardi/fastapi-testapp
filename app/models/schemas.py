"""Pydantic models for request/response validation."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# --- Context ---
class ContextCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="")


class ContextUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    done: bool | None = None


class ContextResponse(BaseModel):
    id: str
    title: str
    description: str
    done: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- Error ---
class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


# --- Health ---
class HealthCheck(BaseModel):
    name: str
    status: str
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    checks: list[HealthCheck] = []


# --- Debug: Network ---
class NetworkScanResult(BaseModel):
    target: str
    ping: dict[str, Any] | None = None
    dns: dict[str, Any] | None = None
    tcp_check: dict[str, Any] | None = None
    traceroute: dict[str, Any] | None = None


# --- Debug: CPU ---
class CpuSpikeRequest(BaseModel):
    duration: int = Field(default=10, ge=1, le=120, description="Duration in seconds")
    cores: int = Field(default=1, ge=1, le=16, description="Number of CPU cores to stress")


class CpuSpikeResponse(BaseModel):
    status: str
    duration: int
    cores: int
    message: str


# --- Info ---
class AppInfoResponse(BaseModel):
    app: dict[str, str]


class MappingEntry(BaseModel):
    endpoint: str
    methods: list[str]
    path: str
