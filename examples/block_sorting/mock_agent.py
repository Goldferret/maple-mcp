"""Mock agent for the block sorting demo.

A scripted FastAPI service that makes REAL MCP tool calls against the
Operator server. No LLM needed — the sequence is predetermined, but
tool results are real.

Requires:
    - Operator MCP server running (maple serve --agent operator)
    - Stub node registered with Workcell Manager
    - ExampleVision configured

Usage:
    python mock_agent.py
    # Agent runs on http://localhost:8202 (same port as real agent)
"""

import asyncio
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

load_dotenv()

app = FastAPI(title="Mock Operator Agent")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

MCP_OPERATOR_URL = os.getenv("MCP_OPERATOR_URL", "http://localhost:8102/mcp")


# ---------------------------------------------------------------------------
# Scripted sequence
# ---------------------------------------------------------------------------

SCRIPT = [
    {
        "reasoning": "I'll start by registering this experiment and observing the workspace.",
        "tool": "start_experiment",
        "input": {
            "name": "Block Sorting Demo",
            "description": "Sort colored blocks into assigned goal zones",
        },
        "capture": "experiment_id",  # capture this field from result
    },
    {
        "reasoning": "Let me observe what's in the workspace using the detection procedure.",
        "tool": "detect",
        "input": {},
    },
    {
        "reasoning": "I see 3 blocks: 2 red and 1 blue. I'll move the first red block to goal zone 1.",
        "tool": "run_node_action",
        "input": {
            "node_name": "StubBot",
            "action_name": "pick_and_place",
            "parameters": {"pick_x": 300, "pick_y": 250, "place_x": 150, "place_y": 100},
        },
    },
    {
        "reasoning": "Now I'll move the blue block to goal zone 2.",
        "tool": "run_node_action",
        "input": {
            "node_name": "StubBot",
            "action_name": "pick_and_place",
            "parameters": {"pick_x": 350, "pick_y": 280, "place_x": 500, "place_y": 100},
        },
    },
    {
        "reasoning": "Finally, I'll move the second red block to goal zone 1.",
        "tool": "run_node_action",
        "input": {
            "node_name": "StubBot",
            "action_name": "pick_and_place",
            "parameters": {"pick_x": 270, "pick_y": 220, "place_x": 180, "place_y": 130},
        },
    },
    {
        "reasoning": "All blocks moved. Let me verify the sorting state.",
        "tool": "verify",
        "input": {},
    },
    {
        "reasoning": "All 3 blocks are correctly sorted! Ending the experiment.",
        "tool": "end_experiment",
        "input": {
            "experiment_id": "{experiment_id}",  # placeholder, filled at runtime
            "summary": "Successfully sorted 3 blocks (2 red, 1 blue) into their assigned goal zones in 3 pick-and-place actions.",
        },
    },
]


# ---------------------------------------------------------------------------
# SSE Streaming Endpoint
# ---------------------------------------------------------------------------


@app.post("/stream")
async def stream(request: dict):
    """Stream scripted agent events with real MCP tool calls.
    
    MCP session is opened inside the generator to keep the entire
    lifecycle in one task (required by anyio cancel scopes).
    """

    async def event_generator():
        """Stream scripted agent events. Tries real MCP calls if server available, falls back to canned."""
        import httpx

        # Check if MCP server is available
        mcp_available = False
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(MCP_OPERATOR_URL.replace("/mcp", "/ping"))
                mcp_available = resp.status_code == 200
        except Exception:
            pass

        captured = {}

        if mcp_available:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(MCP_OPERATOR_URL) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    for step in SCRIPT:
                        reasoning = step["reasoning"]
                        for word in reasoning.split(" "):
                            yield f"data: {json.dumps({'data': word + ' '})}\n\n"
                            await asyncio.sleep(0.05)
                        await asyncio.sleep(0.3)

                        tool_input = json.loads(
                            json.dumps(step["input"]).replace("{experiment_id}", captured.get("experiment_id", ""))
                        )
                        tool_event = {"current_tool_use": {"toolUseId": f"tool-{step['tool']}", "name": step["tool"], "input": tool_input}}
                        yield f"data: {json.dumps(tool_event)}\n\n"

                        try:
                            result = await session.call_tool(step["tool"], tool_input)
                            if "capture" in step:
                                for content in result.content:
                                    if hasattr(content, "text"):
                                        try:
                                            result_data = json.loads(content.text)
                                            if step["capture"] in result_data:
                                                captured[step["capture"]] = result_data[step["capture"]]
                                        except (json.JSONDecodeError, TypeError):
                                            pass
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
        else:
            # Standalone mode — stream scripted events without real MCP calls
            for step in SCRIPT:
                reasoning = step["reasoning"]
                for word in reasoning.split(" "):
                    yield f"data: {json.dumps({'data': word + ' '})}\n\n"
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.3)

                tool_input = step["input"]
                tool_event = {"current_tool_use": {"toolUseId": f"tool-{step['tool']}", "name": step["tool"], "input": tool_input}}
                yield f"data: {json.dumps(tool_event)}\n\n"
                await asyncio.sleep(0.5)

        # Emit final result
        result_event = {
            "result": {
                "stop_reason": "end_turn",
                "experiment_ended": True,
            }
        }
        yield f"data: {json.dumps(result_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/invoke")
async def invoke(request: dict):
    """Non-streaming version."""
    return {
        "message": "Successfully sorted 3 blocks into goal zones.",
        "stop_reason": "end_turn",
        "experiment_ended": True,
    }


@app.get("/ping")
async def ping():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8202)
