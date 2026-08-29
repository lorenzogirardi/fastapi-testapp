"""Tests for the storage service (in-memory fallback mode)."""

import pytest

from app.models.schemas import ContextCreate, ContextUpdate
from app.services.storage import create_context, delete_context, get_all_contexts, get_context, update_context


@pytest.mark.anyio
async def test_create_and_list():
    await create_context(ContextCreate(title="A", description="desc"))
    all_ctx = await get_all_contexts()
    assert len(all_ctx) == 1
    assert all_ctx[0].title == "A"


@pytest.mark.anyio
async def test_get_by_id():
    ctx = await create_context(ContextCreate(title="B"))
    found = await get_context(ctx.id)
    assert found is not None
    assert found.title == "B"


@pytest.mark.anyio
async def test_update():
    ctx = await create_context(ContextCreate(title="C"))
    updated = await update_context(ctx.id, ContextUpdate(title="C2", done=True))
    assert updated.title == "C2"
    assert updated.done is True


@pytest.mark.anyio
async def test_delete():
    ctx = await create_context(ContextCreate(title="D"))
    assert await delete_context(ctx.id) is True
    assert await get_context(ctx.id) is None


@pytest.mark.anyio
async def test_delete_not_found():
    assert await delete_context("nope") is False
