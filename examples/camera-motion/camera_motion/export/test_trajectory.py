import json
import math
import os
import unittest

from camera_motion.engine.camera_path import DEFAULT_START, path
from camera_motion.export.trajectory import K, intrinsics, pose_matrix, rotation_matrix, world_to_camera

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLE = os.path.join(ROOT, "preview", "sample_catalog.json")
PRESETS = os.path.join(ROOT, "presets", "catalog.json")
DURATION_S = 3.0
FPS = 24


def _catalog():
    path_ = PRESETS if os.path.isfile(PRESETS) else SAMPLE
    with open(path_, encoding="utf-8") as f:
        return json.load(f)


def _det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))]
            for i in range(len(a))]


def _frames_for_samples():
    for preset in _catalog():
        for i, frame in enumerate(path(preset, duration_s=DURATION_S, fps=FPS)):
            yield preset["id"], i, frame


class SamplePresetTests(unittest.TestCase):
    def test_rotation_orthonormal_det_plus_one(self):
        for pid, i, frame in _frames_for_samples():
            with self.subTest(id=pid, frame=i):
                r = rotation_matrix(frame["rotation"])
                for a in range(3):
                    for b in range(3):
                        dot = sum(r[k][a] * r[k][b] for k in range(3))
                        self.assertAlmostEqual(dot, 1.0 if a == b else 0.0, places=9)
                self.assertAlmostEqual(_det3(r), 1.0, places=9)
                c2w = pose_matrix(frame)
                self.assertAlmostEqual(_det3([row[:3] for row in c2w[:3]]), 1.0, places=9)

    def test_c2w_times_w2c_is_identity(self):
        for pid, i, frame in _frames_for_samples():
            with self.subTest(id=pid, frame=i):
                prod = _matmul(pose_matrix(frame), world_to_camera(frame))
                for r in range(4):
                    for c in range(4):
                        self.assertAlmostEqual(prod[r][c], 1.0 if r == c else 0.0, delta=1e-9)

    def test_translation_column_is_position(self):
        for pid, i, frame in _frames_for_samples():
            with self.subTest(id=pid, frame=i):
                c2w = pose_matrix(frame)
                for k in range(3):
                    self.assertAlmostEqual(c2w[k][3], frame["position"][k], places=12)
                self.assertEqual(c2w[3], [0.0, 0.0, 0.0, 1.0])


class ConventionTests(unittest.TestCase):
    def test_default_start_looks_at_origin(self):
        frame = {"position": DEFAULT_START["position"], "rotation": DEFAULT_START["rotation"]}
        c2w = pose_matrix(frame)
        forward = [-c2w[k][2] for k in range(3)]  # camera -z axis in world space
        to_origin = [-p for p in frame["position"]]
        norm = math.sqrt(sum(v * v for v in to_origin))
        for k in range(3):
            self.assertAlmostEqual(forward[k], to_origin[k] / norm, places=9)

    def test_yaw_90_looks_down_minus_x(self):
        c2w = pose_matrix({"position": [0.0, 0.0, 5.0], "rotation": [90.0, 0.0, 0.0]})
        forward = [-c2w[k][2] for k in range(3)]
        self.assertAlmostEqual(forward[0], -1.0, places=9)
        self.assertAlmostEqual(forward[2], 0.0, places=9)

    def test_pitch_90_looks_up(self):
        c2w = pose_matrix({"position": [0.0, 0.0, 0.0], "rotation": [0.0, 90.0, 0.0]})
        forward = [-c2w[k][2] for k in range(3)]
        self.assertAlmostEqual(forward[1], 1.0, places=9)


class IntrinsicsTests(unittest.TestCase):
    def test_fy_matches_vertical_fov(self):
        for fov in (30.0, 50.0, 90.0):
            with self.subTest(fov=fov):
                _, fy, _, _ = intrinsics(fov, 1920, 1080)
                self.assertAlmostEqual(fy, 0.5 / math.tan(math.radians(fov) / 2.0), places=12)

    def test_fx_scales_by_aspect(self):
        fx, fy, _, _ = intrinsics(50.0, 1920, 1080)
        self.assertAlmostEqual(fx, fy * 1080 / 1920, places=12)

    def test_principal_point_is_centre(self):
        _, _, cx, cy = intrinsics(50.0, 640, 480)
        self.assertEqual((cx, cy), (0.5, 0.5))

    def test_K_layout(self):
        fx, fy, cx, cy = intrinsics(50.0, 640, 480)
        self.assertEqual(K(50.0, 640, 480), [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])

    def test_frame_fov_values_from_samples(self):
        for pid, i, frame in _frames_for_samples():
            with self.subTest(id=pid, frame=i):
                _, fy, _, _ = intrinsics(frame["fov"], 1, 1)
                self.assertAlmostEqual(fy, 0.5 / math.tan(math.radians(frame["fov"]) / 2.0), places=9)


if __name__ == "__main__":
    unittest.main()
