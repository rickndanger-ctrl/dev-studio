import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "docs", "ci-example.yml")


def _text():
    with open(WORKFLOW, encoding="utf-8") as handle:
        return handle.read()


class CiExampleTest(unittest.TestCase):
    def test_required_steps_and_triggers_are_present(self):
        text = _text()
        for needle in (
            "push:",
            "pull_request:",
            "ubuntu-latest",
            "macos-latest",
            '"3.9"',
            '"3.12"',
            "working-directory: examples/video-studio",
            "actions/checkout@v4",
            "python -W error -m unittest",
            "pip install ../camera-motion",
            "pip install .",
            "stillmove doctor",
            ".github/workflows/video-studio.yml",
        ):
            self.assertIn(needle, text, needle)

    def test_ubuntu_on_3_9_is_excluded(self):
        text = _text()
        self.assertRegex(
            text,
            r"exclude:\s*\n\s*- os: ubuntu-latest\s*\n\s*python-version: \"3\.9\"",
        )

    def test_no_depth_extra_and_no_onnxruntime(self):
        text = _text()
        self.assertNotIn("[depth]", text)
        for line in text.splitlines():
            if "pip" in line:
                self.assertNotIn("onnxruntime", line.lower(), line)

    def test_no_absolute_paths(self):
        text = _text()
        self.assertIsNone(re.search(r"(^|[\s'\"=])/(Users|home|root|private|tmp|var|opt)/", text))
        self.assertIsNone(re.search(r"[A-Za-z]:\\\\", text))

    def test_no_secrets(self):
        text = _text().lower()
        for word in ("secrets.", "token", "password", "api_key", "apikey"):
            self.assertNotIn(word, text)


class StepOrderTests(unittest.TestCase):
    def test_install_runs_before_the_unit_tests(self):
        # The tests import numpy and Pillow, which only the Install step provides.
        with open(WORKFLOW, encoding="utf-8") as f:
            text = f.read()
        self.assertLess(text.index("name: Install"), text.index("name: Unit tests"))


if __name__ == "__main__":
    unittest.main()
