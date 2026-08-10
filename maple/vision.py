"""Vision backend interface for MAPLE detection pipelines."""

from abc import ABC, abstractmethod


class VisionBackend(ABC):
    """Abstract base class for object detection backends.

    Subclass this to implement custom detection and verification logic for your lab.
    See examples/block_sorting/vision.py for a reference implementation.
    """

    @abstractmethod
    def detect_objects(self, image_bytes: bytes, config: dict) -> list[dict]:
        """Detect objects in the workspace.

        Args:
            image_bytes: Raw image data (JPEG or PNG encoded)
            config: Detection configuration (passed from MCP server context)

        Returns:
            List of detection dicts, each with at minimum:
                - "color": str (object class/color name)
                - "center": {"x": int, "y": int}
                - "bbox": {"x": int, "y": int, "width": int, "height": int}
                - "area": int
        """
        raise NotImplementedError

    @abstractmethod
    def verify_goal(self, image_bytes: bytes, config: dict) -> dict:
        """Verify whether the experiment goal has been achieved.

        Args:
            image_bytes: Raw image data (JPEG or PNG encoded)
            config: Verification configuration (goal zones, expected state, etc.)

        Returns:
            Dict with at minimum:
                - "success": bool (whether the goal is met)
                - "details": str (human-readable explanation)
                - Additional fields as needed (e.g., per-object status)
        """
        raise NotImplementedError


class StubBackend(VisionBackend):
    """Returns preconfigured results for testing."""

    def __init__(self, detections: list[dict] = None, verification: dict = None):
        self.detections = detections or []
        self.verification = verification or {"success": True, "details": "Stub: always passes"}

    def detect_objects(self, image_bytes: bytes, config: dict) -> list[dict]:
        return self.detections

    def verify_goal(self, image_bytes: bytes, config: dict) -> dict:
        return self.verification
