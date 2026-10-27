import json
import math
import os
import tempfile
import unittest

from camera_motion.engine.camera_path import path
from camera_motion.export.formats import to_json, to_realestate10k
from camera_motion.export.gltf import to_gltf
from camera_motion.export.importers import load, read_gltf, read_json, read_re10k

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(os.path.dirname(os.path.dirname(HERE)), "camera_motion", "presets", "catalog.json")
FPS = 24
DURATION_S = 3.0
TOL_EXACT = 1e-6
TOL_FLOAT32 = 1e-4


def _catalog():
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f)


def _write(directory, name, text):
    file_path = os.path.join(directory, name)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    return file_path


def _assert_frames(test, got, want, tol, check_fov_all=True):
    test.assertEqual(len(got), len(want))
    for i, (g, w) in enumerate(zip(got, want)):
        with test.subTest(frame=i):
            test.assertAlmostEqual(g["t"], w["t"], delta=tol)
            for a, b in zip(g["position"], w["position"]):
                test.assertAlmostEqual(a, b, delta=tol)
            for a, b in zip(g["rotation"], w["rotation"]):
                test.assertAlmostEqual(a, b, delta=tol)
            if check_fov_all or i == 0:
                test.assertAlmostEqual(g["fov"], w["fov"], delta=tol)


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _load_text(self, text, name):
        return load(_write(self._tmp.name, name, text))

    def test_all_presets_round_trip_re10k(self):
        for preset in _catalog():
            with self.subTest(id=preset["id"]):
                frames = path(preset, duration_s=DURATION_S, fps=FPS)
                text = "\n".join(to_realestate10k(frames, fps=FPS, name=preset["id"])) + "\n"
                result = self._load_text(text, preset["id"] + ".txt")
                self.assertAlmostEqual(result["fps"], FPS, delta=1e-6)
                _assert_frames(self, result["frames"], frames, TOL_EXACT)

    def test_all_presets_round_trip_json(self):
        for preset in _catalog():
            with self.subTest(id=preset["id"]):
                frames = path(preset, duration_s=DURATION_S, fps=FPS)
                text = json.dumps(to_json(frames, fps=FPS), indent=2)
                result = self._load_text(text, preset["id"] + ".json")
                self.assertEqual(result["fps"], FPS)
                _assert_frames(self, result["frames"], frames, TOL_EXACT)

    def test_all_presets_round_trip_gltf(self):
        for preset in _catalog():
            with self.subTest(id=preset["id"]):
                frames = path(preset, duration_s=DURATION_S, fps=FPS)
                text = json.dumps(to_gltf(frames, fps=FPS, name=preset["id"]))
                result = self._load_text(text, preset["id"] + ".gltf")
                self.assertEqual(result["fps"], FPS)
                # Core glTF has one camera yfov, so only the first frame's fov is recovered for
                # dolly_zoom; every other preset has a constant fov.
                animated_fov = preset["type"] == "dolly_zoom"
                _assert_frames(self, result["frames"], frames, TOL_FLOAT32, check_fov_all=not animated_fov)


class ReaderTests(unittest.TestCase):
    def test_read_functions_return_fps_and_frames(self):
        preset = _catalog()[0]
        frames = path(preset, duration_s=DURATION_S, fps=FPS)
        self.assertEqual(read_json(to_json(frames, fps=FPS))["fps"], FPS)
        self.assertEqual(read_gltf(to_gltf(frames, fps=FPS))["fps"], FPS)
        lines = to_realestate10k(frames, fps=FPS)
        self.assertEqual(len(read_re10k(lines)["frames"]), len(frames))

    def test_unwrap_keeps_yaw_continuous_past_180(self):
        preset = {"type": "orbit", "params": {"angle": 270.0}, "easing": "linear"}
        frames = path(preset, duration_s=DURATION_S, fps=FPS)
        result = read_json(to_json(frames, fps=FPS))
        yaws = [f["rotation"][0] for f in result["frames"]]
        self.assertAlmostEqual(yaws[-1], 270.0, delta=TOL_EXACT)


class MalformedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _load(self, text, name="x.txt"):
        return load(_write(self._tmp.name, name, text))

    def test_unknown_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not RealEstate10K"):
            self._load("hello world\n")

    def test_re10k_wrong_field_count(self):
        with self.assertRaisesRegex(ValueError, "19 fields"):
            read_re10k(["0 1 2 3 4 5 6 7 8"])

    def test_re10k_non_numeric_field(self):
        line = " ".join(["x"] + ["0"] * 18)
        with self.assertRaisesRegex(ValueError, "non-numeric"):
            read_re10k([line])

    def test_re10k_without_frames(self):
        with self.assertRaisesRegex(ValueError, "no RealEstate10K frames"):
            read_re10k(["# only a header"])

    def test_json_without_frames(self):
        with self.assertRaisesRegex(ValueError, "'frames' list"):
            read_json({"fps": 24})

    def test_json_frame_missing_c2w(self):
        doc = {"fps": 24, "frames": [{"t": 0.0, "K": [[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]]}]}
        with self.assertRaisesRegex(ValueError, "malformed"):
            read_json(doc)

    def test_invalid_json_file(self):
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            self._load("{ not json")

    def test_json_that_is_neither_format(self):
        with self.assertRaisesRegex(ValueError, "neither glTF"):
            self._load('{"hello": 1}', "x.json")

    def test_gltf_without_animations(self):
        doc = {"asset": {"version": "2.0"}}
        with self.assertRaisesRegex(ValueError, "no animations"):
            read_gltf(doc)

    def test_gltf_bad_base64(self):
        frames = path(_catalog()[0], duration_s=DURATION_S, fps=FPS)
        doc = to_gltf(frames, fps=FPS)
        doc["buffers"][0]["uri"] = "data:application/octet-stream;base64,@@@not-base64@@@"
        with self.assertRaisesRegex(ValueError, "base64"):
            read_gltf(doc)

    def test_gltf_wrong_accessor_type(self):
        frames = path(_catalog()[0], duration_s=DURATION_S, fps=FPS)
        doc = to_gltf(frames, fps=FPS)
        doc["accessors"][1]["type"] = "VEC4"
        with self.assertRaisesRegex(ValueError, "must be float"):
            read_gltf(doc)


if __name__ == "__main__":
    unittest.main()
