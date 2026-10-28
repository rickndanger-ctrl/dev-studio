"""Render one still image along a camera path.

Writes outdir/frames/NNNN.png (one per camera frame), outdir/preview.gif (loops forever, one
frame per camera frame, 1000/fps ms each), an optional outdir/preview.mp4 (only if ffmpeg is on
PATH) and outdir/manifest.json. Frame 0 uses the identity pose, so it is the input image.

With report=True the manifest also gets a "report" with timings, peak memory and hole shares.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image

from stillmove import fitting, warp
from stillmove.depth import check_depth, smooth_depth
from stillmove.layers import Layers

PREVIEW = "preview.gif"
MP4 = "preview.mp4"
MANIFEST = "manifest.json"


ASPECT_MODES = ("crop", "pad")
ASPECT_TOLERANCE = 0.01  # warn when the picture's aspect differs from the camera's by more than 1%


def load_picture(path, width, height, aspect="crop", center=None):
    """(RGB uint8 array of width x height, aspect record) for the picture at path.

    crop (the default) scales the picture to cover the frame and cuts the overflow from the centre, so
    nothing is stretched. center (x, y) in source pixels moves that cut so the point lands in the middle
    of the frame, clamped to the picture; None means the picture's middle. pad scales it to fit inside
    and fills the border with the picture's edge colour.
    The record is {"mode", "source": [w, h], "target": [w, h]}; one warning goes to stderr when the
    aspects differ by more than ASPECT_TOLERANCE.
    """
    if aspect not in ASPECT_MODES:
        raise ValueError("aspect must be crop or pad")
    with Image.open(path) as img:
        img = img.convert("RGB")
    sw, sh = img.size
    record = {"mode": aspect, "source": [sw, sh], "target": [width, height]}
    if (sw, sh) == (width, height):
        return np.asarray(img, dtype=np.uint8), record
    if abs((sw / sh) / (width / height) - 1.0) > ASPECT_TOLERANCE:
        action = "cropped to fill" if aspect == "crop" else "padded with borders"
        print(f"warning: picture {sw}x{sh} differs in aspect from the camera's {width}x{height}; {action}",
              file=sys.stderr)
    if aspect == "crop":
        scale = max(width / sw, height / sh)
        nw, nh = max(width, round(sw * scale)), max(height, round(sh * scale))
        img = img.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - width) // 2, (nh - height) // 2
        if center is not None:
            cx, cy = center[0] * nw / sw, center[1] * nh / sh
            left = min(max(round(cx - width / 2), 0), nw - width)
            top = min(max(round(cy - height / 2), 0), nh - height)
        img = img.crop((left, top, left + width, top + height))
        return np.asarray(img, dtype=np.uint8), record
    scale = min(width / sw, height / sh)
    nw, nh = min(width, max(1, round(sw * scale))), min(height, max(1, round(sh * scale)))
    small = np.asarray(img.resize((nw, nh), Image.LANCZOS), dtype=np.uint8)
    edge = np.concatenate([small[0], small[-1], small[:, 0], small[:, -1]])
    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:] = np.median(edge, axis=0).round().astype(np.uint8)
    left, top = (width - nw) // 2, (height - nh) // 2
    canvas[top:top + nh, left:left + nw] = small
    return canvas, record


def _load_image(path, width, height, aspect="crop"):
    return load_picture(path, width, height, aspect)[0]


def frame_durations_ms(count, fps):
    """Per-frame GIF delays in ms. GIF stores delays in 10 ms units, so each delay is a multiple of
    10 ms, rounded cumulatively: the total is count / fps seconds to within 10 ms."""
    ticks = [max(1, round((i + 1) * 100.0 / fps) - round(i * 100.0 / fps)) for i in range(count)]
    return [10 * t for t in ticks]


def peak_memory_mb():
    """Peak resident memory of this process in MB. ru_maxrss is bytes on macOS, kilobytes on Linux."""
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def _encode_mp4(outdir, fps):
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", os.path.join(outdir, "frames", "%04d.png"),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # yuv420p needs even sizes
        "-pix_fmt", "yuv420p",
        os.path.join(outdir, MP4),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()}")
    return MP4


def render_frames(image, depth, cams, frames_dir, stats=None, alpha=None, fg_depth=None, fit_margin=None,
                  fit_out=None):
    """Warp every camera frame from frame 0 and save frames_dir/NNNN.png. Returns the frame count.

    depth is the raw backend depth (checked). It is smoothed here, once per image, before any
    frame is warped; this is the one loop shared by stillmove render and shotplan. With alpha (a
    matte, HxW in 0..1), each frame is two layers composited together (stillmove.layers); without
    it, each frame is the single warp. stats, if given, gets one (seconds, hole_share) pair per
    frame (hole_share is 0.0 for layered frames: the clean plate covers the reveals). fit_margin
    shortens the move so the subject stays that far inside the frame (stillmove.fitting); fit_out,
    if given, gets the fit record under "fit" (None without a margin).
    """
    depth = smooth_depth(depth, image)  # removes speckle, keeps depth edges
    os.makedirs(frames_dir, exist_ok=True)
    layered = Layers(image, depth, alpha, fg_depth=fg_depth) if alpha is not None else None
    cams, fit_record = fitting.fitted(cams, depth, alpha=alpha,
                                      fg_depth=layered.near if layered is not None else None,
                                      margin=fit_margin)
    if fit_out is not None:
        fit_out["fit"] = fit_record
    cam0 = cams.frames[0]
    for i, frame in enumerate(cams.frames):
        started = time.perf_counter()
        transform = warp.relative(cams, i)
        if layered is not None:
            out, hole_share = layered.compose(cam0, frame, transform), 0.0
        else:
            out, filled = warp.warp(image, depth, cam0.K, frame.K, transform)
            hole_share = warp.hole_fraction(filled)
        Image.fromarray(out).save(os.path.join(frames_dir, "%04d.png" % i))
        if stats is not None:
            stats.append((time.perf_counter() - started, hole_share))
    return len(cams.frames)


def matte_center(image_path, matte):
    """Centre [x, y] in source pixels of the matte's bounding box (alpha >= 0.5), or None if empty."""
    with Image.open(image_path) as img:
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    mask = np.asarray(matte.alpha(rgb)) >= 0.5
    if not mask.any():
        return None
    rows, cols = np.nonzero(mask)
    return [(cols.min() + cols.max() + 1) / 2.0, (rows.min() + rows.max() + 1) / 2.0]


def render(image_path, cams, backend, outdir, mp4=False, report=False, matte=None, fg_depth=None,
           fit_margin=None, aspect="crop", center=None):
    """Render image_path along cams with backend's depth. Returns the manifest dict.

    With a matte (an object with name and alpha(rgb)), frames are layered (see render_frames).
    fit_margin None renders the camera as given; a margin shortens the move so the subject stays that
    far inside the frame (stillmove.fitting), and the manifest's fit field says by how much.
    aspect is 'crop' (fill the frame, centred) or 'pad' (borders); the manifest's aspect field records it.
    center (x, y) in source pixels is the point a crop keeps in the middle (None: the picture's middle);
    the manifest's crop_center records the point used, in source pixels.
    """
    if not cams.frames:
        raise ValueError("the camera path has no frames")
    started = time.perf_counter()
    image, aspect_record = load_picture(image_path, cams.width, cams.height, aspect, center=center)
    sw, sh = aspect_record["source"]
    crop_center = list(center) if center is not None else [sw / 2.0, sh / 2.0]
    depth_started = time.perf_counter()
    depth = check_depth(backend.depth(image), image.shape[:2])
    depth_seconds = time.perf_counter() - depth_started

    stats = []
    alpha = matte.alpha(image) if matte is not None else None
    fit_out = {}
    count = render_frames(image, depth, cams, os.path.join(outdir, "frames"), stats, alpha=alpha,
                          fg_depth=fg_depth, fit_margin=fit_margin, fit_out=fit_out)
    frame_names = [f"frames/{i:04d}.png" for i in range(count)]
    frame_seconds = [s for s, _ in stats]
    hole_shares = [h for _, h in stats]
    pictures = [Image.open(os.path.join(outdir, name)).convert("RGB") for name in frame_names]

    pictures[0].save(
        os.path.join(outdir, PREVIEW),
        save_all=True,
        append_images=pictures[1:],
        duration=frame_durations_ms(len(pictures), cams.fps),
        loop=0,
    )

    mp4_path, note = None, None
    if mp4:
        if shutil.which("ffmpeg"):
            mp4_path = _encode_mp4(outdir, cams.fps)
        else:
            note = "ffmpeg not found on PATH; mp4 skipped"

    manifest = {
        "frames": frame_names,
        "preview": PREVIEW,
        "mp4": mp4_path,
        "backend": backend.name,
        "matte": matte.name if matte is not None else None,
        "fps": cams.fps,
        "width": cams.width,
        "height": cams.height,
        "fit": fit_out.get("fit"),
        "aspect": aspect_record,
        "crop_center": [float(crop_center[0]), float(crop_center[1])],
    }
    if note:
        manifest["note"] = note
    if report:
        manifest["report"] = {
            "frames": len(frame_names),
            "per_frame_mean_s": sum(frame_seconds) / len(frame_seconds),
            "per_frame_max_s": max(frame_seconds),
            "total_s": time.perf_counter() - started,
            "depth_s": depth_seconds,
            "peak_memory_mb": peak_memory_mb(),
            "holes_end_pct": 100.0 * hole_shares[-1],
            "holes_max_pct": 100.0 * max(hole_shares),
        }
    with open(os.path.join(outdir, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return manifest
