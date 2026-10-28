"""Tests for stillmove.matte: the fake ellipse, the backend registry, the 8-bit mask, the CLI, the isnet
error paths, and the isnet model itself (skipped unless the model and onnxruntime are present)."""

import contextlib
import io
import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from stillmove import cli, matte


def model_ready():
    if not os.path.isfile(matte.DEFAULT_ISNET_MODEL):
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


class FakeMatteTests(unittest.TestCase):
    def test_centre_is_one_corner_is_zero(self):
        alpha = matte.FakeMatte().alpha(np.zeros((40, 60, 3), dtype=np.uint8))
        self.assertEqual(alpha.shape, (40, 60))
        self.assertAlmostEqual(float(alpha[20, 30]), 1.0, places=6)
        self.assertAlmostEqual(float(alpha[0, 0]), 0.0, places=6)
        self.assertAlmostEqual(float(alpha[39, 59]), 0.0, places=6)

    def test_values_are_in_range_and_the_edge_is_soft(self):
        alpha = matte.FakeMatte().alpha(np.zeros((40, 60, 3), dtype=np.uint8))
        self.assertTrue(np.all(alpha >= 0.0) and np.all(alpha <= 1.0))
        middle = alpha[(alpha > 0.05) & (alpha < 0.95)]
        self.assertGreater(middle.size, 0)  # some pixels sit on the soft edge, not only 0 and 1

    def test_the_mask_is_symmetric_about_the_centre(self):
        alpha = matte.FakeMatte().alpha(np.zeros((40, 60, 3), dtype=np.uint8))
        np.testing.assert_allclose(alpha, alpha[::-1, ::-1], atol=1e-6)


class RegistryTests(unittest.TestCase):
    def test_get_matte_by_name(self):
        self.assertEqual(matte.get_matte("fake").name, "fake")

    def test_unknown_backend_is_refused(self):
        with self.assertRaises(ValueError):
            matte.get_matte("nope")

    def test_to_8bit_rounds_to_0_255(self):
        mask = matte.to_8bit(np.array([[0.0, 0.5, 1.0, 1.7, -0.2]]))
        self.assertEqual(mask.dtype, np.uint8)
        self.assertEqual(mask.tolist(), [[0, 128, 255, 255, 0]])


class IsnetErrorTests(unittest.TestCase):
    def test_a_missing_model_is_a_clear_error(self):
        folder = tempfile.mkdtemp()
        try:
            with self.assertRaises(FileNotFoundError) as ctx:
                matte.IsnetMatte(model_path=os.path.join(folder, "nope.onnx"))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.assertIn("MODELS.md", str(ctx.exception))


class CliMatteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.image = os.path.join(self.dir, "still.png")
        Image.new("RGB", (60, 40), (90, 120, 150)).save(self.image)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cli_writes_an_8_bit_mask_the_size_of_the_image(self):
        out = os.path.join(self.dir, "mask.png")
        with contextlib.redirect_stdout(io.StringIO()):  # the "wrote ..." line is checked elsewhere, not printed
            self.assertEqual(cli.main(["matte", self.image, "--backend", "fake", "-o", out]), 0)
        with Image.open(out) as mask:
            self.assertEqual(mask.mode, "L")
            self.assertEqual(mask.size, (60, 40))
            values = np.asarray(mask)
        self.assertEqual(int(values[20, 30]), 255)
        self.assertEqual(int(values[0, 0]), 0)

    def test_cli_isnet_without_the_model_is_one_error_line(self):
        out = os.path.join(self.dir, "mask.png")
        if os.path.isfile(matte.DEFAULT_ISNET_MODEL):
            self.skipTest("the model is present here; this checks the missing-model path")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["matte", self.image, "--backend", "isnet", "-o", out])
        self.assertEqual(code, 1)
        self.assertIn("matte model not found", err.getvalue())  # the one error line, captured here
        self.assertFalse(os.path.exists(out))


@unittest.skipUnless(model_ready(), "needs the isnet model and onnxruntime (see video-studio/MODELS.md)")
class IsnetModelTests(unittest.TestCase):
    def test_isnet_alpha_has_the_image_size_and_range(self):
        backend = matte.IsnetMatte()
        rgb = np.zeros((48, 64, 3), dtype=np.uint8)
        rgb[12:36, 16:48] = (200, 180, 60)
        alpha = backend.alpha(rgb)
        self.assertEqual(alpha.shape, (48, 64))
        self.assertEqual(alpha.dtype, np.float32)
        self.assertTrue(np.all(alpha >= 0.0) and np.all(alpha <= 1.0))


if __name__ == "__main__":
    unittest.main()
