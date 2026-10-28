"""Single-photo safe ranges for each camera move, measured with the fake gradient depth.

For each move type, the magnitude is swept (angles in 1 degree steps, distances in 0.05 steps)
up to the catalog value. At the END frame, the share of pixels that are holes (filled, not
seen) is recorded. The safe limit is the largest magnitude whose hole share is at most HOLE_LIMIT.
Each type is checked in every direction it can move, and the smallest limit is kept.

These numbers come from a synthetic picture and a fake depth ramp (3 to 8 units). Real photos vary.
"""
import json
import os

import numpy as np

from stillmove import _cm, cameras, warp
from stillmove.depth import get_backend

HERE = os.path.dirname(os.path.abspath(__file__))
LIMITS_FILE = os.path.join(HERE, "limits.json")

HOLE_LIMIT = 0.08  # 8 % of the pixels at the end frame
ORBIT_RADIUS = 5.0  # the catalog's orbit radius

PARAM = {
    "dolly": "distance", "dolly_zoom": "distance", "crane": "distance",
    "orbit": "angle", "pan": "angle", "tilt": "angle",
}
UNIT = {"distance": "scene units", "angle": "degrees"}
STEP = {"distance": 0.05, "angle": 1.0}
# (axis, sign) for dolly, or (None, sign) for the others.
DIRECTIONS = {
    "dolly": [("z", 1), ("z", -1), ("x", 1), ("x", -1)],
    "dolly_zoom": [("z", 1), ("z", -1)],
    "crane": [(None, 1), (None, -1)],
    "orbit": [(None, 1), (None, -1)],
    "pan": [(None, 1), (None, -1)],
    "tilt": [(None, 1), (None, -1)],
}


def synthetic_image(width, height):
    """A deterministic test picture: a colour ramp with a checker. No photo is needed."""
    y, x = np.mgrid[0:height, 0:width]
    red = (x * 255 // max(width - 1, 1)).astype(np.uint8)
    green = (y * 255 // max(height - 1, 1)).astype(np.uint8)
    checker = (((x // 32) + (y // 32)) % 2 * 90).astype(np.uint8)
    return np.stack([red, green, (255 - checker).astype(np.uint8)], axis=-1)


def _move_preset(kind, magnitude, axis):
    if kind == "dolly":
        params = {"axis": axis, "distance": magnitude}
    elif kind == "orbit":
        params = {"angle": magnitude, "radius": ORBIT_RADIUS}
    else:
        params = {PARAM[kind]: magnitude}
    return {"id": f"{kind}_sweep", "name": kind, "type": kind, "params": params,
            "easing": "linear", "description": "limits sweep"}


def _two_frame_cameras(preset, width, height):
    """Cameras for the move at fps 2 over 1 s: frames 0, 1, 2 with the end pose last."""
    _cm.import_camera_motion()
    from camera_motion.engine.camera_path import path
    from camera_motion.export.formats import to_json

    frames = path(preset, duration_s=1.0, fps=2)
    return cameras._from_document(to_json(frames, width=width, height=height, fps=2))


def end_hole_share(kind, magnitude, axis, image, depth):
    """Share of holes at the end frame of one move, as a fraction in [0, 1]."""
    height, width = image.shape[:2]
    cams = _two_frame_cameras(_move_preset(kind, magnitude, axis), width, height)
    last = len(cams.frames) - 1
    _out, filled = warp.warp(image, depth, cams.frames[0].K, cams.frames[last].K, warp.relative(cams, last))
    return warp.hole_fraction(filled)


def measure_type(kind, image, depth, catalog_value, step=None):
    """Measure one move type. Returns the limit record plus the holes at the catalog value.

    limit: the smallest, over directions, of the largest magnitude with holes <= HOLE_LIMIT.
    holes_at_limit: the largest hole share at that limit over directions.
    """
    param = PARAM[kind]
    step = step or STEP[param]
    count = max(1, round(catalog_value / step))
    limits, holes_at = [], []
    catalog_holes = []
    for axis, sign in DIRECTIONS[kind]:
        sweep = []
        for k in range(1, count + 1):
            magnitude = sign * round(k * step, 6)
            sweep.append((round(k * step, 6), end_hole_share(kind, magnitude, axis, image, depth)))
        safe = [(mag, share) for mag, share in sweep if share <= HOLE_LIMIT]
        limits.append(safe[-1][0] if safe else 0.0)
        holes_at.append(dict(sweep))
        catalog_holes.append(sweep[-1][1])
    limit = min(limits)
    holes_at_limit = max(table.get(limit, 0.0) for table in holes_at)
    return {
        "param": param,
        "unit": UNIT[param],
        "limit": limit,
        "holes_at_limit": round(holes_at_limit, 4),
        "catalog_value": round(catalog_value, 6),
        "holes_at_catalog": round(max(catalog_holes), 4),
    }


def load_limits(path=LIMITS_FILE):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def check_preset(preset, limits=None):
    """The warning body for a catalog preset past its safe range, or None when it is inside."""
    limits = limits if limits is not None else load_limits()
    kind = preset.get("type")
    record = limits.get(kind)
    if record is None:
        return None
    param = record["param"]
    if param not in preset.get("params", {}):
        return None
    value = abs(preset["params"][param])
    if value <= record["limit"]:
        return None
    return (f"{preset['id']} {param} {value:g} exceeds the single-photo safe range "
            f"(<= {record['limit']:g}); expect torn edges")


def safe_range_text(preset, limits=None):
    limits = limits if limits is not None else load_limits()
    record = limits.get(preset.get("type"))
    if record is None:
        return "unknown"
    return f"<= {record['limit']:g} {record['unit']}"
