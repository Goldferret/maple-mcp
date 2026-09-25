"""Stub MADSci node for the block-sorting demo and integration tests.

A minimal but REAL MADSci RestNode (not a hand-rolled fake). It requires no
physical robot — the action handlers return canned success — but it uses the
genuine `madsci.node_module` machinery, so `/info`, `/status`, action dispatch,
and config publishing all behave exactly like a real node.

This doubles as a **reference node**: it shows where a node declares its
physical `constraints` (in its config model, published via NodeInfo.config and
read by MAPLE's `get_robot_constraints`) and how to define `@action` methods.

Run standalone:
    python stub_node.py
    # Registers with the Workcell Manager and serves on node_url (default :2000)

Requires MADSci manager URLs in the environment (WORKCELL_SERVER_URL, etc.),
same as any real node.
"""

from typing import List

from pydantic import Field

from madsci.node_module import RestNode, action
from madsci.common.types.node_types import RestNodeConfig


class StubNodeConfig(RestNodeConfig):
    """Config for the stub node.

    `constraints` / `constraint_description` are custom fields that ride through
    into the published NodeInfo.config, where MAPLE's get_robot_constraints
    reads them. This is the reference pattern for node-declared physical
    constraints.
    """

    constraint_description: str = "Robotic manipulator with single gripper."
    constraints: List[str] = Field(
        default_factory=lambda: [
            "Single gripper. Can hold one object at a time.",
            "Sequential execution. One action at a time.",
            "Pixel-based targeting. Actions use pixel coordinates from an overhead camera.",
        ]
    )


class StubNode(RestNode):
    """A stub robot node — real MADSci node, no physical hardware."""

    config_model = StubNodeConfig

    @action(name="pick_and_place", description="Pick an object and place it at a target location.")
    def pick_and_place(self, pick_x: float, pick_y: float, place_x: float, place_y: float) -> dict:
        return {
            "message": "pick_and_place completed successfully",
            "pick": {"x": pick_x, "y": pick_y},
            "place": {"x": place_x, "y": place_y},
        }

    @action(name="analyze", description="Analyze the last action for quality verification.")
    def analyze(self, task_description: str = "") -> dict:
        return {
            "message": "analyze completed",
            "task_description": task_description,
            "outcome": "ok",
        }


def _register_with_workcell(node_url: str) -> None:
    """Register this node with the Workcell Manager at a reachable URL.

    The CI workcell starts empty and expects runtime node registration. We
    advertise `node_url` (reachable from the workcell — e.g. host.docker.internal
    under Docker) while the node itself binds separately (see __main__).
    """
    try:
        from madsci.client import WorkcellClient

        WorkcellClient().add_node(
            node_name="StubBot",
            node_url=node_url,
            node_description="Stub robot node for MAPLE",
            permanent=False,
        )
        print(f"✓ Registered StubBot at {node_url}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ Could not register with Workcell Manager: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MAPLE Stub Robot Node")
    parser.add_argument("--url", default="http://host.docker.internal:2000/",
                        help="URL the Workcell Manager uses to reach this node.")
    parser.add_argument("--bind", default="http://0.0.0.0:2000/",
                        help="Address this node binds/listens on.")
    args = parser.parse_args()

    _register_with_workcell(args.url)
    StubNode(node_config=StubNodeConfig(node_name="StubBot", node_url=args.bind)).start_node()
