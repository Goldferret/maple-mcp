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


class ExperimentConfig(BaseModel):
    """Experiment brief template."""

    name: str = "Untitled Experiment"
    objective: str = ""
    constraints: list[str] = []


class OperatorConfig(BaseModel):
    """Operator agent configuration."""

    vision_backend: str = ""  # "module:ClassName" or empty for StubBackend
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
