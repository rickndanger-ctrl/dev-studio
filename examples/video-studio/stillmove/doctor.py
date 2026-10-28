"""stillmove doctor: one PASS, WARN or FAIL line per check. Exits 1 only on a FAIL.

A missing ffmpeg is a WARN: mp4 output is optional. Each check that raises is a FAIL.
"""
import os
import shutil
import sys
import tempfile

FAKE_SIZE = (32, 18)  # width, height of the fake render check
FAKE_SECONDS = 1.0
FAKE_FPS = 24


def _check_python():
    if sys.version_info[:2] < (3, 9):
        return "FAIL", f"python {sys.version.split()[0]} is older than 3.9"
    return "PASS", "python >= 3.9"


def _check_pillow():
    import PIL  # noqa: F401

    return "PASS", "pillow"


def _check_numpy():
    import numpy  # noqa: F401

    return "PASS", "numpy"


def _check_camera_motion():
    from stillmove import cameras

    cams = cameras.from_preset("dolly_in", FAKE_SECONDS, FAKE_FPS, *FAKE_SIZE)
    if not cams.frames:
        return "FAIL", "camera path has no frames"
    return "PASS", "camera_motion import"


def _check_fake_render():
    import numpy as np
    from PIL import Image

    from stillmove import cameras
    from stillmove.depth import get_backend
    from stillmove.render import render

    width, height = FAKE_SIZE
    cams = cameras.from_preset("dolly_in", FAKE_SECONDS, FAKE_FPS, width, height)
    expected = round(FAKE_SECONDS * FAKE_FPS) + 1
    with tempfile.TemporaryDirectory() as tmp:
        image = os.path.join(tmp, "in.png")
        Image.fromarray(np.full((height, width, 3), 128, dtype=np.uint8)).save(image)
        manifest = render(image, cams, get_backend("plane"), os.path.join(tmp, "out"))
        if len(manifest["frames"]) != expected:
            return "FAIL", f"{len(manifest['frames'])} frames, expected {expected}"
        for name in manifest["frames"]:
            if not os.path.isfile(os.path.join(tmp, "out", name)):
                return "FAIL", f"missing frame {name}"
    return "PASS", f"fake render {width}x{height}"


SHOTPLAN_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                             "shotplan"))
SHOTPLAN_SAMPLES = os.path.join(SHOTPLAN_DIR, "samples")
SHOTPLAN_SCALE = 0.05  # the sample renders at 5% of its size: small, and still a real fake render


def _on_path(folder):
    if folder not in sys.path:
        sys.path.insert(0, folder)


def _check_shotplan_import(shotplan_dir=SHOTPLAN_DIR):
    _on_path(shotplan_dir)
    import shotplan  # noqa: F401

    return "PASS", "shotplan import"


def _check_shotplan_web_import(shotplan_dir=SHOTPLAN_DIR):
    _on_path(shotplan_dir)
    from web import server  # noqa: F401

    return "PASS", "shotplan web import"


def _check_shotplan_render(shotplan_dir=SHOTPLAN_DIR, samples=SHOTPLAN_SAMPLES):
    """Check one sample (the shortest) and render it with fake depth at a small scale."""
    import json

    _on_path(shotplan_dir)
    import shotplan
    import shotrender

    names = sorted(n for n in os.listdir(samples) if n.endswith(".json")) if os.path.isdir(samples) else []
    if not names:
        return "FAIL", f"no sample plans in {samples}"
    plans = []
    for name in names:
        with open(os.path.join(samples, name), encoding="utf-8") as handle:
            plan = json.load(handle)
        plans.append((sum(s["duration"] for s in plan["shots"]), name, plan))
    _total, name, plan = min(plans, key=lambda item: item[0])
    errors, rows, sequences = shotplan.check(plan, samples)
    if errors:
        return "FAIL", f"{name}: {errors[0]}"
    expected = round(max(row[1] for row in rows) * plan["fps"])
    with tempfile.TemporaryDirectory() as tmp:
        manifest = shotrender.render_plan(plan, samples, os.path.join(tmp, "out"), rows, sequences,
                                          shotplan.load_json(shotplan.CATALOG), depth="fake",
                                          scale=SHOTPLAN_SCALE, use_ffmpeg=False)
        if manifest["frames"] != expected:
            return "FAIL", f"{name}: {manifest['frames']} frames, expected {expected}"
    return "PASS", f"shotplan fake render of {name} at {manifest['width']}x{manifest['height']}"


def _check_ffmpeg():
    if shutil.which("ffmpeg"):
        return "PASS", "ffmpeg"
    return "WARN", "ffmpeg not found on PATH; mp4 output is off"


def _check_depth_model():
    """Model present and SHA256 matches MODELS.md. Missing is a WARN: only da2-small needs it."""
    from stillmove import models
    from stillmove.backends.depth_anything import DEFAULT_MODEL, MODEL_SHA256

    if not os.path.isfile(DEFAULT_MODEL):
        return "WARN", f"depth model not found at {DEFAULT_MODEL}; da2-small is off"
    try:
        models.verify(DEFAULT_MODEL, MODEL_SHA256)
    except ValueError as exc:
        return "FAIL", str(exc)
    return "PASS", "depth model SHA256 matches"


def _check_onnxruntime():
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return "WARN", "onnxruntime not installed; da2-small is off"
    return "PASS", "onnxruntime"


def _check_matte_model():
    """The isnet matte model: present and SHA256 matches MODELS.md. Missing is a WARN: only --backend isnet needs it."""
    from stillmove import models
    from stillmove.matte import DEFAULT_ISNET_MODEL, ISNET_SHA256

    if not os.path.isfile(DEFAULT_ISNET_MODEL):
        return "WARN", f"matte model not found at {DEFAULT_ISNET_MODEL}; the isnet matte is off"
    try:
        models.verify(DEFAULT_ISNET_MODEL, ISNET_SHA256)
    except ValueError as exc:
        return "FAIL", str(exc)
    return "PASS", "matte model SHA256 matches"


CHECKS = (
    ("python >= 3.9", _check_python),
    ("pillow", _check_pillow),
    ("numpy", _check_numpy),
    ("camera_motion import", _check_camera_motion),
    ("fake render", _check_fake_render),
    ("ffmpeg", _check_ffmpeg),
    ("depth model", _check_depth_model),
    ("onnxruntime", _check_onnxruntime),
    ("matte model", _check_matte_model),
    ("shotplan import", _check_shotplan_import),
    ("shotplan web import", _check_shotplan_web_import),
    ("shotplan fake render", _check_shotplan_render),
)


def run_checks():
    """Return a list of (status, name, detail)."""
    results = []
    for name, check in CHECKS:
        try:
            status, detail = check()
        except Exception as exc:  # a check that crashes is a failure, not a crash of doctor
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        results.append((status, name, detail))
    return results


WORKTREE_MARK = "/.claude/worktrees/"


def location_report(module_file=None):
    """Lines saying which folder stillmove loads from. INFO always; WARN for a worktree path.

    module_file defaults to this file's path; the package folder is its parent's parent. A worktree
    copy is easy to run by mistake (an editable install can point at it), so it is flagged.
    """
    path = os.path.abspath(module_file or __file__)
    folder = os.path.dirname(os.path.dirname(path))
    lines = [("INFO", f"loading from {folder}")]
    if WORKTREE_MARK in folder + "/":
        lines.append(("WARN", "loading from a worktree (.claude/worktrees/...)"))
    return lines


def main():
    failed = 0
    for status, text in location_report():
        print(f"{status} {text}")
    for status, name, detail in run_checks():
        print(f"{status} {name}: {detail}" if status != "PASS" else f"{status} {name}")
        if status == "FAIL":
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
