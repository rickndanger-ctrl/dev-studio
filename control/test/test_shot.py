"""Checks for test/shot.sh that run without chromium or a live viewer.

Run from the repo root:  python3 -m unittest test/test_shot.py
"""
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
SHOT = os.path.join(HERE, "shot.sh")


def dry_run(cwd, *args):
    env = dict(os.environ, SHOT_DRY_RUN="1")
    return subprocess.run(["bash", SHOT, *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=30)


class ShotDryRunTest(unittest.TestCase):
    def test_relative_outdir_resolves_against_caller_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            r = dry_run(tmp, "rel/out", "8810")
            self.assertEqual(r.returncode, 0, r.stderr)
            expected = os.path.join(tmp, "rel", "out")
            self.assertTrue(os.path.isdir(expected), "OUTDIR was not created under the caller's cwd")
            self.assertIn(f"OUTDIR={expected} ", r.stdout)
            self.assertIn(os.path.join(expected, "viewer-1280x800.png"), r.stdout)
            self.assertFalse(os.path.exists(os.path.join(HERE, "rel")), "relative OUTDIR leaked into the repo")

    def test_absolute_outdir_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            target = os.path.join(tmp, "abs-out")
            r = dry_run(os.getcwd(), target, "8810")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isdir(target))
            self.assertIn(f"OUTDIR={target} ", r.stdout)

    def test_refuses_live_viewer_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = dry_run(os.path.realpath(tmp), os.path.join(tmp, "out"), "8765")
            self.assertEqual(r.returncode, 2)
            self.assertIn("8765", r.stderr)


if __name__ == "__main__":
    unittest.main()
