"""Depth Anything V2 Small, run with onnxruntime on the CPU provider.

Model: onnx-community/depth-anything-v2-small, file onnx/model.onnx, revision pinned in MODELS.md.
The model is loaded from a local path; nothing is downloaded at run time. The model returns
predicted_depth as relative inverse depth (disparity, larger = nearer), affine-unknown. It is
converted to the depth the warp needs (z in camera 0, > 0) like this:

    d_n   = (disparity - min) / (max - min)            in [0, 1], 1 = nearest
    inv   = inv_far + d_n * (inv_near - inv_far)       inverse depth, from 1/FAR to 1/NEAR
    depth = 1 / inv                                    FAR = 8.0, NEAR = 3.0

Those bounds match the 'gradient' fake, so the scale is in the same range. The result is a
relative depth, not metric: the scale is a choice, and the warp only needs the ratios right.
"""

import os

import numpy as np

from stillmove.depth import DepthBackend, check_depth

DEFAULT_MODEL = os.path.join(os.path.expanduser("~"), "models", "depth-anything-v2-small", "model.onnx")
MODEL_REVISION = "4472b7362082ad9968fee890ca0f1e5aca36b93d"
MODEL_SHA256 = "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c"

NEAR = 3.0
FAR = 8.0
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


def _disparity_to_depth(disparity):
    lo, hi = float(disparity.min()), float(disparity.max())
    span = hi - lo
    if span <= 0:
        d_n = np.zeros_like(disparity)
    else:
        d_n = (disparity - lo) / span
    inv_far, inv_near = 1.0 / FAR, 1.0 / NEAR
    inv = inv_far + d_n * (inv_near - inv_far)
    return 1.0 / inv


class DepthAnythingSmall(DepthBackend):
    name = "da2-small"

    def __init__(self, model_path=DEFAULT_MODEL):
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"depth model not found at {model_path}; see video-studio/MODELS.md for the download"
            )
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
        depth = _disparity_to_depth(np.asarray(full, dtype=np.float32))
        return check_depth(depth, (h, w))
