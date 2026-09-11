"""MAPLE Operator MCP Server — Experiment execution via MADSci.

Provides tools for experiment lifecycle management, action dispatch,
detection, and verification. Uses MADSci ExperimentApplication natively.
"""

from dotenv import load_dotenv
load_dotenv(override=True)

import atexit
import json
import os
from pathlib import Path


from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from madsci.common.types.datapoint_types import ValueDataPoint
from madsci.common.types.experiment_types import ExperimentDesign, ExperimentStatus
from madsci.common.types.step_types import StepDefinition
from madsci.common.types.workflow_types import WorkflowDefinition
from madsci.common.ownership import OwnershipInfo
from madsci.experiment_application import ExperimentApplication
from starlette.requests import Request
from starlette.responses import JSONResponse

from maple.vision import VisionBackend, StubBackend


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

from maple.middleware import AuthMiddleware

mcp = FastMCP("maple-operator", middleware=[AuthMiddleware()])


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# State (vision backend + hooks are server-wide; experiments are per-session)
# ---------------------------------------------------------------------------

_vision_state: dict = {
    "registry": {"default": {"backend": StubBackend(), "capture": {"node": None, "action": "capture_camera_image"}, "pre_capture_action": None, "covers": []}},
    "default_view": "default",
}
_post_action_hooks: list = []


def configure(vision_state: dict = None, post_action_hooks: list = None):
    """Configure the Operator server at startup.

    Args:
        vision_state: Resolved view registry from load_vision_views()
            ({"registry": {...}, "default_view": ...}).
        post_action_hooks: List of post-action hook configs (node, action, args)
    """
    global _vision_state, _post_action_hooks
    if vision_state:
        _vision_state = vision_state
    if post_action_hooks is not None:
        _post_action_hooks = post_action_hooks


# ---------------------------------------------------------------------------
# Hidden endpoint — LLM reasoning capture
# ---------------------------------------------------------------------------


@mcp.custom_route("/log_reasoning", methods=["POST"])
async def log_reasoning(request: Request) -> JSONResponse:
    """Log LLM reasoning to Event Manager. Called by agent-side hooks."""
    body = await request.json()
    reasoning_text = body.get("reasoning_text", body.get("reasoning", ""))
    before_tool = body.get("before_tool", "unknown")
    # Log reasoning is best-effort — no session context available here
    if reasoning_text.strip():
        print(f"[reasoning] (before {before_tool}): {reasoning_text.strip()[:200]}")
    return JSONResponse({"status": "logged"})


# ---------------------------------------------------------------------------
# Lifecycle safety
# ---------------------------------------------------------------------------


def _cleanup_all():
    """Clean up all sessions on shutdown."""
    from maple.sessions import cleanup_all_sessions
    cleanup_all_sessions()


atexit.register(_cleanup_all)


# Schedule periodic idle cleanup
import asyncio as _asyncio

_cleanup_task = None

async def _ensure_cleanup_running():
    """Start the idle cleanup background task if not already running."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = _asyncio.create_task(_idle_cleanup_loop())

async def _idle_cleanup_loop():
    """Background task: reap idle sessions every 5 minutes."""
    from maple.sessions import cleanup_idle_sessions
    while True:
        await _asyncio.sleep(300)  # Check every 5 minutes
        await cleanup_idle_sessions(max_idle_seconds=1800)  # 30 min idle = reap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_default_node(app) -> str:
    """Get the first registered node name from the workcell."""
    nodes = app.workcell_client.get_nodes()
    if not nodes:
        raise ToolError("No nodes registered in workcell.")
    return next(iter(nodes.keys()))


def _submit_workflow(action_name: str, node_name: str = None, args: dict = None, app=None) -> dict:
    """Submit a single-step workflow and return the step result."""
    if app is None:
        raise ToolError("No active experiment.")

    node_name = node_name or _get_default_node(app)

    wf_def = WorkflowDefinition(
        name=action_name,
        steps=[StepDefinition(
            name=action_name,
            node=node_name,
            action=action_name,
            args=args or {}
        )]
    )

    result = app.workcell_client.start_workflow(
        wf_def,
        await_completion=True,
        prompt_on_error=False,
        raise_on_failed=False,
        raise_on_cancelled=False,
    )

    step = result.steps[0]

    if step.result is None:
        raise ToolError(f"Action '{action_name}' returned no result")

    json_result = None
    if step.result.datapoints and step.result.datapoints.json_result:
        dp_id = step.result.datapoints.json_result
        json_result = app.data_client.get_datapoint_value(dp_id)
    elif step.result.json_result is not None:
        json_result = step.result.json_result

    return {
        "status": str(step.status.value) if hasattr(step.status, 'value') else str(step.status),
        "json_result": json_result,
        "action_id": step.result.action_id,
        "errors": [str(e) for e in step.result.errors] if step.result.errors else [],
    }


def _capture_frames(app, view_config: dict) -> dict:
    """Capture frame(s) for a view and return raw datapoint values.

    Runs the optional pre_capture_action (e.g. home_robot) to produce a clean
    scene, then the capture action, then downloads EVERY datapoint the capture
    produced and returns them as {datapoint_key: raw_value}.

    The VisionBackend receives this dict and picks the keys it needs — MAPLE
    does not judge which datapoint is an image; it hands over all raw values.

    Args:
        app: The session's ExperimentApplication.
        view_config: Resolved view entry with 'capture' and 'pre_capture_action'.

    Returns:
        Dict of {datapoint_key: raw_value}. Empty dict if capture produced no
        datapoints.
    """
    capture = view_config["capture"]
    node = capture.get("node")
    action = capture.get("action", "capture_camera_image")
    pre = view_config.get("pre_capture_action")

    # Optional: clean the scene before capturing (e.g. home the arm out of view)
    if pre:
        try:
            _submit_workflow(pre, node_name=node, app=app)
        except Exception as e:
            app.logger.info(f"pre_capture_action '{pre}' skipped: {e}")

    # Dispatch the capture action
    wf_def = WorkflowDefinition(
        name=action,
        steps=[StepDefinition(
            name=action,
            node=node or _get_default_node(app),
            action=action,
            args={},
        )],
    )
    result = app.workcell_client.start_workflow(
        wf_def,
        await_completion=True,
        prompt_on_error=False,
        raise_on_failed=False,
        raise_on_cancelled=False,
    )
    step = result.steps[0]
    if step.result is None or step.result.datapoints is None:
        return {}

    dp_dict = step.result.datapoints.model_dump()
    frames: dict = {}
    for key, dp_id in dp_dict.items():
        if not dp_id:
            continue
        try:
            frames[key] = app.data_client.get_datapoint_value(dp_id)
        except Exception as e:
            app.logger.info(f"Could not download datapoint '{key}' ({dp_id}): {e}")
    return frames


def _run_post_action_hooks(action_result: dict, app=None):
    """Run configured post-action hooks (e.g., Reason2 analysis)."""
    if not _post_action_hooks or not app:
        return

    for hook in _post_action_hooks:
        try:
            hook_args = dict(hook.get("args", {}))
            # Inject video_datapoint_id if available
            if action_result.get("json_result") and action_result["json_result"].get("video_datapoint_id"):
                hook_args.setdefault("video_datapoint_id", action_result["json_result"]["video_datapoint_id"])

            wf = WorkflowDefinition(
                name=f"hook_{hook['action']}",
                steps=[StepDefinition(
                    name=hook["action"],
                    node=hook["node"],
                    action=hook["action"],
                    args=hook_args
                )]
            )
            hook_result = app.workcell_client.start_workflow(
                wf, await_completion=True, prompt_on_error=False, raise_on_failed=False
            )

            # Log hook result if it produced a json_result
            step = hook_result.steps[0]
            if step.result and step.result.datapoints and step.result.datapoints.json_result:
                dp_id = step.result.datapoints.json_result
                hook_data = app.data_client.get_datapoint_value(dp_id)
                if isinstance(hook_data, dict) and "outcome" in hook_data:
                    app.logger.info(
                        f"{hook['node']}: outcome={hook_data.get('outcome')} "
                        f"failure_mode={hook_data.get('failure_mode')} "
                        f"description={hook_data.get('description')} "
                        f"confidence={hook_data.get('confidence')}"
                    )
        except Exception as e:
            if app:
                app.logger.info(f"Post-action hook {hook['node']}/{hook['action']} skipped: {e}")


# ---------------------------------------------------------------------------
# MCP Tools — Experiment Lifecycle
# ---------------------------------------------------------------------------


@mcp.tool
async def start_experiment(name: str, description: str, ctx: Context) -> dict:
    """Initialize a new experiment.

    Args:
        name: Experiment name
        description: Experiment description
    """
    from maple.sessions import create_session

    # Ensure idle cleanup background task is running
    await _ensure_cleanup_running()

    app = ExperimentApplication(
        experiment_design=ExperimentDesign(
            experiment_name=name,
            experiment_description=description,
        )
    )
    app.start_experiment_run(run_name=name, run_description=description)

    if app.logger.config.source:
        app.logger.config.source.experiment_id = app.experiment.experiment_id
    else:
        app.logger.config.source = OwnershipInfo(experiment_id=app.experiment.experiment_id)

    app.logger.info(f"Experiment started: {name}")

    entry = await create_session(ctx, app)
    entry.last_tool_called = "start_experiment"

    return {
        "experiment_id": app.experiment.experiment_id,
        "status": str(app.experiment.status.value),
    }


@mcp.tool
async def end_experiment(experiment_id: str, summary: str, ctx: Context) -> dict:
    """Finalize an experiment.

    Args:
        experiment_id: Experiment ID
        summary: Brief description of what was accomplished or why the experiment ended
    """
    from maple.sessions import get_session, end_session

    entry = await get_session(ctx)

    if entry.last_tool_called != "verify":
        raise ToolError(
            "Cannot end experiment: verify must be called "
            "immediately before end_experiment to confirm the final state. "
            "Call verify now, then call end_experiment."
        )

    app = entry.app

    # Validate experiment_id matches active experiment
    if experiment_id != app.experiment.experiment_id:
        raise ToolError(
            f"experiment_id '{experiment_id}' does not match active experiment "
            f"'{app.experiment.experiment_id}'"
        )

    # Store summary as datapoint
    app.data_client.submit_datapoint(ValueDataPoint(
        label="experiment_summary",
        value={"summary": summary, "experiment_id": experiment_id},
        ownership_info=OwnershipInfo(experiment_id=experiment_id),
    ))

    app.logger.info(f"Ending experiment: {experiment_id}")
    app.logger.info(f"Summary: {summary}")
    app.end_experiment()

    await end_session(ctx)

    return {
        "experiment_id": experiment_id,
        "status": "completed",
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# MCP Tools — Detection
# ---------------------------------------------------------------------------


@mcp.tool
async def detect(ctx: Context, view: str = None, node: str = None) -> dict:
    """Observe the workspace and detect objects.

    Captures frame(s) for a view and runs that view's VisionBackend detection.

    Args:
        view: Named view/scene to observe (e.g. 'block_table'). Optional.
        node: Node whose scene to observe (e.g. 'DOFBOT_Pro_1'). Optional.
            If both view and node are given, node takes precedence. If neither
            is given, the configured default view is used.

    Returns:
        Dict with detections list, count, and the resolved view.
    """
    from maple.sessions import get_session
    from maple.config import resolve_view

    entry = await get_session(ctx)
    app = entry.app

    view_name = resolve_view(view, node, _vision_state)
    view_config = _vision_state["registry"][view_name]

    frames = _capture_frames(app, view_config)
    app.logger.info(f"Detection: captured frames for view '{view_name}'")

    detections = view_config["backend"].detect_objects(frames, {})

    app.data_client.submit_datapoint(ValueDataPoint(
        label="detection_results",
        value={"detections": detections, "count": len(detections), "view": view_name},
        ownership_info=OwnershipInfo(experiment_id=app.experiment.experiment_id),
    ))
    app.logger.info(f"Detection: found {len(detections)} objects (view '{view_name}')")

    entry.last_tool_called = "detect"

    return {"detections": detections, "count": len(detections), "view": view_name}


# ---------------------------------------------------------------------------
# MCP Tools — Verification
# ---------------------------------------------------------------------------


@mcp.tool
async def verify(ctx: Context, view: str = None, node: str = None) -> dict:
    """Verify whether the experiment goal has been achieved.

    Captures frame(s) for a view and runs that view's VisionBackend verification.

    Args:
        view: Named view/scene to verify (e.g. 'block_table'). Optional.
        node: Node whose scene to verify (e.g. 'DOFBOT_Pro_1'). Optional.
            If both are given, node takes precedence. If neither is given, the
            configured default view is used.

    Returns:
        Dict with success status, details, and the resolved view.
    """
    from maple.sessions import get_session
    from maple.config import resolve_view

    entry = await get_session(ctx)
    app = entry.app

    view_name = resolve_view(view, node, _vision_state)
    view_config = _vision_state["registry"][view_name]

    app.logger.info(f"Verification: capturing frames for view '{view_name}'")
    frames = _capture_frames(app, view_config)

    result = view_config["backend"].verify_goal(frames, {})

    app.data_client.submit_datapoint(ValueDataPoint(
        label="verification_results",
        value={**result, "view": view_name} if isinstance(result, dict) else result,
        ownership_info=OwnershipInfo(experiment_id=app.experiment.experiment_id),
    ))
    app.logger.info(f"Verification: success={result.get('success')} (view '{view_name}')")

    entry.last_tool_called = "verify"
    return result


# ---------------------------------------------------------------------------
# MCP Tools — Node Actions
# ---------------------------------------------------------------------------


@mcp.tool
async def run_node_action(node_name: str, action_name: str, ctx: Context, parameters: dict = None) -> dict:
    """Execute an action on a robot node via MADSci workflow.

    Args:
        node_name: Node to run action on (e.g. 'DOFBOT_Pro_1')
        action_name: Action name (e.g. 'pick_and_place', 'home_robot')
        parameters: Action parameters as a dict.
    """
    from maple.sessions import get_session

    entry = await get_session(ctx)
    app = entry.app

    if isinstance(parameters, str):
        parameters = json.loads(parameters)

    app.logger.info(f"Action: {action_name} on {node_name} with {parameters}")

    result = _submit_workflow(action_name, node_name, parameters or {}, app=app)

    entry.last_tool_called = "run_node_action"

    app.logger.info(f"Action result: {result['status']}")

    # Run post-action hooks (e.g., Reason2 analysis)
    _run_post_action_hooks(result, app=app)

    return result


# ---------------------------------------------------------------------------
# MCP Tools — Node Info & Constraints
# ---------------------------------------------------------------------------


@mcp.tool
async def get_robot_constraints(node_name: str, ctx: Context) -> dict:
    """Get physical constraints and capabilities of a robot.

    Args:
        node_name: Name of the robot node
    """
    from maple.sessions import get_session

    await get_session(ctx)  # Verify experiment is active

    return {
        "node_name": node_name,
        "description": "Robotic manipulator with single gripper.",
        "constraints": [
            "Single gripper. Can hold one object at a time.",
            "Sequential execution. One action at a time.",
            "Pixel-based targeting. Actions use pixel coordinates from overhead camera.",
        ],
    }


@mcp.tool
async def get_node_info(node_name: str, ctx: Context) -> dict:
    """Get node capabilities with available actions.

    Args:
        node_name: Name of node
    """
    from maple.sessions import get_session

    entry = await get_session(ctx)
    app = entry.app

    nodes = app.workcell_client.get_nodes()
    if node_name not in nodes:
        raise ToolError(f"Node '{node_name}' not found in workcell")

    raw = nodes[node_name].get("info", {})
    if not raw:
        raise ToolError(f"No info available for node '{node_name}'")

    clean_actions = {}
    for action_name, action_data in raw.get("actions", {}).items():
        desc = action_data.get("description", "")
        params = {}
        for param_name, param_data in action_data.get("args", {}).items():
            params[param_name] = {
                "type": param_data.get("argument_type", "any"),
                "required": param_data.get("required", False),
            }
        clean_actions[action_name] = {
            "description": desc,
            "parameters": params,
        }

    return {
        "node_name": node_name,
        "actions": clean_actions,
    }


# ---------------------------------------------------------------------------
# Auto-load custom tools and vision backend from config
# ---------------------------------------------------------------------------

try:
    from maple.config import load_config, load_vision_views, load_custom_tools
    _config = load_config()
    _vision_state = load_vision_views(_config)
    _post_action_hooks = [
        {"node": h.node, "action": h.action, "args": h.args} for h in _config.operator.post_action_hooks
    ]
    load_custom_tools(_config)
except Exception as e:
    print(f"[MCP] Config auto-load failed: {e}", flush=True)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = mcp.http_app()
