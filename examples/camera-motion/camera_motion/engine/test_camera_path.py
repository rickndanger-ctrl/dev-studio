import json
import math
import os
import unittest

from camera_motion.engine.camera_path import DEFAULT_START, path
from camera_motion.engine.easing import ease

EASINGS = ("linear", "ease_in_out", "ease_in", "ease_out")
FPS = 10
DURATION = 2.0
N = round(DURATION * FPS) + 1

# rotation is [yaw, pitch, roll] (shared spec); angles are degrees.
YAW, PITCH = 0, 1


def _preset(kind, easing="linear", **params):
    return {"type": kind, "params": params, "easing": easing}


def _tracked(kind, frame):
    """The single scalar each move changes, so progress can be checked for monotonic order."""
    if kind in ("dolly", "dolly_zoom"):
        return DEFAULT_START["position"][2] - frame["position"][2]
    if kind == "crane":
        return frame["position"][1]
    if kind in ("orbit", "pan"):
        return frame["rotation"][YAW]
    return frame["rotation"][PITCH]  # tilt


def _assert_pose(test, got, want):
    for g, w in zip(got["position"], want["position"]):
        test.assertAlmostEqual(g, w, places=6)
    for g, w in zip(got["rotation"], want["rotation"]):
        test.assertAlmostEqual(g, w, places=6)
    test.assertAlmostEqual(got["fov"], want["fov"], places=6)


class EasingTests(unittest.TestCase):
    def test_endpoints(self):
        for name in EASINGS:
            with self.subTest(easing=name):
                self.assertAlmostEqual(ease(name, 0), 0.0)
                self.assertAlmostEqual(ease(name, 1), 1.0)

    def test_monotonic(self):
        grid = [i / 100 for i in range(101)]
        for name in EASINGS:
            with self.subTest(easing=name):
                values = [ease(name, u) for u in grid]
                self.assertTrue(all(b >= a for a, b in zip(values, values[1:])))

    def test_cubic_midpoints(self):
        self.assertAlmostEqual(ease("ease_in", 0.5), 0.125)
        self.assertAlmostEqual(ease("ease_out", 0.5), 0.875)
        self.assertAlmostEqual(ease("ease_in_out", 0.5), 0.5)

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            ease("bounce", 0.5)


class CameraPathTests(unittest.TestCase):
    def check_preset(self, kind, extra, expected_end):
        for easing in EASINGS:
            with self.subTest(type=kind, easing=easing):
                frames = path(_preset(kind, easing, **extra), duration_s=DURATION, fps=FPS)
                self.assertEqual(len(frames), N)
                self.assertAlmostEqual(frames[-1]["t"], DURATION)
                _assert_pose(self, frames[0], {**DEFAULT_START, "position": DEFAULT_START["position"]})
                _assert_pose(self, frames[-1], expected_end)
                tracked = [_tracked(kind, f) for f in frames]
                self.assertTrue(all(b >= a - 1e-12 for a, b in zip(tracked, tracked[1:])))

    def test_dolly(self):
        self.check_preset(
            "dolly",
            {"distance": 3.0, "axis": "z"},
            {"position": [0.0, 0.0, 2.0], "rotation": [0.0, 0.0, 0.0], "fov": 50.0},
        )

    def test_dolly_right_moves_plus_x(self):
        for easing in EASINGS:
            with self.subTest(easing=easing):
                frames = path(_preset("dolly", easing, distance=1.0, axis="x"), duration_s=DURATION, fps=FPS)
                xs = [f["position"][0] for f in frames]
                self.assertAlmostEqual(xs[0], 0.0)
                self.assertAlmostEqual(xs[-1], 1.0)
                self.assertTrue(all(b >= a - 1e-12 for a, b in zip(xs, xs[1:])))
                self.assertTrue(all(abs(f["position"][2] - 5.0) < 1e-12 for f in frames))

    def test_dolly_zoom(self):
        # Subject at distance 5 with fov 50: moving 2 leaves d = 3, and fov = 2 atan(k / d).
        k = 5.0 * math.tan(math.radians(25.0))
        self.check_preset(
            "dolly_zoom",
            {"distance": 2.0, "fov_end": 30.0},
            {"position": [0.0, 0.0, 3.0], "rotation": [0.0, 0.0, 0.0],
             "fov": math.degrees(2.0 * math.atan(k / 3.0))},
        )

    def test_crane(self):
        self.check_preset(
            "crane",
            {"distance": 1.5},
            {"position": [0.0, 1.5, 5.0], "rotation": [0.0, 0.0, 0.0], "fov": 50.0},
        )

    def test_orbit(self):
        # 90 degrees about y takes (0, 0, 5) to (5, 0, 0); yaw advances by 90.
        self.check_preset(
            "orbit",
            {"angle": 90.0},
            {"position": [5.0, 0.0, 0.0], "rotation": [90.0, 0.0, 0.0], "fov": 50.0},
        )

    def test_pan(self):
        self.check_preset(
            "pan",
            {"angle": 30.0},
            {"position": [0.0, 0.0, 5.0], "rotation": [30.0, 0.0, 0.0], "fov": 50.0},
        )

    def test_tilt(self):
        self.check_preset(
            "tilt",
            {"angle": 20.0},
            {"position": [0.0, 0.0, 5.0], "rotation": [0.0, 20.0, 0.0], "fov": 50.0},
        )

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            path(_preset("truck"), duration_s=DURATION, fps=FPS)


class OrbitRadiusTest(unittest.TestCase):
    def test_radius_3_ends_at_distance_3(self):
        frames = path({"type": "orbit", "params": {"angle": 90, "radius": 3}})
        x, _, z = frames[-1]["position"]
        self.assertAlmostEqual(math.hypot(x, z), 3.0)


class OrbitChainTests(unittest.TestCase):
    def _dolly_in_end_pose(self):
        # dolly_in (distance 1.0 on z) ends at z = 4.0; that pose is the next move's start.
        dolly = path({"type": "dolly", "params": {"axis": "z", "distance": 1.0}, "easing": "ease_in_out"})
        end = dolly[-1]
        return {"position": end["position"], "rotation": end["rotation"], "fov": end["fov"]}

    def test_orbit_without_radius_keeps_distance_on_every_frame(self):
        start = self._dolly_in_end_pose()
        frames = path({"type": "orbit", "params": {"angle": -90}, "easing": "ease_in_out"}, start=start)
        for frame in frames:
            x, _, z = frame["position"]
            self.assertAlmostEqual(math.hypot(x, z), 4.0, delta=1e-9)

    def test_orbit_with_radius_5_still_eases_to_5(self):
        start = self._dolly_in_end_pose()
        frames = path({"type": "orbit", "params": {"angle": -90, "radius": 5.0}, "easing": "ease_in_out"},
                      start=start)
        x0, _, z0 = frames[0]["position"]
        self.assertAlmostEqual(math.hypot(x0, z0), 4.0, delta=1e-9)
        x1, _, z1 = frames[-1]["position"]
        self.assertAlmostEqual(math.hypot(x1, z1), 5.0, delta=1e-9)


class FrameZeroTests(unittest.TestCase):
    OFF_AXIS = {"position": [3.0, 1.0, 4.0], "rotation": [10.0, 5.0, 0.0], "fov": 40.0}

    def test_frame0_is_start_pose_for_every_type(self):
        presets = [
            _preset("dolly", distance=3.0),
            _preset("dolly_zoom", distance=2.0, fov_end=30.0),
            _preset("crane", distance=1.5),
            _preset("orbit", angle=90.0),
            _preset("orbit", angle=90.0, radius=3.0),
            _preset("pan", angle=30.0),
            _preset("tilt", angle=20.0),
        ]
        for preset in presets:
            for easing in EASINGS:
                with self.subTest(type=preset["type"], params=preset["params"], easing=easing):
                    preset["easing"] = easing
                    frames = path(preset, duration_s=DURATION, fps=FPS, start=self.OFF_AXIS)
                    _assert_pose(self, frames[0], self.OFF_AXIS)

    def test_orbit_radius_3_from_y_axis_start_begins_at_start(self):
        start = {"position": [0.0, 2.0, 0.0], "rotation": [0.0, 0.0, 0.0], "fov": 50.0}
        frames = path(_preset("orbit", angle=90.0, radius=3.0), duration_s=DURATION, fps=FPS, start=start)
        _assert_pose(self, frames[0], start)
        x, _, z = frames[-1]["position"]
        self.assertAlmostEqual(math.hypot(x, z), 3.0)

    def test_orbit_radius_3_starts_at_start_distance_and_ends_at_3(self):
        start = {"position": [0.0, 0.0, 5.0], "rotation": [0.0, 0.0, 0.0], "fov": 50.0}
        frames = path(_preset("orbit", angle=90.0, radius=3.0), duration_s=DURATION, fps=FPS, start=start)
        x0, _, z0 = frames[0]["position"]
        self.assertAlmostEqual(math.hypot(x0, z0), 5.0)
        x1, _, z1 = frames[-1]["position"]
        self.assertAlmostEqual(math.hypot(x1, z1), 3.0)


def _end_pose(frames):
    last = frames[-1]
    return {"position": list(last["position"]), "rotation": list(last["rotation"]), "fov": last["fov"]}


class CameraFrameTests(unittest.TestCase):
    def test_camera_frame_matches_world_at_default_start(self):
        cases = [
            ("dolly", {"distance": 3.0, "axis": "z"}),
            ("dolly", {"distance": 2.0, "axis": "x"}),
            ("dolly_zoom", {"distance": 2.0, "fov_end": 30.0}),
            ("crane", {"distance": 1.5}),
        ]
        for kind, params in cases:
            with self.subTest(kind=kind, params=params):
                world = path(_preset(kind, **params), duration_s=DURATION, fps=FPS)
                camera = path(_preset(kind, frame="camera", **params), duration_s=DURATION, fps=FPS)
                self.assertEqual(len(world), len(camera))
                for w, c in zip(world, camera):
                    for a, b in zip(w["position"], c["position"]):
                        self.assertAlmostEqual(a, b, delta=1e-9)
                    self.assertAlmostEqual(w["fov"], c["fov"], delta=1e-9)

    def test_dolly_after_orbit_moves_toward_subject_in_camera_frame_only(self):
        orbit = path({"type": "orbit", "params": {"angle": 90.0}, "easing": "linear"},
                     duration_s=DURATION, fps=FPS)
        start = _end_pose(orbit)
        d0 = math.sqrt(sum(c * c for c in start["position"]))

        camera = path(_preset("dolly", distance=1.0, axis="z", frame="camera"),
                      duration_s=DURATION, fps=FPS, start=start)
        world = path(_preset("dolly", distance=1.0, axis="z"),
                     duration_s=DURATION, fps=FPS, start=start)

        cam_end = math.sqrt(sum(c * c for c in camera[-1]["position"]))
        world_end = math.sqrt(sum(c * c for c in world[-1]["position"]))
        self.assertLess(cam_end, d0)
        self.assertAlmostEqual(cam_end, d0 - 1.0, delta=1e-9)
        self.assertGreater(world_end, d0)

    def test_crane_after_tilt_moves_along_tilted_up(self):
        tilt = path({"type": "tilt", "params": {"angle": 15.0}, "easing": "linear"},
                    duration_s=DURATION, fps=FPS)
        start = _end_pose(tilt)
        pitch = math.radians(15.0)

        camera = path(_preset("crane", distance=1.0, frame="camera"),
                      duration_s=DURATION, fps=FPS, start=start)
        world = path(_preset("crane", distance=1.0), duration_s=DURATION, fps=FPS, start=start)

        cam_move = [camera[-1]["position"][k] - start["position"][k] for k in range(3)]
        expected_up = (0.0, math.cos(pitch), math.sin(pitch))
        for got, want in zip(cam_move, expected_up):
            self.assertAlmostEqual(got, want, delta=1e-9)
        self.assertGreater(abs(cam_move[2]), 0.1)

        world_move = [world[-1]["position"][k] - start["position"][k] for k in range(3)]
        for got, want in zip(world_move, (0.0, 1.0, 0.0)):
            self.assertAlmostEqual(got, want, delta=1e-9)

    def test_unknown_frame_raises(self):
        with self.assertRaises(ValueError):
            path(_preset("dolly", distance=1.0, axis="z", frame="screen"), duration_s=DURATION, fps=FPS)


CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets", "catalog.json")
GOLDEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_paths.json")
# Stored values for the 12 non-dolly_zoom presets: every frame's t, position, rotation and fov,
# rounded to 9 decimals, generated from the engine at f57b06c. The test compares each number
# within 1e-9, not a hash of the output. Why not byte-exact: libm sin and cos differ in the last
# bit between platforms (macOS arm64 vs Linux glibc x86_64), so exact output differs there.
GOLDEN_TOLERANCE = 1e-9


def _golden_mismatches(frames, stored):
    """Human-readable differences between engine frames and stored golden frames."""
    if len(frames) != len(stored):
        return [f"{len(frames)} frames, expected {len(stored)}"]
    problems = []
    for i, (got, want) in enumerate(zip(frames, stored)):
        for key in ("t", "fov"):
            if abs(got[key] - want[key]) > GOLDEN_TOLERANCE:
                problems.append(f"frame {i} {key}: {got[key]!r} vs {want[key]!r}")
        for key in ("position", "rotation"):
            for axis, (a, b) in enumerate(zip(got[key], want[key])):
                if abs(a - b) > GOLDEN_TOLERANCE:
                    problems.append(f"frame {i} {key}[{axis}]: {a!r} vs {b!r}")
    return problems


def _catalog():
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f)


def _forward(rotation):
    yaw, pitch = math.radians(rotation[0]), math.radians(rotation[1])
    return (-math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch))


def _depth(pos, forward):
    """Distance from the camera to the origin, measured along its forward direction."""
    return -sum(p * f for p, f in zip(pos, forward))


class DollyZoomTests(unittest.TestCase):
    def test_subject_height_is_constant_for_the_catalog_dolly_zooms(self):
        for preset in _catalog():
            if preset["type"] != "dolly_zoom":
                continue
            with self.subTest(id=preset["id"]):
                frames = path(preset, duration_s=DURATION, fps=FPS)
                forward = _forward(DEFAULT_START["rotation"])
                heights = [1.0 / (_depth(f["position"], forward) * math.tan(math.radians(f["fov"]) / 2))
                           for f in frames]
                for h in heights:
                    self.assertAlmostEqual(h / heights[0], 1.0, delta=1e-9)

    def test_camera_frame_subject_height_is_constant(self):
        orbit = path({"type": "orbit", "params": {"angle": 90.0}, "easing": "linear"},
                     duration_s=DURATION, fps=FPS)
        start = {"position": list(orbit[-1]["position"]), "rotation": list(orbit[-1]["rotation"]),
                 "fov": orbit[-1]["fov"]}
        frames = path(_preset("dolly_zoom", distance=1.0, fov_end=75.0, frame="camera"),
                      duration_s=DURATION, fps=FPS, start=start)
        forward = _forward(start["rotation"])
        heights = [1.0 / (_depth(f["position"], forward) * math.tan(math.radians(f["fov"]) / 2))
                   for f in frames]
        for h in heights:
            self.assertAlmostEqual(h / heights[0], 1.0, delta=1e-9)

    def test_unchanged_presets_match_golden_values(self):
        with open(GOLDEN_FILE, encoding="utf-8") as f:
            golden = json.load(f)
        catalog = [p for p in _catalog() if p["type"] != "dolly_zoom"]
        self.assertEqual({p["id"] for p in catalog}, set(golden))
        for preset in catalog:
            with self.subTest(id=preset["id"]):
                problems = _golden_mismatches(path(preset), golden[preset["id"]])
                self.assertFalse(problems, problems[:5])

    def test_subject_distance_override(self):
        frames = path(_preset("dolly_zoom", distance=1.0, subject_distance=10.0),
                      duration_s=DURATION, fps=FPS)
        self.assertAlmostEqual(frames[0]["fov"], 50.0, delta=1e-12)
        k = 10.0 * math.tan(math.radians(25.0))
        self.assertAlmostEqual(frames[-1]["fov"], math.degrees(2.0 * math.atan(k / 9.0)), delta=1e-9)
        self.assertAlmostEqual(frames[-1]["position"][2], 4.0, delta=1e-9)

    def test_dolly_through_the_subject_raises(self):
        with self.assertRaises(ValueError):
            path(_preset("dolly_zoom", distance=4.9), duration_s=DURATION, fps=FPS)

    def test_subject_behind_the_camera_raises(self):
        start = {"position": [0.0, 0.0, -5.0], "rotation": [0.0, 0.0, 0.0], "fov": 50.0}
        with self.assertRaises(ValueError):
            path(_preset("dolly_zoom", distance=1.0), duration_s=DURATION, fps=FPS, start=start)

    def test_fov_end_is_ignored(self):
        a = path(_preset("dolly_zoom", distance=1.0, fov_end=30.0), duration_s=DURATION, fps=FPS)
        b = path(_preset("dolly_zoom", distance=1.0, fov_end=80.0), duration_s=DURATION, fps=FPS)
        self.assertEqual(json.dumps(a), json.dumps(b))


if __name__ == "__main__":
    unittest.main()
