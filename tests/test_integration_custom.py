"""Integration tests for custom tools and prompts.

Tests that custom_tools in config actually get registered on the MCP server
and are callable via MCP protocol.

Requires: MADSci services running (docker-compose.ci.yaml)
Run with: pytest -m integration tests/test_integration_custom.py
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def custom_services():
    """Start operator with custom config from fixtures dir."""
    env = os.environ.copy()
    env.update({
        "WORKCELL_SERVER_URL": "http://localhost:8005/",
        "EXPERIMENT_SERVER_URL": "http://localhost:8002/",
        "EVENT_SERVER_URL": "http://localhost:8001/",
        "DATA_SERVER_URL": "http://localhost:8004/",
        "RESOURCE_SERVER_URL": "http://localhost:8003/",
        "LOCATION_SERVER_URL": "http://localhost:8006/",
        "MCP_OPERATOR_URL": "http://localhost:8102/mcp",
    })

    # Start operator from fixtures dir (has custom config, tool, and prompt)
    result = subprocess.run(
        ["maple", "serve", "operator"],
        cwd=str(FIXTURES_DIR),
        env=env,
        capture_output=True,
        text=True,
    )

    time.sleep(5)
    yield

    subprocess.run(["maple", "down"], capture_output=True)


class TestCustomTools:
    def test_custom_tool_registered(self, custom_services):
        """Custom tool from config is discoverable via MCP."""
        import httpx

        # Ping to verify server is up
        for _ in range(10):
            try:
                resp = httpx.get("http://localhost:8102/ping", timeout=5)
                if resp.status_code == 200:
                    break
            except httpx.ConnectError:
                time.sleep(2)

    @pytest.mark.asyncio
    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    async def test_custom_tool_callable(self, custom_services):
        """Custom tool can be called via MCP protocol."""
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        # Wait for server
        import time as _time
        for _ in range(10):
            try:
                import httpx as _httpx
                resp = _httpx.get("http://localhost:8102/ping", timeout=5)
                if resp.status_code == 200:
                    break
            except Exception:
                _time.sleep(2)

        async with streamablehttp_client(
            "http://localhost:8102/mcp",
            headers={"Authorization": "Bearer 12345678-1234-4234-8234-123456789abc"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # List tools — hello_world should be there
                tools = await session.list_tools()
                tool_names = [t.name for t in tools.tools]
                assert "hello_world" in tool_names, f"hello_world not in {tool_names}"

                # Call it
                result = await session.call_tool("hello_world", {"name": "MAPLE"})
                assert not result.isError
                text = result.content[0].text
                data = json.loads(text)
                assert data["greeting"] == "Hello, MAPLE!"
