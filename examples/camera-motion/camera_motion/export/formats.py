"""Export camera frames as RealEstate10K text lines or a JSON pose list.

RealEstate10K: a header line '# <name>', then one line per frame:
    <t_us> fx fy cx cy 0 0 <12 numbers: OpenCV w2c 3x4, row-major>
Intrinsics are normalized. The w2c is OpenCV (y down, z forward): the OpenGL w2c with
its y and z rows negated.

JSON: {fps, width, height, frames: [{t, K, c2w, w2c}]} in the OpenGL convention.

Blender: a bpy script that keys a camera. glTF: a glTF 2.0 camera animation (JSON).

CLI: python3 -m camera_motion.export.formats <preset.json> --format re10k|json|blender|gltf [-o out]
     [--fps 24 --seconds 3 --width 1024 --height 576]
"""
import argparse
import json
import sys

from camera_motion.engine.camera_path import path
from camera_motion.export.trajectory import K, intrinsics, pose_matrix, world_to_camera

# OpenGL -> OpenCV camera axes: flip y and z.
_GL_TO_CV_ROW_SIGN = (1.0, -1.0, -1.0)


def _num(value):
    return repr(float(value))


def _opencv_w2c_rows(w2c):
    """Top 3 rows of the OpenGL w2c, with the y and z rows negated (diag(1, -1, -1))."""
    return [[_GL_TO_CV_ROW_SIGN[i] * v for v in w2c[i]] for i in range(3)]


def to_realestate10k(frames, width=1024, height=576, fps=24, name=""):
    """Return RealEstate10K lines: a '# name' header, then one line per frame.

    Timestamps are frame["t"] in microseconds. fps is accepted for the common signature
    and does not change the output.
    """
    lines = [f"# {name}"]
    for frame in frames:
        fx, fy, cx, cy = intrinsics(frame["fov"], width, height)
        t_us = round(frame["t"] * 1e6)
        values = [fx, fy, cx, cy, 0, 0]
        w2c_rows = _opencv_w2c_rows(world_to_camera(frame))
        values += [v for row in w2c_rows for v in row]
        lines.append(" ".join([str(t_us)] + [_num(v) for v in values]))
    return lines


def parse_re10k(lines):
    """Parse RealEstate10K lines into a list of ((fx, fy, cx, cy), w2c 3x4 OpenCV rows).

    Header lines ('#') and blank lines are skipped.
    """
    parsed = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = text.split()
        if len(parts) != 19:
            raise ValueError(f"RealEstate10K line needs 19 fields, got {len(parts)}: {text!r}")
        fx, fy, cx, cy = (float(v) for v in parts[1:5])
        numbers = [float(v) for v in parts[7:19]]
        rows = [numbers[i * 4:(i + 1) * 4] for i in range(3)]
        parsed.append(((fx, fy, cx, cy), rows))
    return parsed


def to_json(frames, width=1024, height=576, fps=24):
    """Return {fps, width, height, frames: [{t, K, c2w, w2c}]} in the OpenGL convention."""
    out = []
    for frame in frames:
        out.append({
            "t": frame["t"],
            "K": K(frame["fov"], width, height),
            "c2w": pose_matrix(frame),
            "w2c": world_to_camera(frame),
        })
    return {"fps": fps, "width": width, "height": height, "frames": out}


FORMATS = ("re10k", "json", "blender", "gltf")


def export_text(fmt, frames, preset, width=1024, height=576, fps=24):
    """Return the text of the export file for fmt, one of FORMATS."""
    name = preset.get("name") or preset.get("id", "")
    if fmt == "re10k":
        return "\n".join(to_realestate10k(frames, width, height, fps, name)) + "\n"
    if fmt == "json":
        return json.dumps(to_json(frames, width, height, fps), indent=2) + "\n"
    if fmt == "blender":
        from camera_motion.export.blender import to_blender
        return to_blender(frames, fps, width, height, name)
    if fmt == "gltf":
        from camera_motion.export.gltf import to_gltf
        return json.dumps(to_gltf(frames, fps, width, height, name)) + "\n"
    raise ValueError(f"unknown export format: {fmt!r}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a camera-motion preset as camera poses.")
    parser.add_argument("preset", help="preset JSON file")
    parser.add_argument("--format", choices=FORMATS, required=True)
    parser.add_argument("-o", "--out", help="output file (default: stdout)")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=576)
    args = parser.parse_args(argv)

    with open(args.preset) as f:
        preset = json.load(f)
    frames = path(preset, duration_s=args.seconds, fps=args.fps)
    text = export_text(args.format, frames, preset, args.width, args.height, args.fps)

    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
