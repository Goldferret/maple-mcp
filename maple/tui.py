"""MAPLE TUI — Minimal chat interface for MAPLE agents."""

import json
import time
import uuid
from pathlib import Path

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Input, Markdown, Static


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class StatusBar(Static):
    """Top status bar showing connection state and agent type."""

    agent_name = reactive("operator")
    connected = reactive(False)

    def render(self) -> str:
        icon = "[green]●[/]" if self.connected else "[red]○[/]"
        return f" {icon} MAPLE [{self.agent_name}] "


class MessageBubble(Markdown):
    """A single message bubble that grows as tokens stream in."""

    DEFAULT_CSS = """
    MessageBubble {
        margin: 0;
        padding: 0 1;
    }
    """


class ToolCallIndicator(Static):
    """Inline indicator for tool calls."""

    DEFAULT_CSS = """
    ToolCallIndicator {
        color: $warning;
        margin: 1 1;
    }
    """


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


AGENT_PORTS = {
    "operator": 8202,
    "overseer": 8203,
}


class MapleChatApp(App):
    """MAPLE TUI chat application."""

    TITLE = "MAPLE"
    CSS = """
    #status { height: 1; dock: top; background: $panel; }
    #log { height: 1fr; padding: 0 1; }
    #prompt { dock: bottom; margin-bottom: 1; }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, agent: str = "operator", host: str = "localhost", session_id: str = None, **kwargs):
        super().__init__(**kwargs)
        self._agent = agent
        self._port = AGENT_PORTS.get(agent, 8202)
        self._host = host
        self._base_url = f"http://{host}:{self._port}"
        self._session_id = session_id or str(uuid.uuid4())
        self._client: httpx.AsyncClient | None = None

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status")
        yield VerticalScroll(id="log")
        yield Input(placeholder=f"[{self._agent}] > ", id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        status = self.query_one("#status", StatusBar)
        status.agent_name = self._agent
        self._client = httpx.AsyncClient(timeout=None)

        # Ping agent to check connection
        try:
            resp = await self._client.get(f"{self._base_url}/ping")
            status.connected = resp.status_code == 200
        except httpx.HTTPError:
            status.connected = False

        # Load previous conversation if resuming
        self._load_session_history()

        self.query_one(Input).focus()

    def _load_session_history(self):
        """Load and display previous messages from session files."""
        from pathlib import Path
        import json as _json

        session_dir = (
            Path.home() / ".maple" / "sessions" / self._agent
            / f"session_{self._session_id}" / "agents" / "agent_default" / "messages"
        )
        if not session_dir.exists():
            return

        log = self.query_one("#log", VerticalScroll)
        msg_files = sorted(session_dir.glob("message_*.json"), key=lambda f: int(f.stem.split("_")[1]))

        for msg_file in msg_files:
            try:
                data = _json.loads(msg_file.read_text())
                role = data["message"]["role"]
                content = data["message"].get("content", [])

                if role == "user":
                    text = next((c.get("text", "") for c in content if "text" in c), "")
                    if text:
                        bubble = MessageBubble(f"**You >** {text}")
                        log.mount(bubble)
                elif role == "assistant":
                    # Collect text parts and tool calls
                    texts = [c["text"] for c in content if "text" in c]
                    tools = [c["toolUse"]["name"] for c in content if "toolUse" in c]
                    for tool in tools:
                        indicator = ToolCallIndicator(f"⚡ {tool}")
                        log.mount(indicator)
                    if texts:
                        bubble = MessageBubble("\n".join(texts))
                        log.mount(bubble)
            except Exception:
                continue

        log.scroll_end(animate=False)

    async def on_unmount(self) -> None:
        if self._client:
            await self._client.aclose()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()

        # Exit if experiment ended and user presses Enter
        if getattr(self, "_exit_on_next_submit", False):
            self.exit()
            return

        if not text:
            return

        input_widget = self.query_one(Input)
        input_widget.value = ""
        input_widget.disabled = True

        # Show user message
        log = self.query_one("#log", VerticalScroll)
        user_bubble = MessageBubble(f"**You >** {text}")
        log.mount(user_bubble)
        log.scroll_end(animate=False)

        self.stream_turn(text)

    @work(exclusive=True)
    async def stream_turn(self, message: str) -> None:
        log = self.query_one("#log", VerticalScroll)
        input_widget = self.query_one(Input)
        status = self.query_one("#status", StatusBar)

        # Mount agent response bubble
        bubble = MessageBubble("")
        await log.mount(bubble)
        log.scroll_end(animate=False)

        buffer = ""
        last_flush = time.monotonic()
        FLUSH_INTERVAL = 0.05
        experiment_ended = False
        _pending_tool = None

        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/stream",
                json={"session_id": self._session_id, "message": message},
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    try:
                        event = json.loads(line.removeprefix("data:").strip())
                    except json.JSONDecodeError:
                        continue

                    if "data" in event:
                        # Render pending tool call if LLM is now reasoning again
                        if _pending_tool:
                            if buffer:
                                bubble.update(buffer)
                                buffer = ""
                            tool_input = json.dumps(_pending_tool.get("input", {}), indent=None)
                            indicator = ToolCallIndicator(
                                f"⚡ {_pending_tool['name']} {tool_input}"
                            )
                            await log.mount(indicator)
                            log.scroll_end(animate=False)
                            bubble = MessageBubble("")
                            await log.mount(bubble)
                            _pending_tool = None

                        buffer += event["data"]
                        now = time.monotonic()
                        if now - last_flush >= FLUSH_INTERVAL:
                            bubble.update(buffer)
                            log.scroll_end(animate=False)
                            last_flush = now

                    elif "current_tool_use" in event:
                        new_tool = event["current_tool_use"]
                        new_id = new_tool.get("toolUseId", "")
                        pending_id = _pending_tool.get("toolUseId", "") if _pending_tool else ""

                        if _pending_tool and new_id != pending_id:
                            # Different tool — render the previous one first
                            if buffer:
                                bubble.update(buffer)
                                buffer = ""
                            tool_input = json.dumps(_pending_tool.get("input", {}), indent=None)
                            indicator = ToolCallIndicator(
                                f"⚡ {_pending_tool['name']} {tool_input}"
                            )
                            await log.mount(indicator)
                            log.scroll_end(animate=False)
                            bubble = MessageBubble("")
                            await log.mount(bubble)

                        # Track (or update) the current tool call
                        _pending_tool = new_tool

                    elif "result" in event:
                        # Render any pending tool call before showing result
                        if _pending_tool:
                            if buffer:
                                bubble.update(buffer)
                                buffer = ""
                            tool_input = json.dumps(_pending_tool.get("input", {}), indent=None)
                            indicator = ToolCallIndicator(
                                f"⚡ {_pending_tool['name']} {tool_input}"
                            )
                            await log.mount(indicator)
                            log.scroll_end(animate=False)
                            bubble = MessageBubble("")
                            await log.mount(bubble)
                            _pending_tool = None

                        # Final flush
                        if buffer:
                            bubble.update(buffer)
                        result = event["result"]
                        experiment_ended = result.get("experiment_ended", False)
                        break

            status.connected = True

        except httpx.HTTPError as exc:
            status.connected = False
            bubble.update(f"**Connection error:** {exc}")

        finally:
            if experiment_ended:
                log = self.query_one("#log", VerticalScroll)
                await log.mount(
                    Static("[bold green]✓ Experiment complete. Press Enter to exit.[/]")
                )
                log.scroll_end(animate=False)
                input_widget.disabled = False
                input_widget.placeholder = "Press Enter to exit..."
                input_widget.focus()
                self._exit_on_next_submit = True
            else:
                input_widget.disabled = False
                input_widget.focus()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_chat(agent: str = "operator", host: str = "localhost", session_id: str = None):
    """Launch the MAPLE chat TUI."""
    import uuid
    app = MapleChatApp(agent=agent, host=host, session_id=session_id or str(uuid.uuid4()))
    app.run()
