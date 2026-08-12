"""MAPLE Attach TUI — tail live logs from running services."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.widgets import RichLog, Static

LOG_DIR = Path.home() / ".maple" / "logs"


class ServiceLogPane(Static):
    """A single service's log pane with status header and scrollable log."""

    DEFAULT_CSS = """
    ServiceLogPane {
        border: solid gray;
        height: 1fr;
    }
    ServiceLogPane Static {
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    ServiceLogPane RichLog {
        height: 1fr;
    }
    """

    def __init__(self, service_name: str, **kwargs):
        super().__init__(**kwargs)
        self.service_name = service_name

    def compose(self) -> ComposeResult:
        yield Static(f"[green]● {self.service_name}[/]", id=f"status-{self.service_name}")
        yield RichLog(id=f"log-{self.service_name}", highlight=True, markup=False, wrap=True)


class MapleAttachApp(App):
    """TUI for tailing MAPLE service logs in split panes."""

    TITLE = "MAPLE — Service Logs"
    CSS = """
    Grid { grid-size: 2; height: 1fr; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("ctrl+c", "quit", "Quit")]

    def __init__(self, services: list[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._services = services or self._discover_services()

    def _discover_services(self) -> list[str]:
        """Find services that are currently running (have active PIDs)."""
        pid_dir = Path.home() / ".maple" / "pids"
        if not pid_dir.exists():
            return []
        return [f.stem for f in sorted(pid_dir.glob("*.pid"))]

    def compose(self) -> ComposeResult:
        if not self._services:
            yield Static("[dim]No services found. Run 'maple serve' first.[/]")
            return
        with Grid():
            for name in self._services:
                yield ServiceLogPane(name, id=f"pane-{name}")

    async def on_mount(self) -> None:
        self._offsets: dict[str, int] = {}
        for name in self._services:
            path = LOG_DIR / f"{name}.log"
            path.touch(exist_ok=True)
            self._offsets[name] = path.stat().st_size
            self._show_tail(path, self.query_one(f"#log-{name}", RichLog), lines=20)
        # Single watcher on the logs directory
        self.run_worker(self._watch_logs(), exclusive=True)

    async def _watch_logs(self) -> None:
        """Poll log files for changes every 500ms. Simple and reliable."""
        import asyncio

        try:
            while True:
                await asyncio.sleep(0.5)
                for name in self._services:
                    path = LOG_DIR / f"{name}.log"
                    if not path.exists():
                        continue
                    current_size = path.stat().st_size
                    if current_size != self._offsets.get(name, 0):
                        log = self.query_one(f"#log-{name}", RichLog)
                        self._offsets[name] = self._flush_new_lines(
                            path, log, self._offsets[name]
                        )
        except Exception:
            pass

    def _show_tail(self, path: Path, log: RichLog, lines: int = 20):
        """Show the last N lines of the file on attach."""
        try:
            content = path.read_text(errors="replace")
            recent = content.splitlines()[-lines:]
            for line in recent:
                log.write(line)
        except Exception:
            pass

    def _flush_new_lines(self, path: Path, log: RichLog, offset: int) -> int:
        """Read new content since last offset and write to log."""
        try:
            size = path.stat().st_size
            if size < offset:
                # File was truncated/rotated
                offset = 0
            with open(path, "r", errors="replace") as f:
                f.seek(offset)
                new_data = f.read()
                new_offset = f.tell()
            for line in new_data.splitlines():
                log.write(line)
            return new_offset
        except Exception:
            return offset


def run_attach(services: list[str] = None):
    """Launch the attach TUI."""
    app = MapleAttachApp(services=services)
    app.run()
