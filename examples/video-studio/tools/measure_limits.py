"""Measure single-photo safe ranges and write stillmove/limits.json and docs/LIMITS.md.

Run from video-studio/:  python3 tools/measure_limits.py

Each move type is swept up to its catalog value with stillmove's fake gradient depth (3 to 8
units, near at the bottom) on a generated 1024x576 picture. The end-frame hole share is recorded.
The safe limit is the largest magnitude with holes <= 8 %. Real photos vary; these are not
guarantees for any particular picture.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from stillmove import _cm, limits  # noqa: E402
from stillmove.depth import get_backend  # noqa: E402

WIDTH, HEIGHT = 1024, 576
LIMITS_JSON = limits.LIMITS_FILE
LIMITS_MD = os.path.join(ROOT, "docs", "LIMITS.md")


def catalog_value(kind, presets):
    """The largest magnitude the catalog uses for this move type."""
    param = limits.PARAM[kind]
    values = [abs(p["params"][param]) for p in presets if p["type"] == kind and param in p["params"]]
    if not values:
        raise ValueError(f"the catalog has no {kind} preset")
    return max(values)


def _markdown(results):
    lines = [
        "# Single-photo limits",
        "",
        "How far each camera move can go on one still photo before the edges tear. Measured by",
        "`tools/measure_limits.py` and written to `stillmove/limits.json`.",
        "",
        "**Method.** Each type is swept up to its catalog value: angles in 1 degree steps, distances",
        "in 0.05 steps. Each step runs one move (directions: dolly z and x both ways, dolly zoom z both",
        "ways, crane up and down, orbit both ways, pan and tilt both ways). The share of pixels that",
        "are holes (not seen from the start pose) is measured at the END frame. The safe limit is the",
        f"largest magnitude whose hole share is at most {limits.HOLE_LIMIT:.0%}; the table keeps the",
        "smallest limit over directions. The picture is a generated 1024x576 ramp with a checker.",
        "Depth is the fake gradient (3 to 8 units, near at the bottom). Real photos vary: a photo with",
        "a different depth layout will tear at other magnitudes. These are not guarantees.",
        "",
        "| Type | Param | Safe limit | Holes at limit | Catalog value | Holes at catalog value |",
        "|---|---|---|---|---|---|",
    ]
    for kind, record in results.items():
        unit = record["unit"]
        lines.append(
            f"| {kind} | {record['param']} | <= {record['limit']:g} {unit} | "
            f"{record['holes_at_limit'] * 100:.1f} % | {record['catalog_value']:g} {unit} | "
            f"{record['holes_at_catalog'] * 100:.1f} % |"
        )
    lines.append("")
    return "\n".join(lines)


def main():
    _cm.import_camera_motion()
    from camera_motion.presets import load_catalog

    presets = load_catalog()
    image = limits.synthetic_image(WIDTH, HEIGHT)
    depth = get_backend("gradient").depth(image)

    results = {}
    for kind in limits.PARAM:
        results[kind] = limits.measure_type(kind, image, depth, catalog_value(kind, presets))
        print(f"{kind}: limit {results[kind]['limit']:g} {results[kind]['unit']}, "
              f"holes at limit {results[kind]['holes_at_limit'] * 100:.1f} %", flush=True)

    spec = {
        kind: {key: record[key] for key in ("param", "limit", "unit", "holes_at_limit")}
        for kind, record in results.items()
    }
    with open(LIMITS_JSON, "w", encoding="utf-8") as handle:
        json.dump(spec, handle, indent=2)
        handle.write("\n")
    with open(LIMITS_MD, "w", encoding="utf-8") as handle:
        handle.write(_markdown(results))
    print(f"wrote {LIMITS_JSON} and {LIMITS_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
