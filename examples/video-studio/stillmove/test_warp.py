import unittest

import numpy as np

from stillmove import cameras, warp


def _K(w, h, f):
    return np.array([[f, 0.0, w / 2.0], [0.0, f, h / 2.0], [0.0, 0.0, 1.0]])


def _translate(x=0.0, y=0.0, z=0.0):
    t = np.eye(4)
    t[:3, 3] = [x, y, z]
    return t


def _project(K, w2c, point):
    p = w2c @ np.append(point, 1.0)
    u = K[0, 0] * p[0] / p[2] + K[0, 2]
    v = K[1, 1] * p[1] / p[2] + K[1, 2]
    return u, v


class IdentityAndShiftTest(unittest.TestCase):
    def test_identity_returns_the_same_image_and_no_holes(self):
        rng = np.random.default_rng(1)
        img = rng.integers(0, 255, (20, 30, 3), dtype=np.uint8)
        depth = np.full((20, 30), 2.0)
        K = _K(30, 20, 25.0)
        out, filled = warp.warp(img, depth, K, K, np.eye(4))
        self.assertTrue(np.array_equal(out, img))
        self.assertFalse(filled.any())

    def test_sideways_translation_shifts_a_fronto_parallel_plane_by_f_tx_over_z(self):
        w, h, z, tx = 80, 40, 4.0, 0.4
        K = _K(w, h, 50.0)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[:, 40:42] = 255  # a bright stripe, centred at column 41
        depth = np.full((h, w), z)
        # Camera moves +X by tx, so content moves left by fx*tx/z pixels.
        out, filled = warp.warp(img, depth, K, K, _translate(x=-tx))
        cols = np.nonzero(out[h // 2, :, 0] > 127)[0]
        centre = cols.mean() + 0.5
        expected = 41.0 - K[0, 0] * tx / z
        self.assertLess(abs(centre - expected), 1.0)

    def test_dolly_in_enlarges_the_centre_patch(self):
        w, h = 60, 40
        K = _K(w, h, 40.0)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[10:30, 20:40, 0] = 255
        depth = np.full((h, w), 2.0)
        before = (img[..., 0] > 127).sum()
        # Moving 0.5 toward the plane (z = 2 -> 1.5) scales content by 2 / 1.5.
        out, _ = warp.warp(img, depth, K, K, _translate(z=-0.5))
        after = (out[..., 0] > 127).sum()
        self.assertGreater(after, before * 1.5)
        self.assertGreater(out[20, 30, 0], 127)  # centre stays red


class CameraPresetDirectionTest(unittest.TestCase):
    """The warp must agree with projecting one world point through the same camera poses."""

    def _check(self, preset_id, expected_sign):
        w, h = 160, 90
        cams = cameras.from_preset(preset_id, 3.0, 24, w, h)
        K = cams.frames[0].K
        img = np.zeros((h, w, 3), dtype=np.uint8)
        depth = np.full((h, w), 5.0)  # the plane z = 0 seen from the start camera
        # Paint the pixels whose start-camera position is within 0.1 of world x = 0.5 on z = 0.
        # (x = 1 leaves the frame under pan_left, so the probe point is closer to the centre.)
        for v in range(h):
            for u in range(w):
                x = (u + 0.5 - K[0, 2]) / K[0, 0] * 5.0
                if abs(x - 0.5) < 0.1:
                    img[v, u] = 255
        last = len(cams.frames) - 1
        T = warp.relative(cams, last)
        out, filled = warp.warp(img, depth, K, cams.frames[last].K, T)
        hit = (out[..., 0] > 127) & ~filled
        self.assertTrue(hit.any(), "stripe disappeared")
        centre_u = np.nonzero(hit)[1].mean() + 0.5

        u0, _ = _project(K, cams.frames[0].w2c, np.array([0.5, 0.0, 0.0]))
        u1, _ = _project(cams.frames[last].K, cams.frames[last].w2c, np.array([0.5, 0.0, 0.0]))
        self.assertLess(abs(centre_u - u1), 1.5)
        self.assertEqual(np.sign(u1 - u0), expected_sign)

    def test_dolly_left_moves_content_right(self):
        self._check("dolly_left", +1)

    def test_dolly_right_moves_content_left(self):
        self._check("dolly_right", -1)

    def test_pan_left_moves_content_right(self):
        self._check("pan_left", +1)

    def test_pan_right_moves_content_left(self):
        self._check("pan_right", -1)

    # Both orbits swing the camera around the subject, so a point on the right of the subject
    # drifts toward the centre of the frame for either direction. The check still ties the warp to
    # projection; the left/right difference shows in the camera position, checked below.
    def test_orbit_left_matches_projection(self):
        self._check("orbit_left", -1)

    def test_orbit_right_matches_projection(self):
        self._check("orbit_right", -1)

    def test_orbit_left_ends_on_the_left_and_right_on_the_right(self):
        def centre_x(preset_id):
            cams = cameras.from_preset(preset_id, 3.0, 24, 160, 90)
            return np.linalg.inv(cams.frames[-1].w2c)[0, 3]

        self.assertLess(centre_x("orbit_left"), -1.0)
        self.assertGreater(centre_x("orbit_right"), 1.0)


class HoleFillTest(unittest.TestCase):
    def test_hole_takes_the_background_side_not_the_nearer_one(self):
        # One row: pixel 0 is near (depth 3, colour 10), pixel 5 is far (depth 8, colour 90).
        img = np.zeros((1, 6, 3), dtype=np.uint8)
        img[0, 0] = 10
        img[0, 5] = 90
        depth = np.zeros((1, 6))
        depth[0, 0], depth[0, 5] = 3.0, 8.0
        written = np.array([[True, False, False, False, False, True]])
        out, holes = warp.fill_holes(img, written, depth)
        self.assertTrue(holes[0, 1:5].all())
        self.assertFalse(holes[0, 0])
        for col in range(1, 5):
            self.assertEqual(out[0, col, 0], 90, f"hole {col} should copy the far pixel")

    def test_empty_rows_are_filled_from_the_column(self):
        img = np.zeros((3, 2, 3), dtype=np.uint8)
        img[0] = 7
        depth = np.zeros((3, 2))
        depth[0] = 4.0
        written = np.zeros((3, 2), dtype=bool)
        written[0] = True
        out, _ = warp.fill_holes(img, written, depth)
        self.assertTrue((out[..., 0] == 7).all())

    def test_warp_with_holes_leaves_no_empty_pixel(self):
        w, h = 60, 30
        K = _K(w, h, 40.0)
        img = np.full((h, w, 3), 200, dtype=np.uint8)  # no pixel is 0, so 0 would mean empty
        depth = np.full((h, w), 3.0)
        out, filled = warp.warp(img, depth, K, K, _translate(x=-0.8))
        self.assertTrue(filled.any())
        self.assertTrue((out > 0).all())

    def test_hole_fraction_is_the_share_of_filled_pixels(self):
        filled = np.zeros((4, 5), dtype=bool)
        filled[0, :] = True
        self.assertAlmostEqual(warp.hole_fraction(filled), 0.25)
        self.assertEqual(warp.hole_fraction(np.zeros((2, 2), dtype=bool)), 0.0)


class TwoPlaneGapTest(unittest.TestCase):
    """Near plane (A, depth 3) on the left half, far plane (B, depth 8) on the right half.

    The camera moves sideways, so the near plane slides further than the far one and a gap opens
    at the boundary. The gap must take the far colour B, never the near colour A.
    """

    A = np.array([220, 30, 30], dtype=np.uint8)
    B = np.array([30, 30, 220], dtype=np.uint8)

    def _scene(self):
        w, h = 120, 60
        K = _K(w, h, 60.0)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        depth = np.zeros((h, w))
        img[:, : w // 2] = self.A
        img[:, w // 2 :] = self.B
        depth[:, : w // 2] = 3.0
        depth[:, w // 2 :] = 8.0
        return img, depth, K

    def test_gap_takes_the_far_colour(self):
        img, depth, K = self._scene()
        out, filled = warp.warp(img, depth, K, K, _translate(x=-0.5))
        self.assertTrue(filled.any(), "the move must open a gap")
        filled_pixels = out[filled].astype(int)
        self.assertTrue(np.all(np.abs(filled_pixels - self.B.astype(int)).max(axis=1) <= 2))
        self.assertFalse(np.any(np.abs(filled_pixels - self.A.astype(int)).max(axis=1) <= 2))

    def test_no_empty_pixels_remain(self):
        img, depth, K = self._scene()
        out, _ = warp.warp(img, depth, K, K, _translate(x=-0.5))
        self.assertTrue((out.sum(axis=-1) > 0).all())

    def test_identity_is_still_exact(self):
        img, depth, K = self._scene()
        out, filled = warp.warp(img, depth, K, K, np.eye(4))
        self.assertTrue(np.array_equal(out, img))
        self.assertFalse(filled.any())

    def test_hole_fraction_reports_the_revealed_share(self):
        img, depth, K = self._scene()
        _, filled = warp.warp(img, depth, K, K, _translate(x=-0.5))
        fraction = warp.hole_fraction(filled)
        self.assertGreater(fraction, 0.0)
        self.assertLess(fraction, 0.2)


def _interior_fraction(filled, margin=8):
    """Share of revealed pixels away from the image border (where a camera move brings in new content)."""
    inner = np.asarray(filled, dtype=bool)[margin:-margin, margin:-margin]
    return float(np.mean(inner))


class SamplingCrackTest(unittest.TestCase):
    """Magnification must not leave 1-px cracks; only real disocclusions count as revealed."""

    def _plane(self, w, h, z):
        K = _K(w, h, 60.0)
        img = np.full((h, w, 3), 180, dtype=np.uint8)  # constant colour: 0 means empty
        depth = np.full((h, w), z)
        return img, depth, K

    def test_dolly_in_on_a_plane_leaves_no_interior_holes(self):
        img, depth, K = self._plane(160, 90, 3.0)
        out, filled = warp.warp(img, depth, K, K, _translate(z=-0.15))
        self.assertLess(_interior_fraction(filled), 0.002)
        self.assertTrue((out > 0).all())

    def test_pan_two_degrees_on_a_plane_leaves_no_interior_holes(self):
        img, depth, K = self._plane(160, 90, 3.0)
        t = np.eye(4)
        a = np.radians(2.0)
        t[0, 0], t[0, 2], t[2, 0], t[2, 2] = np.cos(a), np.sin(a), -np.sin(a), np.cos(a)
        out, filled = warp.warp(img, depth, K, K, t)
        # A pan reveals a true strip at the image edge (content from outside the frame). Only the
        # interior is checked for cracks.
        self.assertLess(_interior_fraction(filled), 0.002)
        self.assertTrue((out > 0).all())


def _line_plane(w=200, h=120, vertical=(30, 70, 110, 150), horizontal=(30, 60, 90), width=2):
    """White plane at depth 3 with thin black lines (generated in code)."""
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    for c in vertical:
        img[:, c:c + width] = 0
    for r in horizontal:
        img[r:r + width, :] = 0
    return img, np.full((h, w), 3.0), _K(w, h, 80.0)


def _specks(out):
    """Dark pixels whose 8 neighbours are all light, away from the border."""
    dark = out.astype(float).mean(axis=-1) < 128
    light = ~dark
    count = 0
    for y in range(1, dark.shape[0] - 1):
        for x in range(1, dark.shape[1] - 1):
            if dark[y, x] and light[y - 1:y + 2, x - 1:x + 2].sum() == 8:
                count += 1
    return count


class ThinLineTest(unittest.TestCase):
    """Backward warping must not turn thin dark lines into specks or break them up."""

    def test_dolly_in_on_thin_lines_leaves_no_specks(self):
        img, depth, K = _line_plane()
        out, _ = warp.warp(img, depth, K, K, _translate(z=-0.6))
        self.assertEqual(_specks(out), 0)

    def test_thin_lines_stay_continuous_along_their_length(self):
        img, depth, K = _line_plane(horizontal=())  # vertical lines only, so each row has all of them
        out, _ = warp.warp(img, depth, K, K, _translate(z=-0.6))
        w = img.shape[1]
        cx = w / 2.0
        magnification = 3.0 / 2.4  # depth 3 -> 2.4 after the dolly
        expected = [cx + (c + 1 - cx) * magnification for c in (30, 70, 110, 150)]
        dark = out.astype(float).mean(axis=-1) < 128
        for y in range(10, img.shape[0] - 10):
            for centre in expected:
                lo, hi = int(centre) - 3, int(centre) + 4
                self.assertTrue(dark[y, lo:hi].any(), f"line at x~{centre:.0f} broke at row {y}")


class RelativeTest(unittest.TestCase):
    def test_relative_at_frame_zero_is_identity(self):
        cams = cameras.from_preset("dolly_in", 1.0, 4, 64, 36)
        self.assertTrue(np.allclose(warp.relative(cams, 0), np.eye(4), atol=1e-12))


if __name__ == "__main__":
    unittest.main()
