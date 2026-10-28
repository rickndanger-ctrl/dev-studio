"""Tests for the foreground depth override (fg_depth): a flat foreground at a chosen depth.

Scene: a plane 5.0 deep (the plane backend), a centred fake ellipse for the foreground, and a bright
marker in the top-left corner, outside the matte, standing in for the background. A dolly_in moves
the camera toward the subject, so a layer on a nearer plane grows more.
"""

import unittest
import warnings

import numpy as np

from stillmove import cameras, warp
from stillmove.depth import get_backend
from stillmove.layers import Layers
from stillmove.matte import get_matte

W, H = 64, 48
PLANE = 5.0


def scene():
    image = np.full((H, W, 3), 60, dtype=np.uint8)
    image[4:10, 40:46] = 255  # the background marker: outside the matte, and still in frame after the zoom
    depth = get_backend("plane").depth(image)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    r = np.sqrt(((xs - (W - 1) / 2) / (W * 0.2)) ** 2 + ((ys - (H - 1) / 2) / (H * 0.2)) ** 2)
    alpha = np.clip((1.0 - r) * (H * 0.2) / 2.0 + 0.5, 0.0, 1.0).astype(np.float32)  # a soft small ellipse
    return image, depth, alpha


def dolly_cameras():
    return cameras.from_preset("dolly_in", 0.5, 24, W, H)  # 13 frames, camera moves 1.0 forward


def layer_scale(mask):
    """sqrt of the mask's total weight: its size, proportional to its linear scale."""
    return float(np.sqrt(np.sum(mask)))


def scale_change(first, last):
    return abs(layer_scale(last) / layer_scale(first) - 1.0)


def marker_mask(image, cam0, frame, transform, depth):
    out, _ = warp.warp(np.repeat(image[..., :1], 3, axis=2), depth, cam0.K, frame.K, transform)
    return (out[..., 0].astype(np.float64) > 128).astype(np.float64)


class FgDepthMotionTests(unittest.TestCase):
    def setUp(self):
        self.image, self.depth, self.alpha = scene()
        self.cams = dolly_cameras()
        self.cam0 = self.cams.frames[0]
        self.last = self.cams.frames[-1]
        self.transform = warp.relative(self.cams, len(self.cams.frames) - 1)

    def motion(self, fg_depth):
        layers = Layers(self.image, self.depth, self.alpha, fg_depth=fg_depth)
        fg_first = layers.foreground_alpha(self.cam0, self.cam0, np.eye(4)).astype(np.float64)
        fg_last = layers.foreground_alpha(self.cam0, self.last, self.transform).astype(np.float64)
        bg_first = marker_mask(self.image, self.cam0, self.cam0, np.eye(4), self.depth)
        bg_last = marker_mask(self.image, self.cam0, self.last, self.transform, self.depth)
        return scale_change(fg_first, fg_last), scale_change(bg_first, bg_last)

    def test_a_nearer_foreground_plane_grows_more_than_the_background(self):
        fg_moves, bg_moves = self.motion(fg_depth=4.6)
        self.assertGreater(bg_moves, 0.0)
        self.assertGreater(fg_moves, bg_moves + 0.02, (fg_moves, bg_moves))

    def test_the_default_foreground_plane_moves_like_the_background(self):
        # With the plane at 5.0 everywhere, the default 10th-percentile plane sits on the background's depth.
        fg_moves, bg_moves = self.motion(fg_depth=None)
        self.assertAlmostEqual(fg_moves, bg_moves, delta=0.02)

    def test_identity_is_still_exact_with_a_chosen_depth(self):
        layers = Layers(self.image, self.depth, self.alpha, fg_depth=4.6)
        out = layers.compose(self.cam0, self.cam0, np.eye(4))
        self.assertLessEqual(int(np.max(np.abs(out.astype(int) - self.image.astype(int)))), 1)


class FgDepthCheckTests(unittest.TestCase):
    def setUp(self):
        self.image, self.depth, self.alpha = scene()

    def test_a_depth_of_zero_or_less_is_refused(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(fg_depth=bad), self.assertRaises(ValueError):
                Layers(self.image, self.depth, self.alpha, fg_depth=bad)

    def test_a_depth_behind_the_background_warns(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            Layers(self.image, self.depth, self.alpha, fg_depth=5.5)
        self.assertTrue(any("not nearer" in str(w.message) for w in seen), [str(w.message) for w in seen])

    def test_a_nearer_depth_does_not_warn(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            Layers(self.image, self.depth, self.alpha, fg_depth=4.6)
        self.assertFalse([w for w in seen if "not nearer" in str(w.message)])

    def test_the_default_depth_is_the_tenth_percentile(self):
        layers = Layers(self.image, self.depth, self.alpha)
        self.assertAlmostEqual(layers.near, PLANE, places=6)
        self.assertAlmostEqual(Layers(self.image, self.depth, self.alpha, fg_depth=4.6).near, 4.6, places=9)


if __name__ == "__main__":
    unittest.main()
