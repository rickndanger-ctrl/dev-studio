"""Release checks: one version everywhere, and the folder works when copied elsewhere."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from camera_motion import __version__

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # camera-motion/
IGNORE = shutil.ignore_patterns("__pycache__", "build", "*.egg-info", "*.pyc")
PLACE_NAMES = ("/Users/", "/home/", "/private/", "/var/folders/")
# Test files that hold these strings on purpose, as fixtures.
FIXTURE_FILES = {"test_privacy.py", "test_tutorial.py", "test_release.py", "test_ci_example.py"}


def _pyproject_version():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        text = f.read()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise AssertionError("pyproject.toml has no version")
    return match.group(1)


def _changelog_top_version():
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        for line in f:
            match = re.match(r"^## \[?([^\]\s]+)\]?", line)
            if match:
                return match.group(1)
    raise AssertionError("CHANGELOG.md has no version heading")


class ReleaseTests(unittest.TestCase):
    def test_versions_agree(self):
        self.assertEqual(_pyproject_version(), __version__)
        self.assertEqual(_changelog_top_version(), __version__)

    def test_copy_runs_doctor_and_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "camera-motion")
            shutil.copytree(ROOT, copy, ignore=IGNORE)
            env = dict(os.environ, PYTHONPATH=copy)

            doctor = subprocess.run([sys.executable, "-m", "camera_motion.cli", "doctor"],
                                    cwd=copy, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
            lines = doctor.stdout.splitlines()
            self.assertEqual(len(lines), 8, doctor.stdout)
            self.assertTrue(all(line.startswith("PASS") for line in lines), doctor.stdout)

            tests = subprocess.run([sys.executable, "-W", "error", "-m", "unittest",
                                    "camera_motion.engine.test_camera_path"],
                                   cwd=copy, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(tests.returncode, 0, tests.stdout + tests.stderr)

    def test_no_machine_paths_in_the_folder(self):
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "build", "sequences") and not d.endswith(".egg-info")]
            for name in filenames:
                if name in FIXTURE_FILES or not name.endswith((".py", ".md", ".toml", ".html", ".json")):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                for place in PLACE_NAMES:
                    with self.subTest(file=path, place=place):
                        self.assertNotIn(place, text)


if __name__ == "__main__":
    unittest.main()
