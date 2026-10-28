import os
import shutil
import tempfile
import unittest
from unittest import mock

from stillmove import models


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "model.onnx")
        with open(self.path, "wb") as handle:
            handle.write(b"the real model bytes")
        self.good = models.file_sha256(self.path)

    def test_the_pinned_file_passes_and_returns_the_path(self):
        self.assertEqual(models.verify(self.path, self.good), self.path)

    def test_a_swapped_file_is_refused_and_names_the_expected_sha(self):
        expected = "a" * 64
        with self.assertRaises(ValueError) as ctx:
            models.verify(self.path, expected)
        self.assertIn(expected, str(ctx.exception))
        self.assertIn(self.good, str(ctx.exception))

    def test_a_swapped_file_is_refused_after_the_first_pass(self):
        models.verify(self.path, self.good)
        with open(self.path, "wb") as handle:
            handle.write(b"a different file of a different size")
        with self.assertRaises(ValueError):
            models.verify(self.path, self.good)

    def test_the_hash_is_cached_per_path_size_and_mtime(self):
        models.verify(self.path, self.good)
        with mock.patch.object(models, "file_sha256", wraps=models.file_sha256) as hashed:
            models.verify(self.path, self.good)
            models.verify(self.path, self.good)
        self.assertEqual(hashed.call_count, 0, "a second check must not hash the file again")

    def test_a_missing_file_is_a_file_not_found_error(self):
        with self.assertRaises(FileNotFoundError):
            models.verify(os.path.join(self.dir, "nope.onnx"), self.good)


if __name__ == "__main__":
    unittest.main()


class CallersRefuseASwappedFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.swapped = os.path.join(self.dir, "swapped.onnx")
        with open(self.swapped, "wb") as handle:
            handle.write(b"not the pinned model")

    def test_depth_model_refuses_a_swapped_file_before_loading_it(self):
        from stillmove.backends import depth_anything

        with self.assertRaises(ValueError) as ctx:
            depth_anything.DepthAnythingSmall(model_path=self.swapped)
        self.assertIn(depth_anything.MODEL_SHA256, str(ctx.exception))

    def test_isnet_matte_refuses_a_swapped_file_before_loading_it(self):
        from stillmove import matte

        with self.assertRaises(ValueError) as ctx:
            matte.IsnetMatte(model_path=self.swapped)
        self.assertIn(matte.ISNET_SHA256, str(ctx.exception))

    def test_doctor_reports_a_swapped_depth_model_with_the_expected_sha(self):
        from stillmove import doctor
        from stillmove.backends import depth_anything

        with mock.patch.object(depth_anything, "DEFAULT_MODEL", self.swapped):
            status, detail = doctor._check_depth_model()
        self.assertEqual(status, "FAIL")
        self.assertIn(depth_anything.MODEL_SHA256, detail)
