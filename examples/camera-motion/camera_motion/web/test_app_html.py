import html.parser
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.html")
FORMATS = ("json", "re10k", "py", "gltf")


class _Collect(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.doctype = False
        self.attrs = []  # (tag, attribute name, value)
        self.script = []
        self._in_script = False

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype html"):
            self.doctype = True

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            self.attrs.append((tag, name, value or ""))
        if tag == "script":
            self._in_script = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.script.append(data)


def _read():
    with open(APP, encoding="utf-8") as f:
        return f.read()


class AppHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read()
        cls.parser = _Collect()
        cls.parser.feed(cls.text)
        cls.parser.close()
        cls.script = "".join(cls.parser.script)

    def test_parses_as_html(self):
        self.assertTrue(self.parser.doctype)
        self.assertTrue(self.script)

    def test_no_external_urls(self):
        self.assertNotIn("http://", self.text)
        self.assertNotIn("https://", self.text)
        for tag, name, value in self.parser.attrs:
            if name in ("src", "href"):
                self.assertFalse(value.startswith(("http://", "https://", "//")), f"{tag} {name}={value}")

    def test_references_api_and_anim(self):
        self.assertIn("/api/presets", self.script)
        self.assertIn("/anim/", self.script)

    def test_gallery_tiles_get_help_tooltips_via_dom(self):
        self.assertIn(".title =", self.script)
        self.assertIn("When: ", self.script)
        self.assertIn("Feel: ", self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_references_all_download_formats(self):
        self.assertIn("/download/", self.script)
        for fmt in FORMATS:
            self.assertIn(f'"{fmt}"', self.script, fmt)

    def test_links_to_live_viewer(self):
        hrefs = [value for tag, name, value in self.parser.attrs if tag == "a" and name == "href"]
        self.assertIn("/live.html", hrefs)

    def test_script_is_valid_javascript(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.script)
            result = subprocess.run([node, "--check", path], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
