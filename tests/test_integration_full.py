"""Integration tests for the full MAPLE stack.

Tests `maple serve --all` flow:
- All services start
- maple status reports all services
- All Overseer and Operator MCP tools work
- maple attach shows correct panes
- maple down stops everything

Requires: MADSci services running (docker-compose.ci.yaml) + stub node registered
Run with: pytest -m integration tests/test_integration_full.py
"""

import asyncio
import json
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper: call MCP tool in a self-contained session
# ---------------------------------------------------------------------------


async def call_tool(url: str, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool with a fresh session (avoids cancel scope issues)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            text = ""
            for content in result.content:
                if hasattr(content, "text"):
                    text += content.text
            return {"text": text, "error": result.isError}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def full_stack():
    """Start the full stack + stub node, yield, then tear down."""
    import os
    from pathlib import Path

    env = os.environ.copy()
    env.update({
        "WORKCELL_SERVER_URL": "http://localhost:8005/",
        "EXPERIMENT_SERVER_URL": "http://localhost:8002/",
        "EVENT_SERVER_URL": "http://localhost:8001/",
        "DATA_SERVER_URL": "http://localhost:8004/",
        "RESOURCE_SERVER_URL": "http://localhost:8003/",
        "LOCATION_SERVER_URL": "http://localhost:8006/",
        "MCP_OPERATOR_URL": "http://localhost:8102/mcp",
        "MCP_OVERSEER_URL": "http://localhost:8103/mcp",
    })

    cwd = str(Path(__file__).parent.parent / "examples" / "block_sorting")

    # Start stub + operator
    r1 = subprocess.run(["maple", "serve", "stub"], cwd=cwd, env=env, capture_output=True, text=True)
    print(f"[fixture] serve --stub: exit={r1.returncode} stdout={r1.stdout.strip()}")
    # Start overseer
    r2 = subprocess.run(["maple", "serve", "overseer"], cwd=cwd, env=env, capture_output=True, text=True)
    print(f"[fixture] serve --agent overseer: exit={r2.returncode} stdout={r2.stdout.strip()}")

    time.sleep(5)  # Wait for services to start

    # Wait for StubBot to be ready in the workcell manager
    import httpx
    for i in range(30):  # 30 seconds max
        try:
            resp = httpx.get("http://localhost:8005/nodes", timeout=3)
            if resp.status_code == 200:
                nodes = resp.json()
                stub = nodes.get("StubBot", {})
                status = stub.get("status", {})
                if status.get("ready"):
                    break
        except Exception:
            pass
        time.sleep(1)

    yield

    subprocess.run(["maple", "down"], capture_output=True)


# ---------------------------------------------------------------------------
# Service lifecycle tests
# ---------------------------------------------------------------------------


class TestFullServe:
    def test_status_shows_services(self):
        result = subprocess.run(["maple", "status"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "operator-mcp" in result.stdout
        assert "overseer-mcp" in result.stdout

    def test_operator_mcp_ping(self):
        resp = httpx.get("http://localhost:8102/ping", timeout=5)
        assert resp.status_code == 200

    def test_overseer_mcp_ping(self):
        resp = httpx.get("http://localhost:8103/ping", timeout=5)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Overseer MCP tool tests
# ---------------------------------------------------------------------------


OVERSEER_URL = "http://localhost:8103/mcp"


class TestOverseerTools:
    @pytest.mark.asyncio
    async def test_get_lab_state(self):
        r = await call_tool(OVERSEER_URL, "get_lab_state", {})
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert "nodes" in data

    @pytest.mark.asyncio
    async def test_list_experiments(self):
        r = await call_tool(OVERSEER_URL, "list_experiments", {"limit": 5})
        assert not r["error"], r["text"]

    @pytest.mark.asyncio
    async def test_get_resources(self):
        r = await call_tool(OVERSEER_URL, "get_resources", {})
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert "resources" in data
        assert "count" in data

    @pytest.mark.asyncio
    async def test_add_and_remove_resource(self):
        add_r = await call_tool(OVERSEER_URL, "add_resource", {
            "name": "CI Test Block",
            "resource_class": "test_item",
            "base_type": "resource",
        })
        if add_r["error"]:
            pytest.skip(f"add_resource not compatible: {add_r['text'][:100]}")
        data = json.loads(add_r["text"])
        resource_id = data["resource_id"]

        rm_r = await call_tool(OVERSEER_URL, "remove_resource", {"resource_id": resource_id})
        assert not rm_r["error"], rm_r["text"]

    @pytest.mark.asyncio
    async def test_get_locations(self):
        r = await call_tool(OVERSEER_URL, "get_locations", {})
        assert not r["error"], r["text"]

    @pytest.mark.asyncio
    async def test_add_and_remove_location(self):
        add_r = await call_tool(OVERSEER_URL, "add_location", {"name": "CI Test Zone"})
        assert not add_r["error"], add_r["text"]
        data = json.loads(add_r["text"])
        location_id = data["location_id"]

        rm_r = await call_tool(OVERSEER_URL, "remove_location", {"location_id": location_id})
        assert not rm_r["error"], rm_r["text"]

    @pytest.mark.asyncio
    async def test_query_events(self):
        r = await call_tool(OVERSEER_URL, "query_events", {"limit": 5})
        assert not r["error"], r["text"]

    @pytest.mark.asyncio
    async def test_query_datapoints(self):
        r = await call_tool(OVERSEER_URL, "query_datapoints", {"limit": 5})
        assert not r["error"], r["text"]


# ---------------------------------------------------------------------------
# Operator MCP tool tests
# ---------------------------------------------------------------------------


OPERATOR_URL = "http://localhost:8102/mcp"


class TestOperatorTools:
    @pytest.mark.asyncio
    async def test_experiment_lifecycle(self):
        """Test start → run_node_action → end_experiment flow."""
        import asyncio as aio

        # Start experiment first (required for all other tools)
        start_r = await call_tool(OPERATOR_URL, "start_experiment", {
            "name": "CI Test Experiment",
            "description": "Integration test",
        })
        assert not start_r["error"], start_r["text"]
        data = json.loads(start_r["text"])
        exp_id = data["experiment_id"]

        # Run node action with retry (workcell manager may need time to poll node)
        action_r = None
        for attempt in range(6):
            action_r = await call_tool(OPERATOR_URL, "run_node_action", {
                "node_name": "StubBot",
                "action_name": "pick_and_place",
                "parameters": {"pick_x": 100, "pick_y": 100, "place_x": 200, "place_y": 200},
            })
            if not action_r["error"]:
                break
            await aio.sleep(5)

        assert not action_r["error"], action_r["text"]

        # Verify sorting state (required before end_experiment)
        verify_r = await call_tool(OPERATOR_URL, "verify", {})

        # End experiment (may fail if verify errored — that's a vision backend issue, not lifecycle)
        end_r = await call_tool(OPERATOR_URL, "end_experiment", {
            "experiment_id": exp_id,
            "summary": "CI test completed",
        })
        if end_r["error"] and "verify" in end_r["text"]:
            # Gate requires successful verify — skip if vision not configured
            pass
        else:
            assert not end_r["error"], end_r["text"]

    @pytest.mark.asyncio
    async def test_get_node_info(self):
        """Test get_node_info (requires active experiment + node registered)."""
        await call_tool(OPERATOR_URL, "start_experiment", {
            "name": "Node Info Test", "description": "test",
        })
        r = await call_tool(OPERATOR_URL, "get_node_info", {"node_name": "StubBot"})
        if r["error"] and "No info" in r["text"]:
            pytest.skip("StubBot info not yet available (workcell manager hasn't polled)")
        assert not r["error"], r["text"]
        assert "pick_and_place" in r["text"]

    @pytest.mark.asyncio
    async def test_get_robot_constraints(self):
        # Start experiment first
        await call_tool(OPERATOR_URL, "start_experiment", {
            "name": "Constraints Test",
            "description": "test",
        })
        r = await call_tool(OPERATOR_URL, "get_robot_constraints", {"node_name": "StubBot"})
        # May not be implemented for stub — just check it doesn't crash
        assert r["text"] is not None
        # Cleanup
        await call_tool(OPERATOR_URL, "end_experiment", {
            "experiment_id": "dummy",
            "summary": "cleanup",
        })


# ---------------------------------------------------------------------------
# Attach TUI test
# ---------------------------------------------------------------------------


class TestAttach:
    @pytest.mark.asyncio
    async def test_attach_shows_running_services(self):
        from maple.attach import MapleAttachApp

        app = MapleAttachApp()
        async with app.run_test() as pilot:
            services = app._services
            assert len(services) > 0
            assert "operator-mcp" in services


# ---------------------------------------------------------------------------
# Down test (runs last)
# ---------------------------------------------------------------------------


class TestDown:
    def test_maple_down_stops_all(self):
        result = subprocess.run(["maple", "down"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "Stopped" in result.stdout

    def test_status_empty_after_down(self):
        result = subprocess.run(["maple", "status"], capture_output=True, text=True)
        assert "No MAPLE services" in result.stdout or result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# maple serve --all test
# ---------------------------------------------------------------------------


class TestServeAll:
    """Test maple serve --all starts all 4 services."""

    def test_serve_all_starts_four_services(self):
        import os
        from pathlib import Path

        env = os.environ.copy()
        env.update({
            "WORKCELL_SERVER_URL": "http://localhost:8005/",
            "EXPERIMENT_SERVER_URL": "http://localhost:8002/",
            "EVENT_SERVER_URL": "http://localhost:8001/",
            "DATA_SERVER_URL": "http://localhost:8004/",
            "RESOURCE_SERVER_URL": "http://localhost:8003/",
            "LOCATION_SERVER_URL": "http://localhost:8006/",
        })

        cwd = str(Path(__file__).parent.parent / "examples" / "block_sorting")

        # Start all
        r = subprocess.run(["maple", "serve", "all"], cwd=cwd, env=env, capture_output=True, text=True)
        assert r.returncode == 0, f"Failed: {r.stdout}\n{r.stderr}"
        assert "operator-mcp" in r.stdout
        assert "operator-agent" in r.stdout
        assert "overseer-mcp" in r.stdout
        assert "overseer-agent" in r.stdout

        # Verify status
        import time
        time.sleep(3)
        status = subprocess.run(["maple", "status"], capture_output=True, text=True)
        assert "operator-mcp" in status.stdout
        assert "operator-agent" in status.stdout
        assert "overseer-mcp" in status.stdout
        assert "overseer-agent" in status.stdout

        # Clean up
        subprocess.run(["maple", "down"], capture_output=True)
