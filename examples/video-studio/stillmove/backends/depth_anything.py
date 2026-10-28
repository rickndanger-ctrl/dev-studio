"""Depth Anything V2 Small, run with onnxruntime on the CPU provider.

Model: onnx-community/depth-anything-v2-small, file onnx/model.onnx, revision pinned in MODELS.md.
The model is loaded from a local path; nothing is downloaded at run time. The model returns
predicted_depth as relative inverse depth (disparity, larger = nearer), affine-unknown. It is
converted to depth (z in camera 0, > 0) by disparity_to_depth:

    p2, p98  = 2nd and 98th percentiles of the disparity (robust min and max)
    contrast = (p98 - p2) / p98                        how much the picture's depth varies
    spread   = clamp(contrast / FULL_CONTRAST, 0, 1)   FULL_CONTRAST = 0.5
    d_n      = clip((disparity - p2) / (p98 - p2), 0, 1)   1 = nearest; outliers clip, not widen
    inv_mid  = 1 / MID_DEPTH                           the subject sits at MID_DEPTH = 5.0
    inv_near = inv_mid + spread * (1/3 - inv_mid)      spread 1 reaches depth 3
    inv_far  = inv_mid - spread * (inv_mid - 1/8)      spread 1 reaches depth 8
    depth    = 1 / (inv_far + d_n * (inv_near - inv_far))

A flat picture (spread near 0) stays near depth 5 instead of being stretched to 3..8, so a flat logo
stays flat. FULL_CONTRAST = 0.5 means a picture whose robust disparity range is half its top value
gets the full 3..8 range. That is a judgement, not a measurement; change it if real pictures
disagree. The result is a relative depth, not metric.
"""

import os

import numpy as np

from stillmove import models
from stillmove.depth import DepthBackend, check_depth

DEFAULT_MODEL = os.path.join(os.path.expanduser("~"), "models", "depth-anything-v2-small", "model.onnx")
MODEL_REVISION = "4472b7362082ad9968fee890ca0f1e5aca36b93d"
MODEL_SHA256 = "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c"

NEAR = 3.0
FAR = 8.0
MID_DEPTH = 5.0  # the subject depth the flat picture stays at
FULL_CONTRAST = 0.5  # (p98 - p2) / p98 at which the full 3..8 range is used
PATCH = 14  # the model needs sides that are multiples of 14
LONG_SIDE = 518  # the model's native size for the longer side
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _model_size(height, width):
    """Model input (h, w): the longer side is 518, the other side keeps the ratio, both rounded to 14."""
    if width >= height:
        w = LONG_SIDE
        h = height * LONG_SIDE / width
    else:
        h = LONG_SIDE
        w = width * LONG_SIDE / height
    h = max(PATCH, int(round(h / PATCH)) * PATCH)
    w = max(PATCH, int(round(w / PATCH)) * PATCH)
    return h, w


def disparity_to_depth(disparity):
    """Relative inverse depth (disparity, larger = nearer) -> depth, scaled by the picture's contrast.

    Pure function, no model needed. Returns float64 depths, all > 0, with the same shape as the input.
    """
    disp = np.asarray(disparity, dtype=np.float64)
    p2, p98 = (float(v) for v in np.percentile(disp, [2, 98]))
    contrast = (p98 - p2) / p98 if p98 > 0 else 0.0
    spread = min(max(contrast / FULL_CONTRAST, 0.0), 1.0)
    if p98 > p2:
        d_n = np.clip((disp - p2) / (p98 - p2), 0.0, 1.0)
    else:
        d_n = np.zeros_like(disp)
    inv_mid = 1.0 / MID_DEPTH
    inv_near = inv_mid + spread * (1.0 / NEAR - inv_mid)
    inv_far = inv_mid - spread * (inv_mid - 1.0 / FAR)
    inv = inv_far + d_n * (inv_near - inv_far)
    return 1.0 / inv


class DepthAnythingSmall(DepthBackend):
    name = "da2-small"

    def __init__(self, model_path=DEFAULT_MODEL):
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"depth model not found at {model_path}; see video-studio/MODELS.md for the download"
            )
        models.verify(model_path, MODEL_SHA256)  # refuse a swapped file before it is loaded
        import onnxruntime as ort

        self.model_path = model_path
        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def depth(self, image):
        from PIL import Image

        img = np.asarray(image)
        h, w = img.shape[:2]
        mh, mw = _model_size(h, w)
        resized = Image.fromarray(img.astype(np.uint8)).resize((mw, mh), Image.BICUBIC)
        x = np.asarray(resized, dtype=np.float32) / 255.0
        x = (x - MEAN) / STD
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        out = self._session.run(None, {self._input: x})[0]
        disparity = np.asarray(out, dtype=np.float32).reshape(out.shape[-2], out.shape[-1])
        full = Image.fromarray(disparity).resize((w, h), Image.BILINEAR)
        depth = disparity_to_depth(np.asarray(full, dtype=np.float32))
        return check_depth(depth, (h, w))
