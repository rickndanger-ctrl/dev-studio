"""Fake depth backends: no model, deterministic, used by tests and by doctor.

plane:    every pixel at the same depth (5.0).
gradient: far at the top (8.0) and near at the bottom (3.0), linear in between.
"""
import numpy as np

from stillmove.depth import DepthBackend

PLANE_DEPTH = 5.0
GRADIENT_FAR = 8.0
GRADIENT_NEAR = 3.0


class PlaneBackend(DepthBackend):
    name = "plane"

    def depth(self, image):
        h, w = image.shape[:2]
        return np.full((h, w), PLANE_DEPTH, dtype=np.float32)


class GradientBackend(DepthBackend):
    name = "gradient"

    def depth(self, image):
        h, w = image.shape[:2]
        rows = np.linspace(GRADIENT_FAR, GRADIENT_NEAR, h, dtype=np.float32)
        return np.repeat(rows[:, None], w, axis=1)
