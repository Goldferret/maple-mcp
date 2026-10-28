# Agent → MCP client timeouts

## Summary

The Operator and Overseer agents talk to their MCP servers over an authenticated
HTTP client. That client **must** use the MCP SDK's recommended timeouts, or
long-running node actions will fail.

The agents build the client with:

```python
from mcp.shared._httpx_utils import create_mcp_http_client
auth_client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
```

`create_mcp_http_client` applies `httpx.Timeout(30, read=300)` — 30s for
connect/write/pool and **300s read**, because an MCP server may hold a response
stream open while a tool call runs.

## Why this matters (the bug it prevents)

A node action is a blocking call: the MCP server does not respond until the
underlying MADSci workflow finishes. Physical actions are slow — a DOFBOT
`pick_and_place` takes ~44s, OT-2 live actions are comparable.

If the agent passes a bare `httpx.AsyncClient()` to `streamable_http_client`
(instead of the SDK factory), it opts out of the recommended timeouts and gets
httpx's **5s default read timeout**. The 44s action then fails with
`httpx.ReadTimeout` inside the agent. The agent's turn dies mid-stream, never
emits its terminal event, and the TUI — which correctly waits with
`timeout=None` — hangs with the input disabled. The action itself still
completes on the robot; only the client stream breaks.

This was the regression between `madsci-mcp` (predecessor) and `maple-mcp`:
- `madsci-mcp` used the deprecated `streamablehttp_client(url)` with its
  `sse_read_timeout=300` default, so long actions survived.
- `maple-mcp` added an auth-wrapped custom `http_client` for multi-user auth,
  which silently discarded the SDK's recommended timeouts.

## Known limit

The read timeout is **300s (5 minutes)**. A single tool call that blocks longer
than that will still time out. If future work needs longer actions (e.g. slow
analytical readouts or incubations), pass an explicit timeout instead:

```python
import httpx
from mcp.shared._httpx_utils import create_mcp_http_client
auth_client = create_mcp_http_client(headers={...})
auth_client.timeout = httpx.Timeout(30, read=None)  # no read ceiling
```

Prefer a concrete large value over `None` if "hang forever on a dead server" is
a concern.

## Where

- `maple/operator/agent.py` — `create_operator_agent`
- `maple/overseer/agent.py` — `create_overseer_agent`
