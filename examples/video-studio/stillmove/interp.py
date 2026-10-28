"""Frame interpolation: in-between frames for a rendered move.

An InterpBackend makes the picture between two frames at a fraction t in (0, 1). 'linear' is a fake that
blends pixels, so tests need no model. 'rife' is the slot for a learned model; until one is installed it
says so instead of guessing.

n frames interpolated by a factor f give (n - 1) * f + 1 frames: every original frame is kept, and f - 1
in-between frames go after each pair.

stillmove interp FRAMES --factor 2|4 -o OUT interpolates a folder of frames.
stillmove render --smooth N renders at fps / N and interpolates up: the same duration, smoother motion.
stillmove render --slowmo N renders at fps and interpolates: the same fps, so the clip plays N times longer.
"""
import json
import os
import re
import shutil
import time

import numpy as np
from PIL import Image

from stillmove import cameras
from stillmove.render import MANIFEST, MP4, PREVIEW, _encode_mp4, frame_durations_ms, render

FACTORS = (2, 4)
FRAME_NAME = re.compile(r"^\d{4}\.png$")


class InterpBackend:
    name = "base"

    def between(self, a, b, t):
        """The picture at fraction t (0 < t < 1) between uint8 HxWx3 frames a and b."""
        raise NotImplementedError


class LinearBackend(InterpBackend):
    """The fake: a plain blend of the two pictures. No model."""

    name = "linear"

    def between(self, a, b, t):
        blend = a.astype(np.float32) * (1.0 - t) + b.astype(np.float32) * t
        return np.clip(np.rint(blend), 0, 255).astype(np.uint8)


class RifeBackend(InterpBackend):
    """RIFE, midpoint model (stillmove.backends.rife): t = 0.5, and 0.25 and 0.75 from it. Other t are refused."""

    name = "rife"

    def __init__(self):
        self._impl = None

    def between(self, a, b, t):
        if self._impl is None:
            from stillmove.backends.rife import RifeMidpoint

            self._impl = RifeMidpoint()
        return self._impl.between(a, b, t)


BACKENDS = {"linear": LinearBackend, "rife": RifeBackend}


def get_interp(name):
    if name not in BACKENDS:
        raise ValueError("unknown interpolation backend %r; choose from %s" % (name, ", ".join(BACKENDS)))
    return BACKENDS[name]()


def interpolate(frames, factor, backend):
    """The frames with factor - 1 in-between frames after each pair: (n - 1) * factor + 1 in all."""
    if factor < 2:
        raise ValueError("the interpolation factor must be 2 or more")
    if len(frames) < 2:
        raise ValueError("interpolation needs at least two frames")
    out = []
    for i in range(len(frames) - 1):
        out.append(frames[i])
        for k in range(1, factor):
            out.append(backend.between(frames[i], frames[i + 1], k / factor))
    out.append(frames[-1])
    return out


def read_frames(frames_dir):
    """The PNG frames in a folder, in name order (0000.png, 0001.png, ...)."""
    names = sorted(n for n in os.listdir(frames_dir) if FRAME_NAME.match(n))
    if len(names) < 2:
        raise ValueError("%s has fewer than two frames" % frames_dir)
    return [np.asarray(Image.open(os.path.join(frames_dir, n)).convert("RGB"), dtype=np.uint8) for n in names]


def write_frames(frames, outdir):
    """Write the frames as outdir/frames/NNNN.png and return their manifest names."""
    os.makedirs(os.path.join(outdir, "frames"), exist_ok=True)
    names = []
    for i, frame in enumerate(frames):
        name = "frames/%04d.png" % i
        Image.fromarray(frame).save(os.path.join(outdir, name))
        names.append(name)
    return names


def write_preview(outdir, names, fps):
    pictures = [Image.open(os.path.join(outdir, name)).convert("RGB") for name in names]
    pictures[0].save(os.path.join(outdir, PREVIEW), save_all=True, append_images=pictures[1:],
                     duration=frame_durations_ms(len(pictures), fps), loop=0)


def _write_manifest(outdir, manifest):
    with open(os.path.join(outdir, MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")


def interp_folder(frames_dir, factor, outdir, backend_name="linear", fps=24, mp4=True):
    """stillmove interp: read frames_dir, write the interpolated frames, a preview and a manifest.

    By default it also writes preview.mp4 whenever ffmpeg is on PATH (mp4=False skips it). Without ffmpeg
    there is no error: mp4 is null and the manifest's note says why."""
    backend = get_interp(backend_name)
    started = time.perf_counter()
    frames = read_frames(frames_dir)
    out = interpolate(frames, factor, backend)
    names = write_frames(out, outdir)
    write_preview(outdir, names, fps)
    seconds = time.perf_counter() - started
    mp4_path, note = None, None
    if mp4:
        if shutil.which("ffmpeg"):
            mp4_path = _encode_mp4(outdir, fps)
        else:
            note = "ffmpeg not found on PATH; mp4 skipped"
    manifest = {
        "frames": names,
        "preview": PREVIEW,
        "mp4": mp4_path,
        "fps": fps,
        "interp": {"factor": factor, "mode": "interp", "backend": backend.name,
                   "input_frames": len(frames), "output_frames": len(out),
                   "timing": {"interp_s": seconds}},
    }
    if note:
        manifest["note"] = note
    _write_manifest(outdir, manifest)
    return manifest


def _smooth_cameras(cams, factor):
    """The camera at fps / factor: every factor-th frame. The move keeps its duration exactly."""
    if cams.fps % factor:
        raise ValueError("--smooth %d needs --fps to be a multiple of %d" % (factor, factor))
    if (len(cams.frames) - 1) % factor:
        raise ValueError("--smooth %d needs seconds times fps to be a multiple of %d" % (factor, factor))
    return cameras.Cameras(fps=cams.fps // factor, width=cams.width, height=cams.height,
                           frames=cams.frames[::factor])


def render_interpolated(image_path, cams, backend, outdir, factor, mode, interp_backend="linear",
                        mp4=False, **render_kwargs):
    """Render a move and interpolate it by factor. mode 'smooth' renders at fps / factor first; 'slowmo'
    renders at fps. Both write frames and a preview at the camera's fps, so smooth keeps the duration and
    slowmo stretches it. The manifest records the factor, mode, both fps values and the timing."""
    if mode not in ("smooth", "slowmo"):
        raise ValueError("mode must be smooth or slowmo")
    if factor not in FACTORS:
        raise ValueError("the factor must be 2 or 4")
    interp = get_interp(interp_backend)
    low = _smooth_cameras(cams, factor) if mode == "smooth" else cams
    scratch = os.path.join(outdir, "_rendered")
    started = time.perf_counter()
    rendered = render(image_path, low, backend, scratch, mp4=False, **render_kwargs)
    render_s = time.perf_counter() - started
    started = time.perf_counter()
    frames = read_frames(os.path.join(scratch, "frames"))
    out = interpolate(frames, factor, interp)
    names = write_frames(out, outdir)
    write_preview(outdir, names, cams.fps)
    interp_s = time.perf_counter() - started
    shutil.rmtree(scratch, ignore_errors=True)
    mp4_path, note = None, None
    if mp4:
        if shutil.which("ffmpeg"):
            mp4_path = _encode_mp4(outdir, cams.fps)
        else:
            note = "ffmpeg not found on PATH; mp4 skipped"
    manifest = {
        "frames": names,
        "preview": PREVIEW,
        "mp4": mp4_path,
        "backend": rendered["backend"],
        "matte": rendered["matte"],
        "fps": cams.fps,
        "width": cams.width,
        "height": cams.height,
        "fit": rendered["fit"],
        "aspect": rendered["aspect"],
        "interp": {"factor": factor, "mode": mode, "backend": interp.name,
                   "rendered_fps": low.fps, "output_fps": cams.fps,
                   "rendered_frames": len(frames), "output_frames": len(out),
                   "timing": {"render_s": render_s, "interp_s": interp_s}},
    }
    if note:
        manifest["note"] = note
    _write_manifest(outdir, manifest)
    return manifest
