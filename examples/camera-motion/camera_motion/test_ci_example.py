import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, "docs", "ci-example.yml")

REQUIRED = (
    "name: camera-motion",
    "on:",
    "push:",
    "pull_request:",
    "runs-on: ${{ matrix.os }}",
    "ubuntu-latest",
    "macos-latest",
    '"3.9"',
    '"3.12"',
    "actions/checkout@v4",
    "actions/setup-python@v5",
    "working-directory: examples/camera-motion",
    "python -W error -m unittest",
    "pip install .",
    "camera-motion doctor",
)

ABSOLUTE_MARKERS = ("/Users/", "/home/", "/private/", "/tmp/", "/var/", "C:\\", "~/")
# A slash that starts a token (after whitespace, a quote, '=', '(', ':' or ',') is an absolute path.
LEADING_SLASH = re.compile(r"""(?:^|[\s"'=(:,])/""")


def _text():
    with open(WORKFLOW, encoding="utf-8") as f:
        return f.read()


class CiExampleTests(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(os.path.isfile(WORKFLOW), WORKFLOW)

    def test_contains_required_keys_and_commands(self):
        text = _text()
        for needle in REQUIRED:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_has_no_tabs(self):
        self.assertNotIn("\t", _text())

    def test_has_no_absolute_paths(self):
        text = _text()
        for marker in ABSOLUTE_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, text)
        self.assertIsNone(LEADING_SLASH.search(text), LEADING_SLASH.search(text))

    def test_has_no_secrets(self):
        self.assertNotIn("secrets.", _text())


if __name__ == "__main__":
    unittest.main()
