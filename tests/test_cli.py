"""Tests for MAPLE CLI."""

import re
import pytest
from typer.testing import CliRunner

from maple.cli import app

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


class TestHelp:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"], color=False)
        assert result.exit_code == 0
        assert "MAPLE" in result.output
        assert "serve" in result.output
        assert "chat" in result.output
        assert "down" in result.output
        assert "logs" in result.output
        assert "status" in result.output

    def test_serve_help(self):
        result = runner.invoke(app, ["serve", "--help"], color=False)
        assert result.exit_code == 0
        assert "all" in result.output
        assert "operator" in result.output
        assert "overseer" in result.output
        assert "stub" in result.output
        assert "mock" in result.output

    def test_serve_all_help(self):
        result = runner.invoke(app, ["serve", "all", "--help"], color=False)
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "--dev" in output
        assert "--config" in output

    def test_chat_help(self):
        result = runner.invoke(app, ["chat", "--help"], color=False)
        assert result.exit_code == 0
        assert "operator" in result.output
        assert "overseer" in result.output

    def test_down_help(self):
        result = runner.invoke(app, ["down", "--help"], color=False)
        assert result.exit_code == 0

    def test_logs_help(self):
        result = runner.invoke(app, ["logs", "--help"], color=False)
        assert result.exit_code == 0

    def test_status_help(self):
        result = runner.invoke(app, ["status", "--help"], color=False)
        assert result.exit_code == 0


class TestDown:
    def test_down_no_services(self):
        result = runner.invoke(app, ["down"], color=False)
        assert result.exit_code == 0
        assert "No running" in result.output


class TestDevMode:
    def test_dev_adds_reload_flag(self):
        from maple.services import get_services
        services = get_services(agent="operator", dev=True)
        for name, (cmd, port) in services.items():
            assert "--reload" in cmd, f"{name} missing --reload"

    def test_no_dev_no_reload_flag(self):
        from maple.services import get_services
        services = get_services(agent="operator", dev=False)
        for name, (cmd, port) in services.items():
            assert "--reload" not in cmd, f"{name} should not have --reload"
