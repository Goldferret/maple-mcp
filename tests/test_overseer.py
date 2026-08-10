"""Tests for MAPLE Overseer MCP server tools."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def patch_overseer_ctx(mock_overseer_ctx, monkeypatch):
    """Patch _ctx on the already-imported module."""
    # Patch all the MADSci client constructors so they don't need URLs
    with patch("madsci.client.WorkcellClient", return_value=mock_overseer_ctx.workcell), \
         patch("madsci.client.ExperimentClient", return_value=mock_overseer_ctx.experiment), \
         patch("madsci.client.EventClient", return_value=mock_overseer_ctx.event), \
         patch("madsci.client.DataClient", return_value=mock_overseer_ctx.data), \
         patch("madsci.client.ResourceClient", return_value=mock_overseer_ctx.resource), \
         patch("madsci.client.LocationClient", return_value=mock_overseer_ctx.location):
        import importlib
        import maple.overseer.server
        importlib.reload(maple.overseer.server)
        yield mock_overseer_ctx


class TestGetLabState:
    def test_returns_nodes(self, patch_overseer_ctx):
        from maple.overseer.server import get_lab_state

        result = get_lab_state()
        assert result["total_nodes"] == 1
        assert "Robot_1" in result["nodes"]
        assert result["nodes"]["Robot_1"]["actions"] == ["pick", "place"]

    def test_empty_lab(self, patch_overseer_ctx):
        patch_overseer_ctx.workcell.get_nodes.return_value = {}
        from maple.overseer.server import get_lab_state

        result = get_lab_state()
        assert result["total_nodes"] == 0
        assert result["nodes"] == {}


class TestListExperiments:
    def test_returns_experiments(self, patch_overseer_ctx):
        from maple.overseer.server import list_experiments

        result = list_experiments(limit=10)
        assert result["count"] == 1
        assert result["experiments"][0]["experiment_id"] == "exp-001"
        assert result["experiments"][0]["status"] == "completed"

    def test_respects_limit(self, patch_overseer_ctx):
        from maple.overseer.server import list_experiments

        result = list_experiments(limit=0)
        assert result["count"] == 0


class TestGetExperimentStatus:
    def test_returns_status(self, patch_overseer_ctx):
        from maple.overseer.server import get_experiment_status

        result = get_experiment_status("exp-001")
        assert result["experiment_id"] == "exp-001"
        assert result["name"] == "Test Exp"
        assert result["status"] == "completed"


class TestGetResources:
    def test_returns_resources(self, patch_overseer_ctx):
        from maple.overseer.server import get_resources

        result = get_resources()
        assert result["count"] == 1
        assert result["resources"][0]["name"] == "Test Block"
        assert result["resources"][0]["class"] == "test_item"


class TestAddResource:
    def test_adds_resource(self, patch_overseer_ctx):
        from maple.overseer.server import add_resource

        result = add_resource(name="New Block", resource_class="block", base_type="resource")
        assert result["name"] == "Test Block"  # Returns mock
        assert "resource_id" in result


class TestRemoveResource:
    def test_removes_resource(self, patch_overseer_ctx):
        from maple.overseer.server import remove_resource

        result = remove_resource("res-001")
        assert result["message"] == "Resource removed"


class TestGetLocations:
    def test_returns_locations(self, patch_overseer_ctx):
        from maple.overseer.server import get_locations

        result = get_locations()
        assert result["count"] == 1
        assert result["locations"][0]["name"] == "Test Zone"


class TestAddLocation:
    def test_adds_location(self, patch_overseer_ctx):
        from maple.overseer.server import add_location

        result = add_location(name="New Zone")
        assert result["name"] == "Test Zone"  # Returns mock
        assert "location_id" in result


class TestRemoveLocation:
    def test_removes_location(self, patch_overseer_ctx):
        from maple.overseer.server import remove_location

        result = remove_location("loc-001")
        assert result["message"] == "Location removed"
