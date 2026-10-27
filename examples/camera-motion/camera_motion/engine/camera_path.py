"""Camera path generation: turns a motion preset into per-frame camera state.

Angles are degrees. Rotation is [yaw, pitch, roll]. Position is [x, y, z].
"""
import json
import math
import sys

from camera_motion.engine.easing import ease

DEFAULT_START = {"position": [0, 0, 5], "rotation": [0, 0, 0], "fov": 50}
AXES = {"x": 0, "y": 1, "z": 2}


def _lerp(a, b, e):
    return a + (b - a) * e


def _orbit_offset(pos, radius, e):
    """Return the xz offset to orbit around y at eased progress e.

    Keeps the start direction. The distance eases from the start distance to radius
    (so frame 0 is the start pose). A start on the y axis has no direction, so it uses +z.
    """
    x, z = pos[0], pos[2]
    start_dist = math.hypot(x, z)
    if start_dist == 0:
        ux, uz = 0.0, 1.0
    else:
        ux, uz = x / start_dist, z / start_dist
    dist = start_dist if radius is None else _lerp(start_dist, float(radius), e)
    return ux * dist, uz * dist


def _basis(rotation):
    """Camera forward, right and up for rotation [yaw, pitch, roll]; roll is ignored."""
    yaw, pitch = math.radians(rotation[0]), math.radians(rotation[1])
    f = (-math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch))
    r = (math.cos(yaw), 0.0, -math.sin(yaw))
    u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
    return f, r, u


def _along(pos, vec, amount):
    return [p + v * amount for p, v in zip(pos, vec)]


def _offset(start, preset, e):
    """Return (position, rotation, fov) for eased progress e in [0, 1]."""
    kind = preset["type"]
    pos = list(start["position"])
    rot = list(start["rotation"])
    fov = start["fov"]
    params = preset.get("params", {})
    distance = params.get("distance", 0.0)
    angle = params.get("angle", 0.0)
    frame = params.get("frame", "world")
    if frame not in ("world", "camera"):
        raise ValueError(f"unknown frame: {frame!r}")
    camera = frame == "camera"

    if kind == "dolly":
        axis = params.get("axis", "z")
        if camera:
            # Directions come from the start pose, so they stay fixed over the move.
            f, r, u = _basis(start["rotation"])
            vec = {"x": r, "y": u, "z": f}[axis]
            pos = _along(pos, vec, distance * e)
        else:
            # +distance is camera right on x, toward the subject on z; y keeps its old sign.
            sign = 1.0 if axis == "x" else -1.0
            pos[AXES[axis]] += sign * distance * e
    elif kind == "dolly_zoom":
        # The subject stays at the look-at point: the camera moves toward it while the fov
        # follows the distance, so the subject's projected height stays the same.
        f0, _, _ = _basis(start["rotation"])
        if "subject_distance" in params:
            d0 = float(params["subject_distance"])
        else:
            d0 = -sum(p * c for p, c in zip(start["position"], f0))  # origin measured along forward
        if d0 <= 0:
            raise ValueError(f"dolly_zoom needs the subject in front of the camera (distance {d0:g})")
        d = d0 - distance * e
        if d < 0.05 * d0:
            raise ValueError("dolly_zoom would move through the subject; reduce distance "
                             "or raise subject_distance")
        if camera:
            pos = _along(pos, f0, distance * e)
        else:
            pos[2] -= distance * e
        k = d0 * math.tan(math.radians(start["fov"]) / 2)
        # Frame 0 is the start pose exactly, so take its fov directly, not via tan and atan.
        fov = start["fov"] if d == d0 else math.degrees(2 * math.atan(k / d))
    elif kind == "crane":
        if camera:
            _, _, u = _basis(start["rotation"])
            pos = _along(pos, u, distance * e)
        else:
            pos[1] += distance * e
    elif kind == "orbit":
        theta = math.radians(angle * e)
        x, z = _orbit_offset(pos, params.get("radius"), e)
        pos[0] = x * math.cos(theta) + z * math.sin(theta)
        pos[2] = -x * math.sin(theta) + z * math.cos(theta)
        rot[0] += angle * e
    elif kind == "pan":
        rot[0] += angle * e
    elif kind == "tilt":
        rot[1] += angle * e
    else:
        raise ValueError(f"unknown camera motion type: {kind!r}")
    return pos, rot, fov


def path(preset, duration_s=3.0, fps=24, start=None):
    """Return a list of frames {t, position, rotation, fov} for the preset.

    N = round(duration_s * fps) + 1 frames; frame i has t = i / fps and
    eased progress e = ease(easing, i / (N - 1)).
    """
    start = start or DEFAULT_START
    easing = preset.get("easing", "linear")
    n = round(duration_s * fps) + 1
    frames = []
    for i in range(n):
        u = i / (n - 1) if n > 1 else 0.0
        e = ease(easing, u)
        position, rotation, fov = _offset(start, preset, e)
        frames.append({"t": i / fps, "position": position, "rotation": rotation, "fov": fov})
    return frames


def main(argv):
    if len(argv) != 2:
        print("usage: python3 -m camera_motion.engine.camera_path preset.json", file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        preset = json.load(f)
    frames = path(preset, duration_s=preset.get("duration_s", 3.0), fps=preset.get("fps", 24))
    print("first:", json.dumps(frames[0]))
    print("last:", json.dumps(frames[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
