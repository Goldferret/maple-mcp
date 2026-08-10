"""MAPLE Operator MCP Server — Experiment execution via MADSci.

Provides tools for experiment lifecycle management, action dispatch,
detection, and verification. Uses MADSci ExperimentApplication natively.
"""

from dotenv import load_dotenv
load_dotenv()

import atexit
import json
import os
import tempfile
from pathlib import Path
from typing import Optional


from fastmcp import FastMCP
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

mcp = FastMCP("maple-operator")


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

_app: Optional[ExperimentApplication] = None
_stored_procedure = None
_last_tool_called = None
_vision_backend: VisionBackend = StubBackend()
_post_action_hooks: list = []


def configure(vision_backend: VisionBackend = None, post_action_hooks: list = None):
    """Configure the Operator server at startup.
    
    Args:
        vision_backend: VisionBackend instance for detection
        post_action_hooks: List of post-action hook configs (node, action, args)
    """
    global _vision_backend, _post_action_hooks
    if vision_backend:
        _vision_backend = vision_backend
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
    if _app and reasoning_text.strip():
        _app.logger.info(f"LLM reasoning (before {before_tool}): {reasoning_text.strip()}")
    return JSONResponse({"status": "logged"})


# ---------------------------------------------------------------------------
# Lifecycle safety
# ---------------------------------------------------------------------------


def _cleanup_experiment():
    """Clean up any orphaned experiment on shutdown."""
    global _app
    if _app is not None:
        try:
            _app.end_experiment(status=ExperimentStatus.FAILED)
        except Exception:
            pass
        _app = None


atexit.register(_cleanup_experiment)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_default_node() -> str:
    """Get the first registered node name from the workcell."""
    if _app is None:
        raise ToolError("No active experiment.")
    nodes = _app.workcell_client.get_nodes()
    if not nodes:
        raise ToolError("No nodes registered in workcell.")
    return next(iter(nodes.keys()))


def _submit_workflow(action_name: str, node_name: str = None, args: dict = None) -> dict:
    """Submit a single-step workflow and return the step result."""
    if _app is None:
        raise ToolError("No active experiment. Call start_experiment first.")

    node_name = node_name or _get_default_node()

    wf_def = WorkflowDefinition(
        name=action_name,
        steps=[StepDefinition(
            name=action_name,
            node=node_name,
            action=action_name,
            args=args or {}
        )]
    )

    result = _app.workcell_client.start_workflow(
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
        json_result = _app.data_client.get_datapoint_value(dp_id)
    elif step.result.json_result is not None:
        json_result = step.result.json_result

    return {
        "status": str(step.status.value) if hasattr(step.status, 'value') else str(step.status),
        "json_result": json_result,
        "action_id": step.result.action_id,
        "errors": [str(e) for e in step.result.errors] if step.result.errors else [],
    }


def _capture_frame() -> tuple:
    """Capture camera frame via workflow. Returns (color_image_bytes, depth_image_bytes)."""
    result = _submit_workflow("capture_camera_image")

    if result["json_result"] is None:
        raise ToolError("capture_camera_image returned no data")

    capture_data = result["json_result"]
    color_id = capture_data.get("color_datapoint_id")

    if not color_id:
        raise ToolError("No color image in capture result")

    # Download color image as bytes
    color_bytes = _app.data_client.get_datapoint_value(color_id)
    if isinstance(color_bytes, dict):
        # Value datapoint, not file — try fetching as file
        color_path = tempfile.mktemp(suffix="_color.jpg")
        _app.data_client.save_datapoint_value(color_id, color_path)
        with open(color_path, "rb") as f:
            color_bytes = f.read()
        os.unlink(color_path)

    return color_bytes


def _run_post_action_hooks(action_result: dict):
    """Run configured post-action hooks (e.g., Reason2 analysis)."""
    if not _post_action_hooks or not _app:
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
            hook_result = _app.workcell_client.start_workflow(
                wf, await_completion=True, prompt_on_error=False, raise_on_failed=False
            )

            # Log hook result if it produced a json_result
            step = hook_result.steps[0]
            if step.result and step.result.datapoints and step.result.datapoints.json_result:
                dp_id = step.result.datapoints.json_result
                hook_data = _app.data_client.get_datapoint_value(dp_id)
                if isinstance(hook_data, dict) and "outcome" in hook_data:
                    _app.logger.info(
                        f"{hook['node']}: outcome={hook_data.get('outcome')} "
                        f"failure_mode={hook_data.get('failure_mode')} "
                        f"description={hook_data.get('description')} "
                        f"confidence={hook_data.get('confidence')}"
                    )
        except Exception as e:
            if _app:
                _app.logger.info(f"Post-action hook {hook['node']}/{hook['action']} skipped: {e}")


# ---------------------------------------------------------------------------
# MCP Tools — Experiment Lifecycle
# ---------------------------------------------------------------------------


@mcp.tool
def start_experiment(name: str, description: str) -> dict:
    """Initialize a new experiment.

    Args:
        name: Experiment name
        description: Experiment description
    """
    global _app, _last_tool_called

    if _app is not None:
        try:
            _app.end_experiment(status=ExperimentStatus.FAILED)
        except Exception:
            pass
        _app = None

    _last_tool_called = None

    _app = ExperimentApplication(
        experiment_design=ExperimentDesign(
            experiment_name=name,
            experiment_description=description,
        )
    )
    _app.start_experiment_run(run_name=name, run_description=description)

    if _app.logger.config.source:
        _app.logger.config.source.experiment_id = _app.experiment.experiment_id
    else:
        _app.logger.config.source = OwnershipInfo(experiment_id=_app.experiment.experiment_id)

    _app.logger.info(f"Experiment started: {name}")

    return {
        "experiment_id": _app.experiment.experiment_id,
        "status": str(_app.experiment.status.value),
    }


@mcp.tool
def end_experiment(experiment_id: str, summary: str) -> dict:
    """Finalize an experiment.

    Args:
        experiment_id: Experiment ID
        summary: Brief description of what was accomplished or why the experiment ended
    """
    global _app, _last_tool_called

    if _last_tool_called != "verify":
        raise ToolError(
            "Cannot end experiment: verify must be called "
            "immediately before end_experiment to confirm the final state. "
            "Call verify now, then call end_experiment."
        )

    if _app is None:
        raise ToolError("No active experiment to end")

    # Store summary as datapoint
    _app.data_client.submit_datapoint(ValueDataPoint(
        label="experiment_summary",
        value={"summary": summary, "experiment_id": experiment_id},
        ownership_info=OwnershipInfo(experiment_id=experiment_id),
    ))

    _app.logger.info(f"Ending experiment: {experiment_id}")
    _app.logger.info(f"Summary: {summary}")
    _app.end_experiment()

    result = {
        "experiment_id": experiment_id,
        "status": "completed",
        "summary": summary,
    }

    _app = None
    return result


# ---------------------------------------------------------------------------
# MCP Tools — Detection
# ---------------------------------------------------------------------------


@mcp.tool
def detect() -> dict:
    """Observe the workspace and detect objects.

    Captures an image and runs the configured VisionBackend's detection.

    Returns:
        Dict with detections list and count.
    """
    if _app is None:
        raise ToolError("No active experiment. Call start_experiment first.")

    # Capture frame
    color_bytes = _capture_frame()

    if _app:
        _app.logger.info("Detection: frame captured")

    # Run VisionBackend
    detections = _vision_backend.detect_objects(color_bytes, {})

    if _app:
        _app.data_client.submit_datapoint(ValueDataPoint(
            label="detection_results",
            value={"detections": detections, "count": len(detections)},
            ownership_info=OwnershipInfo(experiment_id=_app.experiment.experiment_id),
        ))
        _app.logger.info(f"Detection: found {len(detections)} objects")

    global _last_tool_called
    _last_tool_called = "detect"

    return {"detections": detections, "count": len(detections)}


# ---------------------------------------------------------------------------
# MCP Tools — Verification
# ---------------------------------------------------------------------------


@mcp.tool
def verify() -> dict:
    """Verify whether the experiment goal has been achieved.

    Captures an image and runs the configured VisionBackend's verification.

    Returns:
        Dict with success status and details.
    """
    if _app is None:
        raise ToolError("No active experiment. Call start_experiment first.")

    if _app:
        _app.logger.info("Verification: capturing frame")
    color_bytes = _capture_frame()

    # Run VisionBackend verification
    result = _vision_backend.verify_goal(color_bytes, {})

    if _app:
        _app.data_client.submit_datapoint(ValueDataPoint(
            label="verification_results",
            value=result,
            ownership_info=OwnershipInfo(experiment_id=_app.experiment.experiment_id),
        ))
        _app.logger.info(f"Verification: success={result.get('success')}")

    global _last_tool_called
    _last_tool_called = "verify"
    return result


# ---------------------------------------------------------------------------
# MCP Tools — Node Actions
# ---------------------------------------------------------------------------


@mcp.tool
def run_node_action(node_name: str, action_name: str, parameters: dict = None) -> dict:
    """Execute an action on a robot node via MADSci workflow.

    Args:
        node_name: Node to run action on (e.g. 'DOFBOT_Pro_1')
        action_name: Action name (e.g. 'pick_and_place', 'home_robot')
        parameters: Action parameters as a dict.
    """
    if isinstance(parameters, str):
        parameters = json.loads(parameters)

    if _app:
        _app.logger.info(f"Action: {action_name} on {node_name} with {parameters}")

    result = _submit_workflow(action_name, node_name, parameters or {})

    global _last_tool_called
    _last_tool_called = "run_node_action"

    if _app:
        _app.logger.info(f"Action result: {result['status']}")

    # Run post-action hooks (e.g., Reason2 analysis)
    _run_post_action_hooks(result)

    return result


# ---------------------------------------------------------------------------
# MCP Tools — Node Info & Constraints
# ---------------------------------------------------------------------------


@mcp.tool
def get_robot_constraints(node_name: str) -> dict:
    """Get physical constraints and capabilities of a robot.

    Args:
        node_name: Name of the robot node
    """
    if _app is None:
        raise ToolError("No active experiment. Call start_experiment first.")

    # Return generic constraints — labs can customize via config in future
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
def get_node_info(node_name: str) -> dict:
    """Get node capabilities with available actions.

    Args:
        node_name: Name of node
    """
    if _app is None:
        raise ToolError("No active experiment. Call start_experiment first.")

    nodes = _app.workcell_client.get_nodes()
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
    from maple.config import load_config, load_vision_backend, load_custom_tools
    _config = load_config()
    _vision_backend = load_vision_backend(_config)
    _post_action_hooks = [
        (h.node, h.action, h.args) for h in _config.operator.post_action_hooks
    ]
    load_custom_tools(_config)
except Exception:
    pass  # Config not available (e.g., no maple.config.yaml in CWD)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = mcp.http_app()
