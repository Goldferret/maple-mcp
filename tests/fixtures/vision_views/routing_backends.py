"""Routing stub backends for the vision view integration test.

Two distinct backends so the test can confirm that detect/verify route to
the correct backend for a given view/node. Neither does real image
processing — they return a marker identifying which backend ran.
"""

from maple.vision import VisionBackend


class RoutingStubA(VisionBackend):
    def detect_objects(self, frames: dict, config: dict) -> list[dict]:
        return [{
            "color": "A",
            "center": {"x": 0, "y": 0},
            "bbox": {"x": 0, "y": 0, "width": 1, "height": 1},
            "area": 1,
            "backend": "A",
            "frame_keys": sorted(frames.keys()),
        }]

    def verify_goal(self, frames: dict, config: dict) -> dict:
        return {"success": True, "details": "A verified", "backend": "A"}


class RoutingStubB(VisionBackend):
    def detect_objects(self, frames: dict, config: dict) -> list[dict]:
        return [{
            "color": "B",
            "center": {"x": 0, "y": 0},
            "bbox": {"x": 0, "y": 0, "width": 1, "height": 1},
            "area": 1,
            "backend": "B",
            "frame_keys": sorted(frames.keys()),
        }]

    def verify_goal(self, frames: dict, config: dict) -> dict:
        return {"success": True, "details": "B verified", "backend": "B"}
