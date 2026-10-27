"""Checks on the shipped sequences: the samples and example.json.

Covers camera-frame moves after a turn, orbits that hold distance, exact joins
between segments, and no internal names in the sequence files.
"""
import glob
import math
import os
import re
import unittest

from camera_motion.engine.camera_path import path
from camera_motion.presets.validate import load_json
from camera_motion.sequences.build import build
from camera_motion.sequences.validate import merge_params, validate_sequence

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(os.path.dirname(HERE))
CATALOG = os.path.join(PACKAGE_DIR, "camera_motion", "presets", "catalog.json")
SAMPLES = sorted(glob.glob(os.path.join(HERE, "samples", "*.json")))
EXAMPLE = os.path.join(HERE, "example.json")
SHIPPED = SAMPLES + [EXAMPLE]
SELF = os.path.abspath(__file__)
INTERNAL_NAME = re.compile(r"Mini [12]", re.IGNORECASE)


def _catalog_by_id():
    data = load_json(CATALOG)
    presets = data if isinstance(data, list) else data["presets"]
    return {preset["id"]: preset for preset in presets}


def _distance(position):
    return math.sqrt(sum(c * c for c in position))


def _sequence(*segments):
    return {"id": "t", "name": "t", "description": "t", "fps": 24, "segments": list(segments)}


def _seg(preset, duration_s, **params):
    seg = {"preset": preset, "duration_s": duration_s}
    if params:
        seg["params"] = params
    return seg


class CameraFrameTests(unittest.TestCase):
    def test_camera_frame_dolly_in_after_orbit_ends_closer_to_origin(self):
        orbit_only = build(_sequence(_seg("dolly_in", 2), _seg("orbit_left", 3, radius=None)), load_json(CATALOG))
        camera = build(_sequence(_seg("dolly_in", 2), _seg("orbit_left", 3, radius=None),
                                 _seg("dolly_in", 1, frame="camera")), load_json(CATALOG))
        world = build(_sequence(_seg("dolly_in", 2), _seg("orbit_left", 3, radius=None),
                                _seg("dolly_in", 1)), load_json(CATALOG))

        before = _distance(orbit_only[-1]["position"])
        after_camera = _distance(camera[-1]["position"])
        after_world = _distance(world[-1]["position"])

        # The camera-frame dolly moves 1.0 along the view, which points at the subject.
        self.assertAlmostEqual(before - after_camera, 1.0, places=2)
        # The world-frame dolly runs along world -z, which no longer points at the subject,
        # so it closes only part of the unit (measured: about 0.63 of 1.0).
        self.assertLess(before - after_world, 0.9)
        self.assertGreater(after_world - after_camera, 0.3)

    def test_shipped_camera_frame_moves_are_flagged_after_a_turn(self):
        # establishing: dolly_out follows pan_left. example: both dolly_in and crane_up follow orbit_left.
        establishing = load_json(os.path.join(HERE, "samples", "establishing.json"))
        self.assertEqual(establishing["segments"][2]["params"], {"frame": "camera"})
        example = load_json(EXAMPLE)
        self.assertEqual(example["segments"][2]["params"], {"frame": "camera"})
        self.assertEqual(example["segments"][3]["params"], {"frame": "camera"})


class OrbitHoldsDistanceTests(unittest.TestCase):
    def test_orbit_after_dolly_holds_distance_on_every_frame(self):
        frames = build(_sequence(_seg("dolly_in", 2), _seg("orbit_left", 3, radius=None)), load_json(CATALOG))
        dolly_end = frames[48]  # 2 s at 24 fps: the dolly ends on frame 48
        start_distance = _distance(dolly_end["position"])
        self.assertAlmostEqual(start_distance, 4.0, places=9)
        for index, frame in enumerate(frames[48:], start=48):
            with self.subTest(frame=index):
                self.assertAlmostEqual(_distance(frame["position"]), start_distance, places=9)

    def test_orbit_with_default_radius_drifts_out_to_radius(self):
        # Shows why radius null matters: without it the orbit eases out to 5.0.
        frames = build(_sequence(_seg("dolly_in", 2), _seg("orbit_left", 3)), load_json(CATALOG))
        self.assertAlmostEqual(_distance(frames[-1]["position"]), 5.0, places=6)

    def test_shipped_product_reveal_orbit_holds_distance(self):
        product = load_json(os.path.join(HERE, "samples", "product_reveal.json"))
        self.assertIsNone(product["segments"][1]["params"]["radius"])
        frames = build(product, load_json(CATALOG))
        dolly_frames = 2 * 24 + 1
        for index, frame in enumerate(frames[dolly_frames - 1:], start=dolly_frames - 1):
            with self.subTest(frame=index):
                self.assertAlmostEqual(_distance(frame["position"]), _distance(frames[dolly_frames - 1]["position"]), places=9)


class JoinTests(unittest.TestCase):
    def test_every_shipped_join_matches_a_manual_chain_exactly(self):
        presets = _catalog_by_id()
        for sequence_path in SHIPPED:
            with self.subTest(sequence=os.path.basename(sequence_path)):
                sequence = load_json(sequence_path)
                fps = sequence["fps"]
                expected = []
                start = None
                for index, seg in enumerate(sequence["segments"]):
                    base = presets[seg["preset"]]
                    preset = dict(base, params=merge_params(base["params"], seg.get("params", {})))
                    seg_frames = path(preset, duration_s=seg["duration_s"], fps=fps, start=start)
                    expected.extend(seg_frames if index == 0 else seg_frames[1:])
                    start = seg_frames[-1]
                built = build(sequence, load_json(CATALOG))
                self.assertEqual(len(built), len(expected))
                for got, want in zip(built, expected):
                    self.assertEqual(got["position"], want["position"])
                    self.assertEqual(got["rotation"], want["rotation"])
                    self.assertEqual(got["fov"], want["fov"])

    def test_every_shipped_sequence_validates(self):
        for sequence_path in SHIPPED:
            with self.subTest(sequence=os.path.basename(sequence_path)):
                self.assertEqual(validate_sequence(load_json(sequence_path)), [])


class NullOverrideTests(unittest.TestCase):
    def test_null_removes_the_param_in_the_merge(self):
        self.assertEqual(merge_params({"angle": 45, "radius": 5.0}, {"radius": None}), {"angle": 45})

    def test_null_on_a_required_param_is_rejected(self):
        errors = validate_sequence(_sequence(_seg("dolly_in", 2, distance=None)))
        self.assertTrue(any("missing required field 'distance'" in e for e in errors), errors)


class InternalNameTests(unittest.TestCase):
    def test_sequence_files_contain_no_internal_names(self):
        for folder, _, files in os.walk(HERE):
            if "__pycache__" in folder:
                continue
            for name in files:
                full = os.path.join(folder, name)
                if full == SELF or not name.endswith((".py", ".json", ".md")):
                    continue
                with open(full, encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIsNone(INTERNAL_NAME.search(text), "internal name in %s" % full)


if __name__ == "__main__":
    unittest.main()
