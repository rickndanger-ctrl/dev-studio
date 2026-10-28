"""Tests for the doctor: the loading folder, the shotplan checks and the original checks."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from stillmove import doctor


class LocationReportTest(unittest.TestCase):
    def test_main_checkout_prints_the_folder_and_no_warning(self):
        lines = doctor.location_report("/path/to/DevStudio/video-studio/stillmove/doctor.py")
        self.assertEqual(lines, [("INFO", "loading from /path/to/DevStudio/video-studio")])

    def test_worktree_path_gets_a_warning(self):
        path = "/path/to/DevStudio/.claude/worktrees/worker-1/video-studio/stillmove/doctor.py"
        statuses = [status for status, _ in doctor.location_report(path)]
        self.assertEqual(statuses, ["INFO", "WARN"])
        self.assertIn("loading from a worktree (.claude/worktrees/...)", doctor.location_report(path)[1][1])

    def test_default_path_comes_from_the_module_file(self):
        path = "/path/to/DevStudio/.claude/worktrees/worker-1/video-studio/stillmove/doctor.py"
        with mock.patch.object(doctor, "__file__", path):
            lines = doctor.location_report()
        self.assertEqual(lines[0][0], "INFO")
        self.assertTrue(any(status == "WARN" for status, _ in lines))

    def test_doctor_output_names_the_folder(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            doctor.main()
        self.assertIn("INFO loading from ", out.getvalue())

SHOTPLAN_CHECKS = ("shotplan import", "shotplan web import", "shotplan fake render")
ORIGINAL_CHECKS = ("python >= 3.9", "pillow", "numpy", "camera_motion import", "fake render",
                   "ffmpeg", "depth model", "onnxruntime")


class DoctorChecksTests(unittest.TestCase):
    def test_the_checks_are_listed_in_order(self):
        names = tuple(name for name, _ in doctor.CHECKS)
        self.assertEqual(names, ORIGINAL_CHECKS + SHOTPLAN_CHECKS)

    def test_shotplan_import_passes(self):
        self.assertEqual(doctor._check_shotplan_import(), ("PASS", "shotplan import"))

    def test_shotplan_web_import_passes(self):
        self.assertEqual(doctor._check_shotplan_web_import(), ("PASS", "shotplan web import"))

    def test_shotplan_fake_render_renders_the_shortest_sample(self):
        status, detail = doctor._check_shotplan_render()
        self.assertEqual(status, "PASS", detail)
        self.assertIn("product_reveal", detail)  # the shortest of the three samples

    def test_no_sample_plans_is_a_failure(self):
        empty = tempfile.mkdtemp()
        try:
            status, detail = doctor._check_shotplan_render(samples=empty)
        finally:
            shutil.rmtree(empty, ignore_errors=True)
        self.assertEqual(status, "FAIL")
        self.assertIn("no sample plans", detail)

    def test_a_sample_that_fails_check_is_a_failure_naming_the_file(self):
        folder = tempfile.mkdtemp()
        try:
            bad = {"id": "bad", "name": "Bad", "fps": 24, "width": 64, "height": 36, "shots": [
                {"id": "a", "image": "placeholder:a",
                 "sequence": {"segments": [{"preset": "dolly_in", "duration_s": 1}]},
                 "duration": 2, "transition": "cut"}]}  # the duration does not match the sequence
            with open(os.path.join(folder, "bad.json"), "w", encoding="utf-8") as handle:
                json.dump(bad, handle)
            status, detail = doctor._check_shotplan_render(samples=folder)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.assertEqual(status, "FAIL")
        self.assertIn("bad.json", detail)
        self.assertIn("does not match", detail)

    def test_a_crashing_check_is_a_failure_not_a_crash(self):
        def boom():
            raise RuntimeError("kaboom")

        with mock.patch.object(doctor, "CHECKS", (("boom", boom),)):
            results = doctor.run_checks()
        self.assertEqual(results, [("FAIL", "boom", "RuntimeError: kaboom")])

    def test_main_exits_0_only_without_a_fail(self):
        def ok():
            return "PASS", "fine"

        def fail():
            return "FAIL", "bad"

        with mock.patch.object(doctor, "CHECKS", (("ok", ok),)):
            self.assertEqual(doctor.main(), 0)
        with mock.patch.object(doctor, "CHECKS", (("ok", ok), ("fail", fail))):
            self.assertEqual(doctor.main(), 1)

    def test_a_warn_does_not_fail_the_doctor(self):
        def warn():
            return "WARN", "optional thing is off"

        with mock.patch.object(doctor, "CHECKS", (("warn", warn),)):
            self.assertEqual(doctor.main(), 0)


if __name__ == "__main__":
    unittest.main()
