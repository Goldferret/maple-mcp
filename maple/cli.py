"""MAPLE CLI — Command-line interface for serving and chatting with agents."""

import typer
from rich.console import Console

app = typer.Typer(
    name="maple",
    help="MAPLE — Model-Agnostic Platform for Laboratory Experiments",
    no_args_is_help=True,
)
serve_app = typer.Typer(help="Start MAPLE services in the background.")
chat_app = typer.Typer(help="Chat with a MAPLE agent via TUI.")
app.add_typer(serve_app, name="serve")
app.add_typer(chat_app, name="chat")

console = Console()


# ---------------------------------------------------------------------------
# maple serve <subcommand>
# ---------------------------------------------------------------------------


@serve_app.command("all")
def serve_all(
    dev: bool = typer.Option(False, "--dev", help="Enable auto-reload on file changes"),
    config: str = typer.Option("maple.config.yaml", help="Path to config file"),
):
    """Start all MCP servers and agents (Operator + Overseer)."""
    _do_serve(agent=None, stub=False, dev=dev, config=config)


@serve_app.command("operator")
def serve_operator(
    dev: bool = typer.Option(False, "--dev", help="Enable auto-reload on file changes"),
    config: str = typer.Option("maple.config.yaml", help="Path to config file"),
):
    """Start Operator MCP server and agent."""
    _do_serve(agent="operator", stub=False, dev=dev, config=config)


@serve_app.command("overseer")
def serve_overseer(
    dev: bool = typer.Option(False, "--dev", help="Enable auto-reload on file changes"),
    config: str = typer.Option("maple.config.yaml", help="Path to config file"),
):
    """Start Overseer MCP server and agent."""
    _do_serve(agent="overseer", stub=False, dev=dev, config=config)


@serve_app.command("stub")
def serve_stub(
    dev: bool = typer.Option(False, "--dev", help="Enable auto-reload on file changes"),
    config: str = typer.Option("maple.config.yaml", help="Path to config file"),
):
    """Start full demo stack (stub node + Operator MCP + mock agent)."""
    _do_serve(agent=None, stub=True, dev=dev, config=config)


@serve_app.command("mock")
def serve_mock(
    dev: bool = typer.Option(False, "--dev", help="Enable auto-reload on file changes"),
):
    """Start mock agent only (for demo/testing)."""
    _do_serve(agent="mock", stub=False, dev=dev, config="maple.config.yaml")


def _do_serve(agent: str = None, stub: bool = False, dev: bool = False, config: str = "maple.config.yaml"):
    """Shared serve logic."""
    from dotenv import load_dotenv
    load_dotenv()

    from maple.services import start_services

    started = start_services(agent=agent, config=config, stub=stub, dev=dev)

    if not started:
        console.print("[red]No services to start.[/red]")
        raise typer.Exit(1)

    console.print()
    for name, port, pid in started:
        console.print(f"  [green]✓[/green] {name:20s} :{port}  (pid {pid})")
    console.print()
    if dev:
        console.print("[yellow]Dev mode: services will auto-reload on file changes[/yellow]")
    console.print("[dim]Logs: ~/.maple/logs/[/dim]")
    console.print("[dim]Stop: maple down[/dim]")


# ---------------------------------------------------------------------------
# maple chat <subcommand>
# ---------------------------------------------------------------------------


@chat_app.command("operator")
def chat_operator(
    host: str = typer.Option("localhost", help="Agent service host"),
    resume: bool = typer.Option(False, "--resume", help="Resume most recent operator session"),
):
    """Chat with the Operator agent."""
    from dotenv import load_dotenv
    load_dotenv()
    from maple.tui import run_chat
    session_id = _get_session_id("operator", resume)
    run_chat(agent="operator", host=host, session_id=session_id)


@chat_app.command("overseer")
def chat_overseer(
    host: str = typer.Option("localhost", help="Agent service host"),
    resume: bool = typer.Option(False, "--resume", help="Resume most recent overseer session"),
):
    """Chat with the Overseer agent."""
    from dotenv import load_dotenv
    load_dotenv()
    from maple.tui import run_chat
    session_id = _get_session_id("overseer", resume)
    run_chat(agent="overseer", host=host, session_id=session_id)


def _get_session_id(agent: str, resume: bool) -> str:
    """Get or create a session ID for the chat.
    
    If resume=True, finds the most recent session file for the agent.
    Otherwise generates a new UUID.
    """
    import uuid
    from pathlib import Path

    sessions_dir = Path.home() / ".maple" / "sessions" / agent

    if resume and sessions_dir.exists():
        json_files = list(sessions_dir.glob("*.json"))
        if json_files:
            latest = max(json_files, key=lambda f: f.stat().st_mtime)
            session_id = latest.stem
            console.print(f"[dim]Resuming session: {session_id[:8]}...[/dim]")
            return session_id
        else:
            console.print("[dim]No previous session found. Starting new.[/dim]")

    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# maple down
# ---------------------------------------------------------------------------


@app.command()
def down():
    """Stop all running MAPLE services."""
    from maple.services import stop_services

    stopped = stop_services()

    if stopped:
        console.print(f"[green]✓[/green] Stopped {len(stopped)} service(s): {', '.join(stopped)}")
    else:
        console.print("[dim]No running services found.[/dim]")


# ---------------------------------------------------------------------------
# maple logs
# ---------------------------------------------------------------------------


@app.command()
def logs():
    """View live service logs in a TUI (tail mode)."""
    from maple.attach import run_attach
    run_attach()


# ---------------------------------------------------------------------------
# maple status
# ---------------------------------------------------------------------------


@app.command()
def status():
    """Check status of running MAPLE services."""
    import httpx
    from maple.services import get_status

    running = get_status()

    if not running:
        console.print("[dim]No MAPLE services registered. Run 'maple serve' first.[/dim]")
        return

    console.print()
    for name, pid, is_running in running:
        if is_running:
            console.print(f"  [green]✓[/green] {name:20s} (pid {pid})")
        else:
            console.print(f"  [red]✗[/red] {name:20s} (pid {pid} — dead)")
    console.print()

    # Also try pinging known endpoints
    endpoints = {
        "operator-mcp": "http://localhost:8102/ping",
        "overseer-mcp": "http://localhost:8103/ping",
        "operator-agent": "http://localhost:8202/ping",
        "overseer-agent": "http://localhost:8203/ping",
    }

    for name, url in endpoints.items():
        try:
            resp = httpx.get(url, timeout=2)
            if resp.status_code == 200:
                console.print(f"  [green]●[/green] {name} responding")
        except Exception:
            pass


if __name__ == "__main__":
    app()
