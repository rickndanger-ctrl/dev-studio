import json
import math
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

from camera_motion.engine.camera_path import path
from camera_motion.preview.anim_svg import render_animated

NS = "{http://www.w3.org/2000/svg}"
FPS = 24
DURATION_S = 3.0
HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(os.path.dirname(HERE), "presets", "catalog.json")
MAX_BYTES = 60000


def _catalog():
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f)


def _group(root, gid):
    found = [g for g in root.iter(f"{NS}g") if g.get("id") == gid]
    assert len(found) == 1, gid
    return found[0]


def _floats(values):
    return [float(v) for v in values.split(";")]


def _pairs(values):
    return [tuple(float(c) for c in v.split()) for v in values.split(";")]


def _strip_animations(root):
    """Remove every animate* element, leaving only the static SVG."""
    for parent in root.iter():
        for child in list(parent):
            if child.tag.startswith(f"{NS}animate"):
                parent.remove(child)


def _polyline(root, css_class):
    found = [p for p in root.iter(f"{NS}polyline") if p.get("class") == css_class]
    assert len(found) == 1, css_class
    return found[0]


def _first_point(polyline):
    x, y = polyline.get("points").split()[0].split(",")
    return (round(float(x), 2) + 0.0, round(float(y), 2) + 0.0)


def _translate(transform):
    match = re.fullmatch(r"translate\(([-\d.]+) ([-\d.]+)\)", transform or "")
    assert match, transform
    return (round(float(match.group(1)), 2) + 0.0, round(float(match.group(2)), 2) + 0.0)


def _rotate(transform):
    match = re.fullmatch(r"rotate\(([-\d.]+)\)", transform or "")
    assert match, transform
    return float(match.group(1))


class AnimSvgTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _render(self, preset):
        frames = path(preset, duration_s=DURATION_S, fps=FPS)
        out = os.path.join(self._tmp.name, preset["id"] + ".svg")
        render_animated(frames, out, title=preset["id"], fps=FPS)
        with open(out, "rb") as f:
            raw = f.read()
        return frames, raw

    def test_every_catalog_preset(self):
        presets = _catalog()
        self.assertEqual(len(presets), 14)
        for preset in presets:
            with self.subTest(id=preset["id"]):
                frames, raw = self._render(preset)
                n = len(frames)

                self.assertLess(len(raw), MAX_BYTES)

                root = ET.fromstring(raw)  # raises if the XML does not parse
                motions = root.findall(f".//{NS}animateMotion")
                self.assertEqual(len(motions), 1)
                motion = motions[0]

                dur = float(motion.get("dur").rstrip("s"))
                self.assertAlmostEqual(dur, (n - 1) / FPS, places=9)

                key_times = motion.get("keyTimes").split(";")
                key_points = motion.get("keyPoints").split(";")
                self.assertEqual(len(key_times), n)
                self.assertEqual(len(key_points), n)
                self.assertAlmostEqual(float(key_times[0]), 0.0)
                self.assertAlmostEqual(float(key_times[-1]), 1.0)
                # Top-down view: only x and z move the camera on screen. A pan, tilt or
                # crane has a zero-length top-down path, so every key is 0.
                moves = any((f["position"][0], f["position"][2])
                            != (frames[0]["position"][0], frames[0]["position"][2]) for f in frames)
                self.assertAlmostEqual(float(key_points[-1]), 1.0 if moves else 0.0)

                top_rotate = _group(root, "cam").find(f"{NS}g").find(f"{NS}animateTransform")
                self.assertEqual(top_rotate.get("type"), "rotate")
                yaw_values = _floats(top_rotate.get("values"))
                self.assertEqual(len(yaw_values), n)
                for got, frame in zip(yaw_values, frames):
                    self.assertAlmostEqual(got, -frame["rotation"][0], delta=0.005)

                side = _group(root, "cam-side")
                translate = side.find(f"{NS}animateTransform")
                self.assertEqual(translate.get("type"), "translate")
                self.assertEqual(len(translate.get("values").split(";")), n)
                self.assertEqual(translate.get("keyTimes"), motion.get("keyTimes"))
                side_rotate = side.find(f"{NS}g").find(f"{NS}animateTransform")
                self.assertEqual(side_rotate.get("type"), "rotate")
                self.assertEqual(len(_floats(side_rotate.get("values"))), n)
                self.assertEqual(side_rotate.get("keyTimes"), motion.get("keyTimes"))
                self.assertEqual(side_rotate.get("dur"), motion.get("dur"))

    def test_camera_group_and_path_present(self):
        preset = _catalog()[0]
        _, raw = self._render(preset)
        root = ET.fromstring(raw)
        cam = [g for g in root.iter(f"{NS}g") if g.get("id") == "cam"]
        self.assertEqual(len(cam), 1)
        self.assertIsNotNone(cam[0].find(f"{NS}animateMotion"))
        self.assertTrue(root.findall(f".//{NS}polyline[@class='path']"))
        self.assertTrue(root.findall(f".//{NS}polyline[@class='path-side']"))

    def test_side_rotate_tracks_pitch(self):
        preset = next(p for p in _catalog() if p["id"] == "tilt_up")
        frames, raw = self._render(preset)
        root = ET.fromstring(raw)
        rotate = _group(root, "cam-side").find(f"{NS}g").find(f"{NS}animateTransform")
        values = _floats(rotate.get("values"))
        self.assertGreater(len(set(values)), 1)
        for got, frame in zip(values, frames):
            self.assertAlmostEqual(got, frame["rotation"][1], delta=0.005)

    def test_tilt_up_nose_is_above_tail_on_last_frame(self):
        preset = next(p for p in _catalog() if p["id"] == "tilt_up")
        frames, raw = self._render(preset)
        root = ET.fromstring(raw)
        rotate = _group(root, "cam-side").find(f"{NS}g").find(f"{NS}animateTransform")
        angle = math.radians(_floats(rotate.get("values"))[-1])
        self.assertGreater(frames[-1]["rotation"][1], 0)

        def turn(x, y):  # SVG rotate(a): clockwise on screen
            return (x * math.cos(angle) - y * math.sin(angle),
                    x * math.sin(angle) + y * math.cos(angle))

        nose_y = turn(-8, 0)[1]
        tail_y = (turn(6, -5)[1] + turn(6, 5)[1]) / 2
        self.assertLess(nose_y, tail_y)

    def test_crane_up_side_translate_y_changes(self):
        preset = next(p for p in _catalog() if p["id"] == "crane_up")
        frames, raw = self._render(preset)
        root = ET.fromstring(raw)
        translate = _group(root, "cam-side").find(f"{NS}animateTransform")
        ys = [y for _, y in _pairs(translate.get("values"))]
        self.assertGreater(len(set(ys)), 1)
        self.assertLess(ys[-1], ys[0])  # crane up raises the camera, so screen y falls

    def test_static_fallback_sits_at_frame_zero(self):
        # Without SMIL, each marker must draw at frame 0: the static transforms resolve to it.
        for preset in _catalog():
            with self.subTest(id=preset["id"]):
                frames, raw = self._render(preset)
                root = ET.fromstring(raw)
                _strip_animations(root)

                top_first = _first_point(_polyline(root, "path"))
                side_first = _first_point(_polyline(root, "path-side"))

                cam = _group(root, "cam")
                self.assertEqual(_translate(cam.get("transform")), top_first)
                top_rotate = _rotate(cam.find(f"{NS}g").get("transform"))
                self.assertAlmostEqual(top_rotate, -frames[0]["rotation"][0], delta=0.005)

                side = _group(root, "cam-side")
                self.assertEqual(_translate(side.get("transform")), side_first)
                side_rotate = _rotate(side.find(f"{NS}g").get("transform"))
                self.assertAlmostEqual(side_rotate, frames[0]["rotation"][1], delta=0.005)

    def test_rejects_single_frame(self):
        with self.assertRaises(ValueError):
            render_animated([{"position": [0, 0, 5], "rotation": [0, 0, 0], "fov": 50}],
                            os.path.join(self._tmp.name, "x.svg"), title="x")


if __name__ == "__main__":
    unittest.main()
