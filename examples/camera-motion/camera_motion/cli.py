"""Command-line entry point for camera-motion.

Usage: python3 -m camera_motion.cli <command> [options]
  list     [--long] [--catalog FILE]              print 'id<TAB>name' per preset; --long adds when/feel lines
  path     <id|file> [--catalog FILE]             print the first and last frame as JSON
  preview  <id|file> -o out.svg [--animate] [--catalog FILE]  render an SVG preview
  export   <id|file> --format re10k|json|blender|gltf -o out [--fps --seconds --width --height]
  serve    [--port 8790]                          serve the web viewer
  sequence <file|sample id> [--export FORMAT | --preview] -o OUT   build a shot sequence

<id|file>: an existing file is loaded as one preset; otherwise the id is looked up in
--catalog, or in the bundled catalog when --catalog is not given.
"""
import argparse
import json
import os
import re
import sys

from camera_motion.engine.camera_path import path as camera_path
from camera_motion.export.formats import FORMATS, export_text
from camera_motion.preview.svg_path import render
from camera_motion.presets import load_help

DEFAULT_PORT = 8790

BUNDLED_CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets", "catalog.json")
SEQUENCE_SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sequences", "samples")
SAMPLE_ID = re.compile(r"[a-z0-9_]+")


class UnknownPreset(Exception):
    pass


def _load_catalog(catalog):
    with open(catalog or BUNDLED_CATALOG, encoding="utf-8") as f:
        return json.load(f)


def _resolve(arg, catalog):
    """Return the preset dict for an existing file, or for an id in the catalog."""
    if os.path.isfile(arg):
        with open(arg, encoding="utf-8") as f:
            return json.load(f)
    presets = _load_catalog(catalog)
    for preset in presets:
        if preset["id"] == arg:
            return preset
    ids = ", ".join(p["id"] for p in presets)
    raise UnknownPreset(f"unknown preset {arg!r}; available ids: {ids}")


def _cmd_list(args):
    help_by_id = load_help() if args.long else {}
    for preset in _load_catalog(args.catalog):
        print(f"{preset['id']}\t{preset.get('name', preset['id'])}")
        info = help_by_id.get(preset["id"], {})
        for key in ("when", "feel"):
            if key in info:
                print(f"  {key}: {info[key]}")
    return 0


def _cmd_path(args):
    frames = camera_path(_resolve(args.preset, args.catalog))
    print(json.dumps({"first": frames[0], "last": frames[-1]}, indent=2))
    return 0


def _ensure_parent(out):
    """Create the folder that will hold the output file, if it is missing."""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)


def _cmd_preview(args):
    preset = _resolve(args.preset, args.catalog)
    frames = camera_path(preset)
    title = preset.get("name", preset.get("id", ""))
    _ensure_parent(args.out)
    if args.animate:
        # Imported here so the static preview does not depend on the animated renderer.
        from camera_motion.preview.anim_svg import render_animated

        render_animated(frames, args.out, title, fps=24)
    else:
        render(frames, args.out, title)
    return 0


def _cmd_export(args):
    preset = _resolve(args.preset, args.catalog)
    frames = camera_path(preset, duration_s=args.seconds, fps=args.fps)
    text = export_text(args.format, frames, preset, args.width, args.height, args.fps)
    _ensure_parent(args.out)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    return 0


def _cmd_serve(args):
    from camera_motion.web.serve import serve

    return serve(args.port)


def _cmd_doctor(args):
    from camera_motion.doctor import main as doctor_main

    return doctor_main()


def _cmd_import(args):
    from camera_motion.export.importers import load

    data = load(args.file)
    frames = data["frames"]
    if args.out:
        _ensure_parent(args.out)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"fps": data["fps"], "frames": frames}, f, indent=2)
            f.write("\n")
    else:
        print(json.dumps({"first": frames[0], "last": frames[-1]}, indent=2))
    return 0


def _cmd_match(args):
    from camera_motion.export.importers import load
    from camera_motion.match import match

    data = load(args.file)
    fov_track = data.get("fov_track", True)
    for preset_id, value in match(data["frames"], fov_track=fov_track):
        print(f"{preset_id}  {value:.4f}")
    if not fov_track:
        print("note: glTF has no zoom track; dolly and dolly_zoom look the same")
    return 0


def _load_sequence(arg):
    """Return the sequence dict for an existing file, or for a bundled sample id."""
    if os.path.isfile(arg):
        path_ = arg
    elif SAMPLE_ID.fullmatch(arg) and os.path.isfile(os.path.join(SEQUENCE_SAMPLES, arg + ".json")):
        path_ = os.path.join(SEQUENCE_SAMPLES, arg + ".json")
    else:
        samples = sorted(name[:-5] for name in os.listdir(SEQUENCE_SAMPLES) if name.endswith(".json"))
        raise ValueError(f"no sequence file or sample {arg!r}; samples: {', '.join(samples)}")
    with open(path_, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as err:
            raise ValueError(f"{path_} is not valid JSON: {err}") from None


def _cmd_sequence(args):
    from camera_motion.sequences.build import build

    if args.export and args.preview:
        print("error: choose one of --export or --preview", file=sys.stderr)
        return 1
    if (args.export or args.preview) and not args.out:
        print("error: -o/--out is required with --export or --preview", file=sys.stderr)
        return 1
    try:
        sequence = _load_sequence(args.sequence)
        frames = build(sequence, _load_catalog(args.catalog))
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    fps = sequence["fps"]
    name = sequence.get("name") or sequence["id"]
    if args.export:
        text = export_text(args.export, frames, {"id": sequence["id"], "name": name}, fps=fps)
        _ensure_parent(args.out)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    elif args.preview:
        from camera_motion.preview.anim_svg import render_animated

        _ensure_parent(args.out)
        render_animated(frames, args.out, name, fps=fps)
    else:
        summary = {"id": sequence["id"], "fps": fps, "frames": len(frames),
                   "first": frames[0], "last": frames[-1]}
        print(json.dumps(summary, indent=2))
    return 0


def _build_parser():
    parser = argparse.ArgumentParser(prog="camera_motion", description="Camera motion presets.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_catalog(p):
        p.add_argument("--catalog", metavar="FILE", help="catalog JSON to look ids up in")

    p_list = sub.add_parser("list", help="list presets in a catalog")
    p_list.add_argument("--long", action="store_true", help="also print when/feel help for each preset")
    add_catalog(p_list)
    p_list.set_defaults(handler=_cmd_list)

    p_path = sub.add_parser("path", help="print the first and last frame of a preset")
    p_path.add_argument("preset", help="preset id or preset JSON file")
    add_catalog(p_path)
    p_path.set_defaults(handler=_cmd_path)

    p_preview = sub.add_parser("preview", help="render an SVG preview of a preset")
    p_preview.add_argument("preset", help="preset id or preset JSON file")
    p_preview.add_argument("-o", "--out", required=True, help="output SVG file")
    p_preview.add_argument("--animate", action="store_true",
                           help="animate the camera along the path (top view, SMIL)")
    add_catalog(p_preview)
    p_preview.set_defaults(handler=_cmd_preview)

    p_export = sub.add_parser("export", help="export camera poses as re10k, json, blender or gltf")
    p_export.add_argument("preset", help="preset id or preset JSON file")
    p_export.add_argument("--format", choices=FORMATS, required=True)
    p_export.add_argument("-o", "--out", required=True, help="output file")
    p_export.add_argument("--fps", type=int, default=24)
    p_export.add_argument("--seconds", type=float, default=3.0)
    p_export.add_argument("--width", type=int, default=1024)
    p_export.add_argument("--height", type=int, default=576)
    add_catalog(p_export)
    p_export.set_defaults(handler=_cmd_export)

    p_serve = sub.add_parser("serve", help="serve the web viewer")
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_serve.set_defaults(handler=_cmd_serve)

    p_doctor = sub.add_parser("doctor", help="check the install: one PASS or FAIL line per check")
    p_doctor.set_defaults(handler=_cmd_doctor)

    p_import = sub.add_parser("import", help="read a re10k, json or glTF camera file")
    p_import.add_argument("file", help="camera file to read")
    p_import.add_argument("-o", "--out", help="write {fps, frames} JSON here instead of printing")
    p_import.set_defaults(handler=_cmd_import)

    p_match = sub.add_parser("match", help="rank catalog presets by how well they match a camera file")
    p_match.add_argument("file", help="camera file to read")
    p_match.set_defaults(handler=_cmd_match)

    p_sequence = sub.add_parser("sequence", help="build a shot sequence from catalog presets")
    p_sequence.add_argument("sequence", help="sequence JSON file or a bundled sample id")
    group = p_sequence.add_mutually_exclusive_group()
    group.add_argument("--export", choices=FORMATS, help="export the frames as re10k, json, blender or gltf")
    group.add_argument("--preview", action="store_true", help="render an animated SVG preview")
    p_sequence.add_argument("-o", "--out", help="output file for --export or --preview")
    add_catalog(p_sequence)
    p_sequence.set_defaults(handler=_cmd_sequence)

    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except UnknownPreset as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except OSError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
