"""The one pyproject.toml in video-studio/: name, licence, both packages, both entry points.

Read as text, so it runs on Python 3.9 (no tomllib). The entry-point targets are imported to
prove they exist.
"""

import importlib
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def _text():
    with open(PYPROJECT, encoding="utf-8") as handle:
        return handle.read()


class PyprojectTests(unittest.TestCase):
    def test_one_project_named_video_studio_with_mit_licence(self):
        text = _text()
        self.assertRegex(text, r'(?m)^name = "video-studio"$')
        self.assertRegex(text, r'(?m)^version = "0\.2\.1"$')
        self.assertRegex(text, r'(?m)^license = \{text = "MIT"\}$')
        self.assertRegex(text, r'(?m)^requires-python = ">=3\.9"$')

    def test_both_entry_points_are_listed(self):
        text = _text()
        self.assertRegex(text, r'(?m)^stillmove = "stillmove\.cli:main"$')
        self.assertRegex(text, r'(?m)^shotplan = "shotplan\.shotplan:main"$')

    def test_both_packages_are_found_and_depth_is_an_extra(self):
        text = _text()
        self.assertIn('"stillmove", "stillmove.*"', text)
        self.assertIn('"shotplan", "shotplan.*"', text)
        self.assertRegex(text, r'(?m)^depth = \["onnxruntime"\]$')
        self.assertRegex(text, r'(?m)^dependencies = \["numpy", "Pillow"\]$')

    def test_package_data_lists_the_runtime_files(self):
        text = _text()
        for name in ("limits.json", "web/app.html", "schema.json", "samples/*.json", "samples/sequences/*.json"):
            self.assertIn('"%s"' % name, text)

    def test_entry_point_targets_import_and_are_callable(self):
        self.assertTrue(callable(importlib.import_module("stillmove.cli").main))
        self.assertTrue(callable(importlib.import_module("shotplan.shotplan").main))


if __name__ == "__main__":
    unittest.main()
