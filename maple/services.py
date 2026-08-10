"""MAPLE Service Manager — daemon process management for MCP servers and agents."""

import os
import signal
import subprocess
import sys
from pathlib import Path

MAPLE_DIR = Path.home() / ".maple"
PID_DIR = MAPLE_DIR / "pids"
LOG_DIR = MAPLE_DIR / "logs"


def ensure_dirs():
    """Create MAPLE directories if they don't exist."""
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------

def get_services(agent: str = None, config: str = "maple.config.yaml", stub: bool = False, dev: bool = False) -> dict:
    """Get service definitions based on what to start.
    
    Args:
        agent: 'operator', 'overseer', 'mock', or None for all
        config: Path to config file
        stub: If True, start the full stub demo stack (stub node + operator MCP + mock agent)
        dev: If True, add --reload flag to uvicorn for auto-restart on file changes
        
    Returns:
        Dict of service_name -> (command, port)
    """
    python = sys.executable
    services = {}
    reload_flag = ["--reload"] if dev else []

    if stub:
        # Full demo stack — stub node + operator MCP + mock agent
        stub_script = _find_stub_script()
        if stub_script:
            services["stub-node"] = (
                [python, str(stub_script)],
                2000,
            )
        services["operator-mcp"] = (
            [python, "-m", "uvicorn", "maple.operator.server:app", "--host", "0.0.0.0", "--port", "8102"] + reload_flag,
            8102,
        )
        services["mock-agent"] = (
            [python, "-m", "uvicorn", "mock_agent:app", "--host", "0.0.0.0", "--port", "8202"] + reload_flag,
            8202,
        )
        return services

    if agent in (None, "operator"):
        services["operator-mcp"] = (
            [python, "-m", "uvicorn", "maple.operator.server:app", "--host", "0.0.0.0", "--port", "8102"] + reload_flag,
            8102,
        )
        services["operator-agent"] = (
            [python, "-m", "uvicorn", "maple.operator.agent:app", "--host", "0.0.0.0", "--port", "8202"] + reload_flag,
            8202,
        )

    if agent in (None, "overseer"):
        services["overseer-mcp"] = (
            [python, "-m", "uvicorn", "maple.overseer.server:app", "--host", "0.0.0.0", "--port", "8103"] + reload_flag,
            8103,
        )
        services["overseer-agent"] = (
            [python, "-m", "uvicorn", "maple.overseer.agent:app", "--host", "0.0.0.0", "--port", "8203"] + reload_flag,
            8203,
        )

    if agent == "mock":
        services["mock-agent"] = (
            [python, "-m", "uvicorn", "mock_agent:app", "--host", "0.0.0.0", "--port", "8202"] + reload_flag,
            8202,
        )

    return services


def _find_stub_script() -> Path | None:
    """Find stub_node.py in CWD or examples."""
    local = Path("stub_node.py")
    if local.exists():
        return local.resolve()
    bundled = Path(__file__).parent.parent / "examples" / "block_sorting" / "stub_node.py"
    if bundled.exists():
        return bundled.resolve()
    return None

    return services


# ---------------------------------------------------------------------------
# Start (daemonize)
# ---------------------------------------------------------------------------


def start_services(agent: str = None, config: str = "maple.config.yaml", stub: bool = False, dev: bool = False) -> list[tuple[str, int, int]]:
    """Start services as detached background processes.
    
    Returns:
        List of (service_name, port, pid) for started services.
    """
    ensure_dirs()
    services = get_services(agent, config, stub=stub, dev=dev)
    started = []

    for name, (cmd, port) in services.items():
        # Check if already running
        pid_file = PID_DIR / f"{name}.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            if _is_running(pid):
                started.append((name, port, pid))
                continue
            # Stale PID file
            pid_file.unlink()

        # Open log file
        log_file = LOG_DIR / f"{name}.log"
        log_handle = open(log_file, "a")

        # Start detached process
        proc = subprocess.Popen(
            cmd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )

        # Store PID
        pid_file.write_text(str(proc.pid))
        started.append((name, port, proc.pid))

    return started


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def stop_services() -> list[str]:
    """Stop all running MAPLE services.
    
    Returns:
        List of service names that were stopped.
    """
    ensure_dirs()
    stopped = []

    for pid_file in PID_DIR.glob("*.pid"):
        name = pid_file.stem
        pid = int(pid_file.read_text().strip())

        if _is_running(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                # Wait briefly for graceful shutdown
                for _ in range(20):  # 2 seconds
                    if not _is_running(pid):
                        break
                    import time
                    time.sleep(0.1)
                else:
                    # Force kill if still running
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stopped.append(name)

        pid_file.unlink()

    return stopped


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_status() -> list[tuple[str, int, bool]]:
    """Get status of all MAPLE services.
    
    Returns:
        List of (service_name, pid, is_running)
    """
    ensure_dirs()
    statuses = []

    for pid_file in PID_DIR.glob("*.pid"):
        name = pid_file.stem
        pid = int(pid_file.read_text().strip())
        running = _is_running(pid)
        statuses.append((name, pid, running))

    return statuses


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_running(pid: int) -> bool:
    """Check if a process with given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
