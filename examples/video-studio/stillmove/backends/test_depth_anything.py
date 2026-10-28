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
    def test_model_size_keeps_ratio_and_multiples_of_14(self):
        self.assertEqual(da._model_size(576, 1024), (294, 518))
        self.assertEqual(da._model_size(1024, 576), (518, 294))
        h, w = da._model_size(100, 333)
        self.assertEqual((h % 14, w % 14), (0, 0))

    def test_disparity_maps_nearest_to_near_and_farthest_to_far(self):
        disparity = np.array([[0.0, 2.0, 4.0]], dtype=np.float32)
        depth = da._disparity_to_depth(disparity)
        self.assertAlmostEqual(float(depth[0, 2]), da.NEAR, places=5)  # largest disparity = nearest
        self.assertAlmostEqual(float(depth[0, 0]), da.FAR, places=5)  # smallest = farthest
        self.assertTrue(np.all(depth > 0))

    def test_constant_disparity_gives_one_constant_depth(self):
        depth = da._disparity_to_depth(np.full((4, 4), 7.0, dtype=np.float32))
        self.assertTrue(np.allclose(depth, da.FAR))

    def test_result_passes_the_depth_check(self):
        disparity = np.linspace(0.1, 9.0, 12, dtype=np.float32).reshape(3, 4)
        check_depth(da._disparity_to_depth(disparity), (3, 4))


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
