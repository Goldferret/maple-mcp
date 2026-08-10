"""Unit tests for session management and auth."""

import asyncio
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maple.auth import get_or_create_token, is_valid_token


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestAuth:
    def test_is_valid_token_accepts_uuid4(self):
        token = str(uuid.uuid4())
        assert is_valid_token(token)

    def test_is_valid_token_rejects_garbage(self):
        assert not is_valid_token("not-a-uuid")
        assert not is_valid_token("")
        assert not is_valid_token("12345")

    def test_get_or_create_token_creates_file(self, tmp_path):
        creds = tmp_path / "credentials"
        with patch("maple.auth.CREDENTIALS_FILE", creds):
            token = get_or_create_token()
            assert creds.exists()
            assert is_valid_token(token)
            assert creds.read_text().strip() == token

    def test_get_or_create_token_reads_existing(self, tmp_path):
        creds = tmp_path / "credentials"
        existing = str(uuid.uuid4())
        creds.write_text(existing)
        with patch("maple.auth.CREDENTIALS_FILE", creds):
            token = get_or_create_token()
            assert token == existing

    def test_get_or_create_token_regenerates_if_invalid(self, tmp_path):
        creds = tmp_path / "credentials"
        creds.write_text("garbage-not-uuid")
        with patch("maple.auth.CREDENTIALS_FILE", creds):
            token = get_or_create_token()
            assert is_valid_token(token)
            assert token != "garbage-not-uuid"


# ---------------------------------------------------------------------------
# Session store tests
# ---------------------------------------------------------------------------


class TestSessions:
    @pytest.fixture
    def mock_ctx(self):
        """Create a mock Context that returns a token from get_state."""
        ctx = MagicMock()
        token = str(uuid.uuid4())
        ctx.get_state = MagicMock(return_value=asyncio.coroutine(lambda *a: token)())
        # Make it work as async
        async def _get_state(key):
            return token
        ctx.get_state = _get_state
        ctx._token = token
        return ctx

    @pytest.mark.asyncio
    async def test_create_and_get_session(self):
        from maple.sessions import _sessions, create_session, get_session, end_session

        token = str(uuid.uuid4())
        ctx = MagicMock()
        async def _get_state(key):
            return token
        ctx.get_state = _get_state

        mock_app = MagicMock()
        mock_app.experiment.experiment_id = "exp-test"

        # Create
        entry = await create_session(ctx, mock_app)
        assert entry.app == mock_app
        assert token in _sessions

        # Get
        entry2 = await get_session(ctx)
        assert entry2.app == mock_app

        # End
        await end_session(ctx)
        assert token not in _sessions

    @pytest.mark.asyncio
    async def test_create_session_rejects_duplicate(self):
        from maple.sessions import _sessions, create_session
        from fastmcp.exceptions import ToolError

        token = str(uuid.uuid4())
        ctx = MagicMock()
        async def _get_state(key):
            return token
        ctx.get_state = _get_state

        mock_app1 = MagicMock()
        mock_app1.experiment.experiment_id = "exp-1"
        mock_app1.end_experiment = MagicMock()

        mock_app2 = MagicMock()
        mock_app2.experiment.experiment_id = "exp-2"
        mock_app2.end_experiment = MagicMock()

        # First succeeds
        await create_session(ctx, mock_app1)

        # Second fails and cleans up
        with pytest.raises(ToolError, match="already active"):
            await create_session(ctx, mock_app2)

        # Loser's app was cleaned up
        mock_app2.end_experiment.assert_called_once()

        # Original still there
        assert _sessions[token].app == mock_app1

        # Cleanup
        _sessions.pop(token, None)

    @pytest.mark.asyncio
    async def test_get_session_raises_without_experiment(self):
        from maple.sessions import get_session
        from fastmcp.exceptions import ToolError

        token = str(uuid.uuid4())
        ctx = MagicMock()
        async def _get_state(key):
            return token
        ctx.get_state = _get_state

        with pytest.raises(ToolError, match="No active experiment"):
            await get_session(ctx)

    @pytest.mark.asyncio
    async def test_get_session_raises_without_identity(self):
        from maple.sessions import get_session
        from fastmcp.exceptions import ToolError

        ctx = MagicMock()
        async def _get_state(key):
            return None
        ctx.get_state = _get_state

        with pytest.raises(ToolError, match="Authorization required"):
            await get_session(ctx)

    @pytest.mark.asyncio
    async def test_idle_cleanup(self):
        from maple.sessions import _sessions, cleanup_idle_sessions, SessionEntry

        token = str(uuid.uuid4())
        mock_app = MagicMock()
        mock_app.end_experiment = MagicMock()

        entry = SessionEntry(mock_app)
        entry.last_accessed_at = 0  # Ancient timestamp
        _sessions[token] = entry

        await cleanup_idle_sessions(max_idle_seconds=0)

        assert token not in _sessions
        mock_app.end_experiment.assert_called_once()

    def test_cleanup_all_sessions(self):
        from maple.sessions import _sessions, cleanup_all_sessions, SessionEntry

        token1 = str(uuid.uuid4())
        token2 = str(uuid.uuid4())
        app1 = MagicMock()
        app1.end_experiment = MagicMock()
        app2 = MagicMock()
        app2.end_experiment = MagicMock()

        _sessions[token1] = SessionEntry(app1)
        _sessions[token2] = SessionEntry(app2)

        cleanup_all_sessions()

        assert len(_sessions) == 0
        app1.end_experiment.assert_called_once()
        app2.end_experiment.assert_called_once()


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------


class TestMiddleware:
    def test_is_valid_token_formats(self):
        # Valid UUIDv4
        assert is_valid_token("550e8400-e29b-41d4-a716-446655440000")
        # Wrong format
        assert not is_valid_token("550e8400e29b41d4a716446655440000")  # no dashes
        assert not is_valid_token("hello-world-this-is-not-uuid")
        assert not is_valid_token("")
