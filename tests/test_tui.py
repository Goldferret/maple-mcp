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


# ---------------------------------------------------------------------------
# Opening-brief auto-send logic (--test collaborative brief + config.experiment)
# ---------------------------------------------------------------------------

import json


def test_opening_brief_test_mode_returns_collaborative_brief():
    """--test mode returns the bundled collaborative brief as JSON."""
    from maple.tui import MapleChatApp

    app = MapleChatApp(agent="operator", test=True)
    brief = app._opening_brief()
    assert brief is not None
    data = json.loads(brief)
    assert data["name"] == "Interactive Testing Session"
    # It must tell the agent tasks arrive as chat messages and not to auto-end.
    assert "chat message" in data["objective"].lower()
    assert "await" in data["objective"].lower()


def test_opening_brief_non_test_empty_config_returns_none(tmp_path, monkeypatch):
    """Without --test and no experiment objective, nothing is auto-sent."""
    from maple.tui import MapleChatApp

    monkeypatch.chdir(tmp_path)  # no maple.config.yaml here -> default config
    app = MapleChatApp(agent="operator", test=False)
    assert app._opening_brief() is None


def test_opening_brief_non_test_filled_config_returns_experiment_brief(tmp_path, monkeypatch):
    """Without --test but with a filled experiment block, that brief is sent."""
    from maple.tui import MapleChatApp

    (tmp_path / "maple.config.yaml").write_text(
        "experiment:\n"
        "  name: My Run\n"
        "  objective: Do the thing\n"
        "  constraints:\n"
        "    - Only one at a time\n"
    )
    monkeypatch.chdir(tmp_path)
    app = MapleChatApp(agent="operator", test=False)
    brief = app._opening_brief()
    assert brief is not None
    data = json.loads(brief)
    assert data["name"] == "My Run"
    assert data["objective"] == "Do the thing"
    assert data["constraints"] == ["Only one at a time"]


@pytest.mark.asyncio
async def test_load_session_history_fresh_returns_false():
    """A brand-new session (no message files) reports no history loaded."""
    from maple.tui import MapleChatApp

    app = MapleChatApp(agent="operator", session_id="fresh-no-history-xyz")
    async with app.run_test():
        assert app._load_session_history() is False
