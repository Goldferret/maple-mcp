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


async def call_tool(tool_name: str, arguments: dict, token: str) -> dict:
    """Call an MCP tool using the streamable_http_client (works across mcp versions).

    Auth header is carried by a pre-built httpx.AsyncClient, since the client
    does not accept a `headers` kwarg directly.
    """
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client

    # create_mcp_http_client carries MCP streaming timeouts (a bare AsyncClient
    # has no read timeout and its SSE GET stream never closes -> aclose hangs).
    auth_client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    try:
        async with streamable_http_client(
            OPERATOR_URL, http_client=auth_client
        ) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = ""
                for content in result.content:
                    if hasattr(content, "text"):
                        text += content.text
                return {"text": text, "error": result.isError}
    finally:
        await auth_client.aclose()


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


@pytest.fixture
async def experiment(vision_services):
    """Per-test active experiment on a fresh token.

    Async + function-scoped: runs on the same event loop as the test (under
    asyncio_mode=auto), so there is no competing loop and no task-group
    deadlock. Each test gets its own token, so the one-experiment-per-token
    rule never collides across tests.
    """
    token = str(uuid.uuid4())
    r = await call_tool("start_experiment",
                        {"name": "vv-test", "description": "vision view test"},
                        token=token)
    assert not r["error"], r["text"]
    yield token
    # Teardown reaps the session (verify-before-end rule makes explicit end awkward).


class TestVisionViewRouting:
    @pytest.mark.asyncio
    async def test_detect_default_view(self, experiment):
        """No view/node -> default_view (view_a -> RoutingStubA)."""
        r = await call_tool("detect", {}, token=experiment)
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["view"] == "view_a"
        assert data["detections"][0]["backend"] == "A"

    @pytest.mark.asyncio
    async def test_detect_explicit_view_b(self, experiment):
        """view=view_b -> RoutingStubB."""
        r = await call_tool("detect", {"view": "view_b"}, token=experiment)
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["view"] == "view_b"
        assert data["detections"][0]["backend"] == "B"

    @pytest.mark.asyncio
    async def test_detect_by_node_resolves_view(self, experiment):
        """node=OtherBot is covered by view_b."""
        r = await call_tool("detect", {"node": "OtherBot"}, token=experiment)
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["view"] == "view_b"
        assert data["detections"][0]["backend"] == "B"

    @pytest.mark.asyncio
    async def test_node_wins_over_view(self, experiment):
        """view=view_a + node=OtherBot -> node wins -> view_b."""
        r = await call_tool("detect", {"view": "view_a", "node": "OtherBot"}, token=experiment)
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["view"] == "view_b"
        assert data["detections"][0]["backend"] == "B"

    @pytest.mark.asyncio
    async def test_capture_produced_frames(self, experiment):
        """The capture path actually downloaded a datapoint into frames."""
        r = await call_tool("detect", {"view": "view_a"}, token=experiment)
        data = json.loads(r["text"])
        # analyze action returns a json_result datapoint -> frames has that key
        assert data["detections"][0]["frame_keys"], "expected at least one frame key"

    @pytest.mark.asyncio
    async def test_verify_routes_to_view_backend(self, experiment):
        """verify(view=view_b) -> RoutingStubB.verify_goal."""
        r = await call_tool("verify", {"view": "view_b"}, token=experiment)
        assert not r["error"], r["text"]
        data = json.loads(r["text"])
        assert data["backend"] == "B"

    @pytest.mark.asyncio
    async def test_unknown_view_errors(self, experiment):
        r = await call_tool("detect", {"view": "nonexistent"}, token=experiment)
        assert r["error"]
        assert "Unknown view" in r["text"]
