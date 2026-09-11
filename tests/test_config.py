"""Tests for MAPLE config system."""

import pytest
from pathlib import Path

from maple.config import (
    load_config,
    import_string,
    load_vision_backend,
    load_vision_views,
    resolve_view,
    MapleConfig,
)
from fastmcp.exceptions import ToolError


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


# ---------------------------------------------------------------------------
# Vision view registry
# ---------------------------------------------------------------------------


def _multi_view_config():
    """A config with two views for routing/resolution tests."""
    return MapleConfig(**{
        "operator": {
            "vision": {
                "views": {
                    "block_table": {
                        "backend": "maple.vision:StubBackend",
                        "capture": {"node": "DOFBOT_Pro_1", "action": "capture_camera_image"},
                        "covers": ["DOFBOT_Pro_1"],
                    },
                    "ot2_deck": {
                        "backend": "maple.vision:SecondStubBackend",
                        "capture": {"node": "OT-2", "action": "capture_camera_image"},
                        "pre_capture_action": "home_robot",
                        "covers": ["OT-2"],
                    },
                },
                "default_view": "block_table",
            }
        }
    })


class TestLoadVisionViews:
    def test_builds_registry_with_instantiated_backends(self):
        from maple.vision import StubBackend, SecondStubBackend
        state = load_vision_views(_multi_view_config())
        reg = state["registry"]
        assert set(reg.keys()) == {"block_table", "ot2_deck"}
        assert isinstance(reg["block_table"]["backend"], StubBackend)
        assert isinstance(reg["ot2_deck"]["backend"], SecondStubBackend)
        assert state["default_view"] == "block_table"

    def test_capture_and_pre_capture_parsed(self):
        state = load_vision_views(_multi_view_config())
        reg = state["registry"]
        assert reg["ot2_deck"]["capture"] == {"node": "OT-2", "action": "capture_camera_image"}
        assert reg["ot2_deck"]["pre_capture_action"] == "home_robot"
        assert reg["block_table"]["pre_capture_action"] is None
        assert reg["block_table"]["covers"] == ["DOFBOT_Pro_1"]

    def test_default_view_falls_back_to_first_when_unset(self):
        cfg = MapleConfig(**{
            "operator": {"vision": {"views": {
                "only": {"backend": "maple.vision:StubBackend",
                         "capture": {"node": "N", "action": "capture_camera_image"}},
            }}}
        })
        state = load_vision_views(cfg)
        assert state["default_view"] == "only"

    def test_backward_compat_synthesizes_default_from_vision_backend(self):
        from maple.vision import StubBackend
        cfg = MapleConfig(operator={"vision_backend": "maple.vision:StubBackend"})
        state = load_vision_views(cfg)
        assert list(state["registry"].keys()) == ["default"]
        assert state["default_view"] == "default"
        assert isinstance(state["registry"]["default"]["backend"], StubBackend)

    def test_backward_compat_empty_config_uses_stub(self):
        from maple.vision import StubBackend
        state = load_vision_views(MapleConfig())
        assert isinstance(state["registry"]["default"]["backend"], StubBackend)

    def test_invalid_backend_path_raises(self):
        cfg = MapleConfig(**{
            "operator": {"vision": {"views": {
                "bad": {"backend": "nonexistent_module:Nope",
                        "capture": {"node": "N", "action": "capture_camera_image"}},
            }}}
        })
        with pytest.raises(ImportError):
            load_vision_views(cfg)


class TestResolveView:
    def test_view_only(self):
        state = load_vision_views(_multi_view_config())
        assert resolve_view("ot2_deck", None, state) == "ot2_deck"

    def test_node_only_resolves_via_covers(self):
        state = load_vision_views(_multi_view_config())
        assert resolve_view(None, "OT-2", state) == "ot2_deck"
        assert resolve_view(None, "DOFBOT_Pro_1", state) == "block_table"

    def test_node_wins_over_view_when_both_given(self):
        state = load_vision_views(_multi_view_config())
        # view says block_table, node says OT-2 -> node wins
        assert resolve_view("block_table", "OT-2", state) == "ot2_deck"

    def test_neither_uses_default(self):
        state = load_vision_views(_multi_view_config())
        assert resolve_view(None, None, state) == "block_table"

    def test_unknown_view_raises(self):
        state = load_vision_views(_multi_view_config())
        with pytest.raises(ToolError, match="Unknown view"):
            resolve_view("nope", None, state)

    def test_uncovered_node_raises(self):
        state = load_vision_views(_multi_view_config())
        with pytest.raises(ToolError, match="No view covers node"):
            resolve_view(None, "UnknownNode", state)

    def test_ambiguous_node_coverage_raises(self):
        cfg = MapleConfig(**{
            "operator": {"vision": {"views": {
                "a": {"backend": "maple.vision:StubBackend",
                      "capture": {"node": "shared", "action": "capture_camera_image"},
                      "covers": ["shared"]},
                "b": {"backend": "maple.vision:SecondStubBackend",
                      "capture": {"node": "shared", "action": "capture_camera_image"},
                      "covers": ["shared"]},
            }, "default_view": "a"}}
        })
        state = load_vision_views(cfg)
        with pytest.raises(ToolError, match="covered by multiple views"):
            resolve_view(None, "shared", state)

    def test_correct_backend_selected_per_view(self):
        """The resolved view's backend is the right class — routing sanity."""
        from maple.vision import StubBackend, SecondStubBackend
        state = load_vision_views(_multi_view_config())

        v1 = resolve_view("block_table", None, state)
        v2 = resolve_view("ot2_deck", None, state)
        assert isinstance(state["registry"][v1]["backend"], StubBackend)
        assert isinstance(state["registry"][v2]["backend"], SecondStubBackend)
        # And their outputs differ (proves distinct dispatch)
        assert state["registry"][v2]["backend"].verify_goal({}, {})["marker"] == "second"
