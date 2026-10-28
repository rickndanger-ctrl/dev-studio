"""The esrgan backend: tiling (with a fake network), scales, refusals, SHA check, and the real model if installed."""

import importlib.util
import os
import tempfile
import unittest

import numpy as np

from stillmove.backends.esrgan import DEFAULT_MODEL, EsrganGeneral

MISSING = os.path.join(os.path.dirname(__file__), "no-such-esrgan-model.onnx")


class FakeSession:
    """Stands in for the ONNX session: nearest-neighbour x4, which is local, so tiling must not change it."""

    def run(self, outputs, feed):
        x = feed["image"]
        return [np.repeat(np.repeat(x, 4, axis=2), 4, axis=3)]


def picture(h, w, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


class EsrganTilingTests(unittest.TestCase):
    def test_tiled_x4_equals_the_untiled_picture(self):
        img = picture(50, 37)
        tiled = EsrganGeneral(session=FakeSession(), tile=16, overlap=5).upscale(img, 4)
        whole = EsrganGeneral(session=FakeSession(), tile=1000, overlap=5).upscale(img, 4)
        self.assertEqual(tiled.shape, (200, 148, 3))
        np.testing.assert_array_equal(tiled, whole)

    def test_tiled_x2_matches_the_untiled_picture_within_a_level(self):
        img = picture(40, 33, seed=3)
        tiled = EsrganGeneral(session=FakeSession(), tile=12, overlap=8).upscale(img, 2)
        whole = EsrganGeneral(session=FakeSession(), tile=1000, overlap=8).upscale(img, 2)
        self.assertEqual(tiled.shape, (80, 66, 3))
        self.assertLessEqual(int(np.abs(tiled.astype(int) - whole.astype(int)).max()), 1)

    def test_a_picture_smaller_than_one_tile_works(self):
        out = EsrganGeneral(session=FakeSession(), tile=128).upscale(picture(7, 9), 4)
        self.assertEqual(out.shape, (28, 36, 3))


class EsrganRefusalTests(unittest.TestCase):
    def test_other_scales_are_refused(self):
        with self.assertRaisesRegex(ValueError, "scale 2 or 4"):
            EsrganGeneral(session=FakeSession()).upscale(picture(8, 8), 3)

    def test_a_missing_model_says_where_it_should_be(self):
        with self.assertRaisesRegex(ValueError, "not installed at"):
            EsrganGeneral(model_path=MISSING).upscale(picture(8, 8), 4)

    def test_a_swapped_model_is_refused_with_the_expected_sha(self):
        with tempfile.NamedTemporaryFile(suffix=".onnx", delete=False) as handle:
            handle.write(b"not the pinned model")
        self.addCleanup(os.remove, handle.name)
        with self.assertRaisesRegex(ValueError, "expected SHA256 a93b2f01"):
            EsrganGeneral(model_path=handle.name).upscale(picture(8, 8), 4)


HAVE_MODEL = os.path.isfile(DEFAULT_MODEL) and importlib.util.find_spec("onnxruntime") is not None


@unittest.skipUnless(HAVE_MODEL, "needs onnxruntime and the Real-ESRGAN model at ~/models/realesrgan (see MODELS.md)")
class EsrganModelTests(unittest.TestCase):
    def test_the_real_model_returns_the_right_shape_and_type(self):
        img = picture(30, 45, seed=5)
        out = EsrganGeneral().upscale(img, 4)
        self.assertEqual(out.shape, (120, 180, 3))
        self.assertEqual(out.dtype, np.uint8)


if __name__ == "__main__":
    unittest.main()
