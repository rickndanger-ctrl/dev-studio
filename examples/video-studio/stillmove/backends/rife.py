"""RIFE frame interpolation: the midpoint model, run with onnxruntime on the CPU provider.

Model: hzwer/arXiv2020-RIFE (MIT). Weights: the 'RIFE model' zip linked in its README, flownet.pkl,
exported to ONNX by tools/export_rife.py (torch runs only in ~/venvs/rife-convert). Details and hashes
are in MODELS.md. The model is loaded from a local path and nothing is downloaded at run time.

The file makes only the picture halfway between two frames (t = 0.5). Quarter points come from it:
t = 0.25 is mid(a, mid(a, b)) and t = 0.75 is mid(mid(a, b), b). Other fractions are refused.
A pair's midpoint is cached, so a 4x interpolation makes 3 model calls per pair, not 4.

The model needs sides that are multiples of 32: inputs are padded by edge replication and the
output is cropped back to the picture's size.
"""

import os

import numpy as np

from stillmove import models

DEFAULT_MODEL = os.path.join(os.path.expanduser("~"), "models", "rife", "rife_ifnet_midpoint.onnx")
MODEL_SHA256 = "a02e9572a6bc641100145f9a3883085acfeed204a40a69cb02e25a2d8d7621c4"
PAD = 32
INPUT_NAME = "frames"


def _pad(img):
    """Edge-replicate an HxWx3 uint8 picture to multiples of 32. Returns (padded, original height, width)."""
    h, w = img.shape[:2]
    ph, pw = (-h) % PAD, (-w) % PAD
    return np.pad(img, ((0, ph), (0, pw), (0, 0)), mode="edge"), h, w


class RifeMidpoint:
    """Midpoint and quarter-point frames from RIFE. session is for tests; normally onnxruntime loads model_path."""

    name = "rife"

    def __init__(self, model_path=DEFAULT_MODEL, session=None):
        self.model_path = model_path
        self._session = session
        self._pair = None  # (a, b) last asked for a midpoint, by identity
        self._mid = None

    def _load(self):
        if self._session is None:
            if not os.path.isfile(self.model_path):
                raise ValueError(f"the rife model is not installed at {self.model_path}; see MODELS.md")
            models.verify(self.model_path, MODEL_SHA256)
            try:
                import onnxruntime as ort
            except ImportError as exc:
                raise ValueError("the rife backend needs onnxruntime; install it in the stillmove venv") from exc
            self._session = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
        return self._session

    def _infer(self, a, b):
        """One model call: the picture halfway between uint8 HxWx3 frames a and b."""
        pa, h, w = _pad(a)
        pb, _, _ = _pad(b)
        x = np.concatenate([pa, pb], axis=2).astype(np.float32) / 255.0
        x = np.ascontiguousarray(np.transpose(x, (2, 0, 1))[None])
        out = self._load().run(None, {INPUT_NAME: x})[0]
        picture = np.transpose(np.clip(out[0, :, :h, :w], 0.0, 1.0), (1, 2, 0))
        return np.rint(picture * 255.0).astype(np.uint8)

    def mid(self, a, b):
        """The midpoint of a and b. The last pair asked for is cached, so its midpoint is computed once."""
        if self._pair is not None and self._pair[0] is a and self._pair[1] is b:
            return self._mid
        result = self._infer(a, b)
        self._pair = (a, b)
        self._mid = result
        return result

    def between(self, a, b, t):
        """The picture at fraction t between a and b: 0.5 directly, 0.25 and 0.75 from the midpoint.

        Quarter points are not cached: a 4x interpolation makes mid(a, b) once and two more calls.
        """
        if abs(t - 0.5) < 1e-9:
            return self.mid(a, b)
        if abs(t - 0.25) < 1e-9:
            return self._infer(a, self.mid(a, b))
        if abs(t - 0.75) < 1e-9:
            return self._infer(self.mid(a, b), b)
        raise ValueError(
            "the rife backend makes only the midpoint (t = 0.5) and the quarter points (0.25, 0.75); "
            "t = %g is not supported" % t
        )
