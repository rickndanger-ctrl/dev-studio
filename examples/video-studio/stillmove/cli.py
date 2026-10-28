"""stillmove command line.

  stillmove render IMAGE (--preset ID | --cam FILE) [--seconds 3 --fps 24] [--backend plane] [--mp4] -o OUTDIR
  stillmove presets                     list camera-motion presets
  stillmove serve [--port 8800]         web page and JSON API on 127.0.0.1
  stillmove doctor                      one PASS, WARN or FAIL line per check

Every error prints one 'error:' line on stderr and exits 1.
"""
import argparse
import sys

from PIL import Image

from stillmove import cameras, limits
from stillmove.depth import get_backend
from stillmove.render import render

DEFAULT_PORT = 8800


def _image_size(path):
    with Image.open(path) as img:
        return img.size


def _catalog_preset(preset_id):
    for preset in _catalog_list():
        if preset.get("id") == preset_id:
            return preset
    raise ValueError(f"unknown camera preset: {preset_id!r}")


def _print_report(report):
    print(f"frames: {report['frames']}")
    print(f"per-frame time: mean {report['per_frame_mean_s']:.3f} s, max {report['per_frame_max_s']:.3f} s")
    print(f"total time: {report['total_s']:.2f} s")
    print(f"depth time: {report['depth_s']:.3f} s")
    print(f"peak memory: {report['peak_memory_mb']:.1f} MB")
    print(f"holes at end frame: {report['holes_end_pct']:.1f} %, max over frames: {report['holes_max_pct']:.1f} %")


def _cmd_render(args):
    if bool(args.preset) == bool(args.cam):
        raise ValueError("give exactly one of --preset or --cam")
    if args.seconds <= 0:
        raise ValueError("--seconds must be greater than 0")
    if args.fps <= 0:
        raise ValueError("--fps must be greater than 0")
    width, height = _image_size(args.image)  # an unreadable image fails before any warning
    if args.preset:
        preset = _catalog_preset(args.preset)
        warning = limits.check_preset(preset)
        if warning and args.strict:
            raise ValueError(warning)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        cams = cameras.from_preset(args.preset, args.seconds, args.fps, width, height)
    else:
        cams = cameras.load(args.cam)
    manifest = render(args.image, cams, get_backend(args.backend), args.out, mp4=args.mp4,
                      report=args.photo_report)
    print(f"wrote {len(manifest['frames'])} frames to {args.out}")
    if args.photo_report:
        _print_report(manifest["report"])
    return 0


def _cmd_presets(args):
    for preset in _catalog_list():
        print(f"{preset['id']}\t{preset.get('name', preset['id'])}\t{limits.safe_range_text(preset)}")
    return 0


def _catalog_list():
    # Make camera_motion importable first (see stillmove/_cm.py), then read the catalog.
    from stillmove import _cm

    _cm.import_camera_motion()
    from camera_motion.presets import load_catalog

    return load_catalog()


def _cmd_serve(args):
    from stillmove.web.server import serve

    return serve(args.port)


def _cmd_doctor(args):
    from stillmove.doctor import main as doctor_main

    return doctor_main()


def _build_parser():
    parser = argparse.ArgumentParser(prog="stillmove", description="Move a still image along a camera path.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="render a still image along a camera path")
    p_render.add_argument("image", help="input image (any format Pillow reads)")
    # Both are optional here; _cmd_render reports giving both or neither as one 'error:' line.
    p_render.add_argument("--preset", metavar="ID", help="camera-motion preset id")
    p_render.add_argument("--cam", metavar="FILE", help="camera file (camera-motion export, json or re10k)")
    p_render.add_argument("--seconds", type=float, default=3.0)
    p_render.add_argument("--fps", type=int, default=24)
    p_render.add_argument("--backend", default="plane", help="depth backend: plane, gradient (fakes) or da2-small (Depth Anything V2 Small, needs onnxruntime)")
    p_render.add_argument("--mp4", action="store_true", help="also write preview.mp4 if ffmpeg is on PATH")
    p_render.add_argument("--strict", action="store_true",
                          help="refuse a preset past its single-photo safe range (exit 1)")
    p_render.add_argument("--photo-report", action="store_true",
                          help="print timings, peak memory and hole shares for the render")
    p_render.add_argument("-o", "--out", required=True, metavar="OUTDIR", help="output folder")
    p_render.set_defaults(handler=_cmd_render)

    p_presets = sub.add_parser("presets", help="list camera-motion presets")
    p_presets.set_defaults(handler=_cmd_presets)

    p_serve = sub.add_parser("serve", help="serve the web page and JSON API on 127.0.0.1")
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_serve.set_defaults(handler=_cmd_serve)

    p_doctor = sub.add_parser("doctor", help="check the install: one PASS, WARN or FAIL line per check")
    p_doctor.set_defaults(handler=_cmd_doctor)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except Exception as err:  # every failure is one line for the user, not a traceback
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
