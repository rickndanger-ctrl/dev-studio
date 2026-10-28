"""Generate the example images in docs/example/ with the gradient depth backend.

Run from video-studio/:  python3 tools/make_example.py

Draws a 320x180 test image in code (gradient sky, checkerboard ground, a circle), then renders
dolly_in and orbit_left from it. Each GIF is kept under 1 MB by lowering the frame rate if needed.
No network and no photos.
"""
import os
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "example")
WIDTH, HEIGHT = 320, 180
HORIZON = 90
PRESETS = ("dolly_in", "orbit_left")
SECONDS = 2.0
FPS_TRIES = (12, 10, 8, 6)
MAX_GIF_BYTES = 1_000_000

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def make_image():
    """A gradient sky, a checkerboard ground and a circle, as an HxWx3 uint8 array."""
    image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    t = np.linspace(0.0, 1.0, HORIZON)[:, None]
    top, bottom = np.array([40, 90, 190]), np.array([200, 225, 250])
    image[:HORIZON] = (top + (bottom - top) * t[:, :, None]).astype(np.uint8)

    ys, xs = np.mgrid[HORIZON:HEIGHT, 0:WIDTH]
    checker = ((xs // 20) + (ys - HORIZON) // 10) % 2 == 0
    image[HORIZON:] = np.where(checker[:, :, None], np.array([190, 170, 130]), np.array([90, 110, 70])).astype(np.uint8)

    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    circle = (xs - 160) ** 2 + (ys - 95) ** 2 <= 28 ** 2
    image[circle] = (220, 60, 50)
    return image


def render_example(image_path, preset_id, workdir):
    """Render preset_id and return the GIF path, lowering fps until it is under MAX_GIF_BYTES."""
    from stillmove import cameras, depth, render

    backend = depth.get_backend("gradient")
    for fps in FPS_TRIES:
        cams = cameras.from_preset(preset_id, SECONDS, fps, WIDTH, HEIGHT)
        manifest = render.render(image_path, cams, backend, os.path.join(workdir, f"{preset_id}-{fps}"))
        gif = os.path.join(workdir, f"{preset_id}-{fps}", manifest["preview"])
        if os.path.getsize(gif) < MAX_GIF_BYTES:
            return gif
    raise RuntimeError(f"{preset_id}: the GIF stays over {MAX_GIF_BYTES} bytes even at {FPS_TRIES[-1]} fps")


def main():
    os.makedirs(OUT, exist_ok=True)
    image_path = os.path.join(OUT, "input.png")
    Image.fromarray(make_image()).save(image_path)
    with tempfile.TemporaryDirectory() as workdir:
        for preset_id in PRESETS:
            shutil.copyfile(render_example(image_path, preset_id, workdir), os.path.join(OUT, f"{preset_id}.gif"))
    for name in sorted(os.listdir(OUT)):
        print(f"{name}: {os.path.getsize(os.path.join(OUT, name))} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
