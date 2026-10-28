"""Checks on makeclip/app.html: it parses, its script is valid JavaScript, every control id the script
uses exists, every POST sends the header a cross-site form cannot set, and it loads nothing from
outside this Mac.
"""

import html.parser
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "app.html")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
EXTERNAL = re.compile(r"https?://|(?:src|href|action)\s*=\s*[\"']//", re.IGNORECASE)


def read_page():
    with open(PAGE, encoding="utf-8") as handle:
        return handle.read()


class _Collector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.stack = []
        self.errors = []
        self.script = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)
        if tag == "script":
            self._in_script = True
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("</%s> does not close an open <%s>" % (tag, self.stack[-1] if self.stack else "nothing"))
            return
        self.stack.pop()

    def handle_data(self, data):
        if self._in_script:
            self.script.append(data)


def parse():
    collector = _Collector()
    collector.feed(read_page())
    collector.close()
    return collector


def script_text():
    return "".join(parse().script)


class PageTests(unittest.TestCase):
    def test_page_parses_with_balanced_tags(self):
        collector = parse()
        self.assertEqual(collector.errors, [])
        self.assertEqual(collector.stack, [], "tags left open: %s" % collector.stack)

    def test_script_is_valid_javascript(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script_text())
            result = subprocess.run([node, "--check", path], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_control_id_the_script_uses_exists_in_the_page(self):
        used = set(re.findall(r"\$\(\"([A-Za-z0-9_-]+)\"\)", script_text()))
        self.assertTrue(used)
        missing = sorted(used - parse().ids)
        self.assertEqual(missing, [], "ids used by the script but not in the page")

    def test_every_post_sends_the_makeclip_header(self):
        text = script_text()
        self.assertIn('const HEADER = "makeclip";', text)
        self.assertEqual(text.count('method: "POST"'), 1, "every POST must go through post()")
        body = text[text.index("async function post("):]
        body = body[:body.index("\nfunction plainError")]
        self.assertIn('"X-Requested-With": HEADER', body)
        self.assertEqual(text.count("fetch("), 2, "fetch is called only by getJSON and post")

    def test_every_api_route_the_page_uses_is_in_the_contract(self):
        contract = {"api/options", "api/images", "api/upload", "api/render", "api/render/status", "api/clips",
                    "api/promo/plans", "api/promo/load", "api/promo/render"}
        used = set(re.findall(r"\"(api/[a-z/]+)", script_text()))
        self.assertTrue(used <= contract, used - contract)
        self.assertEqual(used, contract)

    def test_no_external_urls(self):
        text = read_page()
        self.assertEqual(EXTERNAL.findall(text), [])
        self.assertNotIn("<link", text, "no stylesheets or fonts from elsewhere")

    def test_the_plain_language_controls_are_present(self):
        text = read_page()
        for phrase in ("Layers (cut out the subject)", "Square 1080", "Wide 16:9", "Vertical",
                       "Real (learned depth)", "Make clip", "Download MP4", "Download GIF",
                       "Recent clips", "not installed", "Drop a PNG or JPEG picture here"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_the_smooth_control_is_sent_and_not_blocked_for_every_size(self):
        text = read_page()
        self.assertIn('id="smooth"', text)
        self.assertIn("smooth: Number($(\"smooth\").value)", text)
        self.assertNotIn("syncSmooth", text)
        self.assertNotIn("smooth-note", text)

    def test_the_result_line_names_the_interpolation_smooth_used(self):
        self.assertIn("smooth-result", parse().ids)
        text = script_text()
        self.assertIn("function showSmoothResult(smooth)", text)
        self.assertIn("showSmoothResult(status.smooth);", text)
        self.assertIn('smooth.backend === "rife" ? "RIFE model" : "linear blend"', text)
        self.assertIn('$("smooth-result").hidden = true;', text)

    def test_recent_clips_use_the_friendly_date_formatter(self):
        text = script_text()
        self.assertIn("function formatMade(iso, now)", text)
        self.assertIn("formatMade(c.made)", text)

    def test_the_make_a_promo_tab_is_in_the_page(self):
        ids = parse().ids
        for name in ("tab-clip-btn", "tab-promo-btn", "tab-clip", "tab-promo", "promo-plans", "promo-load",
                     "promo-missing", "promo-shots", "promo-add", "promo-size", "promo-render", "promo-message",
                     "promo-progress-wrap", "promo-bar", "promo-progress-text", "promo-video",
                     "promo-downloads", "promo-dl-mp4", "promo-dl-gif"):
            with self.subTest(id=name):
                self.assertIn(name, ids)
        text = read_page()
        self.assertIn("Make a promo", text)
        self.assertIn("shot \" + shot + \" of \" + shots", script_text())

    def test_a_plan_picture_is_labelled_by_file_name_and_the_found_flag(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not on PATH")
        match = re.search(r"function pictureLabel\(.*?\n\}\n", script_text(), re.DOTALL)
        self.assertIsNotNone(match, "pictureLabel is missing from the page")
        probe = match.group(0) + (
            'console.log(JSON.stringify([pictureLabel("../stillmove-input/logo.png", true),'
            ' pictureLabel("../stillmove-input/logo.png", false),'
            ' pictureLabel("upload.png", undefined)]));')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "label.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(probe)
            result = subprocess.run([node, path], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            "logo.png (from the plan)",
            "logo.png (still needed)",
            "upload.png (not on this Mac)"])
        self.assertIn("fillRowPictures(select, select.value, row._planFound);", script_text())
        self.assertIn("planFound: shot.found", script_text())

    def test_the_plan_dropdown_starts_on_a_named_placeholder_and_is_wide_enough(self):
        self.assertIn('<select id="promo-plans" aria-label="Plan files"><option value="">Choose a plan…</option></select>',
                      read_page())
        self.assertIn("#promo-plans { flex: 1 1 16em; }", read_page())
        self.assertIn('none.textContent = state.plans.length ? "Choose a plan…"', script_text())

    def test_the_keep_in_frame_note_shows_under_the_finished_video(self):
        ids = parse().ids
        self.assertIn("fit-note", ids)
        self.assertIn("promo-fit-note", ids)
        text = script_text()
        self.assertIn("function showFitNote(id, notes)", text)
        self.assertIn("showClip(status.clip, status.notes);", text)
        self.assertIn("showPromoClip(status.clip, status.notes);", text)
        self.assertIn('$("fit-note").hidden = true;', text)
        self.assertIn('$("promo-fit-note").hidden = true;', text)

    def test_progress_reads_the_status_route(self):
        text = script_text()
        self.assertIn("frame \" + frame + \" of \" + frames", text)
        self.assertIn("setInterval(checkStatus, 1000)", text)


if __name__ == "__main__":
    unittest.main()
