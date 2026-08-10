"""MAPLE session management — per-user experiment state.

Provides session isolation so multiple users can run experiments
simultaneously against the same MCP server. State is keyed to
a device token (auto-generated UUID in ~/.maple/credentials).

Architecture:
- Middleware reads Authorization header → stashes token in context
- Tools call get_session(ctx) to retrieve their experiment
- State survives reconnects (same token = same state bucket)
- One active experiment per token (v0.1.0 contract)
"""

import asyncio
import time
from typing import Optional

from fastmcp import Context
from fastmcp.exceptions import ToolError
from madsci.common.types.experiment_types import ExperimentStatus


# ---------------------------------------------------------------------------
# Session Store
# ---------------------------------------------------------------------------

_sessions: dict[str, "SessionEntry"] = {}
_lock = asyncio.Lock()


class SessionEntry:
    """Tracks one user's active experiment and metadata."""

    def __init__(self, app):
        self.app = app
        self.last_accessed_at = time.time()
        self.last_tool_called: Optional[str] = None

    def touch(self):
        self.last_accessed_at = time.time()


# ---------------------------------------------------------------------------
# Session Helpers (used by tools)
# ---------------------------------------------------------------------------


async def get_session(ctx: Context) -> "SessionEntry":
    """Get the current user's session. Raises ToolError if no active experiment.
    
    Args:
        ctx: FastMCP Context (injected automatically, hidden from LLM schema)
        
    Returns:
        SessionEntry with the active ExperimentApplication
    """
    identity = await ctx.get_state("maple_identity")
    if not identity:
        raise ToolError("Authorization required. No identity token found.")

    entry = _sessions.get(identity)
    if entry is None:
        raise ToolError("No active experiment. Call start_experiment first.")

    entry.touch()
    return entry


async def get_session_optional(ctx: Context) -> Optional["SessionEntry"]:
    """Get the current user's session, or None if no experiment is active.
    
    Use this for tools that work differently with/without an active experiment.
    """
    identity = await ctx.get_state("maple_identity")
    if not identity:
        return None
    entry = _sessions.get(identity)
    if entry:
        entry.touch()
    return entry


async def create_session(ctx: Context, app) -> "SessionEntry":
    """Create a new session for the current user.
    
    Atomically checks for existing session and reserves the slot before
    the caller creates the ExperimentApplication. Prevents race conditions.
    
    Args:
        ctx: FastMCP Context
        app: ExperimentApplication instance (already created)
        
    Returns:
        The new SessionEntry
    """
    identity = await ctx.get_state("maple_identity")
    if not identity:
        raise ToolError("Authorization required. No identity token found.")

    async with _lock:
        if identity in _sessions:
            # Race: caller already created app but slot is taken. Clean up.
            try:
                app.end_experiment(status=ExperimentStatus.FAILED)
            except Exception:
                pass
            raise ToolError(
                "An experiment is already active for this identity. "
                "Call end_experiment first before starting a new one."
            )
        entry = SessionEntry(app)
        _sessions[identity] = entry

    return entry


async def end_session(ctx: Context):
    """Remove the current user's session from the store.
    
    Does NOT finalize the experiment — the calling tool owns that responsibility.
    
    Args:
        ctx: FastMCP Context
    """
    identity = await ctx.get_state("maple_identity")
    if not identity:
        return

    async with _lock:
        _sessions.pop(identity, None)


async def get_identity(ctx: Context) -> str:
    """Get the current user's identity token from context.
    
    Args:
        ctx: FastMCP Context
        
    Returns:
        The identity token string
    """
    identity = await ctx.get_state("maple_identity")
    if not identity:
        raise ToolError("Authorization required. No identity token found.")
    return identity


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


async def cleanup_idle_sessions(max_idle_seconds: int = 1800):
    """Clean up sessions that have been idle for too long.
    
    Args:
        max_idle_seconds: Maximum idle time before reaping (default 30 minutes)
    """
    now = time.time()
    to_remove = []

    async with _lock:
        for token, entry in _sessions.items():
            if now - entry.last_accessed_at > max_idle_seconds:
                to_remove.append(token)

        for token in to_remove:
            entry = _sessions.pop(token)
            if entry.app:
                try:
                    entry.app.end_experiment(status=ExperimentStatus.FAILED)
                except Exception:
                    pass


def cleanup_all_sessions():
    """Clean up all sessions on server shutdown (sync, for atexit/lifespan)."""
    for token, entry in _sessions.items():
        if entry.app:
            try:
                entry.app.end_experiment(status=ExperimentStatus.FAILED)
            except Exception:
                pass
    _sessions.clear()
