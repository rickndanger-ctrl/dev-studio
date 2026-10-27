"""Match an imported camera path against the preset catalog.

Each preset is re-run from the input's first pose, over the input's duration and frame
count, then scored frame by frame against the input:

    score = RMS position error + RMS rotation error / 45 + RMS fov error / 45

Positions are scene units, rotation errors are degrees wrapped to +-180 (per yaw, pitch
and roll), fov errors are degrees. Lower is better.

Frames are paired by index, so the input must be evenly spaced in time: every gap must
match the inferred fps to within 1e-3 (relative), or match() raises ValueError.
"""
import math

from camera_motion.engine.camera_path import path
from camera_motion.export.importers import check_even_spacing
from camera_motion.presets import load_catalog

ANGLE_SCALE = 45.0


def _wrap(degrees):
    return (degrees + 180.0) % 360.0 - 180.0


def _start_pose(frame):
    return {"position": list(frame["position"]), "rotation": list(frame["rotation"]), "fov": frame["fov"]}


def score(frames, candidate, fov_track=True):
    """Combined error between two equal-length frame lists. Lower is better.

    With fov_track False (glTF has no zoom track), the fov term is left out.
    """
    n = len(frames)
    pos_sq = 0.0
    rot_sq = 0.0
    fov_sq = 0.0
    for a, b in zip(frames, candidate):
        pos_sq += sum((x - y) ** 2 for x, y in zip(a["position"], b["position"]))
        rot_sq += sum(_wrap(x - y) ** 2 for x, y in zip(a["rotation"], b["rotation"]))
        fov_sq += (a["fov"] - b["fov"]) ** 2
    pos_rms = math.sqrt(pos_sq / n)
    rot_rms = math.sqrt(rot_sq / (3 * n))
    total = pos_rms + rot_rms / ANGLE_SCALE
    if fov_track:
        total += math.sqrt(fov_sq / n) / ANGLE_SCALE
    return total


def match(frames, catalog=None, top=3, fov_track=True):
    """Return the top presets as [(id, score)], best (lowest score) first."""
    n = len(frames)
    if n < 2:
        raise ValueError("need at least 2 frames to match")
    duration = float(frames[-1]["t"]) - float(frames[0]["t"])
    if duration <= 0:
        raise ValueError("frames must span a positive duration")
    fps = (n - 1) / duration
    check_even_spacing([f["t"] for f in frames])
    start = _start_pose(frames[0])

    scored = []
    for preset in load_catalog(catalog):
        candidate = path(preset, duration_s=duration, fps=fps, start=start)
        if len(candidate) != n:
            raise ValueError(f"preset {preset['id']!r} gave {len(candidate)} frames, expected {n}")
        scored.append((preset["id"], score(frames, candidate, fov_track)))
    scored.sort(key=lambda item: item[1])
    return scored[:top]
