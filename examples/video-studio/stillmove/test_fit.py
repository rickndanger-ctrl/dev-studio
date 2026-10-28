"""Tests for stillmove.fit: scaling the move, finding the subject, and fitting the move inside the margin.

The margin is checked by the test itself, with its own projection, so it does not rely on fit.py's
arithmetic. Synthetic cameras from camera-motion's dolly_in; tiny images."""

import unittest

import numpy as np

from stillmove import cameras, fit

W, H = 64, 36
MARGIN = 0.06


def dolly():
    return cameras.from_preset("dolly_in", 0.5, 24, W, H)


def rect(u0, u1, v0, v1):
    alpha = np.zeros((H, W), dtype=np.float32)
    alpha[v0:v1, u0:u1] = 1.0
    return alpha


def project_all(cams, points):
    """Pixel positions of the points in every frame of cams, by the test's own projection."""
    c2w0 = np.linalg.inv(cams.frames[0].w2c)
    world = (c2w0 @ np.concatenate([points, np.ones((len(points), 1))], axis=1).T).T[:, :3]
    out = []
    for f in cams.frames:
        cam = (np.asarray(f.w2c) @ np.concatenate([world, np.ones((len(world), 1))], axis=1).T).T[:, :3]
        u = f.K[0, 0] * cam[:, 0] / cam[:, 2] + f.K[0, 2]
        v = f.K[1, 1] * cam[:, 1] / cam[:, 2] + f.K[1, 2]
        out.append((u, v))
    return out


def inside_margin(cams, points, s, tol=1e-6):
    for u, v in project_all(fit.scale_cameras(cams, s), points):
        if u.min() < MARGIN * W - tol or u.max() > W * (1 - MARGIN) + tol:
            return False
        if v.min() < MARGIN * H - tol or v.max() > H * (1 - MARGIN) + tol:
            return False
    return True


class ScaleTests(unittest.TestCase):
    def setUp(self):
        self.cams = dolly()

    def test_s_one_gives_the_same_frames(self):
        out = fit.scale_cameras(self.cams, 1.0)
        self.assertEqual(len(out.frames), len(self.cams.frames))
        for a, b in zip(out.frames, self.cams.frames):
            np.testing.assert_array_equal(a.K, b.K)
            np.testing.assert_array_equal(a.w2c, b.w2c)

    def test_s_zero_holds_frame_zero(self):
        out = fit.scale_cameras(self.cams, 0.0)
        for f in out.frames:
            np.testing.assert_array_equal(f.K, self.cams.frames[0].K)
            np.testing.assert_allclose(f.w2c, self.cams.frames[0].w2c, atol=1e-12)

    def test_frame_count_and_times_never_change(self):
        for s in (0.0, 0.3, 0.5, 1.0):
            with self.subTest(s=s):
                out = fit.scale_cameras(self.cams, s)
                self.assertEqual(len(out.frames), len(self.cams.frames))
                self.assertEqual([f.t for f in out.frames], [f.t for f in self.cams.frames])

    def test_the_first_frame_is_unchanged_at_any_scale(self):
        out = fit.scale_cameras(self.cams, 0.4)
        np.testing.assert_allclose(out.frames[0].w2c, self.cams.frames[0].w2c, atol=1e-12)
        np.testing.assert_allclose(out.frames[0].K, self.cams.frames[0].K, atol=1e-12)

    def test_a_scale_outside_zero_one_is_refused(self):
        with self.assertRaises(ValueError):
            fit.scale_cameras(self.cams, 1.2)


class SubjectTests(unittest.TestCase):
    def setUp(self):
        self.cams = dolly()
        self.K = self.cams.frames[0].K
        self.flat = np.full((H, W), 5.0, dtype=np.float32)

    def test_a_flat_depth_map_has_no_subject(self):
        self.assertIsNone(fit.subject_points(self.flat, K=self.K))

    def test_a_clearly_nearer_region_is_the_subject(self):
        depth = np.full((H, W), 5.0, dtype=np.float32)
        depth[:, :20] = 3.0  # the left third is clearly nearer
        points = fit.subject_points(depth, K=self.K, step=2)
        self.assertIsNotNone(points)
        self.assertTrue(np.allclose(points[:, 2], 3.0))

    def test_alpha_points_sit_at_fg_depth_when_given(self):
        points = fit.subject_points(self.flat, rect(10, 20, 10, 20), fg_depth=4.6, K=self.K)
        self.assertTrue(np.allclose(points[:, 2], 4.6))

    def test_subject_points_need_the_intrinsics(self):
        with self.assertRaises(ValueError):
            fit.subject_points(self.flat, rect(10, 20, 10, 20))


class FitTests(unittest.TestCase):
    def setUp(self):
        self.cams = dolly()
        self.K = self.cams.frames[0].K

    def test_a_subject_near_an_edge_is_scaled_and_then_inside_the_margin_on_every_frame(self):
        # Inside the margin at frame 0, but the dolly pushes it past the right edge's margin.
        points = fit.subject_points(np.full((H, W), 5.0), rect(46, 56, 12, 24), K=self.K, step=2)
        self.assertTrue(inside_margin(self.cams, points, 0.0))
        result = fit.fit_move(self.cams, points, margin=MARGIN)
        self.assertLess(result.scale, 1.0)
        self.assertIn("move shortened to", result.note)
        self.assertTrue(inside_margin(self.cams, points, result.scale), result)

    def test_the_scale_is_the_largest_that_fits(self):
        points = fit.subject_points(np.full((H, W), 5.0), rect(54, 62, 12, 24), K=self.K, step=2)
        result = fit.fit_move(self.cams, points, margin=MARGIN)
        higher = min(1.0, result.scale + 0.02)
        self.assertFalse(inside_margin(self.cams, points, higher))

    def test_a_small_centred_subject_keeps_full_scale(self):
        points = fit.subject_points(np.full((H, W), 5.0), rect(30, 34, 16, 20), K=self.K, step=2)
        result = fit.fit_move(self.cams, points, margin=MARGIN)
        self.assertEqual(result.scale, 1.0)
        self.assertEqual(result.note, "")

    def test_no_subject_leaves_the_move_as_planned(self):
        result = fit.fit_move(self.cams, None, margin=MARGIN)
        self.assertEqual((result.scale, result.note), (1.0, ""))

    def test_a_subject_already_over_the_margin_at_frame_zero_is_left_as_planned(self):
        points = fit.subject_points(np.full((H, W), 5.0), rect(62, 64, 12, 24), K=self.K, step=2)
        result = fit.fit_move(self.cams, points, margin=MARGIN)
        self.assertEqual(result.scale, 1.0)
        self.assertEqual(result.worst_frame, 0)
        self.assertIn("already within 6% of the edge in the first frame; move left as planned", result.note)

    def test_the_note_names_the_percentage(self):
        points = fit.subject_points(np.full((H, W), 5.0), rect(46, 56, 12, 24), K=self.K, step=2)
        result = fit.fit_move(self.cams, points, margin=MARGIN)
        self.assertRegex(result.note, r"move shortened to \d+% to keep the subject 6% inside the frame")


if __name__ == "__main__":
    unittest.main()
