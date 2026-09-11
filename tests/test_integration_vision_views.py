"""Integration test for the vision view registry.

Verifies the full path: detect/verify tool -> view/node resolution ->
capture workflow -> datapoint download -> correct VisionBackend dispatch.

Uses the stub node's `analyze` action as a stand-in for a camera (the stub
node has no real camera), so this exercises the capture mechanism end-to-end
without camera hardware. Two views back onto two distinct routing stub
backends so we can confirm routing.

Requires: MADSci services running (docker-compose.ci.yaml)
Run with: pytest -m integration tests/test_integration_vision_views.py
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "vision_views"
OPERATOR_URL = "http://localhost:8102/mcp"
TEST_TOKEN = str(uuid.uuid4())


async def call_tool(tool_name: str, arguments: dict, token: str = TEST_TOKEN) -> dict:
    # Uses deprecated streamablehttp_client intentionally — accepts headers
    # directly and fails fast. See test_integration_full.py for rationale.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        OPERATOR_URL, headers={"Authorization": f"Bearer {token}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            text = ""
            for content in result.content:
                if hasattr(content, "text"):
                    text += content.text
            return {"text": text, "error": result.isError}


@pytest.fixture(scope="module", autouse=True)
def vision_services():
    """Start stub node + operator using the vision_views fixture config."""
    env = os.environ.copy()
    env.update({
        "WORKCELL_SERVER_URL": "http://localhost:8005/",
        "EXPERIMENT_SERVER_URL": "http://localhost:8002/",
        "EVENT_SERVER_URL": "http://localhost:8001/",
        "DATA_SERVER_URL": "http://localhost:8004/",
        "RESOURCE_SERVER_URL": "http://localhost:8003/",
        "LOCATION_SERVER_URL": "http://localhost:8006/",
        "MCP_OPERATOR_URL": OPERATOR_URL,
    })

    # `serve stub` from the fixtures dir: uses bundled stub_node.py (StubBot)
    # and loads THIS dir's maple.config.yaml (the two-view registry).
    subprocess.run(["maple", "serve", "stub"], cwd=str(FIXTURES_DIR), env=env,
                   capture_output=True, text=True)

    time.sleep(5)

    # Wait for StubBot ready
    import httpx
    for _ in range(30):
        try:
            resp = httpx.get("http://localhost:8005/nodes", timeout=3)
            if resp.status_code == 200 and resp.json().get("StubBot", {}).get("status", {}).get("ready"):
                break
        except Exception:
            pass
        time.sleep(1)

    yield

    subprocess.run(["maple", "down"], capture_output=True)


@pytest.fixture(scope="module")
def experiment(vision_services):
    """Start an experiment so detect/verify have an active session."""
    import asyncio
    r = asyncio.run(
        call_tool("start_experiment", {"name": "vv-test", "description": "vision view test"})
    )
    assert not r["error"], r["text"]
    yield
    # end_experiment requires verify immediately before; just let teardown reap.


class TestVisionViewRouting:
    @pytest.mark.asyncio
    async def test_detect_default_view(self, experiment):
        """No view/node -> default_view (view_a -> RoutingStubA)."""
        r = await call_tool("detect", {})
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["view"] == "view_a"
        assert data["detections"][0]["backend"] == "A"

    @pytest.mark.asyncio
    async def test_detect_explicit_view_b(self, experiment):
        """view=view_b -> RoutingStubB."""
        r = await call_tool("detect", {"view": "view_b"})
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["view"] == "view_b"
        assert data["detections"][0]["backend"] == "B"

    @pytest.mark.asyncio
    async def test_detect_by_node_resolves_view(self, experiment):
        """node=OtherBot is covered by view_b."""
        r = await call_tool("detect", {"node": "OtherBot"})
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["view"] == "view_b"
        assert data["detections"][0]["backend"] == "B"

    @pytest.mark.asyncio
    async def test_node_wins_over_view(self, experiment):
        """view=view_a + node=OtherBot -> node wins -> view_b."""
        r = await call_tool("detect", {"view": "view_a", "node": "OtherBot"})
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["view"] == "view_b"
        assert data["detections"][0]["backend"] == "B"

    @pytest.mark.asyncio
    async def test_capture_produced_frames(self, experiment):
        """The capture path actually downloaded a datapoint into frames."""
        r = await call_tool("detect", {"view": "view_a"})
        data = json.loads(r["text"])
        # analyze action returns a json_result datapoint -> frames has that key
        assert data["detections"][0]["frame_keys"], "expected at least one frame key"

    @pytest.mark.asyncio
    async def test_verify_routes_to_view_backend(self, experiment):
        """verify(view=view_b) -> RoutingStubB.verify_goal."""
        r = await call_tool("verify", {"view": "view_b"})
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["backend"] == "B"

    @pytest.mark.asyncio
    async def test_unknown_view_errors(self, experiment):
        r = await call_tool("detect", {"view": "nonexistent"})
        assert r["error"]
        assert "Unknown view" in r["text"]
