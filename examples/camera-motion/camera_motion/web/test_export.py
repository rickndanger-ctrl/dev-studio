import json
import math
import os
import tempfile
import unittest

from camera_motion.engine.camera_path import path
from camera_motion.web import export_frames
from camera_motion.web.export_frames import DURATION_S, FPS, build, default_catalog, main

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(os.path.dirname(HERE), "preview", "sample_catalog.json")


def _load(path_):
    with open(path_, encoding="utf-8") as f:
        return json.load(f)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.presets = _load(SAMPLE)
        self.out = os.path.join(self.tmp, "frames.json")
        main(SAMPLE, self.out)
        self.doc = _load(self.out)

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_preset_present(self):
        self.assertEqual(sorted(self.doc), sorted(p["id"] for p in self.presets))

    def test_entry_metadata(self):
        for preset in self.presets:
            with self.subTest(id=preset["id"]):
                entry = self.doc[preset["id"]]
                self.assertEqual(entry["name"], preset.get("name", preset["id"]))
                self.assertEqual(entry["type"], preset["type"])
                self.assertEqual(entry["fps"], FPS)

    def test_frame_counts_match_engine_path(self):
        for preset in self.presets:
            with self.subTest(id=preset["id"]):
                expected = path(preset, duration_s=DURATION_S, fps=FPS)
                self.assertEqual(len(self.doc[preset["id"]]["frames"]), len(expected))

    def test_frame_values_match_engine_path(self):
        for preset in self.presets:
            with self.subTest(id=preset["id"]):
                expected = path(preset, duration_s=DURATION_S, fps=FPS)
                for got, want in zip(self.doc[preset["id"]]["frames"], expected):
                    self.assertAlmostEqual(got["t"], want["t"])
                    self.assertAlmostEqual(got["fov"], want["fov"])
                    for g, w in zip(got["position"] + got["rotation"],
                                    want["position"] + want["rotation"]):
                        self.assertAlmostEqual(g, w)

    def test_frame_shape(self):
        for entry in self.doc.values():
            for frame in entry["frames"]:
                self.assertEqual(set(frame), {"t", "position", "rotation", "fov"})
                self.assertEqual(len(frame["position"]), 3)
                self.assertEqual(len(frame["rotation"]), 3)

    def test_all_numbers_finite(self):
        for pid, entry in self.doc.items():
            for frame in entry["frames"]:
                numbers = [frame["t"], frame["fov"], *frame["position"], *frame["rotation"]]
                with self.subTest(id=pid):
                    self.assertTrue(all(math.isfinite(n) for n in numbers))

    def test_build_rejects_duplicate_ids(self):
        catalog = os.path.join(self.tmp, "dup.json")
        preset = self.presets[0]
        with open(catalog, "w", encoding="utf-8") as f:
            json.dump([preset, preset], f)
        with self.assertRaises(ValueError):
            build(catalog)

    def test_default_catalog_exists(self):
        self.assertTrue(os.path.isfile(default_catalog()))

    def test_main_returns_out_path_and_creates_folder(self):
        out = os.path.join(self.tmp, "nested", "frames.json")
        self.assertEqual(main(SAMPLE, out), out)
        self.assertTrue(os.path.isfile(out))

    def test_build_accepts_the_object_form_catalog(self):
        object_form = os.path.join(self.tmp, "object_form.json")
        with open(object_form, "w", encoding="utf-8") as f:
            json.dump({"presets": self.presets}, f)
        doc = build(object_form)
        self.assertEqual(set(doc), {preset["id"] for preset in self.presets})


if __name__ == "__main__":
    unittest.main()
