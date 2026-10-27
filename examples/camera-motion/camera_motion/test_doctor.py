import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from camera_motion import doctor
from camera_motion.cli import main as cli_main
from camera_motion.presets import load_catalog


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

    def test_help_present_check_runs(self):
        code, out = _run(doctor.main)
        self.assertIn("PASS help present", out)
        self.assertEqual(len(doctor.CHECKS), 9)

    def test_missing_help_fails_and_exits_1(self):
        with mock.patch.object(doctor, "load_help", side_effect=FileNotFoundError("help file not found: x")):
            code, out = _run(doctor.main)
        self.assertEqual(code, 1)
        self.assertIn("FAIL help present: FileNotFoundError: help file not found", out)

    def test_doctor_reads_the_catalog_with_load_catalog(self):
        with mock.patch.object(doctor, "load_catalog", wraps=load_catalog) as spy:
            code, out = _run(doctor.main)
        self.assertEqual(code, 0, out)
        self.assertTrue(spy.called)

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
