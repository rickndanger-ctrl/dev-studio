import base64
import json
import math
import struct
import unittest

from camera_motion.engine.camera_path import path
from camera_motion.export.gltf import DATA_URI_PREFIX, to_gltf
from camera_motion.export.quat import mat_to_quat, quat_to_mat
from camera_motion.export.trajectory import pose_matrix

START = {"position": [1.0, 2.0, 5.0], "rotation": [20.0, -10.0, 5.0], "fov": 60.0}
PRESET = {"type": "orbit", "params": {"angle": 45, "radius": 3}, "easing": "ease_in_out"}
ORBIT_270 = {"type": "orbit", "params": {"angle": 270, "radius": 3}, "easing": "linear"}
FPS = 10
DURATION = 1.5
TOL = 1e-5  # float32 storage
REQUIRED = ("asset", "scene", "scenes", "nodes", "cameras", "buffers",
            "bufferViews", "accessors", "animations")
COUNTS = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}


def _frames():
    return path(PRESET, duration_s=DURATION, fps=FPS, start=START)


def _blob(doc):
    uri = doc["buffers"][0]["uri"]
    assert uri.startswith(DATA_URI_PREFIX)
    return base64.b64decode(uri[len(DATA_URI_PREFIX):])


def _values(doc, accessor_index):
    """Decode one accessor into a list of tuples, one per element."""
    accessor = doc["accessors"][accessor_index]
    view = doc["bufferViews"][accessor["bufferView"]]
    width = COUNTS[accessor["type"]]
    count = accessor["count"] * width
    start = view.get("byteOffset", 0)
    flat = struct.unpack("<%df" % count, _blob(doc)[start:start + 4 * count])
    return [tuple(flat[i:i + width]) for i in range(0, len(flat), width)]


def _assert_rotation(test, quat, want_matrix):
    got = quat_to_mat(quat)
    for i in range(3):
        for j in range(3):
            test.assertAlmostEqual(got[i][j], want_matrix[i][j], delta=TOL)


class GltfTests(unittest.TestCase):
    def setUp(self):
        self.frames = _frames()
        self.doc = to_gltf(self.frames, fps=FPS, width=640, height=360, name="cam")

    def test_json_round_trip(self):
        self.assertEqual(json.loads(json.dumps(self.doc)), self.doc)

    def test_required_keys_and_version(self):
        for key in REQUIRED:
            self.assertIn(key, self.doc)
        self.assertEqual(self.doc["asset"]["version"], "2.0")
        self.assertIn("fov", self.doc["asset"]["extras"]["note"])

    def test_counts_match_engine(self):
        n = len(self.frames)
        self.assertEqual([a["count"] for a in self.doc["accessors"]], [n, n, n])
        self.assertEqual(len(_values(self.doc, 0)), n)
        self.assertEqual(len(_values(self.doc, 1)), n)
        self.assertEqual(len(_values(self.doc, 2)), n)
        self.assertEqual(len(self.doc["animations"][0]["channels"]), 2)
        self.assertEqual(len(self.doc["animations"][0]["samplers"]), 2)

    def test_camera_and_node(self):
        camera = self.doc["cameras"][0]
        self.assertEqual(camera["type"], "perspective")
        self.assertAlmostEqual(camera["perspective"]["yfov"], math.radians(START["fov"]))
        self.assertAlmostEqual(camera["perspective"]["aspectRatio"], 640 / 360)
        self.assertAlmostEqual(camera["perspective"]["znear"], 0.01)
        self.assertEqual(self.doc["nodes"][0]["camera"], 0)

    def test_quaternions_are_unit_length(self):
        for quat in _values(self.doc, 2):
            self.assertAlmostEqual(math.sqrt(sum(v * v for v in quat)), 1.0, delta=1e-6)

    def test_first_and_last_match_engine(self):
        decoded_trans = _values(self.doc, 1)
        decoded_rot = _values(self.doc, 2)
        for idx in (0, -1):
            frame = self.frames[idx]
            for got, want in zip(decoded_trans[idx], frame["position"]):
                self.assertAlmostEqual(got, want, delta=TOL)
            # Compare rotation matrices so the check does not depend on quaternion sign.
            want_rot = [row[:3] for row in pose_matrix(frame)[:3]]
            _assert_rotation(self, decoded_rot[idx], want_rot)

    def test_times_match_engine(self):
        for got, frame in zip(_values(self.doc, 0), self.frames):
            self.assertAlmostEqual(got[0], frame["t"], delta=TOL)

    def test_orbit_270_rotation_track_is_continuous(self):
        frames = path(ORBIT_270, duration_s=3.0, fps=24, start=START)
        doc = to_gltf(frames, fps=24, width=640, height=360)
        quats = _values(doc, 2)
        for prev, cur in zip(quats, quats[1:]):
            dot = sum(a * b for a, b in zip(prev, cur))
            self.assertGreaterEqual(dot, -1e-6)
        # The per-frame quaternions do flip sign on this path, so the check above is meaningful.
        raw = [mat_to_quat([row[:3] for row in pose_matrix(f)[:3]]) for f in frames]
        self.assertTrue(any(sum(a * b for a, b in zip(p, c)) < 0 for p, c in zip(raw, raw[1:])))

    def test_orbit_270_every_frame_matches_engine_rotation(self):
        frames = path(ORBIT_270, duration_s=3.0, fps=24, start=START)
        doc = to_gltf(frames, fps=24, width=640, height=360)
        for quat, frame in zip(_values(doc, 2), frames):
            _assert_rotation(self, quat, [row[:3] for row in pose_matrix(frame)[:3]])


if __name__ == "__main__":
    unittest.main()
