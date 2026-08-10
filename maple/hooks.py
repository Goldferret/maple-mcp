"""Agent event hooks for MAPLE provenance capture."""

import os
import requests


class ReasoningCaptureHook:
    """Captures LLM reasoning text and logs to MADSci Event Manager.
    
    Intercepts text tokens between tool calls, buffers them,
    and flushes to the Event Manager when a tool is invoked.
    """

    def __init__(self, mcp_base_url: str = None):
        self.mcp_base_url = mcp_base_url or os.getenv("MCP_EXECUTOR_URL", "http://localhost:8102")
        self._buffer = ""

    def accumulate(self, text: str):
        """Add text to the reasoning buffer."""
        self._buffer += text

    def flush(self, tool_name: str):
        """Flush buffered reasoning to the MCP server's log endpoint."""
        if not self._buffer.strip():
            return
        try:
            requests.post(
                f"{self.mcp_base_url}/log_reasoning",
                json={
                    "reasoning": self._buffer.strip(),
                    "before_tool": tool_name,
                },
                timeout=5,
            )
        except Exception:
            pass
        self._buffer = ""
