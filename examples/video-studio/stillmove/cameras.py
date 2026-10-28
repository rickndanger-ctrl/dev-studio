"""Camera trajectories for warping, in pixel intrinsics and OpenCV extrinsics.

Source is a camera-motion export (JSON, from a file) or a preset run in memory. Both give the
same data: per frame, normalized K and an OpenGL w2c. Here K is converted to pixels and w2c to
OpenCV axes (y down, z forward) with diag(1, -1, -1, 1).
"""

import json
from dataclasses import dataclass

import numpy as np

from stillmove import _cm

_GL_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])


@dataclass
class Frame:
    t: float
    K: np.ndarray  # 3x3 pixel intrinsics
    w2c: np.ndarray  # 4x4 OpenCV world-to-camera


@dataclass
class Cameras:
    fps: int
    width: int
    height: int
    frames: list


def _from_document(doc):
    """Build Cameras from a camera-motion JSON document (already parsed)."""
    if not isinstance(doc, dict):
        raise ValueError("camera file must be a JSON object")
    for key in ("fps", "width", "height", "frames"):
        if key not in doc:
            raise ValueError(f"camera file is missing '{key}'")
    width, height = int(doc["width"]), int(doc["height"])
    if width <= 0 or height <= 0:
        raise ValueError("camera file width and height must be positive")
    frames = []
    for i, item in enumerate(doc["frames"]):
        try:
            k = np.array(item["K"], dtype=np.float64)
            w2c_gl = np.array(item["w2c"], dtype=np.float64)
            t = float(item["t"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"frame {i} is missing or has bad K, w2c or t: {exc}") from exc
        if k.shape != (3, 3) or w2c_gl.shape != (4, 4):
            raise ValueError(f"frame {i}: K must be 3x3 and w2c 4x4")
        pixel_k = np.array(
            [
                [k[0, 0] * width, 0.0, k[0, 2] * width],
                [0.0, k[1, 1] * height, k[1, 2] * height],
                [0.0, 0.0, 1.0],
            ]
        )
        frames.append(Frame(t=t, K=pixel_k, w2c=_GL_TO_CV @ w2c_gl))
    if not frames:
        raise ValueError("camera file has no frames")
    try:
        fps = int(round(float(doc["fps"])))
    except (TypeError, ValueError) as exc:
        raise ValueError("camera file fps must be a number") from exc
    if fps <= 0:
        raise ValueError("camera file fps must be positive, got %s" % doc["fps"])
    return Cameras(fps=fps, width=width, height=height, frames=frames)


def load(path):
    """Read a camera-motion JSON export from disk."""
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    return _from_document(doc)


def from_preset(preset_id, seconds, fps, width, height):
    """Run a camera-motion preset in memory and return Cameras (no file is written)."""
    _cm.import_camera_motion()
    from camera_motion.engine.camera_path import path
    from camera_motion.export.formats import to_json
    from camera_motion.presets import load_catalog

    matches = [p for p in load_catalog() if p.get("id") == preset_id]
    if not matches:
        raise ValueError(f"unknown camera preset: {preset_id!r}")
    frames = path(matches[0], duration_s=seconds, fps=fps)
    doc = to_json(frames, width=width, height=height, fps=fps)
    return _from_document(doc)
