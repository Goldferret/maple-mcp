"""MAPLE Overseer Agent — Lab monitoring and management agent service."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(os.getcwd()) / ".env", override=True)

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from strands import Agent
from strands.session import FileSessionManager
from strands.tools.executors import SequentialToolExecutor
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamable_http_client


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MCP_OVERSEER_URL = os.getenv("MCP_OVERSEER_URL", "http://localhost:8103/mcp")
SESSIONS_DIR = Path(os.getenv("MAPLE_SESSIONS_DIR", str(Path.home() / ".maple" / "sessions")))
OVERSEER_SESSIONS_DIR = SESSIONS_DIR / "overseer"
OVERSEER_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()

# Load custom prompt from config if specified
try:
    from maple.config import load_config
    _cfg = load_config()
    if _cfg.overseer.prompt:
        _custom_prompt_path = Path(_cfg.overseer.prompt)
        if _custom_prompt_path.exists():
            SYSTEM_PROMPT = _custom_prompt_path.read_text()
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
            model_id=os.getenv("OVERSEER_MODEL", "qwen3:30b-a3b"),
            temperature=0.1,
            max_tokens=4000,
            keep_alive="30m",
            options={"num_ctx": 16384, "think": False, "num_predict": 4000},
        )
    elif provider == "anthropic":
        from strands.models.anthropic import AnthropicModel
        return AnthropicModel(
            client_args={"api_key": os.getenv("ANTHROPIC_API_KEY")},
            model_id=os.getenv("OVERSEER_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=4000,
            params={"temperature": 0.7},
        )
    else:
        from strands.models.openai import OpenAIModel
        return OpenAIModel(
            client_args={"api_key": os.getenv("OPENAI_API_KEY")},
            model_id=os.getenv("OVERSEER_MODEL", "gpt-4o"),
            params={"max_tokens": 4000, "temperature": 0.7},
        )


def create_overseer_agent(session_id: str, extra_hooks: list = None) -> Agent:
    """Create a configured Overseer agent instance.
    
    Args:
        session_id: Unique session identifier for conversation history.
        extra_hooks: Additional Strands HookProvider instances to attach.
    """
    from maple.auth import get_or_create_token

    model = _create_model()
    token = get_or_create_token()
    import httpx
    auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    mcp_client = MCPClient(lambda: streamable_http_client(
        MCP_OVERSEER_URL,
        http_client=auth_client,
    ))

    # Hook to capture complete tool calls
    from strands.hooks import HookProvider, HookRegistry
    from strands.hooks.events import BeforeToolCallEvent

    tool_queue = []

    class ToolCallCapture(HookProvider):
        def register_hooks(self, registry: HookRegistry, **kwargs):
            registry.add_callback(BeforeToolCallEvent, self._on_tool)

        def _on_tool(self, event: BeforeToolCallEvent):
            tool_queue.append({
                "name": event.tool_use.get("name", ""),
                "input": event.tool_use.get("input", {}),
            })
            name = event.tool_use.get("name", "?")
            print(f"INFO:     Tool call: {name}", flush=True)

    hooks = extra_hooks or []
    hooks.append(ToolCallCapture())

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[mcp_client],
        hooks=hooks,
        tool_executor=SequentialToolExecutor(),
        callback_handler=None,
        session_manager=FileSessionManager(
            session_id=session_id,
            storage_dir=str(OVERSEER_SESSIONS_DIR),
        ),
    )
    agent._tool_call_queue = tool_queue
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
    agent = create_overseer_agent(session_id)
    result = await asyncio.to_thread(agent, message)
    return {
        "message": result.message,
        "stop_reason": result.stop_reason,
    }


@app.post("/stream")
async def stream(request: dict):
    session_id = request["session_id"]
    message = request["message"]

    async def event_generator():
        agent = create_overseer_agent(session_id)
        tool_queue = getattr(agent, "_tool_call_queue", [])

        async for event in agent.stream_async(message):
            out = {}

            # Emit any queued complete tool calls
            while tool_queue:
                tool = tool_queue.pop(0)
                tool_event = {
                    "current_tool_use": {
                        "toolUseId": f"tool-{tool['name']}",
                        "name": tool["name"],
                        "input": tool["input"],
                    }
                }
                yield f"data: {json.dumps(tool_event, default=str)}\n\n"

            # Skip raw streaming tool deltas
            if "current_tool_use" in event:
                continue

            if "data" in event:
                out["data"] = event["data"]

            if "result" in event:
                r = event["result"]
                out["result"] = {
                    "stop_reason": getattr(r, "stop_reason", None),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8203)
