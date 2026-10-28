"""Real-ESRGAN general x4 v3 (SRVGG compact), run with onnxruntime on the CPU provider.

Model: xinntao/Real-ESRGAN, release v0.2.5.0, realesr-general-x4v3.pth (BSD-3-Clause via the repo
LICENSE), exported to ONNX by tools/export_esrgan.py (torch runs only in ~/venvs/rife-convert). Hashes
and the license evidence are in MODELS.md. The model is loaded from a local path; nothing is downloaded.

upscale(img, scale) takes uint8 HxWx3 and returns (H*scale)x(W*scale)x3 uint8. The network makes x4;
scale 4 is that output, scale 2 is the x4 output reduced by Lanczos to half its size. Other scales are
refused.

Memory is bounded by tiling: the picture is cut into core tiles, each core is processed with OVERLAP
pixels of context on every side, and only the core of the result is kept. The per-window function
has the contract of stillmove.upscale.tiled_map (fn(window, scale) -> uint8 (h*scale, w*scale, 3)), so
the local loop below can be swapped for that tiler without changing the model code.
"""

import os

import numpy as np
from PIL import Image

from stillmove import models

DEFAULT_MODEL = os.path.join(os.path.expanduser("~"), "models", "realesrgan", "realesr-general-x4v3.onnx")
MODEL_SHA256 = "a93b2f013c19bc043e975915ffa4d8dc9ed8270ec9fbc87dcec21bce42db4d61"
NET_SCALE = 4
SCALES = (2, 4)
TILE = 256  # core tile side in source pixels (tiled_map in stillmove.upscale)
OVERLAP = 16  # context on each side, in source pixels


class EsrganGeneral:
    """Real-ESRGAN x4, tiled. session is for tests; normally onnxruntime loads model_path."""

    name = "esrgan"

    def __init__(self, model_path=DEFAULT_MODEL, session=None, tile=TILE, overlap=OVERLAP):
        self.model_path = model_path
        self._session = session
        self.tile = tile
        self.overlap = overlap

    def _load(self):
        if self._session is None:
            if not os.path.isfile(self.model_path):
                raise ValueError(f"the esrgan upscale model is not installed at {self.model_path}; see MODELS.md")
            models.verify(self.model_path, MODEL_SHA256)
            try:
                import onnxruntime as ort
            except ImportError as exc:
                raise ValueError("the esrgan backend needs onnxruntime; install it in the stillmove venv") from exc
            self._session = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
        return self._session

    def _x4(self, window):
        """The network on one uint8 window: returns uint8 (4h, 4w, 3)."""
        x = (window.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = self._load().run(None, {"image": np.ascontiguousarray(x)})[0][0]
        picture = np.transpose(np.clip(out, 0.0, 1.0), (1, 2, 0))
        return np.rint(picture * 255.0).astype(np.uint8)

    def upscale_window(self, window, scale):
        """The per-window function: x4 from the network, then Lanczos down by 2 for scale 2."""
        big = self._x4(window)
        if scale == NET_SCALE:
            return big
        h, w = window.shape[:2]
        return np.asarray(Image.fromarray(big).resize((w * scale, h * scale), Image.LANCZOS), dtype=np.uint8)

    def upscale(self, img, scale):
        """Tiled by stillmove.upscale.tiled_map with blended seams; scale 2 or 4 only."""
        if scale not in SCALES:
            raise ValueError("the esrgan backend makes scale 2 or 4; scale %r is not supported" % (scale,))
        from stillmove.upscale import tiled_map  # lazy: upscale.py imports this module's class at call time

        return tiled_map(np.asarray(img, dtype=np.uint8), scale, self.upscale_window,
                         tile=self.tile, overlap=self.overlap, seam="blend")
