"""Export every catalog preset as camera frames for the web viewer.

Output shape: {preset_id: {name, type, fps, frames: [{t, position, rotation, fov}]}}.
Angles are degrees. Rotation is [yaw, pitch, roll].

Run from camera-motion/:  python3 -m camera_motion.web.export_frames [catalog.json] [-o out.json]
"""
import argparse
import json
import math
import os
import sys

from camera_motion.engine.camera_path import path
from camera_motion.presets import load_catalog

FPS = 24
DURATION_S = 3.0

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PRESETS_CATALOG = os.path.join(ROOT, "presets", "catalog.json")
DEFAULT_OUT = os.path.join(HERE, "frames.json")


def default_catalog():
    """The bundled presets/catalog.json."""
    return PRESETS_CATALOG


def _pose(frame):
    return {
        "t": frame["t"],
        "position": list(frame["position"]),
        "rotation": list(frame["rotation"]),
        "fov": frame["fov"],
    }


def _check_finite(preset_id, frames):
    for frame in frames:
        numbers = [frame["t"], frame["fov"], *frame["position"], *frame["rotation"]]
        if not all(math.isfinite(n) for n in numbers):
            raise ValueError(f"non-finite value in preset {preset_id!r}")


def build(catalog_path):
    """Return the frames document for every preset in catalog_path."""
    presets = load_catalog(catalog_path)
    doc = {}
    for preset in presets:
        pid = preset["id"]
        if pid in doc:
            raise ValueError(f"duplicate preset id {pid!r}")
        frames = [_pose(f) for f in path(preset, duration_s=DURATION_S, fps=FPS)]
        _check_finite(pid, frames)
        doc[pid] = {
            "name": preset.get("name", pid),
            "type": preset["type"],
            "fps": FPS,
            "frames": frames,
        }
    return doc


def main(catalog=None, out=None):
    """Write the frames JSON to out (default web/frames.json) and return its path."""
    catalog = catalog or default_catalog()
    out = out or DEFAULT_OUT
    doc = build(catalog)
    folder = os.path.dirname(out)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    return out


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Export camera presets as frames JSON.")
    parser.add_argument("catalog", nargs="?", default=None, help="catalog JSON (default: presets/catalog.json)")
    parser.add_argument("-o", "--out", default=None, help="output JSON (default: web/frames.json)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    print(main(args.catalog, args.out))
