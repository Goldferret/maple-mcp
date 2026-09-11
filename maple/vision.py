"""Vision backend interface for MAPLE detection pipelines.

A VisionBackend is PURE: it receives already-downloaded datapoint values
(a dict of {datapoint_key: raw_value}) and returns detection/verification
results. It never touches MADSci — MAPLE captures frames and downloads
datapoints, then hands the raw values to the backend.

This means a VisionBackend can be unit-tested in complete isolation with a
saved image file and zero MADSci installed:

    backend = MyVision()
    frames = {"color_datapoint_id": open("frame.jpg", "rb").read()}
    result = backend.detect_objects(frames, {})
"""

from abc import ABC, abstractmethod


class VisionBackend(ABC):
    """Abstract base class for object detection backends.

    Subclass this to implement custom detection and verification logic for
    your lab. MAPLE handles frame capture and datapoint download; your
    backend only interprets the raw values.

    See examples/block_sorting/vision.py for a reference implementation.
    """

    @abstractmethod
    def detect_objects(self, frames: dict, config: dict) -> list[dict]:
        """Detect objects in the workspace.

        Args:
            frames: Dict of {datapoint_key: raw_value} for every datapoint the
                capture produced. For a camera node this typically looks like
                {"color_datapoint_id": <jpeg bytes>, "depth_datapoint_id": <bytes>}.
                Your backend reads the key(s) it needs and ignores the rest —
                you know your own lab's camera node and which datapoints it emits.
            config: Detection configuration (passed from the view context).

        Returns:
            List of detection dicts, each with at minimum:
                - "color": str (object class/color name)
                - "center": {"x": int, "y": int}
                - "bbox": {"x": int, "y": int, "width": int, "height": int}
                - "area": int
        """
        raise NotImplementedError

    @abstractmethod
    def verify_goal(self, frames: dict, config: dict) -> dict:
        """Verify whether the experiment goal has been achieved.

        Args:
            frames: Dict of {datapoint_key: raw_value}, same shape as
                detect_objects receives.
            config: Verification configuration (goal zones, expected state, etc.).

        Returns:
            Dict with at minimum:
                - "success": bool (whether the goal is met)
                - "details": str (human-readable explanation)
                - Additional fields as needed (e.g., per-object status)
        """
        raise NotImplementedError


class StubBackend(VisionBackend):
    """Returns preconfigured results for testing.

    Ignores frames entirely — used to exercise the MAPLE pipeline without
    real hardware or image processing.
    """

    def __init__(self, detections: list[dict] = None, verification: dict = None):
        self.detections = detections or []
        self.verification = verification or {"success": True, "details": "Stub: always passes"}

    def detect_objects(self, frames: dict, config: dict) -> list[dict]:
        return self.detections

    def verify_goal(self, frames: dict, config: dict) -> dict:
        return self.verification


class SecondStubBackend(VisionBackend):
    """A second, distinct stub backend for routing tests.

    Returns results tagged with a marker so tests can confirm that the
    correct backend was invoked for a given view/node.
    """

    def __init__(self, marker: str = "second"):
        self.marker = marker

    def detect_objects(self, frames: dict, config: dict) -> list[dict]:
        return [{"color": self.marker, "center": {"x": 0, "y": 0},
                 "bbox": {"x": 0, "y": 0, "width": 1, "height": 1}, "area": 1}]

    def verify_goal(self, frames: dict, config: dict) -> dict:
        return {"success": True, "details": f"verified by {self.marker}", "marker": self.marker}
