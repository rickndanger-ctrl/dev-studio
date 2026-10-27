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


def _is_environment(dirpath):
    return os.path.isfile(os.path.join(dirpath, "pyvenv.cfg"))


def machine_path_hits(root):
    """(file, place) for each machine path found in the folder's source files.

    Skips dot-directories and any directory that holds a pyvenv.cfg (a virtual environment),
    plus caches, build output and sequences.
    """
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", "build", "sequences")
                       and not d.startswith(".")
                       and not d.endswith(".egg-info")
                       and not _is_environment(os.path.join(dirpath, d))]
        for name in filenames:
            if name in FIXTURE_FILES or not name.endswith((".py", ".md", ".toml", ".html", ".json")):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            hits.extend((path, place) for place in PLACE_NAMES if place in text)
    return hits


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


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
            self.assertEqual(len(lines), 9, doctor.stdout)
            self.assertTrue(all(line.startswith("PASS") for line in lines), doctor.stdout)

            tests = subprocess.run([sys.executable, "-W", "error", "-m", "unittest",
                                    "camera_motion.engine.test_camera_path"],
                                   cwd=copy, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(tests.returncode, 0, tests.stdout + tests.stderr)

    def test_no_machine_paths_in_the_folder(self):
        self.assertEqual(machine_path_hits(ROOT), [])

    def test_scan_ignores_virtual_environments_and_dot_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = "path = '/Users/example/x'\n"
            for rel in (".venv/lib/mod.py", ".hidden/mod.py", "myenv/lib/mod.py"):
                full = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(full))
                with open(full, "w", encoding="utf-8") as f:
                    f.write(bad)
            with open(os.path.join(tmp, "myenv", "pyvenv.cfg"), "w", encoding="utf-8") as f:
                f.write("home = /usr/bin\n")
            os.makedirs(os.path.join(tmp, "src"))
            real = os.path.join(tmp, "src", "mod.py")
            with open(real, "w", encoding="utf-8") as f:
                f.write(bad)
            self.assertEqual(machine_path_hits(tmp), [(real, "/Users/")])

    def test_one_venv_location_in_readme_and_tutorial(self):
        for text in (_read("README.md"), _read("docs", "TUTORIAL.md")):
            self.assertIn("~/venvs/camera-motion", text)
            self.assertNotIn(".venv", text)

    def test_readme_tests_section_names_the_folder(self):
        tests = _read("README.md").split("## Tests", 1)[1]
        self.assertIn("folder that contains pyproject.toml", tests)

    def test_troubleshooting_covers_port_and_page_title(self):
        for text in (_read("README.md"), _read("docs", "TUTORIAL.md")):
            troubleshooting = text.split("## Troubleshooting", 1)[1]
            self.assertIn("port N is in use", troubleshooting)
            self.assertIn("'Camera motion'", troubleshooting)


if __name__ == "__main__":
    unittest.main()
