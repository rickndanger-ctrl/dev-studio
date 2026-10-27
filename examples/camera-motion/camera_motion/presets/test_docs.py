"""Tests for the generated preset docs.

The generator is presets/make_docs.py, which exposes:
    render_markdown() -> str
    render_html() -> str

These tests check that both outputs name every catalog preset, that repeated runs
are identical, that the committed files under camera-motion/docs/ match a fresh
render, and that the HTML loads nothing from the network.

Run from camera-motion/:
    python3 -W error -m unittest camera_motion.presets.test_docs
"""

import html as html_lib
import os
import re
import unittest

from camera_motion.presets import make_docs, validate

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))  # camera-motion/

CATALOG_FILE = os.path.join(HERE, "catalog.json")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
MARKDOWN_FILE = os.path.join(DOCS_DIR, "PRESETS.md")
HTML_FILE = os.path.join(DOCS_DIR, "presets.html")

# An attribute value that starts with http:// or https:// (quoted or bare), or a CSS url() to one.
NETWORK_ATTRIBUTE = re.compile(r"""\b(?:src|href)\s*=\s*["']?\s*https?:""", re.IGNORECASE)
NETWORK_CSS = re.compile(r"""url\(\s*["']?\s*https?:""", re.IGNORECASE)


def load_presets():
    catalog = validate.load_json(CATALOG_FILE)
    return catalog if isinstance(catalog, list) else catalog["presets"]


def read_committed(path):
    with open(path, "rb") as handle:
        return handle.read()


class RenderedContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.presets = load_presets()
        cls.markdown = make_docs.render_markdown()
        cls.html = make_docs.render_html()

    def test_outputs_are_strings(self):
        self.assertIsInstance(self.markdown, str)
        self.assertIsInstance(self.html, str)
        self.assertTrue(self.markdown.strip())
        self.assertTrue(self.html.strip())

    def test_markdown_names_every_preset_id_and_name(self):
        for preset in self.presets:
            with self.subTest(id=preset["id"]):
                self.assertIn(preset["id"], self.markdown)
                self.assertIn(preset["name"], self.markdown)

    def test_html_names_every_preset_id_and_name(self):
        for preset in self.presets:
            with self.subTest(id=preset["id"]):
                self.assertIn(preset["id"], self.html)
                # The name may be escaped in HTML (for example "&" becomes "&amp;").
                self.assertIn(html_lib.escape(preset["name"], quote=True), self.html)

    def test_two_runs_are_identical(self):
        self.assertEqual(make_docs.render_markdown(), self.markdown)
        self.assertEqual(make_docs.render_html(), self.html)

    def test_html_loads_nothing_from_the_network(self):
        self.assertIsNone(
            NETWORK_ATTRIBUTE.search(self.html),
            "src or href points at http(s): %s" % NETWORK_ATTRIBUTE.search(self.html),
        )
        self.assertIsNone(NETWORK_CSS.search(self.html), "CSS url() points at http(s)")


class CommittedDocsTests(unittest.TestCase):
    def test_committed_markdown_matches_fresh_output(self):
        fresh = make_docs.render_markdown().encode("utf-8")
        self.assertEqual(
            read_committed(MARKDOWN_FILE),
            fresh,
            "docs/PRESETS.md is stale; regenerate it from make_docs.render_markdown()",
        )

    def test_committed_html_matches_fresh_output(self):
        fresh = make_docs.render_html().encode("utf-8")
        self.assertEqual(
            read_committed(HTML_FILE),
            fresh,
            "docs/presets.html is stale; regenerate it from make_docs.render_html()",
        )


if __name__ == "__main__":
    unittest.main()
