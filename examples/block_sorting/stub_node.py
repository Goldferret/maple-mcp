"""Stub MADSci node for the block sorting demo.

Implements the MADSci node REST protocol with a single action:
pick_and_place. Always returns success. No real robot needed.

Usage:
    python stub_node.py
    # Node runs on http://0.0.0.0:2000
    # Registers with Workcell Manager using NODE_URL from .env
"""

import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

app = FastAPI(title="Stub Robot Node")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

actions: dict[str, dict] = {}

NODE_INFO = {
    "node_name": "StubBot",
    "node_description": "A stub robot node for MAPLE demos",
    "module_name": "stub_node",
    "module_version": "0.1.0",
    "node_type": "device",
    "actions": {
        "pick_and_place": {
            "name": "pick_and_place",
            "description": "Pick an object and place it at a target location",
            "args": {
                "pick_x": {"name": "pick_x", "description": "Pick X coordinate", "argument_type": "float", "required": True, "default": None},
                "pick_y": {"name": "pick_y", "description": "Pick Y coordinate", "argument_type": "float", "required": True, "default": None},
                "place_x": {"name": "place_x", "description": "Place X coordinate", "argument_type": "float", "required": True, "default": None},
                "place_y": {"name": "place_y", "description": "Place Y coordinate", "argument_type": "float", "required": True, "default": None},
            },
        },
        "analyze": {
            "name": "analyze",
            "description": "Analyze the last action for quality verification",
            "args": {
                "task_description": {"name": "task_description", "description": "What to analyze", "argument_type": "str", "required": False, "default": ""},
            },
        },
    },
}

NODE_STATUS = {
    "ready": True,
    "busy": False,
    "running_actions": [],
    "paused": False,
    "locked": False,
    "stopped": False,
    "errored": False,
    "errors": [],
    "initializing": False,
    "waiting_for_config": [],
    "config_values": {},
    "description": "Node is ready",
}


# ---------------------------------------------------------------------------
# Endpoints — MADSci Node Protocol
# ---------------------------------------------------------------------------


@app.get("/info")
async def get_info():
    return NODE_INFO


@app.get("/status")
async def get_status():
    return NODE_STATUS


@app.post("/action/{action_name}")
async def create_action(action_name: str, request: dict = None):
    """Create a new action instance."""
    action_id = str(uuid.uuid4())
    actions[action_id] = {
        "action_id": action_id,
        "action_name": action_name,
        "args": request.get("args", {}) if request else {},
        "status": "created",
    }
    return {"action_id": action_id}


@app.post("/action/{action_name}/{action_id}/start")
async def start_action(action_name: str, action_id: str):
    """Start an action — immediately completes for stub."""
    if action_id in actions:
        actions[action_id]["status"] = "succeeded"
    return {
        "action_id": action_id,
        "status": "succeeded",
        "json_result": {
            "message": f"pick_and_place completed successfully",
            "action_name": action_name,
        },
    }


@app.get("/action/{action_name}/{action_id}/result")
async def get_action_result(action_name: str, action_id: str):
    """Get action result — always succeeded for stub."""
    action = actions.get(action_id, {})
    return {
        "action_id": action_id,
        "action_name": action_name,
        "status": "succeeded",
        "json_result": {
            "message": f"{action_name} completed",
            "args": action.get("args", {}),
        },
        "errors": [],
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/state")
async def get_state():
    return {}


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="MAPLE Stub Robot Node")
    parser.add_argument("--url", default="http://host.docker.internal:2000",
                        help="URL the Workcell Manager should use to reach this node (default: http://host.docker.internal:2000)")
    parser.add_argument("--port", type=int, default=2000, help="Port to listen on (default: 2000)")
    args = parser.parse_args()

    # Auto-register with MADSci Workcell Manager
    try:
        from madsci.client import WorkcellClient

        client = WorkcellClient()
        client.add_node(
            node_name="StubBot",
            node_url=args.url,
            node_description="Stub robot node for MAPLE demo",
            permanent=False,
        )
        print(f"✓ Registered StubBot at {args.url} with Workcell Manager")
    except Exception as e:
        print(f"⚠ Could not register with Workcell Manager: {e}")
        print("  (Start MADSci services first, or register manually)")

    uvicorn.run(app, host="0.0.0.0", port=args.port)
