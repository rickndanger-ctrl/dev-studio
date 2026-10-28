"""Upscaling: a bigger picture from a still, or from a folder of frames.

An UpscaleBackend makes a larger uint8 HxWx3 picture from a smaller one. 'lanczos' is the fake: Pillow's
Lanczos resize, no model. 'esrgan' is the slot for a learned model; until one is installed it says so.

tiled_map runs any per-picture function over tiles, each with an overlap margin, so a large picture needs
only a tile's worth of working memory at a time. The seams are either blended (linear ramps across the
margins, normalised) or cropped (each tile keeps only its own core).
"""
import json
import os
import re
import shutil
import time

import numpy as np
from PIL import Image

from stillmove.render import MANIFEST, _encode_mp4

SCALES = (2, 4)
SEAMS = ("blend", "crop")
DEFAULT_TILE = 256
DEFAULT_OVERLAP = 16
FRAME_NAME = re.compile(r"^\d{4}\.png$")


class UpscaleBackend:
    name = "base"

    def upscale(self, img, scale):
        """A picture scale times larger than the uint8 HxWx3 img."""
        raise NotImplementedError


class LanczosBackend(UpscaleBackend):
    """The fake: Pillow's Lanczos resize. No model."""

    name = "lanczos"

    def upscale(self, img, scale):
        h, w = img.shape[:2]
        return np.asarray(Image.fromarray(img).resize((w * scale, h * scale), Image.LANCZOS), dtype=np.uint8)


class EsrganBackend(UpscaleBackend):
    """Real-ESRGAN x4 (general v3) from stillmove.backends.esrgan: SHA-checked, run tile by tile with
    tiled_map (tile 256, overlap 16, blended seams). Without the model it raises an error saying where
    the model should go; scale 2 is the network's x4 reduced by Lanczos."""

    name = "esrgan"

    def __init__(self, model=None, keep_colours=True):
        if model is None:
            from stillmove.backends.esrgan import EsrganGeneral

            model = EsrganGeneral()
        self.model = model
        self.keep_colours = keep_colours

    def upscale(self, img, scale):
        big = self.model.upscale(img, scale)
        if not self.keep_colours:
            return big
        from stillmove import colorkeep

        # luminance: the learned picture under one global tone curve, clamped to the Lanczos range; colour:
        # Lanczos, matched to the source
        return colorkeep.keep_colours(big, LanczosBackend().upscale(img, scale), img, radius=scale)


BACKENDS = {"lanczos": LanczosBackend, "esrgan": EsrganBackend}


def get_upscaler(name, keep_colours=True):
    if name not in BACKENDS:
        raise ValueError("unknown upscale backend %r; choose from %s" % (name, ", ".join(BACKENDS)))
    if name == "esrgan":
        return EsrganBackend(keep_colours=keep_colours)
    return BACKENDS[name]()


def _axis_weights(length, core_lo, core_hi, low_margin, high_margin):
    """Weights along one axis of a window (in output pixels).

    1 over the core. A margin that borders another tile (low_margin / high_margin rows, both 0 at the
    picture's edge) ramps linearly from just above 0 at the window's edge up to 1 at the core. Nothing is 0,
    so a window's own edge never leaves a hole in the sum."""
    w = np.ones(length)
    if low_margin:
        w[:core_lo] = (np.arange(core_lo) + 1.0) / (core_lo + 1.0)
    if high_margin:
        w[core_hi:] = (np.arange(length - core_hi, 0, -1)) / (length - core_hi + 1.0)
    return w


def tiled_map(img, scale, fn, tile=DEFAULT_TILE, overlap=DEFAULT_OVERLAP, seam="blend"):
    """Apply fn(window, scale) to tiles of img and join the results.

    fn must return a picture scale times larger than the window it is given. Each tile is cut with a margin
    of `overlap` input pixels on every side that is not the picture's edge. seam 'crop' keeps each tile's own
    core; 'blend' ramps the weights across the margins and normalises the sum.
    """
    if seam not in SEAMS:
        raise ValueError("seam must be blend or crop")
    if tile < 1 or overlap < 0 or overlap >= tile:
        raise ValueError("need tile >= 1 and 0 <= overlap < tile")
    height, width, channels = img.shape
    out_h, out_w = height * scale, width * scale
    if seam == "crop":
        out = np.zeros((out_h, out_w, channels), dtype=np.uint8)
    else:
        acc = np.zeros((out_h, out_w, channels), dtype=np.float64)
        wsum = np.zeros((out_h, out_w, 1), dtype=np.float64)
    for y0 in range(0, height, tile):
        for x0 in range(0, width, tile):
            y1, x1 = min(height, y0 + tile), min(width, x0 + tile)
            wy0, wy1 = max(0, y0 - overlap), min(height, y1 + overlap)
            wx0, wx1 = max(0, x0 - overlap), min(width, x1 + overlap)
            up = np.asarray(fn(img[wy0:wy1, wx0:wx1], scale))
            expect = ((wy1 - wy0) * scale, (wx1 - wx0) * scale, channels)
            if up.shape != expect:
                raise ValueError("fn returned %s for a window that needs %s" % (up.shape, expect))
            core_y = ((y0 - wy0) * scale, (y1 - wy0) * scale)
            core_x = ((x0 - wx0) * scale, (x1 - wx0) * scale)
            if seam == "crop":
                out[y0 * scale:y1 * scale, x0 * scale:x1 * scale] = up[
                    core_y[0]:core_y[1], core_x[0]:core_x[1]]
                continue
            ramp_y = _axis_weights(up.shape[0], core_y[0], core_y[1], wy0 > 0, wy1 < height)
            ramp_x = _axis_weights(up.shape[1], core_x[0], core_x[1], wx0 > 0, wx1 < width)
            weight = (ramp_y[:, None] * ramp_x[None, :])[..., None]
            acc[wy0 * scale:wy1 * scale, wx0 * scale:wx1 * scale] += up * weight
            wsum[wy0 * scale:wy1 * scale, wx0 * scale:wx1 * scale] += weight
    if seam == "crop":
        return out
    return np.clip(np.rint(acc / wsum), 0, 255).astype(np.uint8)


def upscale_image(img, scale, backend, tile=DEFAULT_TILE, overlap=DEFAULT_OVERLAP, seam="blend"):
    if scale not in SCALES:
        raise ValueError("the upscale factor must be 2 or 4")
    return tiled_map(img, scale, backend.upscale, tile=tile, overlap=overlap, seam=seam)


def _write_manifest(outdir, manifest):
    with open(os.path.join(outdir, MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")


def upscale_path(src, scale, outdir, backend_name="lanczos", fps=24, mp4=True, tile=DEFAULT_TILE,
                 overlap=DEFAULT_OVERLAP, seam="blend", keep_colours=True):
    """stillmove upscale: a picture becomes outdir/upscaled.png; a folder of NNNN.png frames becomes
    outdir/frames/NNNN.png, with preview.mp4 when ffmpeg is on PATH (mp4=False skips it).
    keep_colours (esrgan only) takes the colour from the Lanczos picture; the manifest records the flag."""
    backend = get_upscaler(backend_name, keep_colours=keep_colours)
    started = time.perf_counter()
    os.makedirs(outdir, exist_ok=True)
    options = {"scale": scale, "backend": backend.name, "tile": tile, "overlap": overlap, "seam": seam,
               "keep_colours": keep_colours}
    if os.path.isdir(src):
        names = sorted(n for n in os.listdir(src) if FRAME_NAME.match(n))
        if not names:
            raise ValueError("%s has no NNNN.png frames" % src)
        os.makedirs(os.path.join(outdir, "frames"), exist_ok=True)
        out_names, size = [], None
        for i, name in enumerate(names):
            img = np.asarray(Image.open(os.path.join(src, name)).convert("RGB"), dtype=np.uint8)
            big = upscale_image(img, scale, backend, tile=tile, overlap=overlap, seam=seam)
            rel = "frames/%04d.png" % i
            Image.fromarray(big).save(os.path.join(outdir, rel))
            out_names.append(rel)
            size = [img.shape[1], img.shape[0]]
        mp4_path, note = None, None
        if mp4:
            if shutil.which("ffmpeg"):
                mp4_path = _encode_mp4(outdir, fps)
            else:
                note = "ffmpeg not found on PATH; mp4 skipped"
        manifest = {"kind": "frames", "frames": out_names, "mp4": mp4_path, "fps": fps,
                    "input": size, "output": [size[0] * scale, size[1] * scale], "upscale": options,
                    "timing": {"upscale_s": time.perf_counter() - started}}
        if note:
            manifest["note"] = note
    else:
        img = np.asarray(Image.open(src).convert("RGB"), dtype=np.uint8)
        big = upscale_image(img, scale, backend, tile=tile, overlap=overlap, seam=seam)
        Image.fromarray(big).save(os.path.join(outdir, "upscaled.png"))
        manifest = {"kind": "picture", "image": "upscaled.png", "input": [img.shape[1], img.shape[0]],
                    "output": [big.shape[1], big.shape[0]], "upscale": options,
                    "timing": {"upscale_s": time.perf_counter() - started}}
    _write_manifest(outdir, manifest)
    return manifest
