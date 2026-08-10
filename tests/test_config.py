"""Tests for MAPLE config system."""

import pytest
from pathlib import Path

from maple.config import load_config, import_string, load_vision_backend, MapleConfig


class TestLoadConfig:
    def test_default_when_no_file(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.experiment.name == "Untitled Experiment"
        assert cfg.operator.vision_backend == ""
        assert cfg.operator.post_action_hooks == []

    def test_loads_valid_config(self, tmp_config):
        cfg = load_config(tmp_config)
        assert cfg.experiment.name == "Test Experiment"
        assert cfg.experiment.objective == "Sort blocks by color"
        assert len(cfg.experiment.constraints) == 1
        assert len(cfg.operator.post_action_hooks) == 1
        assert cfg.operator.post_action_hooks[0].node == "TestNode"
        assert cfg.operator.post_action_hooks[0].action == "test_action"
        assert cfg.operator.post_action_hooks[0].args == {"key": "value"}

    def test_post_action_hooks_structured_correctly(self, tmp_config):
        cfg = load_config(tmp_config)
        hook = cfg.operator.post_action_hooks[0]
        # Verify hook can be destructured for dispatch
        node, action, args = hook.node, hook.action, hook.args
        assert node == "TestNode"
        assert action == "test_action"
        assert isinstance(args, dict)

    def test_invalid_config_raises(self, tmp_path):
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("operator:\n  post_action_hooks: 'not a list'")
        with pytest.raises(Exception):
            load_config(bad_config)

    def test_empty_file_returns_defaults(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        cfg = load_config(empty)
        assert cfg == MapleConfig()


class TestImportString:
    def test_colon_format(self):
        cls = import_string("maple.vision:StubBackend")
        assert cls.__name__ == "StubBackend"

    def test_dot_format(self):
        cls = import_string("maple.vision.StubBackend")
        assert cls.__name__ == "StubBackend"

    def test_invalid_module_raises(self):
        with pytest.raises(ImportError, match="Cannot import module"):
            import_string("nonexistent_module:SomeClass")

    def test_invalid_attribute_raises(self):
        with pytest.raises(ImportError, match="has no attribute"):
            import_string("maple.vision:NonexistentClass")

    def test_invalid_format_raises(self):
        with pytest.raises(ImportError, match="Invalid import path"):
            import_string("nocolonordot")


class TestLoadVisionBackend:
    def test_empty_path_returns_stub(self):
        cfg = MapleConfig()
        backend = load_vision_backend(cfg)
        from maple.vision import StubBackend
        assert isinstance(backend, StubBackend)

    def test_valid_path_loads_class(self):
        cfg = MapleConfig(operator={"vision_backend": "maple.vision:StubBackend"})
        backend = load_vision_backend(cfg)
        from maple.vision import StubBackend
        assert isinstance(backend, StubBackend)


class TestLoadCustomTools:
    def test_empty_list_no_error(self):
        from maple.config import load_custom_tools
        cfg = MapleConfig()
        load_custom_tools(cfg)

    def test_invalid_tool_path_raises(self):
        from unittest.mock import MagicMock, patch
        from maple.config import load_custom_tools
        with patch.dict("sys.modules", {"maple.operator.server": MagicMock()}):
            cfg = MapleConfig(operator={"custom_tools": ["nonexistent_module:fake_func"]})
            with pytest.raises(ImportError, match="Failed to load custom tool"):
                load_custom_tools(cfg)

    def test_valid_tool_loads_and_registers(self):
        from unittest.mock import MagicMock, patch
        from maple.config import load_custom_tools

        mock_mcp = MagicMock()
        mock_server = MagicMock()
        mock_server.mcp = mock_mcp

        with patch.dict("sys.modules", {"maple.operator.server": mock_server}):
            # Use a real importable function as the custom tool
            cfg = MapleConfig(operator={"custom_tools": ["os.path:exists"]})
            load_custom_tools(cfg)
            # Verify mcp.tool was called with the function
            mock_mcp.tool.assert_called_once()

    def test_overseer_empty_list_no_error(self):
        from maple.config import load_overseer_custom_tools
        cfg = MapleConfig()
        load_overseer_custom_tools(cfg)

    def test_overseer_invalid_tool_path_raises(self):
        from unittest.mock import MagicMock, patch
        from maple.config import load_overseer_custom_tools
        with patch.dict("sys.modules", {"maple.overseer.server": MagicMock()}):
            cfg = MapleConfig(overseer={"custom_tools": ["nonexistent:func"]})
            with pytest.raises(ImportError, match="Failed to load custom tool"):
                load_overseer_custom_tools(cfg)

    def test_overseer_valid_tool_loads_and_registers(self):
        from unittest.mock import MagicMock, patch
        from maple.config import load_overseer_custom_tools

        mock_mcp = MagicMock()
        mock_server = MagicMock()
        mock_server.mcp = mock_mcp

        with patch.dict("sys.modules", {"maple.overseer.server": mock_server}):
            cfg = MapleConfig(overseer={"custom_tools": ["os.path:exists"]})
            load_overseer_custom_tools(cfg)
            mock_mcp.tool.assert_called_once()


class TestAgentExtensions:
    """Test that agent factories accept extra_hooks."""

    def test_operator_agent_accepts_extra_hooks(self):
        """Verify create_operator_agent signature accepts extra_hooks."""
        import inspect
        from maple.operator.agent import create_operator_agent
        sig = inspect.signature(create_operator_agent)
        assert "extra_hooks" in sig.parameters
        param = sig.parameters["extra_hooks"]
        assert param.default is None

    def test_overseer_agent_accepts_extra_hooks(self):
        """Verify create_overseer_agent signature accepts extra_hooks."""
        import inspect
        from maple.overseer.agent import create_overseer_agent
        sig = inspect.signature(create_overseer_agent)
        assert "extra_hooks" in sig.parameters
        param = sig.parameters["extra_hooks"]
        assert param.default is None
