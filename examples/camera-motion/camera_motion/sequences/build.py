"""Build a shot sequence: chain catalog presets into one list of frames.

Each segment is one preset run through engine.camera_path.path(). Its preset
params are the catalog params with the segment's overrides merged on top (a null
override removes that param), and it starts at the previous segment's end pose.
The first frame of every later segment is the same pose as the previous segment's
last frame, so it is dropped. Times run continuously: frame i has t = i / fps across
the whole sequence. The rules come from sequences/validate.py.

Run from camera-motion/:
    python3 -m camera_motion.sequences.build camera_motion/sequences/example.json
    python3 -m camera_motion.sequences.build SEQUENCE.json -o frames.json
"""

import argparse
import json
import os
import sys

from camera_motion.engine.camera_path import path
from camera_motion.presets.validate import load_json
from camera_motion.sequences.validate import index_presets, merged_preset, validate_sequence

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(os.path.dirname(HERE), "presets", "catalog.json")


def _copy_pose(frame):
    return {"position": list(frame["position"]), "rotation": list(frame["rotation"]), "fov": frame["fov"]}


def build(sequence, catalog):
    """Return the sequence's frames: a list of {t, position, rotation, fov}.

    Raises ValueError, listing every error, if validate_sequence() finds any.
    """
    errors = validate_sequence(sequence, catalog)
    if errors:
        raise ValueError("; ".join(errors))

    presets_by_id = index_presets(catalog)
    fps = sequence["fps"]
    frames = []
    start = None
    for index, seg in enumerate(sequence["segments"]):
        preset = merged_preset(presets_by_id[seg["preset"]], seg.get("params", {}))
        seg_frames = path(preset, duration_s=seg["duration_s"], fps=fps, start=start)
        if index > 0:
            seg_frames = seg_frames[1:]  # same pose as the previous segment's last frame
        frames.extend(seg_frames)
        start = _copy_pose(seg_frames[-1])

    for i, frame in enumerate(frames):
        frame["t"] = i / fps
    return frames


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a camera shot sequence from catalog presets.")
    parser.add_argument("sequence", help="sequence JSON file")
    parser.add_argument("--catalog", default=CATALOG_PATH, help="preset catalog (default: presets/catalog.json)")
    parser.add_argument("-o", "--out", help="also write the frames to this JSON file")
    args = parser.parse_args(argv)

    try:
        sequence = load_json(args.sequence)
        catalog = load_json(args.catalog)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    try:
        frames = build(sequence, catalog)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    fps = sequence["fps"]
    print("sequence %s: %d segment(s), %d frame(s), %.3f s at %d fps"
          % (sequence["id"], len(sequence["segments"]), len(frames), frames[-1]["t"], fps))
    print("start pose: position %s rotation %s fov %s"
          % (frames[0]["position"], frames[0]["rotation"], frames[0]["fov"]))
    print("end pose:   position %s rotation %s fov %s"
          % (frames[-1]["position"], frames[-1]["rotation"], frames[-1]["fov"]))

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(frames, handle, indent=2)
            handle.write("\n")
        print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
