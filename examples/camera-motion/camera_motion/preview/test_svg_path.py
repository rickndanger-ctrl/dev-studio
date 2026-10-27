import contextlib
import io
import json
import math
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from camera_motion.engine.camera_path import path
from camera_motion.preview import render_all
from camera_motion.preview.svg_path import frames_for, look_direction, main, render

NS = "{http://www.w3.org/2000/svg}"
HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "sample_catalog.json")


def _preset(**extra):
    return {"type": "orbit", "params": {"angle": 90.0}, "easing": "ease_in_out",
            "duration_s": 2.0, "fps": 10, **extra}


def _parse(svg_text):
    return ET.fromstring(svg_text)


def _render_text(preset, tmp):
    frames = frames_for(preset)
    out = os.path.join(tmp, "out.svg")
    render(frames, out, title=preset.get("id", "test"))
    with open(out, encoding="utf-8") as f:
        return frames, f.read()


class RenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_xml(self):
        _, text = _render_text(_preset(), self.tmp)
        root = _parse(text)
        self.assertEqual(root.tag, NS + "svg")

    def test_each_panel_has_one_polyline_with_n_points(self):
        frames, text = _render_text(_preset(), self.tmp)
        root = _parse(text)
        lines = root.findall(f".//{NS}polyline[@class='path']")
        self.assertEqual(len(lines), 2)
        for line in lines:
            pts = line.get("points").split()
            self.assertEqual(len(pts), len(frames))

    def test_start_and_end_markers_per_panel(self):
        _, text = _render_text(_preset(), self.tmp)
        root = _parse(text)
        starts = root.findall(f".//{NS}circle[@class='start']")
        ends = root.findall(f".//{NS}circle[@class='end']")
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(ends), 2)
        self.assertTrue(all(c.get("fill") == "green" for c in starts))
        self.assertTrue(all(c.get("fill") == "red" for c in ends))

    def test_subject_and_look_ticks_present(self):
        _, text = _render_text(_preset(), self.tmp)
        root = _parse(text)
        self.assertEqual(len(root.findall(f".//{NS}path[@class='subject']")), 2)
        self.assertGreater(len(root.findall(f".//{NS}line[@class='look']")), 0)

    def test_first_polyline_point_is_start_marker(self):
        _, text = _render_text(_preset(), self.tmp)
        root = _parse(text)
        for line, start in zip(root.findall(f".//{NS}polyline[@class='path']"),
                               root.findall(f".//{NS}circle[@class='start']")):
            first = line.get("points").split()[0].split(",")
            self.assertAlmostEqual(float(first[0]), float(start.get("cx")), places=2)
            self.assertAlmostEqual(float(first[1]), float(start.get("cy")), places=2)

    def test_render_returns_out_path_and_creates_folder(self):
        out = os.path.join(self.tmp, "nested", "dir", "x.svg")
        self.assertEqual(render(frames_for(_preset()), out, title="x"), out)
        self.assertTrue(os.path.isfile(out))

    def test_title_is_escaped(self):
        _, text = _render_text(_preset(id="a<b&c"), self.tmp)
        root = _parse(text)
        title = root.find(f".//{NS}text[@class='title']")
        self.assertEqual(title.text, "a<b&c")

    def test_empty_frames_raise(self):
        with self.assertRaises(ValueError):
            render([], os.path.join(self.tmp, "e.svg"), title="e")


class LookDirectionTests(unittest.TestCase):
    def test_yaw_zero_looks_down_negative_z(self):
        dx, dy, dz = look_direction([0.0, 0.0, 0.0])
        self.assertAlmostEqual(dx, 0.0)
        self.assertAlmostEqual(dy, 0.0)
        self.assertAlmostEqual(dz, -1.0)

    def test_yaw_90_looks_down_negative_x(self):
        dx, _, dz = look_direction([90.0, 0.0, 0.0])
        self.assertAlmostEqual(dx, -1.0)
        self.assertAlmostEqual(dz, 0.0, places=9)

    def test_pitch_90_looks_up(self):
        _, dy, _ = look_direction([0.0, 90.0, 0.0])
        self.assertAlmostEqual(dy, 1.0)

    def test_unit_length(self):
        dx, dy, dz = look_direction([37.0, -12.0, 5.0])
        self.assertAlmostEqual(math.sqrt(dx * dx + dy * dy + dz * dz), 1.0)


class CliTests(unittest.TestCase):
    def test_cli_writes_named_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset_file = os.path.join(tmp, "p.json")
            with open(preset_file, "w", encoding="utf-8") as f:
                json.dump({"id": "cli_case", **_preset()}, f)
            out = os.path.join(tmp, "custom.svg")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([preset_file, "-o", out]), 0)
            with open(out, encoding="utf-8") as f:
                _parse(f.read())

    def test_cli_default_name_uses_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset_file = os.path.join(tmp, "p.json")
            with open(preset_file, "w", encoding="utf-8") as f:
                json.dump({"id": "default_case", **_preset()}, f)
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main([preset_file]), 0)
                self.assertTrue(os.path.isfile(os.path.join(tmp, "default_case.svg")))
            finally:
                os.chdir(cwd)


class CatalogTests(unittest.TestCase):
    def test_every_catalog_preset_renders(self):
        with open(CATALOG, encoding="utf-8") as f:
            presets = json.load(f)
        self.assertGreater(len(presets), 0)
        with tempfile.TemporaryDirectory() as tmp:
            for preset in presets:
                with self.subTest(id=preset["id"]):
                    frames = frames_for(preset)
                    self.assertEqual(frames, path(preset, duration_s=preset.get("duration_s", 3.0),
                                                  fps=preset.get("fps", 24)))
                    out = os.path.join(tmp, preset["id"] + ".svg")
                    render(frames, out, title=preset["id"])
                    with open(out, encoding="utf-8") as f:
                        root = _parse(f.read())
                    lines = root.findall(f".//{NS}polyline[@class='path']")
                    self.assertEqual(len(lines), 2)
                    self.assertEqual(len(lines[0].get("points").split()), len(frames))


class RenderAllTests(unittest.TestCase):
    def test_writes_svg_per_preset_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = render_all.main(CATALOG, tmp)
            self.assertEqual(index, os.path.join(tmp, "index.html"))
            with open(CATALOG, encoding="utf-8") as f:
                presets = json.load(f)
            for preset in presets:
                svg = os.path.join(tmp, preset["id"] + ".svg")
                self.assertTrue(os.path.isfile(svg))
                with open(svg, encoding="utf-8") as f:
                    _parse(f.read())
            with open(index, encoding="utf-8") as f:
                html = f.read()
            for preset in presets:
                self.assertIn(f'src="{preset["id"]}.svg"', html)


if __name__ == "__main__":
    unittest.main()
