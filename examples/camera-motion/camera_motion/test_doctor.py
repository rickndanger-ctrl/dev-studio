import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from camera_motion import doctor
from camera_motion.cli import main as cli_main


def _run(fn):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fn()
    return code, out.getvalue()


class DoctorTests(unittest.TestCase):
    def test_all_checks_pass(self):
        code, out = _run(doctor.main)
        self.assertEqual(code, 0, out)
        self.assertNotIn("FAIL", out)
        self.assertEqual(out.count("PASS "), len(doctor.CHECKS))

    def test_cli_doctor_exits_0(self):
        code, out = _run(lambda: cli_main(["doctor"]))
        self.assertEqual(code, 0, out)

    def test_missing_catalog_fails_and_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing.json")
            with mock.patch.object(doctor, "CATALOG", missing):
                code, out = _run(doctor.main)
        self.assertEqual(code, 1)
        self.assertIn("FAIL catalog validates:", out)

    def test_missing_package_file_fails_and_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(doctor, "PACKAGE", tmp):
                code, out = _run(doctor.main)
        self.assertEqual(code, 1)
        self.assertIn("FAIL package data present: missing", out)


if __name__ == "__main__":
    unittest.main()
