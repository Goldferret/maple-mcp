"""MAPLE Overseer MCP Server — Lab oversight and experiment management.

Provides tools for querying lab state, managing experiments, browsing
event/data history, and managing resources and locations.
Uses MADSci client libraries natively.
"""

from dotenv import load_dotenv
load_dotenv(override=True)

import os
from pathlib import Path


from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from madsci.client import (
    DataClient,
    EventClient,
    ExperimentClient,
    LocationClient,
    ResourceClient,
    WorkcellClient,
)
from madsci.common.types.location_types import Location
from madsci.common.types.resource_types import Resource
from starlette.requests import Request
from starlette.responses import JSONResponse


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("maple-overseer")


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class OverseerContext:
    """Holds all MADSci client instances for the Overseer.
    
    When MADSci upgrades to v0.6.0+, this can be replaced with MadsciClientMixin.
    """

    def __init__(self):
        self.workcell = WorkcellClient()
        self.experiment = ExperimentClient()
        self.event = EventClient()
        self.data = DataClient()
        self.resource = ResourceClient()
        self.location = LocationClient()


_ctx = OverseerContext()


# ---------------------------------------------------------------------------
# MCP Tools — Lab State
# ---------------------------------------------------------------------------


@mcp.tool
def get_lab_state() -> dict:
    """Get the current state of the lab including all registered nodes and their status.

    Returns a summary of available nodes, their actions, and whether they are reachable.
    """
    client = _ctx.workcell
    nodes = client.get_nodes()

    result = {}
    for name, node_data in nodes.items():
        info = node_data.get("info", {})
        status = node_data.get("status", {})
        actions = list(info.get("actions", {}).keys()) if info else []
        result[name] = {
            "actions": actions,
            "status": status,
            "node_id": info.get("node_id") if info else None,
        }

    return {"nodes": result, "total_nodes": len(result)}


# ---------------------------------------------------------------------------
# MCP Tools — Experiments
# ---------------------------------------------------------------------------


@mcp.tool
def list_experiments(limit: int = 20) -> dict:
    """List recent experiments.

    Args:
        limit: Maximum number of experiments to return (default 20)
    """
    client = _ctx.experiment
    try:
        experiments = client.get_experiments()[:limit]
        return {
            "experiments": [
                {
                    "experiment_id": exp.experiment_id,
                    "name": exp.experiment_design.experiment_name if exp.experiment_design else "unknown",
                    "status": str(exp.status.value) if hasattr(exp.status, "value") else str(exp.status),
                }
                for exp in experiments
            ],
            "count": len(experiments),
        }
    except Exception as e:
        raise ToolError(f"Failed to list experiments: {e}")


@mcp.tool
def get_experiment_status(experiment_id: str) -> dict:
    """Get detailed status of a specific experiment.

    Args:
        experiment_id: The experiment ID to query
    """
    client = _ctx.experiment
    try:
        exp = client.get_experiment(experiment_id=experiment_id)
        return {
            "experiment_id": exp.experiment_id,
            "name": exp.experiment_design.experiment_name if exp.experiment_design else "unknown",
            "description": exp.experiment_design.experiment_description if exp.experiment_design else "",
            "status": str(exp.status.value) if hasattr(exp.status, "value") else str(exp.status),
            "run_name": exp.run_name,
        }
    except Exception as e:
        raise ToolError(f"Failed to get experiment: {e}")


@mcp.tool
def cancel_experiment(experiment_id: str) -> dict:
    """Cancel a running experiment.

    Args:
        experiment_id: The experiment ID to cancel
    """
    client = _ctx.experiment
    try:
        exp = client.cancel_experiment(experiment_id=experiment_id)
        return {
            "experiment_id": exp.experiment_id,
            "status": str(exp.status.value) if hasattr(exp.status, "value") else str(exp.status),
            "message": "Experiment cancelled",
        }
    except Exception as e:
        raise ToolError(f"Failed to cancel experiment: {e}")


# ---------------------------------------------------------------------------
# MCP Tools — Events
# ---------------------------------------------------------------------------


@mcp.tool
def query_events(experiment_id: str = None, limit: int = 50) -> dict:
    """Query the event log for experiment activity.

    Args:
        experiment_id: Filter by experiment ID (optional)
        limit: Maximum events to return (default 50)
    """
    import requests

    event_url = os.getenv("EVENT_SERVER_URL", "http://localhost:8001").rstrip("/")
    query = {}
    if experiment_id:
        query["source.experiment_id"] = experiment_id

    try:
        resp = requests.post(f"{event_url}/events/query", json=query, timeout=30)
        if not resp.ok:
            raise ToolError(f"Event query failed: {resp.status_code}")

        data = resp.json()
        events = sorted(data.values(), key=lambda e: e.get("event_timestamp", ""))

        # Return most recent events up to limit
        recent = events[-limit:] if len(events) > limit else events
        return {
            "events": [
                {
                    "timestamp": ev.get("event_timestamp", ""),
                    "type": ev.get("event_type", ""),
                    "data": ev.get("event_data", ""),
                }
                for ev in recent
            ],
            "total": len(events),
            "returned": len(recent),
        }
    except requests.RequestException as e:
        raise ToolError(f"Failed to query events: {e}")


# ---------------------------------------------------------------------------
# MCP Tools — Datapoints
# ---------------------------------------------------------------------------


@mcp.tool
def query_datapoints(label: str = None, experiment_id: str = None, limit: int = 20) -> dict:
    """Query stored datapoints (images, JSON results, telemetry, etc.).

    Args:
        label: Filter by datapoint label (e.g., 'detection_results', 'verification_results')
        experiment_id: Filter by experiment ID
        limit: Maximum results to return
    """
    import requests

    data_url = os.getenv("DATA_SERVER_URL", "http://localhost:8004").rstrip("/")
    query = {}
    if label:
        query["label"] = label
    if experiment_id:
        query["ownership_info.experiment_id"] = experiment_id

    try:
        resp = requests.post(f"{data_url}/datapoints/query", json=query, timeout=30)
        if not resp.ok:
            raise ToolError(f"Datapoint query failed: {resp.status_code}")

        data = resp.json()
        # Sort by timestamp, return most recent
        items = sorted(data.values(), key=lambda d: d.get("data_timestamp", ""))
        recent = items[-limit:] if len(items) > limit else items

        return {
            "datapoints": [
                {
                    "id": dp.get("_id") or dp.get("datapoint_id", ""),
                    "label": dp.get("label", ""),
                    "timestamp": dp.get("data_timestamp", ""),
                    "type": dp.get("data_type", ""),
                }
                for dp in recent
            ],
            "total": len(items),
            "returned": len(recent),
        }
    except requests.RequestException as e:
        raise ToolError(f"Failed to query datapoints: {e}")


# ---------------------------------------------------------------------------
# MCP Tools — Resources
# ---------------------------------------------------------------------------


@mcp.tool
def get_resources(resource_name: str = None, resource_class: str = None) -> dict:
    """Get resources registered in the lab.

    Args:
        resource_name: Filter by resource name (optional)
        resource_class: Filter by resource class (optional)
    """
    client = _ctx.resource
    try:
        result = client.query_resource(
            resource_name=resource_name,
            resource_class=resource_class,
            multiple=True,
        )
        if isinstance(result, list):
            resources = result
        else:
            resources = [result] if result else []

        return {
            "resources": [
                {
                    "resource_id": r.resource_id,
                    "name": r.resource_name,
                    "class": r.resource_class,
                    "type": r.base_type,
                }
                for r in resources
            ],
            "count": len(resources),
        }
    except Exception as e:
        # "Resource not found" means empty — not an error
        if "not found" in str(e).lower():
            return {"resources": [], "count": 0}
        raise ToolError(f"Failed to get resources: {e}")


@mcp.tool
def add_resource(name: str, resource_class: str, base_type: str = "resource") -> dict:
    """Register a new resource in the lab.

    Args:
        name: Name of the resource (e.g., 'Red Block A')
        resource_class: Class of resource (e.g., 'colored_block', 'sample_tube')
        base_type: Resource base type (default 'resource'). Options: resource, asset, consumable, container, collection, slot
    """
    client = _ctx.resource
    try:
        resource = Resource(
            resource_name=name,
            resource_class=resource_class,
            base_type=base_type,
        )
        created = client.add_resource(resource)
        return {
            "resource_id": created.resource_id,
            "name": created.resource_name,
            "class": created.resource_class,
            "message": f"Resource '{name}' registered",
        }
    except Exception as e:
        raise ToolError(f"Failed to add resource: {e}")


@mcp.tool
def remove_resource(resource_id: str) -> dict:
    """Remove a resource from the lab.

    Args:
        resource_id: ID of the resource to remove
    """
    client = _ctx.resource
    try:
        removed = client.remove_resource(resource_id)
        return {
            "resource_id": removed.resource_id,
            "name": removed.resource_name,
            "message": "Resource removed",
        }
    except Exception as e:
        raise ToolError(f"Failed to remove resource: {e}")


# ---------------------------------------------------------------------------
# MCP Tools — Locations
# ---------------------------------------------------------------------------


@mcp.tool
def get_locations() -> dict:
    """Get all registered locations in the lab."""
    client = _ctx.location
    try:
        locations = client.get_locations()
        return {
            "locations": [
                {
                    "location_id": loc.location_id,
                    "name": loc.location_name,
                    "resource_id": loc.resource_id,
                }
                for loc in locations
            ],
            "count": len(locations),
        }
    except Exception as e:
        raise ToolError(f"Failed to get locations: {e}")


@mcp.tool
def add_location(name: str) -> dict:
    """Register a new location in the lab.

    Args:
        name: Name of the location (e.g., 'Goal Zone 1', 'Station A')
    """
    client = _ctx.location
    try:
        location = Location(location_name=name)
        created = client.add_location(location)
        return {
            "location_id": created.location_id,
            "name": created.location_name,
            "message": f"Location '{name}' registered",
        }
    except Exception as e:
        raise ToolError(f"Failed to add location: {e}")


@mcp.tool
def remove_location(location_id: str) -> dict:
    """Remove a location from the lab.

    Args:
        location_id: ID of the location to remove
    """
    client = _ctx.location
    try:
        result = client.delete_location(location_id)
        return {
            "location_id": location_id,
            "message": "Location removed",
        }
    except Exception as e:
        raise ToolError(f"Failed to remove location: {e}")


# ---------------------------------------------------------------------------
# Auto-load custom tools from config
# ---------------------------------------------------------------------------

try:
    from maple.config import load_config, load_overseer_custom_tools
    _config = load_config()
    load_overseer_custom_tools(_config)
except Exception:
    pass  # Config not available

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = mcp.http_app()
