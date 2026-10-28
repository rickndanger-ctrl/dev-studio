import hashlib
import os
import unittest

import numpy as np

from stillmove.backends import depth_anything as da
from stillmove.depth import check_depth

try:
    import onnxruntime  # noqa: F401

    _HAVE_RUNTIME = True
except ImportError:
    _HAVE_RUNTIME = False

_HAVE_MODEL = os.path.isfile(da.DEFAULT_MODEL) and _HAVE_RUNTIME


class MathTest(unittest.TestCase):
    """disparity_to_depth is pure: these run without the model."""

    def test_model_size_keeps_ratio_and_multiples_of_14(self):
        self.assertEqual(da._model_size(576, 1024), (294, 518))
        self.assertEqual(da._model_size(1024, 576), (518, 294))
        h, w = da._model_size(100, 333)
        self.assertEqual((h % 14, w % 14), (0, 0))

    def test_flat_picture_stays_near_the_subject_depth(self):
        # Uniform disparity with +-1% noise: the picture is almost flat.
        rng = np.random.default_rng(0)
        disparity = 100.0 * (1.0 + rng.uniform(-0.01, 0.01, size=(60, 80)))
        depth = da.disparity_to_depth(disparity)
        self.assertLess(depth.max() - depth.min(), 0.3)
        self.assertLess(abs(float(np.median(depth)) - 5.0), 0.2)

    def test_strong_contrast_uses_the_full_three_to_eight_range(self):
        disparity = np.linspace(1.0, 10.0, 200)
        depth = da.disparity_to_depth(disparity)
        self.assertAlmostEqual(float(depth.min()), 3.0, delta=0.2)
        self.assertAlmostEqual(float(depth.max()), 8.0, delta=0.2)

    def test_more_disparity_means_nearer(self):
        disparity = np.linspace(1.0, 10.0, 200)
        depth = da.disparity_to_depth(disparity)
        self.assertTrue(np.all(np.diff(depth) <= 1e-12))  # depth never grows as disparity grows

    def test_outliers_above_p98_do_not_widen_the_range(self):
        base = np.linspace(1.0, 10.0, 1000)
        spiked = base.copy()
        spiked[:5] = 1e6  # a few extreme values far above the 98th percentile
        clean = da.disparity_to_depth(base)
        with_outliers = da.disparity_to_depth(spiked)
        self.assertLessEqual(with_outliers.max(), 8.0 + 1e-9)
        self.assertGreaterEqual(with_outliers.min(), 3.0 - 1e-9)
        self.assertAlmostEqual(float(with_outliers.max() - with_outliers.min()),
                               float(clean.max() - clean.min()), delta=0.2)

    def test_constant_disparity_is_flat_at_the_subject_depth(self):
        depth = da.disparity_to_depth(np.full((4, 4), 7.0))
        self.assertTrue(np.allclose(depth, 5.0))

    def test_result_passes_the_depth_check(self):
        disparity = np.linspace(0.1, 9.0, 12).reshape(3, 4)
        check_depth(da.disparity_to_depth(disparity), (3, 4))


class MissingModelTest(unittest.TestCase):
    def test_missing_file_raises_a_clear_error(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            da.DepthAnythingSmall(model_path="/nonexistent/model.onnx")
        self.assertIn("MODELS.md", str(ctx.exception))


@unittest.skipUnless(_HAVE_MODEL, "depth model not installed")
class ModelTest(unittest.TestCase):
    def test_file_matches_the_pinned_sha256(self):
        digest = hashlib.sha256()
        with open(da.DEFAULT_MODEL, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        self.assertEqual(digest.hexdigest(), da.MODEL_SHA256)

    def test_depth_of_a_generated_image_is_finite_and_shaped(self):
        backend = da.DepthAnythingSmall()
        image = np.zeros((48, 80, 3), dtype=np.uint8)
        image[:, 40:] = 200  # two flat halves, generated in code
        depth = backend.depth(image)
        self.assertEqual(depth.shape, (48, 80))
        self.assertEqual(depth.dtype, np.float32)
        self.assertTrue(np.all(np.isfinite(depth)))
        self.assertTrue(np.all(depth > 0))


if __name__ == "__main__":
    unittest.main()
