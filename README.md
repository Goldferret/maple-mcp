# MAPLE

**Model-Agnostic Platform for Laboratory Experiments**

MAPLE adds LLM agent capabilities to any [MADSci](https://github.com/AD-SDL/MADSci)-powered laboratory (v0.5.x) via MCP. Two agents — an **Operator** for experiment execution and an **Overseer** for lab monitoring — connect to your lab through configurable MCP servers.

## Architecture

![MAPLE Architecture](figures/architecture-1.png)

## Quick Start

```bash
pip install maple-mcp
cd your-experiment/
cp .env.example .env          # Configure MADSci URLs + model provider
maple serve stub              # Start demo (no LLM needed)
maple chat operator           # Open TUI — type anything
maple down                    # Stop all services
```

For a real LLM experiment:
```bash
maple serve operator          # Start with your configured model
maple chat operator           # Run — auto-sends your experiment brief (see below)
maple chat operator --resume  # Pick up where you left off
maple chat operator --test    # Collaborative session — drive the robot turn-by-turn
```

`maple chat operator` auto-sends the `experiment:` block from your
`maple.config.yaml` as the opening brief, so the agent starts running as soon as
the TUI opens (only on a fresh session, and only if an objective is set). Use
`--test` for an interactive session instead: the agent starts one experiment,
then waits and executes tasks you type ("home the DOFBOT", "what are its
constraints?") until you tell it to stop — useful for connectivity checks and
exploring a new robot.

See [`examples/block_sorting/`](examples/block_sorting/) for a complete walkthrough.

## Installation

```bash
pip install maple-mcp
```

Requires Python 3.10+ and a running [MADSci](https://github.com/AD-SDL/MADSci) lab (v0.5.x).

## CLI

```
maple serve {all, operator, overseer, stub, mock} [--dev]
maple chat {operator, overseer} [--resume] [--test]
maple down
maple status
maple logs
```

`--test` (operator only): collaborative mode — interactive, turn-by-turn control
instead of an autonomous run.

## Configuration

One `maple.config.yaml` per experiment:

```yaml
experiment:
  name: My Experiment
  objective: Sort samples by type
  constraints:
    - "Only handle one sample at a time"

operator:
  vision:
    views:
      workspace:
        backend: "vision:MyVision"
        capture:
          node: MyRobot
          action: capture_camera_image
        covers: [MyRobot]
    default_view: workspace
  custom_tools:
    - "my_tools:prepare_sample"
  post_action_hooks:
    - node: AnalysisNode
      action: verify_placement
```

Infrastructure goes in `.env` (IPs, API keys, model provider).

The `experiment:` block is the brief the Operator runs. `maple chat operator`
serializes it and sends it as the opening message on a fresh session, so the
agent begins autonomously when the TUI opens. Leave `objective` empty to disable
auto-send and type the brief yourself. `--test` ignores this block and sends the
built-in collaborative brief instead.

## Extending MAPLE

Config keys below are dotted paths into `maple.config.yaml` — e.g.
`operator.vision.views` is the `views:` key nested under `operator:` → `vision:`.

| Extension Point | Mechanism | Config Key |
|---|---|---|
| Vision detection/verification | Subclass `VisionBackend` (pure: `frames` in, results out) | — (referenced by a view's `backend`) |
| Vision views (scenes + routing) | Declare views mapping cameras/nodes to backends | `operator.vision.views` |
| MCP tools | `@mcp.tool` decorator | `operator.custom_tools` / `overseer.custom_tools` |
| Agent hooks | `extra_hooks` param on factory | Programmatic |
| Post-action hooks | YAML (no code) | `operator.post_action_hooks` |
| System prompts | Markdown file | `operator.prompt` / `overseer.prompt` |

## Supported Models

| Provider | Environment Variable |
|---|---|
| OpenAI | `MODEL_PROVIDER=openai` |
| Anthropic | `MODEL_PROVIDER=anthropic` |
| Ollama (local, free) | `MODEL_PROVIDER=ollama` |

## Multi-User

Each device auto-generates a unique identity token. Multiple users can run experiments simultaneously — sessions are isolated automatically.

## Programmatic Usage

```python
from maple.operator.agent import create_operator_agent

agent = create_operator_agent("my-session")
result = agent("Sort the colored blocks by color.")
```

## Testing

```bash
pytest -m "not integration"                    # Unit tests (no network)
docker compose -f docker-compose.ci.yaml up -d # Start MADSci
pytest -m integration                          # Integration tests
```

## Compatibility

- Python 3.10+
- MADSci >=0.5.0, <0.6.0
- FastMCP 3.x

## License

[MIT](LICENSE)
