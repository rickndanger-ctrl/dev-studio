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

from stillmove import warp
from stillmove.depth import check_depth, smooth_depth

PREVIEW = "preview.gif"
MP4 = "preview.mp4"
MANIFEST = "manifest.json"


def _load_image(path, width, height):
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.size != (width, height):
            img = img.resize((width, height), Image.LANCZOS)
        return np.asarray(img, dtype=np.uint8)


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


def render_frames(image, depth, cams, frames_dir, stats=None):
    """Warp every camera frame from frame 0 and save frames_dir/NNNN.png. Returns the frame count.

    depth is the raw backend depth (checked). It is smoothed here, once per image, before any
    frame is warped; this is the one loop shared by stillmove render and shotplan. stats, if
    given, gets one (seconds, hole_share) pair per frame.
    """
    depth = smooth_depth(depth, image)  # removes speckle, keeps depth edges
    os.makedirs(frames_dir, exist_ok=True)
    cam0 = cams.frames[0]
    for i, frame in enumerate(cams.frames):
        started = time.perf_counter()
        out, filled = warp.warp(image, depth, cam0.K, frame.K, warp.relative(cams, i))
        Image.fromarray(out).save(os.path.join(frames_dir, "%04d.png" % i))
        if stats is not None:
            stats.append((time.perf_counter() - started, warp.hole_fraction(filled)))
    return len(cams.frames)


def render(image_path, cams, backend, outdir, mp4=False, report=False):
    """Render image_path along cams with backend's depth. Returns the manifest dict."""
    if not cams.frames:
        raise ValueError("the camera path has no frames")
    started = time.perf_counter()
    image = _load_image(image_path, cams.width, cams.height)
    depth_started = time.perf_counter()
    depth = check_depth(backend.depth(image), image.shape[:2])
    depth_seconds = time.perf_counter() - depth_started

    stats = []
    count = render_frames(image, depth, cams, os.path.join(outdir, "frames"), stats)
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
        "fps": cams.fps,
        "width": cams.width,
        "height": cams.height,
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
