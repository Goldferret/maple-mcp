"""Shared helpers for integration tests — deterministic service startup/teardown."""

import subprocess
import time


def _port_free(port: int) -> bool:
    """True if nothing is listening on localhost:port. Portable (no lsof)."""
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("localhost", port)) != 0


def wait_for_port_free(port: int, timeout: int = 20) -> None:
    """Block until localhost:port has no listener (or timeout)."""
    for _ in range(timeout):
        if _port_free(port):
            return
        time.sleep(1)


def wait_for_node_ready(url: str = "http://localhost:2000/status", timeout: int = 30) -> bool:
    """Block until the stub node's /status responds. Returns True if ready."""
    import httpx
    for _ in range(timeout):
        try:
            if httpx.get(url, timeout=3).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def clean_slate(down: bool = True, port: int = 2000) -> None:
    """Stop MAPLE services and wait for the node port to be released."""
    if down:
        subprocess.run(["maple", "down"], capture_output=True)
    wait_for_port_free(port)
