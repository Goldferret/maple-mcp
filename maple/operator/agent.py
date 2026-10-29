"""MAPLE Operator Agent — Experiment execution agent service."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Explicit path required — dotenv's default search uses caller's __file__ location,
# not CWD, which fails when imported via uvicorn
load_dotenv(Path(os.getcwd()) / ".env", override=True)

import asyncio
import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from strands import Agent
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry
from strands.session import FileSessionManager
from strands.tools.executors import SequentialToolExecutor
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamable_http_client


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MCP_OPERATOR_URL = os.getenv("MCP_OPERATOR_URL", "http://localhost:8102/mcp")
SESSIONS_DIR = Path(os.getenv("MAPLE_SESSIONS_DIR", str(Path.home() / ".maple" / "sessions")))
OPERATOR_SESSIONS_DIR = SESSIONS_DIR / "operator"
OPERATOR_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()

# Load custom prompt from config if specified
try:
    from maple.config import load_config
    _cfg = load_config()
    if _cfg.operator.prompt:
        _custom_prompt_path = Path(_cfg.operator.prompt)
        if _custom_prompt_path.exists():
            SYSTEM_PROMPT = _custom_prompt_path.read_text()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class ReasoningCaptureHook(HookProvider):
    """Captures LLM reasoning text between tool calls and logs to MCP server."""

    def __init__(self, mcp_base_url: str):
        self.mcp_base_url = mcp_base_url.replace("/mcp", "")
        self.buffer: list[str] = []

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.on_after_tool_call)

    def accumulate(self, text: str) -> None:
        """Feed streamed text tokens into the reasoning buffer."""
        self.buffer.append(text)

    def on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name == "start_experiment":
            return
        self._flush(name)

    def on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name == "start_experiment":
            self._flush("start_experiment")

    def _flush(self, before_tool: str) -> None:
        if not self.buffer:
            return
        reasoning = "".join(self.buffer).strip()
        self.buffer.clear()
        if not reasoning:
            return
        try:
            import requests
            requests.post(
                f"{self.mcp_base_url}/log_reasoning",
                json={"reasoning_text": reasoning, "before_tool": before_tool},
                timeout=5,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Agent Factory
# ---------------------------------------------------------------------------


def _create_model():
    """Create model from environment variables."""
    provider = os.getenv("MODEL_PROVIDER", "openai").lower()

    if provider == "ollama":
        from strands.models.ollama import OllamaModel
        return OllamaModel(
            host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            model_id=os.getenv("OPERATOR_MODEL", "qwen3:30b-a3b"),
            temperature=0.1,
            max_tokens=8000,
            keep_alive="30m",
            options={"num_ctx": 32768, "think": False, "num_predict": 8000},
        )
    elif provider == "anthropic":
        from strands.models.anthropic import AnthropicModel
        return AnthropicModel(
            client_args={"api_key": os.getenv("ANTHROPIC_API_KEY")},
            model_id=os.getenv("OPERATOR_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=4000,
            params={"temperature": 0.7},
        )
    else:
        from strands.models.openai import OpenAIModel
        return OpenAIModel(
            client_args={"api_key": os.getenv("OPENAI_API_KEY")},
            model_id=os.getenv("OPERATOR_MODEL", "gpt-4o"),
            params={"max_tokens": 4000, "temperature": 0.7},
        )


def create_operator_agent(session_id: str, extra_hooks: list = None) -> Agent:
    """Create a configured Operator agent instance.
    
    Args:
        session_id: Unique session identifier for conversation history.
        extra_hooks: Additional Strands HookProvider instances to attach.
    """
    from maple.auth import get_or_create_token
    from mcp.shared._httpx_utils import create_mcp_http_client

    model = _create_model()
    token = get_or_create_token()
    # SDK factory keeps MCP-recommended timeouts (300s read) for long node actions.
    auth_client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    mcp_client = MCPClient(lambda: streamable_http_client(
        MCP_OPERATOR_URL,
        http_client=auth_client,
    ))
    reasoning_hook = ReasoningCaptureHook(mcp_base_url=MCP_OPERATOR_URL)

    system_prompt = SYSTEM_PROMPT
    if os.getenv("MODEL_PROVIDER", "openai").lower() == "ollama":
        system_prompt += (
            "\n\nCRITICAL: After every tool result, you MUST immediately call "
            "the next required tool. Never stop after a single tool call. "
            "Continue calling tools until end_experiment is called."
        )

    hooks = [reasoning_hook]
    if extra_hooks:
        hooks.extend(extra_hooks)

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[mcp_client],
        hooks=hooks,
        tool_executor=SequentialToolExecutor(),
        callback_handler=None,
        conversation_manager=None,
        session_manager=FileSessionManager(
            session_id=session_id,
            storage_dir=str(OPERATOR_SESSIONS_DIR),
        ),
    )
    agent._reasoning_hook = reasoning_hook

    # Inject a tool_started event into the stream at dispatch, before the
    # (often long/blocking) tool runs, so the TUI shows the tool call immediately
    # instead of only after it completes.
    from strands.middleware import ExecuteToolStage
    from strands.types.events import TypedEvent

    async def _announce_tool(context, next_fn):
        tu = getattr(context, "tool_use", None) or {}
        name = tu.get("name", "?")
        print(f"INFO:     Tool call: {name}", flush=True)
        yield TypedEvent({"maple_tool_started": {"name": name, "input": tu.get("input", {})}})
        async for event in next_fn(context):
            yield event

    agent.add_middleware(ExecuteToolStage, _announce_tool)

    return agent


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.post("/invoke")
async def invoke(request: dict):
    session_id = request["session_id"]
    message = request["message"]
    agent = create_operator_agent(session_id)
    result = await asyncio.to_thread(agent, message)
    return {
        "message": result.message,
        "stop_reason": result.stop_reason,
        "experiment_ended": _check_experiment_ended(result),
    }


@app.post("/stream")
async def stream(request: dict):
    session_id = request["session_id"]
    message = request["message"]

    async def event_generator():
        agent = create_operator_agent(session_id)
        reasoning_hook = getattr(agent, "_reasoning_hook", None)
        experiment_ended = False

        async for event in agent.stream_async(message):
            out = {}

            # Skip raw streaming tool deltas — the ExecuteToolStage middleware
            # emits a clean tool_started event (handled below) instead.
            if "current_tool_use" in event:
                continue

            # Tool-start injected by the ExecuteToolStage middleware — forward it
            # immediately so the TUI shows a pending indicator before the call runs.
            if "maple_tool_started" in event:
                ts = event["maple_tool_started"]
                started_event = {"current_tool_use": {
                    "toolUseId": f"tool-{ts['name']}",
                    "name": ts["name"],
                    "input": ts["input"],
                    "pending": True,
                }}
                yield f"data: {json.dumps(started_event, default=str)}\n\n"
                if ts["name"] == "end_experiment":
                    experiment_ended = True
                continue

            if "data" in event:
                out["data"] = event["data"]
                if reasoning_hook and isinstance(event["data"], str):
                    reasoning_hook.accumulate(event["data"])

            if "result" in event:
                r = event["result"]
                out["result"] = {
                    "stop_reason": getattr(r, "stop_reason", None),
                    "experiment_ended": experiment_ended,
                }

            if out:
                yield f"data: {json.dumps(out, default=str)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/ping")
async def ping():
    return {"status": "ok"}


def _check_experiment_ended(result) -> bool:
    """Check if end_experiment was called during this invocation."""
    msg = getattr(result, "message", "") or ""
    return "experiment" in msg.lower() and ("complete" in msg.lower() or "ended" in msg.lower())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8202)
