"""Tests for the two-layer render (stillmove/layers.py) with the fake matte and small pictures.

Scene: a striped background and a foreground ellipse painted a unique colour, so any pixel of
that colour can only have come from the foreground.
"""

import os
import tempfile
import unittest

import numpy as np

from stillmove import cameras, warp
from stillmove.depth import get_backend
from stillmove.layers import Layers, plane_homography, sample_backward
from stillmove.matte import get_matte
from stillmove.render import MANIFEST, render

W, H = 48, 36
FPS = 24
SECONDS = 0.5
FG = np.array([255, 0, 255], dtype=np.uint8)  # the foreground's unique colour


class EdgeMatte:
    """A foreground that touches the left edge of the picture, the case where a warp hole can sit
    next to foreground pixels: the hole is filled from the edge, and its alpha comes with it."""

    name = "edge"

    def alpha(self, rgb):
        h, w = rgb.shape[:2]
        xs = np.broadcast_to(np.arange(w), (h, w))
        return (xs < w * 0.4).astype(np.float32)


def scene(matte=None):
    """(image, alpha): a striped background, and the foreground composited over it with the matte, as a
    real picture is: I = a * FG + (1 - a) * B. The matting model is only exact for such a picture; a
    scene with the soft edge left as plain background cannot be inverted (F would fall outside 0..255)."""
    matte = matte or get_matte("fake")
    background = np.zeros((H, W, 3), dtype=np.uint8)
    background[..., 0] = (20 + 2 * np.arange(W))[None, :]
    background[..., 1] = (40 + 3 * np.arange(H))[:, None]
    background[..., 2] = 60
    alpha = matte.alpha(background)
    a = np.asarray(alpha, dtype=np.float64)[..., None]
    image = np.rint(a * FG.astype(np.float64) + (1.0 - a) * background.astype(np.float64)).astype(np.uint8)
    return image, alpha


def identity_cameras(frames=2):
    k = np.array([[float(W), 0.0, W / 2.0], [0.0, float(W), H / 2.0], [0.0, 0.0, 1.0]])
    return cameras.Cameras(fps=FPS, width=W, height=H,
                           frames=[cameras.Frame(t=i / FPS, K=k.copy(), w2c=np.eye(4)) for i in range(frames)])


def fg_mask(image):
    return np.all(image == FG, axis=-1)


def foreground_coverage(layers, cam0, frame, transform):
    """Where the warped foreground really is: target pixels whose source point lies inside the
    picture, and whose sampled alpha is above 0. Built from the raw alpha plane, not from the
    layer's compose path, so it can catch a leak in that path."""
    Hm = plane_homography(cam0.K, frame.K, transform, layers.near)
    sampled, inside = sample_backward(layers.alpha[..., None].astype(np.float64), Hm)
    return inside & (sampled[..., 0] > 0)


def eroded_core(alpha):
    """Source pixels whose matte is 1 and so are all their 4 neighbours: the interior of the core."""
    core = alpha >= 1.0
    core = core.copy()
    core[0, :] = core[-1, :] = core[:, 0] = core[:, -1] = False
    inner = core.copy()
    inner[1:-1, 1:-1] = core[1:-1, 1:-1] & core[:-2, 1:-1] & core[2:, 1:-1] & core[1:-1, :-2] & core[1:-1, 2:]
    inner[0, :] = inner[-1, :] = inner[:, 0] = inner[:, -1] = False
    return inner


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.image, self.alpha = scene()
        self.depth = get_backend("gradient").depth(self.image)
        self.layers = Layers(self.image, self.depth, self.alpha)

    def test_identity_move_gives_the_original_picture(self):
        cams = identity_cameras()
        frame = self.layers.compose(cams.frames[0], cams.frames[1], warp.relative(cams, 1))
        # Within one level, not exact: the foreground colour is F = (I - (1 - a) B) / a, which divides
        # by alpha, so rounding can move a soft-edged pixel by one level. On this scene it is exact.
        self.assertLessEqual(int(np.abs(frame.astype(int) - self.image.astype(int)).max()), 1)

    def test_identity_move_matches_the_single_warp(self):
        cams = identity_cameras()
        single, _ = warp.warp(self.image, self.depth, cams.frames[0].K, cams.frames[1].K, warp.relative(cams, 1))
        layered = self.layers.compose(cams.frames[0], cams.frames[1], warp.relative(cams, 1))
        # One level, for the same reason as above.
        self.assertLessEqual(int(np.abs(layered.astype(int) - single.astype(int)).max()), 1)

    def test_the_plate_never_contains_the_foreground_colour(self):
        self.assertFalse(fg_mask(self.layers.plate).any())

    def test_foreground_colour_appears_outside_the_warped_foreground_alpha_on_no_frame(self):
        cams = cameras.from_preset("orbit_left", SECONDS, FPS, W, H)
        holes_seen = False
        for i in range(len(cams.frames)):
            with self.subTest(frame=i):
                cam0, frame = cams.frames[0], cams.frames[i]
                transform = warp.relative(cams, i)
                out = self.layers.compose(cam0, frame, transform)
                outside = ~foreground_coverage(self.layers, cam0, frame, transform)
                self.assertFalse(fg_mask(out)[outside].any())
                _, bg_filled = warp.warp(self.layers.plate, self.depth, cam0.K, frame.K, transform)
                holes_seen = holes_seen or bool(bg_filled.any())
        self.assertTrue(holes_seen, "the move should open background holes, or the test checks nothing")

    def test_no_foreground_colour_in_background_holes_when_the_foreground_touches_the_edge(self):
        # The case that matters: a hole at the picture edge is filled from the foreground's edge
        # pixels, and their alpha of 1 would carry the foreground colour into the hole.
        image, alpha = scene(EdgeMatte())
        depth = get_backend("gradient").depth(image)
        layers = Layers(image, depth, alpha)
        self.assertFalse(fg_mask(layers.plate).any())
        cams = cameras.from_preset("orbit_left", SECONDS, FPS, W, H)
        holes_seen = False
        for i in range(len(cams.frames)):
            with self.subTest(frame=i):
                cam0, frame = cams.frames[0], cams.frames[i]
                transform = warp.relative(cams, i)
                out = layers.compose(cam0, frame, transform)
                outside = ~foreground_coverage(layers, cam0, frame, transform)
                self.assertFalse(fg_mask(out)[outside].any())
                _, filled = warp.warp(layers.plate, depth, cam0.K, frame.K, transform)
                holes_seen = holes_seen or bool(filled.any())
        self.assertTrue(holes_seen, "the move should open background holes, or the test checks nothing")

    def test_plane_homography_matches_projecting_the_plane_points_directly(self):
        cams = cameras.from_preset("dolly_in", SECONDS, FPS, W, H)
        cam0, frame = cams.frames[0], cams.frames[-1]
        transform = warp.relative(cams, len(cams.frames) - 1)
        near = 2.5
        R, t = transform[:3, :3], transform[:3, 3]
        Hm = plane_homography(cam0.K, frame.K, transform, near)
        for u, v in ((10.5, 7.5), (30.5, 20.5), (W / 2, H / 2)):
            with self.subTest(pixel=(u, v)):
                ray = np.linalg.inv(cam0.K) @ np.array([u, v, 1.0])
                X = ray * (near / ray[2])  # the point where this pixel's ray meets the plane z = near
                direct = frame.K @ (R @ X + t)
                mapped = Hm @ np.array([u, v, 1.0])
                np.testing.assert_allclose(mapped[:2] / mapped[2], direct[:2] / direct[2], atol=1e-9)

    def test_magnified_move_has_no_pinholes_in_the_eroded_foreground(self):
        # dolly_in moves the camera toward the subject, so the foreground is enlarged. Any pixel
        # whose source lies in the eroded core must be fully opaque; a pinhole would show as a gap.
        cams = cameras.from_preset("dolly_in", SECONDS, FPS, W, H)
        cam0, frame = cams.frames[0], cams.frames[-1]
        transform = warp.relative(cams, len(cams.frames) - 1)
        core = eroded_core(self.alpha).astype(np.float64)[..., None]
        Hm = plane_homography(cam0.K, frame.K, transform, self.layers.near)
        sampled, inside = sample_backward(core, Hm)
        target_core = inside & (sampled[..., 0] >= 1.0 - 1e-9)
        self.assertTrue(target_core.any(), "the eroded core must be visible in the last frame")
        alpha = self.layers.foreground_alpha(cam0, frame, transform)
        self.assertTrue(np.all(alpha[target_core] > 1.0 - 1e-6), alpha[target_core].min())

    def test_foreground_is_visible_in_the_first_frame(self):
        cams = identity_cameras()
        frame = self.layers.compose(cams.frames[0], cams.frames[0], warp.relative(cams, 0))
        self.assertTrue(fg_mask(frame).any())

    def test_matte_must_match_the_picture_size(self):
        with self.assertRaises(ValueError):
            Layers(self.image, self.depth, self.alpha[:-1])

    def test_matte_covering_everything_is_refused(self):
        with self.assertRaises(ValueError):
            Layers(self.image, self.depth, np.ones((H, W), dtype=np.float32))


LIGHT = 230  # the light background of the ghost test
DARK = 30    # the dark thin strokes drawn on it


def _segment_distance(xs, ys, p0, p1):
    vx, vy = p1[0] - p0[0], p1[1] - p0[1]
    t = np.clip(((xs - p0[0]) * vx + (ys - p0[1]) * vy) / (vx * vx + vy * vy), 0.0, 1.0)
    return np.hypot(xs - (p0[0] + t * vx), ys - (p0[1] + t * vy))


def stroke_coverage(h, w):
    """Thin soft lines: coverage 1 at the core, fading to 0 over about a pixel."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    d = np.minimum(_segment_distance(xs, ys, (5, 5), (w - 6, h - 6)),
                   _segment_distance(xs, ys, (w - 8, 3), (8, h - 4)))
    return np.clip((1.2 - d) / 1.0, 0.0, 1.0).astype(np.float32)


class StrokeMatte:
    """A matte that is the stroke coverage itself, so the picture is an exact mix of dark and light."""

    name = "strokes"

    def __init__(self, cov):
        self.cov = cov

    def alpha(self, rgb):
        return self.cov


class GhostTests(unittest.TestCase):
    def test_thin_soft_strokes_do_not_ghost_when_the_foreground_moves(self):
        # The strokes have partial alpha, so they are both foreground and an edge. Before the fix the
        # dark strokes stayed in the background plate; a moving foreground then doubled them. Where
        # the warped foreground is absent, the output must show only the light background.
        cov = stroke_coverage(H, W)
        image = np.round(LIGHT * (1 - cov[..., None]) + DARK * cov[..., None]).astype(np.uint8)
        image = np.broadcast_to(image, (H, W, 3)).copy()
        depth = np.full((H, W), 6.0, dtype=np.float32)
        layers = Layers(image, depth, StrokeMatte(cov).alpha(image), fg_depth=3.0)
        cams = cameras.from_preset("dolly_in", SECONDS, FPS, W, H)
        cam0, frame = cams.frames[0], cams.frames[-1]
        transform = warp.relative(cams, len(cams.frames) - 1)
        out = layers.compose(cam0, frame, transform)
        absent = layers.foreground_alpha(cam0, frame, transform) == 0
        self.assertTrue(absent.any(), "the move must leave some pixels with no foreground")
        self.assertGreaterEqual(int(out[absent].min()), LIGHT - 4)


class RenderWithLayersTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.image, _ = scene()
        from PIL import Image
        self.path = os.path.join(self.tmp, "in.png")
        Image.fromarray(self.image).save(self.path)

    def test_frame_count_is_unchanged_by_the_layers(self):
        cams = cameras.from_preset("dolly_in", SECONDS, FPS, W, H)
        plain = render(self.path, cams, get_backend("gradient"), os.path.join(self.tmp, "plain"))
        layered = render(self.path, cams, get_backend("gradient"), os.path.join(self.tmp, "layers"),
                         matte=get_matte("fake"))
        self.assertEqual(len(layered["frames"]), len(cams.frames))
        self.assertEqual(len(layered["frames"]), len(plain["frames"]))
        for name in layered["frames"]:
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, "layers", name)))

    def test_manifest_names_the_matte(self):
        cams = identity_cameras()
        manifest = render(self.path, cams, get_backend("gradient"), os.path.join(self.tmp, "m"),
                          matte=get_matte("fake"))
        self.assertEqual(manifest["matte"], "fake")
        with open(os.path.join(self.tmp, "m", MANIFEST), encoding="utf-8") as f:
            self.assertIn('"matte": "fake"', f.read())

    def test_without_a_matte_the_manifest_says_none(self):
        cams = identity_cameras()
        manifest = render(self.path, cams, get_backend("gradient"), os.path.join(self.tmp, "n"))
        self.assertIsNone(manifest["matte"])


if __name__ == "__main__":
    unittest.main()
