"""Validate every preset in the catalog, then run each one through the camera-path engine.

Run from camera-motion/:
    python3 -m camera_motion.integration.run_catalog
    python3 -m camera_motion.integration.run_catalog --catalog path/to/other.json

For each preset it prints one row: id, frame count, start pose, end pose and
end fov. A preset that fails validation (or whose path cannot be generated, or
has a non-finite number) is listed as FAIL with its errors, and the exit
status is 1. Exit status is 0 only when every preset passes.
"""

import argparse
import math
import os
import sys

from camera_motion.engine.camera_path import path
from camera_motion.presets.validate import SCHEMA_PATH, SchemaError, load_json, validate_catalog

DEFAULT_CATALOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets", "catalog.json"
)


def _fmt_vec(values):
    return "(" + ", ".join("%.3f" % v for v in values) + ")"


def _fmt_pose(frame):
    return "pos %s rot %s" % (_fmt_vec(frame["position"]), _fmt_vec(frame["rotation"]))


def _is_finite(frame):
    numbers = list(frame["position"]) + list(frame["rotation"]) + [frame["fov"]]
    return all(math.isfinite(v) for v in numbers)


def _presets_of(catalog):
    if isinstance(catalog, list):
        return catalog
    if isinstance(catalog, dict):
        return catalog.get("presets")
    return None


def run(catalog_path):
    """Return (rows, catalog_errors). rows is a list of (preset_id, status, cells, notes)."""
    schema = load_json(SCHEMA_PATH)
    catalog = load_json(catalog_path)
    presets = _presets_of(catalog)
    errors = validate_catalog(catalog, schema)
    if not isinstance(presets, list):
        return [], errors

    rows = []
    attributed = set()
    for index, preset in enumerate(presets):
        prefix = "presets[%d]" % index
        own = [e for e in errors if e.startswith(prefix + ":") or e.startswith(prefix + ".")]
        attributed.update(own)
        preset_id = preset.get("id", "?") if isinstance(preset, dict) else "?"

        if own:
            rows.append((preset_id, "FAIL", None, own))
            continue
        try:
            frames = path(preset)
        except (ValueError, KeyError, TypeError) as exc:
            rows.append((preset_id, "FAIL", None, ["path: %s" % exc]))
            continue
        if not all(_is_finite(f) for f in frames):
            rows.append((preset_id, "FAIL", None, ["path: non-finite value in generated frames"]))
            continue
        cells = (len(frames), _fmt_pose(frames[0]), _fmt_pose(frames[-1]), "%.3f" % frames[-1]["fov"])
        rows.append((preset_id, "OK", cells, []))

    catalog_errors = [e for e in errors if e not in attributed]
    return rows, catalog_errors


def print_table(rows, catalog_errors):
    headers = ("id", "frames", "start pose", "end pose", "end fov", "result")
    body = []
    for preset_id, status, cells, _notes in rows:
        if cells is None:
            body.append((preset_id, "-", "-", "-", "-", status))
        else:
            frames, start, end, fov = cells
            body.append((preset_id, str(frames), start, end, fov, status))

    widths = [max(len(str(r[i])) for r in [headers] + body) for i in range(len(headers))]

    def line(values):
        return "  ".join(str(v).ljust(widths[i]) for i, v in enumerate(values)).rstrip()

    print(line(headers))
    print(line(["-" * w for w in widths]))
    for r in body:
        print(line(r))

    for preset_id, status, _cells, notes in rows:
        for note in notes:
            print("FAIL %s: %s" % (preset_id, note), file=sys.stderr)
    for message in catalog_errors:
        print("FAIL catalog: %s" % message, file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate the camera-motion catalog and run every preset.")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help="catalog JSON (default: presets/catalog.json)")
    args = parser.parse_args(argv)

    try:
        rows, catalog_errors = run(args.catalog)
    except (OSError, ValueError, SchemaError) as exc:
        print("error: %s: %s" % (args.catalog, exc), file=sys.stderr)
        return 1

    print_table(rows, catalog_errors)
    failed = [r for r in rows if r[1] != "OK"]
    if failed or catalog_errors:
        print("FAILED: %d preset(s) failed, %d catalog error(s)" % (len(failed), len(catalog_errors)), file=sys.stderr)
        return 1
    print("OK: %d preset(s) validated and generated paths" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
