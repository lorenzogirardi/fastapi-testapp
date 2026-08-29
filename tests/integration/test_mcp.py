"""Mid/tip layer — MCP server over the real streamable-HTTP transport.

Requires the Docker stack running at localhost:8000 (docker compose up -d).
Unlike tests/test_mcp.py (which calls tool functions directly), this drives
the actual wire protocol: session negotiation, JSON-RPC tool calls, and the
Basic Auth ASGI middleware guarding the /api/mcp mount.
"""

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

pytestmark = pytest.mark.integration

_AUTH = httpx.BasicAuth("admin", "password")


@pytest.mark.anyio
async def test_tool_list_and_call_over_real_transport(live_service):
    async with streamablehttp_client(f"{live_service}/api/mcp", auth=_AUTH) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "create_context" in names
            assert "health" in names
            assert "cpu_spike" in names

            result = await session.call_tool("health", {})
            assert result.isError is not True


@pytest.mark.anyio
async def test_full_context_lifecycle_over_real_transport(live_service):
    async with streamablehttp_client(f"{live_service}/api/mcp", auth=_AUTH) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            created = await session.call_tool(
                "create_context", {"title": "mcp-wire-e2e", "description": "via streamable http"}
            )
            assert created.isError is not True

            import json

            created_data = json.loads(created.content[0].text)
            context_id = created_data["id"]

            deleted = await session.call_tool("delete_context", {"context_id": context_id})
            assert deleted.isError is not True


@pytest.mark.anyio
async def test_unauthenticated_request_is_rejected(live_service):
    with pytest.raises(Exception):
        async with streamablehttp_client(f"{live_service}/api/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()


@pytest.mark.anyio
async def test_wrong_credentials_rejected(live_service):
    bad_auth = httpx.BasicAuth("admin", "wrong-password")
    with pytest.raises(Exception):
        async with streamablehttp_client(f"{live_service}/api/mcp", auth=bad_auth) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
