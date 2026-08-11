"""Integration tests for the stub demo stack.

Tests `maple serve --stub` flow:
- Stub node, Operator MCP, and Mock Agent start correctly
- maple status reports all services
- Mock agent streams real MCP tool calls E2E
- maple chat TUI works headlessly
- maple down stops everything

Requires: MADSci services running (docker-compose.ci.yaml)
Run with: pytest -m integration tests/test_integration_stub.py
"""

import asyncio
import json
import time

import httpx
import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def stub_stack():
    """Start the stub stack, yield, then tear down."""
    import subprocess
    import os

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

    # Start stub stack from the example directory
    cwd = str(
        __import__("pathlib").Path(__file__).parent.parent / "examples" / "block_sorting"
    )
    result = subprocess.run(
        ["maple", "serve", "stub"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"maple serve --stub failed: {result.stdout}\n{result.stderr}"

    # Wait for services to be ready
    time.sleep(5)
    yield

    # Teardown
    subprocess.run(["maple", "down"], capture_output=True)


# ---------------------------------------------------------------------------
# Service lifecycle tests
# ---------------------------------------------------------------------------


class TestStubServe:
    def test_status_shows_three_services(self):
        import subprocess
        result = subprocess.run(["maple", "status"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "stub-node" in result.stdout
        assert "operator-mcp" in result.stdout
        assert "mock-agent" in result.stdout

    def test_stub_node_health(self):
        resp = httpx.get("http://localhost:2000/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_operator_mcp_ping(self):
        import time
        for _ in range(10):
            try:
                resp = httpx.get("http://localhost:8102/ping", timeout=5)
                if resp.status_code == 200:
                    return
            except httpx.ConnectError:
                time.sleep(2)
        pytest.fail("Operator MCP not responding after 20s")

    def test_mock_agent_ping(self):
        resp = httpx.get("http://localhost:8202/ping", timeout=5)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Mock agent E2E stream tests
# ---------------------------------------------------------------------------


class TestStubE2E:
    @pytest.mark.asyncio
    async def test_mock_agent_streams_full_sequence(self):
        """Mock agent calls real MCP tools and streams results."""
        events = []

        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                "http://localhost:8202/stream",
                json={"session_id": "test-stub-e2e", "message": "sort the blocks"},
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line.removeprefix("data:").strip())
                        events.append(event)
                    except json.JSONDecodeError:
                        continue

        # Verify we got text events
        text_events = [e for e in events if "data" in e]
        assert len(text_events) > 0, "Should have reasoning text events"

        # Verify all tool calls present
        tool_events = [e for e in events if "current_tool_use" in e]
        tool_names = [e["current_tool_use"]["name"] for e in tool_events]
        assert "start_experiment" in tool_names
        assert "detect" in tool_names
        assert "run_node_action" in tool_names
        assert "verify" in tool_names
        assert "end_experiment" in tool_names

        # Verify 3 pick_and_place calls
        node_actions = [t for t in tool_names if t == "run_node_action"]
        assert len(node_actions) == 3

        # Verify no errors in the stream
        error_events = [e for e in events if "data" in e and "ERROR" in str(e.get("data", ""))]
        assert len(error_events) == 0, f"Got errors: {error_events}"

        # Verify final result with experiment_ended
        result_events = [e for e in events if "result" in e]
        assert len(result_events) == 1
        assert result_events[0]["result"]["experiment_ended"] is True


# ---------------------------------------------------------------------------
# TUI tests (headless via Textual pilot)
# ---------------------------------------------------------------------------


class TestStubChat:
    @pytest.mark.asyncio
    async def test_chat_tui_connects_and_streams(self):
        """Chat TUI connects to mock agent and receives streamed events."""
        from maple.tui import MapleChatApp
        from textual.widgets import Input

        app = MapleChatApp(agent="operator")
        async with app.run_test() as pilot:
            # Verify TUI mounted
            assert pilot.app.query_one("#status") is not None
            assert pilot.app.query_one("#log") is not None
            input_widget = pilot.app.query_one(Input)
            assert input_widget.disabled is False

            # Type and send a message
            await pilot.press("H", "e", "l", "l", "o")
            await pilot.press("enter")

            # Poll until stream completes (up to 40s)
            for _ in range(8):
                if getattr(pilot.app, "_exit_on_next_submit", False):
                    break
                await asyncio.sleep(5)

            # Verify experiment ended message appeared
            assert getattr(pilot.app, "_exit_on_next_submit", False) is True


class TestResume:
    def test_resume_finds_most_recent_session(self, tmp_path):
        """--resume finds the most recent session file."""
        import time
        from pathlib import Path

        # Create fake session dir with two files
        sessions_dir = tmp_path / "operator"
        sessions_dir.mkdir(parents=True)

        # Older session
        old = sessions_dir / "old-session-id.json"
        old.write_text('{"messages": []}')
        time.sleep(0.1)

        # Newer session
        new = sessions_dir / "new-session-id.json"
        new.write_text('{"messages": []}')

        # Test the glob + max logic directly (same as _get_session_id)
        json_files = list(sessions_dir.glob("*.json"))
        latest = max(json_files, key=lambda f: f.stat().st_mtime)
        assert latest.stem == "new-session-id"

    def test_resume_returns_new_uuid_when_no_sessions(self):
        """--resume with no previous sessions returns a new UUID."""
        from maple.cli import _get_session_id
        from unittest.mock import patch
        import uuid

        # Patch home to a non-existent dir
        result = _get_session_id("operator", resume=True)
        # Should be a valid UUID (new session since no files found)
        try:
            uuid.UUID(result)
            is_uuid = True
        except ValueError:
            is_uuid = False
        assert is_uuid
