"""Shared test fixtures for MAPLE."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary maple.config.yaml."""
    config_content = """
experiment:
  name: Test Experiment
  objective: Sort blocks by color
  constraints:
    - "Only pick one block at a time"

operator:
  vision_backend: ""
  post_action_hooks:
    - node: TestNode
      action: test_action
      args:
        key: value
"""
    config_file = tmp_path / "maple.config.yaml"
    config_file.write_text(config_content)
    return config_file


@pytest.fixture
def mock_overseer_ctx():
    """Mock OverseerContext with all clients stubbed."""
    ctx = MagicMock()

    # WorkcellClient
    ctx.workcell.get_nodes.return_value = {
        "Robot_1": {
            "info": {"node_id": "node-001", "actions": {"pick": {}, "place": {}}},
            "status": {"ready": True},
        }
    }

    # ExperimentClient
    mock_exp = MagicMock()
    mock_exp.experiment_id = "exp-001"
    mock_exp.experiment_design.experiment_name = "Test Exp"
    mock_exp.status.value = "completed"
    mock_exp.run_name = "run-1"
    ctx.experiment.get_experiments.return_value = [mock_exp]
    ctx.experiment.get_experiment.return_value = mock_exp
    ctx.experiment.cancel_experiment.return_value = mock_exp

    # ResourceClient
    mock_resource = MagicMock()
    mock_resource.resource_id = "res-001"
    mock_resource.resource_name = "Test Block"
    mock_resource.resource_class = "test_item"
    mock_resource.base_type = "resource"
    ctx.resource.query_resource.return_value = [mock_resource]
    ctx.resource.add_resource.return_value = mock_resource
    ctx.resource.remove_resource.return_value = mock_resource

    # LocationClient
    mock_location = MagicMock()
    mock_location.location_id = "loc-001"
    mock_location.location_name = "Test Zone"
    mock_location.resource_id = None
    ctx.location.get_locations.return_value = [mock_location]
    ctx.location.add_location.return_value = mock_location
    ctx.location.delete_location.return_value = True

    return ctx
