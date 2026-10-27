"""camera-motion doctor: check that this install works, one line per check.

Run: python3 -m camera_motion.doctor   (or: camera-motion doctor)
Prints 'PASS <check>' or 'FAIL <check>: <reason>' per check. Exits 0 when all pass, else 1.

The checks read CATALOG at call time, so a test can point it somewhere else.
"""
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

from camera_motion.engine.camera_path import path as camera_path
from camera_motion.export.formats import export_text, parse_re10k
from camera_motion.preview.anim_svg import render_animated
from camera_motion.presets import load_catalog, load_help
from camera_motion.presets.validate import SchemaError, load_json, validate_catalog

PACKAGE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(PACKAGE, "presets", "catalog.json")
SCHEMA = os.path.join(PACKAGE, "presets", "schema.json")
PACKAGE_FILES = ("presets/catalog.json", "presets/schema.json", "web/app.html", "web/index.html")
MIN_PYTHON = (3, 9)
FPS = 24
EXPORT_EXT = {"json": "json", "re10k": "txt", "blender": "py", "gltf": "gltf"}


def check_python():
    if sys.version_info[:2] >= MIN_PYTHON:
        return None
    return "running Python %d.%d" % sys.version_info[:2]


def check_package_data():
    missing = [p for p in PACKAGE_FILES if not os.path.isfile(os.path.join(PACKAGE, p))]
    return ("missing " + ", ".join(missing)) if missing else None


def check_catalog():
    try:
        catalog = load_json(CATALOG)
        errors = validate_catalog(catalog, load_json(SCHEMA))
        presets = load_catalog(CATALOG)
    except (OSError, ValueError, SchemaError) as exc:
        return str(exc)
    if errors:
        shown = "; ".join(errors[:3])
        return shown + (" ..." if len(errors) > 3 else "")
    if not presets:
        return "catalog has no presets"
    return None


def check_help():
    help_by_id = load_help()
    if not help_by_id:
        return "help.json has no entries"
    missing = [p["id"] for p in load_catalog(CATALOG) if p["id"] not in help_by_id]
    return ("no help for " + ", ".join(missing)) if missing else None


def _first_preset():
    return load_catalog(CATALOG)[0]


def check_anim():
    preset = _first_preset()
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "anim.svg")
        render_animated(camera_path(preset), out, preset.get("name", preset.get("id", "")), fps=FPS)
        ET.parse(out)
    return None


def _export_check(fmt):
    def check():
        preset = _first_preset()
        frames = camera_path(preset)
        text = export_text(fmt, frames, preset)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "export." + EXPORT_EXT[fmt])
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            with open(out, encoding="utf-8") as f:
                back = f.read()
            if fmt in ("json", "gltf"):
                json.loads(back)
            elif fmt == "re10k":
                count = len(parse_re10k(back.splitlines()))
                if count != len(frames):
                    return f"{count} frames, expected {len(frames)}"
            elif fmt == "blender":
                compile(back, out, "exec")
        return None

    return check


CHECKS = (
    ("python >= 3.9", check_python),
    ("package data present", check_package_data),
    ("catalog validates", check_catalog),
    ("help present", check_help),
    ("anim renders", check_anim),
    ("export json", _export_check("json")),
    ("export re10k", _export_check("re10k")),
    ("export blender", _export_check("blender")),
    ("export gltf", _export_check("gltf")),
)


def run_checks():
    """Return a list of (name, reason); reason is None when the check passes."""
    results = []
    for name, check in CHECKS:
        try:
            reason = check()
        except Exception as exc:  # a check that crashes is a failure, not a crash of doctor
            reason = f"{type(exc).__name__}: {exc}"
        results.append((name, reason))
    return results


def main():
    failed = 0
    for name, reason in run_checks():
        if reason is None:
            print(f"PASS {name}")
        else:
            print(f"FAIL {name}: {reason}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
