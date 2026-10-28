"""stillmove command line.

  stillmove render IMAGE (--preset ID | --cam FILE) [--seconds 3 --fps 24] [--backend plane] [--mp4]
                   [--layers [--matte fake|isnet]] -o OUTDIR
  stillmove mesh IMAGE (--preset ID | --cam FILE) [--depth fake|gradient|FILE.png] [--grid 256x144] -o OUTDIR
                                        a textured grid mesh (OBJ, Blender coordinates) and scene.py
  stillmove presets                     list camera-motion presets
  stillmove serve [--port 8800]         web page and JSON API on 127.0.0.1
  stillmove doctor                      one PASS, WARN or FAIL line per check

Every error prints one 'error:' line on stderr and exits 1.
"""
import argparse
import os
import sys

from PIL import Image

from stillmove import cameras, fitting, limits
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


def _fit_margin(args):
    """None with --no-fit (the camera is used as given), else the checked --fit-margin."""
    return None if args.no_fit else fitting.check_margin(args.fit_margin)


def _print_fit_note(record):
    if record and record.get("note"):
        print(f"note: {record['note']}", file=sys.stderr)


def _upscale_kwargs(args):
    return {"upscale": args.upscale, "upscale_backend": args.upscale_backend,
            "keep_colours": getattr(args, "keep_colours", True)}


def _render_shape(args, cams, out, matte, center):
    """One shape of --sizes: a plain render, or an interpolated one with --smooth or --slowmo (its manifest
    gets an interp block, as for a single render)."""
    backend = get_backend(args.backend)
    if args.smooth or args.slowmo:
        from stillmove import interp

        factor, mode = (args.smooth, "smooth") if args.smooth else (args.slowmo, "slowmo")
        return interp.render_interpolated(args.image, cams, backend, out, factor, mode,
                                          interp_backend=args.interp_backend, mp4=args.mp4, matte=matte,
                                          fg_depth=args.fg_depth, fit_margin=_fit_margin(args),
                                          aspect=args.aspect, center=center, **_upscale_kwargs(args))
    return render(args.image, cams, backend, out, mp4=args.mp4, matte=matte, fg_depth=args.fg_depth,
                  fit_margin=_fit_margin(args), aspect=args.aspect, center=center, **_upscale_kwargs(args))


def _render_sizes(args):
    """stillmove render --sizes: the same move cropped to each shape, into OUT/<id>/, zipped to OUT/sizes.zip."""
    import zipfile

    from stillmove import sizes
    from stillmove.matte import get_matte
    from stillmove.render import PREVIEW, matte_center

    ids = [s.strip() for s in args.sizes.split(",") if s.strip()]
    unknown = [s for s in ids if s not in sizes.SIZES]
    if not ids or unknown:
        raise ValueError("--sizes takes %s (got %s)" % (", ".join(sizes.SIZES), args.sizes))
    if args.preset:
        warning = limits.check_preset(_catalog_preset(args.preset))
        if warning and args.strict:
            raise ValueError(warning)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        base = None
    else:
        base = cameras.load(args.cam)  # the file's amount and timing; each shape keeps its poses and fov
    matte = get_matte(args.matte) if args.layers else None
    center = matte_center(args.image, matte) if matte is not None else None
    os.makedirs(args.out, exist_ok=True)
    zip_path = os.path.join(args.out, "sizes.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for sid in ids:
            width, height = sizes.SIZES[sid]
            if base is None:
                cams = cameras.from_preset(args.preset, args.seconds, args.fps, width, height)
            else:
                cams = cameras.retarget(base, width, height)
            out = os.path.join(args.out, sid)
            manifest = _render_shape(args, cams, out, matte, center)
            preview = manifest["mp4"] or os.path.join(out, PREVIEW)
            ext = os.path.splitext(preview)[1].lstrip(".")
            archive.write(os.path.join(out, os.path.basename(preview)), arcname=f"{sid}.{ext}")
            print(f"wrote {len(manifest['frames'])} frames to {out}")
            _print_fit_note(manifest["fit"])
    print(f"wrote {zip_path}")
    return 0


def _cmd_render(args):
    if bool(args.preset) == bool(args.cam):
        raise ValueError("give exactly one of --preset or --cam")
    if args.seconds <= 0:
        raise ValueError("--seconds must be greater than 0")
    if args.fps <= 0:
        raise ValueError("--fps must be greater than 0")
    if args.smooth and args.slowmo:
        raise ValueError("give --smooth or --slowmo, not both")
    if args.sizes:
        return _render_sizes(args)
    width, height = _image_size(args.image)  # an unreadable image fails before any warning
    if (args.width is None) != (args.height is None):
        raise ValueError("give both --width and --height, or neither")
    if args.width is not None:
        if args.width < 1 or args.height < 1:
            raise ValueError("--width and --height must be at least 1")
        width, height = args.width, args.height  # the picture is cropped or padded to this frame
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
        # Without --width/--height a camera file keeps its own frame size.
        if args.width is not None and (cams.width, cams.height) != (width, height):
            cams = cameras.retarget(cams, width, height)
    if args.fg_depth is not None and not args.layers:
        raise ValueError("--fg-depth needs --layers")
    matte = None
    if args.layers:
        from stillmove.matte import get_matte

        matte = get_matte(args.matte)
    if args.smooth or args.slowmo:
        from stillmove import interp

        factor, mode = (args.smooth, "smooth") if args.smooth else (args.slowmo, "slowmo")
        manifest = interp.render_interpolated(args.image, cams, get_backend(args.backend), args.out, factor,
                                              mode, interp_backend=args.interp_backend, mp4=args.mp4,
                                              matte=matte, fg_depth=args.fg_depth, fit_margin=_fit_margin(args),
                                              aspect=args.aspect, **_upscale_kwargs(args))
    else:
        manifest = render(args.image, cams, get_backend(args.backend), args.out, mp4=args.mp4,
                          report=args.photo_report, matte=matte, fg_depth=args.fg_depth,
                          fit_margin=_fit_margin(args), aspect=args.aspect, **_upscale_kwargs(args))
    print(f"wrote {len(manifest['frames'])} frames to {args.out}")
    _print_fit_note(manifest["fit"])
    if manifest.get("note"):
        print(f"note: {manifest['note']}", file=sys.stderr)
    if args.photo_report:
        _print_report(manifest["report"])
    return 0


def _cmd_upscale(args):
    from stillmove import upscale

    manifest = upscale.upscale_path(args.src, args.scale, args.out, backend_name=args.backend, fps=args.fps,
                                    mp4=not args.no_mp4, keep_colours=args.keep_colours)
    if manifest["kind"] == "frames":
        print(f"wrote {len(manifest['frames'])} frames to {args.out}")
    else:
        print(f"wrote {args.out}/upscaled.png ({manifest['output'][0]}x{manifest['output'][1]})")
    return 0


def _cmd_interp(args):
    from stillmove import interp

    manifest = interp.interp_folder(args.frames, args.factor, args.out, backend_name=args.backend, fps=args.fps,
                                    mp4=not args.no_mp4)
    print(f"wrote {len(manifest['frames'])} frames to {args.out}")
    return 0


def _cmd_mesh(args):
    from stillmove import mesh

    if bool(args.preset) == bool(args.cam):
        raise ValueError("give exactly one of --preset or --cam")
    if args.seconds <= 0 or args.fps <= 0:
        raise ValueError("--seconds and --fps must be greater than 0")
    if args.preset:
        width, height = _image_size(args.image)
        cams = cameras.from_preset(args.preset, args.seconds, args.fps, width, height)
    else:
        cams = cameras.load(args.cam)
    grid = mesh.resolve_grid(args.grid, cams.width, cams.height)
    matte = None
    if args.matte:
        from stillmove.matte import get_matte

        matte = get_matte(args.matte)  # isnet needs its model; a missing model is one error line
    summary = mesh.write_mesh(args.image, cams, args.depth, grid, args.out, near=args.near, far=args.far,
                              blender_preview=args.blender_preview, matte=matte,
                              fit_margin=_fit_margin(args))
    print(f"wrote {summary['vertices']} vertices and {summary['faces']} faces to {args.out}")
    _print_fit_note(summary["fit"])
    if args.blender_preview:
        from stillmove import blender_preview

        video = blender_preview.run_preview(args.out)
        if video:
            print(f"wrote {video}")
    return 0


def _cmd_matte(args):
    import numpy as np

    from stillmove import matte

    backend = matte.get_matte(args.backend)
    with Image.open(args.image) as img:
        rgb = np.asarray(img.convert("RGB"))
    mask = matte.to_8bit(backend.alpha(rgb))
    Image.fromarray(mask).save(args.out)
    print(f"wrote {args.out} ({mask.shape[1]}x{mask.shape[0]}, 8-bit, {args.backend} matte)")
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
    p_render.add_argument("--cam", metavar="FILE", help="camera file (camera-motion export, json)")
    p_render.add_argument("--seconds", type=float, default=3.0)
    p_render.add_argument("--width", type=int, default=None,
                          help="output width in pixels (give with --height; default: the picture's size)")
    p_render.add_argument("--height", type=int, default=None,
                          help="output height in pixels (give with --width; default: the picture's size)")
    p_render.add_argument("--fps", type=int, default=24)
    p_render.add_argument("--backend", default="plane", help="depth backend: plane, gradient (fakes) or da2-small (Depth Anything V2 Small, needs onnxruntime)")
    p_render.add_argument("--mp4", action="store_true", help="also write preview.mp4 if ffmpeg is on PATH")
    p_render.add_argument("--smooth", type=int, choices=(2, 4), metavar="N",
                          help="render at fps / N, then interpolate N times: the same duration, smoother motion")
    p_render.add_argument("--slowmo", type=int, choices=(2, 4), metavar="N",
                          help="render at fps, then interpolate N times: the same fps, so it plays N times longer")
    p_render.add_argument("--upscale", type=int, choices=(2, 4), metavar="N",
                          help="enlarge the picture N times before the move (the frames are N times bigger)")
    p_render.add_argument("--upscale-backend", default="lanczos", choices=("lanczos", "esrgan"),
                          help="upscaler for --upscale: lanczos (no model) or esrgan (needs its model)")
    p_render.add_argument("--no-keep-colours", dest="keep_colours", action="store_false",
                          help="with esrgan, take the colour from the learned picture (default: keep the source's colour)")
    p_render.add_argument("--interp-backend", default="linear", choices=("linear", "rife"),
                          help="frame interpolation for --smooth and --slowmo: linear (a blend) or rife (midpoint model, 2x and 4x)")
    p_render.add_argument("--aspect", choices=("crop", "pad"), default="crop",
                          help="a picture whose shape differs from the camera's: crop to fill the frame (default) "
                               "or pad it with borders")
    p_render.add_argument("--strict", action="store_true",
                          help="refuse a preset past its single-photo safe range (exit 1)")
    p_render.add_argument("--photo-report", action="store_true",
                          help="print timings, peak memory and hole shares for the render")
    p_render.add_argument("--layers", action="store_true",
                          help="Move subject and background separately so the subject floats, then merge them")
    p_render.add_argument("--matte", default="fake", choices=("fake", "isnet"),
                          help="matte for --layers: fake (a centred ellipse) or isnet")
    p_render.add_argument("--fg-depth", type=float, default=None, metavar="D",
                          help="with --layers: put the foreground plane at depth D instead of the 10th percentile")
    p_render.add_argument("--no-fit", action="store_true",
                          help="Disable auto-shortening of the camera move that keeps the subject off the frame edges")
    p_render.add_argument("--fit-margin", type=float, default=fitting.DEFAULT_MARGIN, metavar="F",
                          help="keep the subject at least F of the frame inside, shortening the move if needed "
                               "(0 < F < 0.4, default 0.06)")
    p_render.add_argument("--sizes", metavar="IDS",
                          help="comma list of square,wide,vertical: render the move once per shape into OUTDIR/<id>/ "
                               "and zip the previews to OUTDIR/sizes.zip")
    p_render.add_argument("-o", "--out", required=True, metavar="OUTDIR", help="output folder")
    p_render.set_defaults(handler=_cmd_render)

    p_mesh = sub.add_parser("mesh", help="Build a 3D surface for Blender from a photo; with --matte, the cut-out subject in front of a flat backdrop.")
    p_mesh.add_argument("image", help="input image (any format Pillow reads)")
    p_mesh.add_argument("--depth", default="fake", help="fake (plane), plane, gradient, da2-small, or a grayscale .png (white near, black far); smoothed like render")
    p_mesh.add_argument("--preset", metavar="ID", help="camera-motion preset id")
    p_mesh.add_argument("--cam", metavar="FILE", help="camera file (camera-motion export, json)")
    p_mesh.add_argument("--seconds", type=float, default=3.0)
    p_mesh.add_argument("--fps", type=int, default=24)
    p_mesh.add_argument("--grid", default="auto",
                        help="vertices across by down: auto (long side 256, short side from the image) or WxH, e.g. 32x18")
    p_mesh.add_argument("--matte", choices=("fake", "isnet"), default=None,
                        help="split the picture into a 'foreground' object (the matte) and a clean 'backdrop'")
    p_mesh.add_argument("--blender-preview", action="store_true",
                        help="also write preview_render.py and, if Blender is found, render blender-preview.mp4")
    p_mesh.add_argument("--near", type=float, default=3.0, help="depth of white pixels in a .png depth")
    p_mesh.add_argument("--far", type=float, default=8.0, help="depth of black pixels in a .png depth")
    p_mesh.add_argument("--no-fit", action="store_true",
                        help="Disable auto-shortening of the camera move that keeps the subject off the frame edges")
    p_mesh.add_argument("--fit-margin", type=float, default=fitting.DEFAULT_MARGIN, metavar="F",
                        help="keep the subject at least F of the frame inside (0 < F < 0.4, default 0.06)")
    p_mesh.add_argument("-o", "--out", required=True, metavar="OUTDIR", help="output folder")
    p_mesh.set_defaults(handler=_cmd_mesh)

    p_matte = sub.add_parser("matte", help="Extract the main subject from a photo and write a soft-edged black-and-white mask.")
    p_matte.add_argument("image", help="input image (any format Pillow reads)")
    p_matte.add_argument("--backend", default="fake", help="fake (a soft ellipse, no model) or isnet (IS-Net, onnxruntime)")
    p_matte.add_argument("-o", "--out", required=True, metavar="MASK.PNG", help="output mask file")
    p_matte.set_defaults(handler=_cmd_matte)

    p_upscale = sub.add_parser("upscale", help="enlarge a picture, or a folder of frames, 2x or 4x")
    p_upscale.add_argument("src", metavar="IMAGE_OR_FRAMES_DIR", help="a picture, or a folder of NNNN.png frames")
    p_upscale.add_argument("--scale", type=int, choices=(2, 4), default=2, help="enlargement (default 2)")
    p_upscale.add_argument("--backend", default="lanczos", choices=("lanczos", "esrgan"),
                           help="lanczos (no model) or esrgan (needs its model)")
    p_upscale.add_argument("--no-keep-colours", dest="keep_colours", action="store_false",
                           help="with esrgan, take the colour from the learned picture (default: keep the source's colour)")
    p_upscale.add_argument("--fps", type=int, default=24, help="fps for the frames' preview.mp4 (default 24)")
    p_upscale.add_argument("--no-mp4", action="store_true",
                           help="do not write preview.mp4 for a folder (it is written whenever ffmpeg is on PATH)")
    p_upscale.add_argument("-o", "--out", required=True, metavar="OUTDIR", help="output folder")
    p_upscale.set_defaults(handler=_cmd_upscale)
    p_interp = sub.add_parser("interp", help="interpolate a folder of frames: more in-between frames")
    p_interp.add_argument("frames", metavar="FRAMES_DIR", help="folder of NNNN.png frames, in order")
    p_interp.add_argument("--factor", type=int, choices=(2, 4), default=2,
                          help="how many steps between each pair of frames (default 2)")
    p_interp.add_argument("--backend", default="linear", choices=("linear", "rife"),
                          help="linear (a blend, no model) or rife (midpoint model, 2x and 4x)")
    p_interp.add_argument("--fps", type=int, default=24, help="fps written to the preview and manifest (default 24)")
    p_interp.add_argument("--no-mp4", action="store_true",
                          help="do not write preview.mp4 (it is written whenever ffmpeg is on PATH)")
    p_interp.add_argument("-o", "--out", required=True, metavar="OUTDIR", help="output folder")
    p_interp.set_defaults(handler=_cmd_interp)
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
