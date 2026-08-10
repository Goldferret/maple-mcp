"""Example VisionBackend for the block sorting demo.

Returns hardcoded detections and verification results.
Demonstrates how to implement the VisionBackend interface.
"""

from maple.vision import VisionBackend


class ExampleVision(VisionBackend):
    """Hardcoded vision backend for the block sorting demo.

    Returns fixed block positions for detection and always-sorted
    for verification. Used with the mock agent to demonstrate
    the full MAPLE pipeline without real hardware or cameras.
    """

    def detect_objects(self, image_bytes: bytes, config: dict) -> list[dict]:
        """Return 3 hardcoded blocks in the detection zone."""
        return [
            {
                "color": "red",
                "center": {"x": 300, "y": 250},
                "bbox": {"x": 280, "y": 230, "width": 40, "height": 40},
                "area": 1600,
                "zone": "detection_zone",
            },
            {
                "color": "blue",
                "center": {"x": 350, "y": 280},
                "bbox": {"x": 330, "y": 260, "width": 40, "height": 40},
                "area": 1600,
                "zone": "detection_zone",
            },
            {
                "color": "red",
                "center": {"x": 270, "y": 220},
                "bbox": {"x": 250, "y": 200, "width": 40, "height": 40},
                "area": 1600,
                "zone": "detection_zone",
            },
        ]

    def verify_goal(self, image_bytes: bytes, config: dict) -> dict:
        """Always returns success — used with mock agent."""
        return {
            "success": True,
            "details": "All 3 blocks sorted into correct goal zones",
            "blocks": [
                {"color": "red", "zone": "goal_zone_1", "correct": True},
                {"color": "blue", "zone": "goal_zone_2", "correct": True},
                {"color": "red", "zone": "goal_zone_1", "correct": True},
            ],
        }
