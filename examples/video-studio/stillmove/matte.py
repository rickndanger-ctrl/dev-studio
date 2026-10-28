"""Foreground matte: a mask that is 1 on the subject, 0 on the background, soft at the edge.

Backends:
  fake   a centred ellipse with a soft edge (a smoothstep a few pixels wide). No model.
  isnet  IS-Net (general use), run with onnxruntime on the CPU. The model file is pinned in
         video-studio/MODELS.md and is never downloaded here. onnxruntime is imported only when
         the backend is built, so the rest of stillmove does not need it.

isnet preprocessing, as the rembg release of this model expects: RGB resized to 1024x1024,
x / 255 - 0.5 (so the std is 1.0), NCHW float32. Output: the first map, min-max scaled to 0..1 and
resized back to the image size with a smooth filter.
"""

import os

import numpy as np

from stillmove import models

DEFAULT_ISNET_MODEL = os.path.join(os.path.expanduser("~"), "models", "isnet-general-use", "isnet-general-use.onnx")
ISNET_SHA256 = "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a"  # video-studio/MODELS.md
ISNET_SIZE = 1024
FAKE_SOFT_PX = 2.0  # half-width of the soft edge, in pixels


def _smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class FakeMatte:
    name = "fake"

    def alpha(self, rgb):
        """A centred ellipse, half the image's width and height: 1 inside, 0 outside, soft at the edge."""
        rgb = np.asarray(rgb)
        h, w = rgb.shape[:2]
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
        ax, ay = max(w * 0.5, 1.0), max(h * 0.5, 1.0)
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        r = np.sqrt(((xs - cx) / ax) ** 2 + ((ys - cy) / ay) ** 2)
        # distance to the ellipse edge in pixels, positive inside
        inside_px = (1.0 - r) * min(ax, ay)
        return _smoothstep(-FAKE_SOFT_PX, FAKE_SOFT_PX, inside_px).astype(np.float32)


class IsnetMatte:
    name = "isnet"

    def __init__(self, model_path=DEFAULT_ISNET_MODEL):
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"matte model not found at {model_path}; see video-studio/MODELS.md for the download"
            )
        models.verify(model_path, ISNET_SHA256)  # refuse a swapped file before it is loaded
        try:
            import onnxruntime as ort
        except ImportError:
            raise RuntimeError(
                "onnxruntime is not installed, so the isnet matte is off; install it in the video-studio venv"
            ) from None
        self.model_path = model_path
        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def alpha(self, rgb):
        from PIL import Image

        rgb = np.asarray(rgb)
        h, w = rgb.shape[:2]
        resized = Image.fromarray(rgb.astype(np.uint8)).resize((ISNET_SIZE, ISNET_SIZE), Image.BILINEAR)
        x = np.asarray(resized, dtype=np.float32) / 255.0 - 0.5
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        out = self._session.run(None, {self._input: x})[0]
        pred = np.asarray(out, dtype=np.float32).reshape(out.shape[-2], out.shape[-1])
        lo, hi = float(pred.min()), float(pred.max())
        pred = (pred - lo) / (hi - lo) if hi > lo else np.zeros_like(pred)
        full = Image.fromarray(pred.astype(np.float32)).resize((w, h), Image.BICUBIC)
        return np.clip(np.asarray(full, dtype=np.float32), 0.0, 1.0)


def get_matte(name, isnet_model=DEFAULT_ISNET_MODEL):
    """A matte backend by name: 'fake' or 'isnet'."""
    if name == "fake":
        return FakeMatte()
    if name == "isnet":
        return IsnetMatte(isnet_model)
    raise ValueError(f"unknown matte backend {name!r}; choose fake or isnet")


def to_8bit(alpha):
    """A float mask in 0..1 as uint8 0..255."""
    return np.clip(np.rint(np.asarray(alpha, dtype=np.float64) * 255.0), 0, 255).astype(np.uint8)
