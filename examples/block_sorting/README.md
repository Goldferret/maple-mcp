# Block Sorting Demo

A self-contained example demonstrating the full MAPLE pipeline. Uses a stub robot node, mock agent (no LLM needed), and an example VisionBackend.

## What's Included

| File | Purpose |
|------|---------|
| `vision.py` | `ExampleVision` — hardcoded VisionBackend (3 blocks, always sorted) |
| `stub_node.py` | Minimal MADSci node with `pick_and_place` action |
| `mock_agent.py` | Scripted agent — walks through full experiment without an LLM |
| `maple.config.yaml` | MAPLE config pointing to `vision:ExampleVision` |
| `.env.example` | Template for service URLs |

## Prerequisites

- Python 3.10+
- `pip install maple-mcp`
- A running MADSci lab (real or example lab via `docker compose`)

## Setup

### 1. Configure environment

```bash
cd examples/block_sorting
cp .env.example .env
```

Edit `.env` with your MADSci service URLs. Set `NODE_URL` to the address the Workcell Manager can reach your stub node at.

### 2. Start the stub node

```bash
python stub_node.py
```

You should see:
```
✓ Registered StubBot at <NODE_URL> with Workcell Manager
INFO:     Uvicorn running on http://0.0.0.0:2000
```

### 3. Start the mock agent

```bash
python mock_agent.py
```

### 4. Launch the TUI

In a separate terminal:

```bash
maple chat --agent operator
```

Type anything (e.g., "sort the blocks") and press Enter. The mock agent streams a scripted experiment:

1. Registers the experiment
2. Detects 3 blocks (2 red, 1 blue)
3. Sorts each block with `pick_and_place`
4. Verifies all blocks sorted
5. Ends the experiment

Press Enter again to exit.

## Cleanup

The stub node registers as non-permanent. To remove it, restart the Workcell Manager:

```bash
docker restart workcell_manager
```

## Going Live

To use a real LLM instead of the mock agent:

1. Install Ollama: `curl -fsSL https://ollama.com/install.sh | sh`
2. Pull a model: `ollama pull qwen3:8b`
3. Add to `.env`:
   ```
   MODEL_PROVIDER=ollama
   OLLAMA_HOST=http://localhost:11434
   OPERATOR_MODEL=qwen3:8b
   ```
4. Skip the mock agent — use `maple serve --agent operator --config maple.config.yaml` instead
5. Run `maple chat --agent operator` and tell it: "Sort the blocks by color"

To use real hardware, replace `ExampleVision` with your own `VisionBackend` subclass and update `vision_backend` in `maple.config.yaml`.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| StubBot shows `ready=False` | Check the Workcell Manager can reach NODE_URL |
| Workflow stuck in "Queued" | Node isn't ready. Verify with `get_nodes()` |
| Node name already exists | Restart Workcell Manager to clear non-permanent nodes |
