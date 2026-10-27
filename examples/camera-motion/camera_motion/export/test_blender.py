import ast
import math
import random
import unittest

from camera_motion.engine.camera_path import DEFAULT_START, path
from camera_motion.export.blender import MW, to_blender
from camera_motion.export.quat import make_continuous, mat_to_quat, quat_to_mat
from camera_motion.export.trajectory import pose_matrix

TOL = 1e-9


def _frames_literal(src):
    """FRAMES as a Python value, read from the script's AST without running bpy."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FRAMES":
            return ast.literal_eval(node.value)
    raise AssertionError("no FRAMES assignment")


def _expected_blender(frame):
    """The engine pose converted to Blender: location and (w, x, y, z) quaternion."""
    c2w = pose_matrix(frame)
    rot = [row[:3] for row in c2w[:3]]
    rot_b = [[sum(MW[i][k] * rot[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    pos = [c2w[i][3] for i in range(3)]
    loc_b = [sum(MW[i][k] * pos[k] for k in range(3)) for i in range(3)]
    x, y, z, w = mat_to_quat(rot_b)
    return loc_b, (w, x, y, z)


def _assert_close(test, got, want):
    for g, w in zip(got, want):
        test.assertAlmostEqual(g, w, delta=TOL)


class QuatTests(unittest.TestCase):
    def test_round_trip_random_rotations(self):
        rng = random.Random(7)
        for _ in range(200):
            q = [rng.gauss(0, 1) for _ in range(4)]
            norm = math.sqrt(sum(v * v for v in q))
            q = [v / norm for v in q]
            r = quat_to_mat(q)
            x, y, z, w = mat_to_quat(r)
            self.assertGreaterEqual(w, 0.0)
            self.assertAlmostEqual(x * x + y * y + z * z + w * w, 1.0, delta=TOL)
            back = quat_to_mat((x, y, z, w))
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(back[i][j], r[i][j], delta=1e-9)

    def test_identity(self):
        self.assertEqual(mat_to_quat([[1, 0, 0], [0, 1, 0], [0, 0, 1]]), (0.0, 0.0, 0.0, 1.0))

    def test_make_continuous_flips_only_when_dot_negative(self):
        out = make_continuous([(0, 0, 0, 1), (0, 0, 0, -1), (0, 0, 0.6, -0.8), (0, 0, -0.6, 0.8)])
        self.assertEqual(out[0], (0, 0, 0, 1))
        self.assertEqual(out[1], (0, 0, 0, 1))
        self.assertEqual(out[2], (0, 0, -0.6, 0.8))  # dot with (0, 0, 0, 1) was -0.8, so it flips
        self.assertEqual(out[3], (0, 0, -0.6, 0.8))  # dot with the flipped previous is positive
        self.assertEqual(make_continuous([]), [])

    def test_half_turn_branches(self):
        # 180 degree rotations around x, y and z exercise the non-trace branches.
        for r in (
            [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
            [[-1, 0, 0], [0, 1, 0], [0, 0, -1]],
            [[-1, 0, 0], [0, -1, 0], [0, 0, 1]],
        ):
            x, y, z, w = mat_to_quat(r)
            self.assertAlmostEqual(w, 0.0, delta=TOL)
            back = quat_to_mat((x, y, z, w))
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(back[i][j], r[i][j], delta=1e-9)


class BlenderScriptTests(unittest.TestCase):
    def setUp(self):
        self.frames = path({"type": "dolly", "params": {"distance": 1.0, "axis": "z"}, "easing": "ease_in_out"})
        self.src = to_blender(self.frames, name="Shot")
        self.rows = _frames_literal(self.src)

    def test_script_compiles(self):
        compile(self.src, "x", "exec")

    def test_imports_only_bpy_and_math(self):
        tree = ast.parse(self.src)
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module)
        self.assertEqual(modules, {"bpy", "math"})

    def test_one_keyframe_per_frame(self):
        self.assertEqual(len(self.rows), len(self.frames))
        self.assertIn("scene.frame_end = len(FRAMES)", self.src)

    def test_scene_settings_and_camera_name(self):
        self.assertIn("scene.render.fps = FPS", self.src)
        self.assertIn("NAME = 'Shot'", self.src)
        self.assertIn("rotation_mode = 'QUATERNION'", self.src)
        self.assertIn("sensor_fit = 'VERTICAL'", self.src)
        self.assertIn("keyframe_insert(data_path='lens'", self.src)

    def test_first_and_last_match_converted_engine_pose(self):
        for index in (0, -1):
            with self.subTest(index=index):
                loc, quat, fov = self.rows[index]
                want_loc, want_quat = _expected_blender(self.frames[index])
                _assert_close(self, loc, want_loc)
                _assert_close(self, quat, want_quat)
                self.assertAlmostEqual(fov, self.frames[index]["fov"], delta=TOL)

    def test_default_start_camera_looks_at_origin_in_blender(self):
        default = {"t": 0.0, "position": DEFAULT_START["position"],
                   "rotation": DEFAULT_START["rotation"], "fov": DEFAULT_START["fov"]}
        src = to_blender([default], name="Default")
        loc, (w, x, y, z), _ = _frames_literal(src)[0]
        r = quat_to_mat((x, y, z, w))
        forward = [-r[k][2] for k in range(3)]  # camera -Z in Blender world
        to_origin = [-c for c in loc]
        norm = math.sqrt(sum(c * c for c in to_origin))
        for k in range(3):
            self.assertAlmostEqual(forward[k], to_origin[k] / norm, delta=TOL)

    def test_orbit_270_consecutive_dots_nonnegative(self):
        frames = path({"type": "orbit", "params": {"angle": 270}, "easing": "linear"})
        rows = _frames_literal(to_blender(frames))
        quats = [quat for _, quat, _ in rows]
        for a, b in zip(quats, quats[1:]):
            self.assertGreaterEqual(sum(p * q for p, q in zip(a, b)), 0.0)

    def test_orbit_270_needs_the_flip(self):
        # Without make_continuous, mat_to_quat's w >= 0 rule produces a negative dot.
        frames = path({"type": "orbit", "params": {"angle": 270}, "easing": "linear"})
        raw = [_expected_blender(f)[1] for f in frames]  # no continuity pass
        dots = [sum(p * q for p, q in zip(a, b)) for a, b in zip(raw, raw[1:])]
        self.assertTrue(any(d < 0 for d in dots))

    def test_empty_name_falls_back_to_camera(self):
        self.assertIn("NAME = 'Camera'", to_blender(self.frames))


if __name__ == "__main__":
    unittest.main()
