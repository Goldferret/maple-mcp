"""Configuration system for MAPLE."""

import importlib
import sys
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PostActionHook(BaseModel):
    """A MADSci node action to call after each operator action."""

    node: str
    action: str
    args: dict = {}


class CaptureConfig(BaseModel):
    """How to capture a frame for a view: which node + action to invoke."""

    node: str
    action: str = "capture_camera_image"


class ViewConfig(BaseModel):
    """A named scene the LLM can request via detect/verify.

    - backend: "module:ClassName" VisionBackend implementation
    - capture: node + action MAPLE runs to grab frame(s)
    - pre_capture_action: optional action run BEFORE capture (e.g. home_robot)
      to produce a clean scene
    - covers: which node names this view can observe (used to resolve
      detect(node=...) -> view)
    """

    backend: str
    capture: CaptureConfig
    pre_capture_action: Optional[str] = None
    covers: list[str] = []


class VisionConfig(BaseModel):
    """The view registry for an experiment."""

    views: dict[str, ViewConfig] = {}
    default_view: Optional[str] = None


class ExperimentConfig(BaseModel):
    """Experiment brief template."""

    name: str = "Untitled Experiment"
    objective: str = ""
    constraints: list[str] = []


class OperatorConfig(BaseModel):
    """Operator agent configuration."""

    vision_backend: str = ""  # DEPRECATED single-backend path (backward-compat)
    vision: VisionConfig = VisionConfig()  # NEW: view registry
    custom_tools: list[str] = []  # ["module:function_name", ...] to register at startup
    post_action_hooks: list[PostActionHook] = []
    prompt: str = ""  # Path to custom prompt file, or empty for default


class OverseerConfig(BaseModel):
    """Overseer agent configuration."""

    custom_tools: list[str] = []  # ["module:function_name", ...] to register at startup
    prompt: str = ""  # Path to custom prompt file, or empty for default


class MapleConfig(BaseModel):
    """Root MAPLE configuration."""

    experiment: ExperimentConfig = ExperimentConfig()
    operator: OperatorConfig = OperatorConfig()
    overseer: OverseerConfig = OverseerConfig()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: Optional[Path] = None) -> MapleConfig:
    """Load MAPLE configuration from a YAML file.

    Args:
        path: Path to config file. Defaults to maple.config.yaml in current directory.

    Returns:
        Validated MapleConfig instance.
    """
    path = path or Path("maple.config.yaml")
    if not path.exists():
        return MapleConfig()
    data = yaml.safe_load(path.read_text())
    return MapleConfig(**(data or {}))


# ---------------------------------------------------------------------------
# Dynamic Import
# ---------------------------------------------------------------------------


def import_string(dotted_path: str):
    """Import a class or object from a 'module:ClassName' string.

    Supports both colon-separated ('vision:BlockSortingVision') and
    dot-separated ('vision.BlockSortingVision') formats.

    If the module is a relative file path (e.g., 'vision' resolving to
    './vision.py'), the current directory is added to sys.path temporarily.

    Args:
        dotted_path: Import path in 'module:Class' or 'module.Class' format.

    Returns:
        The imported class or object.
    """
    if ":" in dotted_path:
        module_path, class_name = dotted_path.rsplit(":", 1)
    else:
        module_path, _, class_name = dotted_path.rpartition(".")

    if not module_path or not class_name:
        raise ImportError(f"Invalid import path: '{dotted_path}'. Use 'module:ClassName' format.")

    # Support relative imports from CWD
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        raise ImportError(f"Cannot import module '{module_path}' from '{dotted_path}'") from e

    try:
        return getattr(module, class_name)
    except AttributeError as e:
        raise ImportError(f"Module '{module_path}' has no attribute '{class_name}'") from e


def load_vision_backend(config: MapleConfig):
    """Load the VisionBackend class from config and instantiate it.

    Args:
        config: Loaded MapleConfig.

    Returns:
        A VisionBackend instance. StubBackend if no backend specified.
    """
    from maple.vision import StubBackend

    backend_path = config.operator.vision_backend
    if not backend_path:
        return StubBackend()

    cls = import_string(backend_path)
    return cls()


def load_vision_views(config: MapleConfig) -> dict:
    """Load the view registry from config into resolved runtime entries.

    Each view's backend string is imported and instantiated. The returned
    registry is keyed by view name and holds everything the operator needs
    to capture and interpret a frame for that view.

    Backward-compat: if no `vision.views` are configured but the deprecated
    `vision_backend` is set (or nothing is set), a single implicit "default"
    view is synthesized so existing setups keep working.

    Args:
        config: Loaded MapleConfig.

    Returns:
        {
          "registry": {view_name: {
                "backend": VisionBackend instance,
                "capture": {"node": str, "action": str},
                "pre_capture_action": str | None,
                "covers": [node names],
          }},
          "default_view": str | None,
        }
    """
    from maple.vision import StubBackend

    vision = config.operator.vision
    registry: dict = {}

    if vision.views:
        for name, view in vision.views.items():
            backend_cls = import_string(view.backend)
            registry[name] = {
                "backend": backend_cls(),
                "capture": {"node": view.capture.node, "action": view.capture.action},
                "pre_capture_action": view.pre_capture_action,
                "covers": list(view.covers),
            }
        default_view = vision.default_view or next(iter(registry.keys()))
        return {"registry": registry, "default_view": default_view}

    # No views configured — synthesize a single implicit view for backward-compat.
    backend_path = config.operator.vision_backend
    backend = import_string(backend_path)() if backend_path else StubBackend()
    registry["default"] = {
        "backend": backend,
        "capture": {"node": None, "action": "capture_camera_image"},
        "pre_capture_action": None,
        "covers": [],
    }
    return {"registry": registry, "default_view": "default"}


def resolve_view(view_name: Optional[str], node: Optional[str], vision_state: dict) -> str:
    """Resolve a (view, node) request to a concrete view name.

    Precedence (per design): node wins over view when both are given, because
    a node uniquely identifies the scene the LLM is operating on.

    - both given         -> resolve via node (find the view whose covers includes it)
    - only node          -> resolve via node
    - only view          -> use view
    - neither            -> default_view

    Raises ToolError on unknown view, uncovered node, or ambiguous coverage.
    """
    from fastmcp.exceptions import ToolError

    registry = vision_state["registry"]
    default_view = vision_state["default_view"]

    if node is not None:
        matches = [name for name, v in registry.items() if node in v["covers"]]
        if len(matches) == 1:
            return matches[0]
        if len(matches) == 0:
            raise ToolError(
                f"No view covers node '{node}'. Available views: {list(registry.keys())}. "
                f"Check the 'covers' lists in operator.vision.views."
            )
        raise ToolError(
            f"Node '{node}' is covered by multiple views {matches}. "
            f"Specify 'view' explicitly to disambiguate."
        )

    if view_name is not None:
        if view_name not in registry:
            raise ToolError(
                f"Unknown view '{view_name}'. Available views: {list(registry.keys())}."
            )
        return view_name

    if default_view is None or default_view not in registry:
        raise ToolError("No view specified and no default_view configured.")
    return default_view


def load_custom_tools(config: MapleConfig):
    """Load and register custom MCP tools from config.

    Each entry in config.operator.custom_tools should be a "module:function"
    string. The function must already be decorated with @mcp.tool, OR it will
    be registered programmatically.

    Args:
        config: Loaded MapleConfig.
    """
    if not config.operator.custom_tools:
        return

    from maple.operator.server import mcp

    for tool_path in config.operator.custom_tools:
        try:
            func = import_string(tool_path)
            # If not already registered, register it
            if not hasattr(func, "__mcp_tool__"):
                mcp.tool(func)
        except ImportError as e:
            raise ImportError(
                f"Failed to load custom tool '{tool_path}': {e}. "
                f"Check the path in maple.config.yaml under operator.custom_tools."
            ) from e


def load_overseer_custom_tools(config: MapleConfig):
    """Load and register custom MCP tools for the Overseer.

    Args:
        config: Loaded MapleConfig.
    """
    if not config.overseer.custom_tools:
        return

    from maple.overseer.server import mcp

    for tool_path in config.overseer.custom_tools:
        try:
            func = import_string(tool_path)
            if not hasattr(func, "__mcp_tool__"):
                mcp.tool(func)
        except ImportError as e:
            raise ImportError(
                f"Failed to load custom tool '{tool_path}': {e}. "
                f"Check the path in maple.config.yaml under overseer.custom_tools."
            ) from e
