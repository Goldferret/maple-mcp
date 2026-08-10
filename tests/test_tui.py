"""Tests for MAPLE TUI composition."""

import pytest

textual = pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_chat_app_composes():
    """MapleChatApp mounts without error."""
    from maple.tui import MapleChatApp

    app = MapleChatApp(agent="operator")
    async with app.run_test() as pilot:
        # Verify key widgets exist
        assert pilot.app.query_one("#status") is not None
        assert pilot.app.query_one("#log") is not None
        assert pilot.app.query_one("#prompt") is not None


@pytest.mark.asyncio
async def test_status_bar_shows_agent_name():
    """StatusBar displays the agent name."""
    from maple.tui import MapleChatApp

    app = MapleChatApp(agent="overseer")
    async with app.run_test() as pilot:
        status = pilot.app.query_one("#status")
        assert "overseer" in status.render()


@pytest.mark.asyncio
async def test_input_disabled_during_stream():
    """Input widget exists and starts enabled."""
    from maple.tui import MapleChatApp
    from textual.widgets import Input

    app = MapleChatApp(agent="operator")
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one(Input)
        assert input_widget.disabled is False
