import unittest

import numpy as np

from stillmove.depth import DepthBackend, check_depth, get_backend, smooth_depth


def _image(h=18, w=32):
    return np.zeros((h, w, 3), dtype=np.uint8)


class FakeBackendTests(unittest.TestCase):
    def test_plane_is_constant_five(self):
        depth = get_backend("plane").depth(_image())
        self.assertEqual(depth.shape, (18, 32))
        self.assertEqual(depth.dtype, np.float32)
        np.testing.assert_allclose(depth, 5.0)

    def test_gradient_is_far_at_top_and_near_at_bottom(self):
        depth = get_backend("gradient").depth(_image())
        self.assertAlmostEqual(float(depth[0, 0]), 8.0, places=5)
        self.assertAlmostEqual(float(depth[-1, 0]), 3.0, places=5)
        column = depth[:, 0]
        self.assertTrue(np.all(np.diff(column) <= 0))

    def test_backends_are_depth_backends(self):
        for name in ("plane", "gradient"):
            with self.subTest(name=name):
                self.assertIsInstance(get_backend(name), DepthBackend)
                self.assertEqual(get_backend(name).name, name)

    def test_unknown_backend_is_a_value_error(self):
        with self.assertRaisesRegex(ValueError, "unknown depth backend"):
            get_backend("no-such-model")


class CheckDepthTests(unittest.TestCase):
    def test_accepts_matching_positive_depth(self):
        arr = check_depth(np.full((4, 6), 2.0), (4, 6))
        self.assertEqual(arr.dtype, np.float32)

    def test_rejects_wrong_shape(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            check_depth(np.full((4, 5), 2.0), (4, 6))

    def test_rejects_zero_or_negative_depth(self):
        for bad in (0.0, -1.0):
            with self.subTest(value=bad):
                with self.assertRaisesRegex(ValueError, "greater than 0"):
                    check_depth(np.full((4, 6), bad), (4, 6))


class SmoothDepthTest(unittest.TestCase):
    def test_flat_depth_stays_flat(self):
        depth = np.full((20, 24), 4.0)
        img = np.full((20, 24, 3), 90, dtype=np.uint8)
        self.assertLess(np.abs(smooth_depth(depth, img) - 4.0).max(), 1e-12)

    def test_isolated_speck_is_removed(self):
        depth = np.full((20, 24), 4.0)
        depth[10, 12] = 9.0
        img = np.full((20, 24, 3), 90, dtype=np.uint8)
        out = smooth_depth(depth, img)
        self.assertLess(abs(out[10, 12] - 4.0), 0.05)

    def test_depth_step_at_an_image_edge_is_kept(self):
        depth = np.where(np.arange(30)[None, :] < 15, 3.0, 8.0) * np.ones((20, 1))
        img = np.where(np.arange(30)[None, :, None] < 15, 40, 220) * np.ones((20, 1, 3))
        img = img.astype(np.uint8)
        out = smooth_depth(depth, img)
        self.assertAlmostEqual(out[10, 12], 3.0, places=2)
        self.assertAlmostEqual(out[10, 17], 8.0, places=2)


if __name__ == "__main__":
    unittest.main()
