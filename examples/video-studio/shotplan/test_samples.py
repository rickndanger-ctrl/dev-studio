"""The shipped samples: each passes check, draws a timeline that parses as XML, and exports.

The CLI's own output is captured, so a test run prints only the unittest summary.
"""

import contextlib
import glob
import io
import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

import shotplan  # noqa: E402  (sets up the import path for camera_motion)
from camera_motion.presets.validate import load_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = sorted(glob.glob(os.path.join(HERE, "samples", "*.json")))
SVG = "{http://www.w3.org/2000/svg}"


def quiet(argv):
    """Run shotplan's CLI with its output captured; return the exit status."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return shotplan.main(argv)


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_three_samples_are_shipped(self):
        names = {os.path.basename(p) for p in SAMPLES}
        self.assertEqual(names, {"product_reveal.json", "house_tour.json", "travel_montage.json"})

    def test_each_sample_passes_check(self):
        for path in SAMPLES:
            with self.subTest(sample=os.path.basename(path)):
                errors, rows, _ = shotplan.check(load_json(path), os.path.dirname(path))
                self.assertEqual(errors, [])
                self.assertTrue(rows)

    def test_each_sample_timeline_parses_with_one_block_per_move(self):
        for path in SAMPLES:
            with self.subTest(sample=os.path.basename(path)):
                _, _, seqs = shotplan.check(load_json(path), os.path.dirname(path))
                out = os.path.join(self.dir, os.path.basename(path) + ".svg")
                self.assertEqual(quiet(["timeline", path, "--svg", out]), 0)
                root = ET.parse(out).getroot()
                moves = [e for e in root.iter(SVG + "rect") if e.get("class") == "move"]
                self.assertEqual(len(moves), sum(len(s["segments"]) for s in seqs.values()))

    def test_each_sample_timeline_has_a_lane_per_shot_and_a_band_per_crossfade(self):
        for path in SAMPLES:
            with self.subTest(sample=os.path.basename(path)):
                plan = load_json(path)
                out = os.path.join(self.dir, os.path.basename(path) + ".svg")
                self.assertEqual(quiet(["timeline", path, "--svg", out]), 0)
                root = ET.parse(out).getroot()
                spans = [e for e in root.iter(SVG + "rect") if e.get("class") == "span"]
                bands = [e for e in root.iter(SVG + "rect") if e.get("class") == "crossfade"]
                crossfades = [s for s in plan["shots"][:-1] if isinstance(s["transition"], dict)]
                self.assertEqual(len(spans), len(plan["shots"]))
                self.assertEqual(len(bands), len(crossfades))

    def test_each_sample_exports_one_file_per_shot(self):
        for path in SAMPLES:
            with self.subTest(sample=os.path.basename(path)):
                plan = load_json(path)
                out = os.path.join(self.dir, plan["id"])
                self.assertEqual(quiet(["export", path, out]), 0)
                for shot in plan["shots"]:
                    with open(os.path.join(out, shot["id"] + ".json"), encoding="utf-8") as handle:
                        data = json.load(handle)
                    self.assertEqual((data["fps"], data["width"], data["height"]),
                                     (plan["fps"], plan["width"], plan["height"]))
                    self.assertGreater(len(data["frames"]), 1)

    def test_a_bad_plan_gets_no_timeline_and_exit_1(self):
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write('{"id": "bad", "name": "Bad", "fps": 24, "width": 320, "height": 180, "shots": '
                         '[{"id": "a", "image": "placeholder:x", "sequence": {"segments": '
                         '[{"preset": "dolly_in", "duration_s": 1}]}, "duration": 2, "transition": "cut"}]}')
        out = os.path.join(self.dir, "bad.svg")
        self.assertEqual(quiet(["timeline", bad, "--svg", out]), 1)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
