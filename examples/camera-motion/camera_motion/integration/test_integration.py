"""Integration tests: the preset catalog validates, runs through the engine,
and each end pose moves the camera in the direction the schema describes.

Run from camera-motion/:
    python3 -W error -m unittest
"""

import math
import os
import unittest

from camera_motion.engine import camera_path
from camera_motion.presets import validate

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)  # camera_motion/

CATALOG_FILE = os.path.join(PACKAGE, "presets", "catalog.json")
SCHEMA_FILE = os.path.join(PACKAGE, "presets", "schema.json")

START = camera_path.DEFAULT_START
FRAME_COUNT = round(3.0 * 24) + 1  # engine defaults: 3 s at 24 fps, inclusive
ALL_TYPES = {"dolly", "dolly_zoom", "crane", "orbit", "pan", "tilt"}
EPS = 1e-9


def load_presets():
    catalog = validate.load_json(CATALOG_FILE)
    return catalog if isinstance(catalog, list) else catalog["presets"]


PRESETS = load_presets()
BY_ID = {preset["id"]: preset for preset in PRESETS}


def sign(value):
    if abs(value) < EPS:
        return 0
    return 1 if value > 0 else -1


def forward_of(rotation):
    """Unit view direction for [yaw, pitch, roll] in degrees, using the engine's rotation.

    Yaw rotates about +y by the right-hand rule, so positive yaw turns the view left.
    Positive pitch looks up.
    """
    yaw, pitch = math.radians(rotation[0]), math.radians(rotation[1])
    return (
        -math.sin(yaw) * math.cos(pitch),
        math.sin(pitch),
        -math.cos(yaw) * math.cos(pitch),
    )


def end_frame(preset):
    return camera_path.path(preset)[-1]


class CatalogRunsTests(unittest.TestCase):
    def test_catalog_validates(self):
        errors = validate.validate_catalog(validate.load_json(CATALOG_FILE), validate.load_json(SCHEMA_FILE))
        self.assertEqual(errors, [])

    def test_catalog_covers_every_motion_type(self):
        self.assertEqual({preset["type"] for preset in PRESETS}, ALL_TYPES)

    def test_every_preset_runs_to_the_expected_frame_count(self):
        for preset in PRESETS:
            with self.subTest(id=preset["id"]):
                frames = camera_path.path(preset)
                self.assertEqual(len(frames), FRAME_COUNT)
                self.assertEqual(frames[0]["t"], 0.0)
                self.assertAlmostEqual(frames[-1]["t"], 3.0)

    def test_every_frame_is_finite(self):
        for preset in PRESETS:
            with self.subTest(id=preset["id"]):
                for frame in camera_path.path(preset):
                    values = frame["position"] + frame["rotation"] + [frame["fov"], frame["t"]]
                    self.assertTrue(all(math.isfinite(v) for v in values), frame)

    def test_every_preset_starts_at_the_default_pose(self):
        for preset in PRESETS:
            with self.subTest(id=preset["id"]):
                first = camera_path.path(preset)[0]
                self.assertEqual(first["position"], START["position"])
                self.assertEqual(first["rotation"], START["rotation"])
                self.assertEqual(first["fov"], START["fov"])

    def test_every_preset_actually_moves(self):
        # The schema says every move must move; a zero-length move would pass the schema.
        for preset in PRESETS:
            with self.subTest(id=preset["id"]):
                end = end_frame(preset)
                changed = (
                    end["position"] != START["position"]
                    or end["rotation"] != START["rotation"]
                    or end["fov"] != START["fov"]
                )
                self.assertTrue(changed, "end pose equals the start pose")


class EndPoseDirectionTests(unittest.TestCase):
    """For each preset, the end pose must match the sign conventions in schema.json."""

    def test_dolly_z_moves_toward_the_subject_for_positive_distance(self):
        # Schema: dolly z positive = forward toward the subject. The subject is at the
        # origin and the camera starts at z = +5, so forward means z decreases.
        for preset in PRESETS:
            if preset["type"] != "dolly" or preset["params"]["axis"] != "z":
                continue
            with self.subTest(id=preset["id"]):
                delta_z = end_frame(preset)["position"][2] - START["position"][2]
                self.assertEqual(sign(delta_z), -sign(preset["params"]["distance"]))

    def test_dolly_right_moves_right_for_positive_distance(self):
        # Engine convention: +distance on x moves the camera right.
        end = end_frame(BY_ID["dolly_right"])
        self.assertGreater(end["position"][0] - START["position"][0], 0)

    def test_dolly_left_moves_left_for_negative_distance(self):
        # Engine convention: dolly_left (-1.0) moves the camera left.
        end = end_frame(BY_ID["dolly_left"])
        self.assertLess(end["position"][0] - START["position"][0], 0)

    def test_crane_moves_up_for_positive_distance(self):
        for preset in PRESETS:
            if preset["type"] != "crane":
                continue
            with self.subTest(id=preset["id"]):
                delta_y = end_frame(preset)["position"][1] - START["position"][1]
                self.assertEqual(sign(delta_y), sign(preset["params"]["distance"]))

    def test_dolly_zoom_moves_in_and_reaches_its_fov_end(self):
        for preset in PRESETS:
            if preset["type"] != "dolly_zoom":
                continue
            with self.subTest(id=preset["id"]):
                end = end_frame(preset)
                delta_z = end["position"][2] - START["position"][2]
                self.assertEqual(sign(delta_z), -sign(preset["params"]["distance"]))
                self.assertAlmostEqual(end["fov"], preset["params"]["fov_end"])

    def test_orbit_turns_counter_clockwise_from_above_for_positive_angle(self):
        # Schema: positive orbit angle = counter-clockwise seen from above.
        # With +y as up, the y component of start x end is positive for CCW.
        for preset in PRESETS:
            if preset["type"] != "orbit":
                continue
            with self.subTest(id=preset["id"]):
                end = end_frame(preset)
                start_pos, end_pos = START["position"], end["position"]
                ccw_component = start_pos[2] * end_pos[0] - start_pos[0] * end_pos[2]
                self.assertEqual(sign(ccw_component), sign(preset["params"]["angle"]))
                self.assertAlmostEqual(end["rotation"][0], preset["params"]["angle"])

    def test_orbit_keeps_its_radius_and_height(self):
        for preset in PRESETS:
            if preset["type"] != "orbit":
                continue
            with self.subTest(id=preset["id"]):
                end_pos = end_frame(preset)["position"]
                self.assertAlmostEqual(math.hypot(end_pos[0], end_pos[2]), preset["params"]["radius"])
                self.assertAlmostEqual(end_pos[1], START["position"][1])

    def test_pan_turns_left_for_positive_angle(self):
        # Engine convention: positive yaw = turn left. Catalog: pan_left is +30.
        for preset in PRESETS:
            if preset["type"] != "pan":
                continue
            with self.subTest(id=preset["id"]):
                end = end_frame(preset)
                self.assertAlmostEqual(end["rotation"][0], preset["params"]["angle"])
                self.assertEqual(end["position"], START["position"])
                # Left is -x for a camera looking down -z, so positive yaw makes the view x negative.
                self.assertEqual(sign(forward_of(end["rotation"])[0]), -sign(preset["params"]["angle"]))

    def test_tilt_looks_up_for_positive_angle(self):
        # Engine convention: positive pitch = look up.
        for preset in PRESETS:
            if preset["type"] != "tilt":
                continue
            with self.subTest(id=preset["id"]):
                end = end_frame(preset)
                self.assertAlmostEqual(end["rotation"][1], preset["params"]["angle"])
                self.assertEqual(end["position"], START["position"])
                self.assertEqual(sign(forward_of(end["rotation"])[1]), sign(preset["params"]["angle"]))


class NamedPresetDirectionTests(unittest.TestCase):
    """Each catalog id must do what its name says. The per-type checks above only
    prove that each preset agrees with its own sign, so a flipped angle still passes them.
    """

    def test_dolly_in_moves_toward_the_subject(self):
        self.assertLess(end_frame(BY_ID["dolly_in"])["position"][2], START["position"][2])

    def test_dolly_out_moves_away_from_the_subject(self):
        self.assertGreater(end_frame(BY_ID["dolly_out"])["position"][2], START["position"][2])

    def test_crane_up_rises_and_crane_down_lowers(self):
        self.assertGreater(end_frame(BY_ID["crane_up"])["position"][1], START["position"][1])
        self.assertLess(end_frame(BY_ID["crane_down"])["position"][1], START["position"][1])

    def test_dolly_zoom_in_moves_in_and_widens_the_lens(self):
        end = end_frame(BY_ID["dolly_zoom_in"])
        self.assertLess(end["position"][2], START["position"][2])
        self.assertGreater(end["fov"], START["fov"])

    def test_dolly_zoom_out_moves_back_and_narrows_the_lens(self):
        end = end_frame(BY_ID["dolly_zoom_out"])
        self.assertGreater(end["position"][2], START["position"][2])
        self.assertLess(end["fov"], START["fov"])

    def test_orbit_left_ends_on_the_left_of_the_subject(self):
        # Engine convention: +angle makes the camera travel to its right, so "left" is a negative angle.
        self.assertLess(end_frame(BY_ID["orbit_left"])["position"][0], 0)

    def test_orbit_right_ends_on_the_right_of_the_subject(self):
        self.assertGreater(end_frame(BY_ID["orbit_right"])["position"][0], 0)

    def test_pan_left_turns_the_view_left(self):
        self.assertLess(forward_of(end_frame(BY_ID["pan_left"])["rotation"])[0], 0)

    def test_pan_right_turns_the_view_right(self):
        self.assertGreater(forward_of(end_frame(BY_ID["pan_right"])["rotation"])[0], 0)

    def test_tilt_up_looks_up_and_tilt_down_looks_down(self):
        self.assertGreater(forward_of(end_frame(BY_ID["tilt_up"])["rotation"])[1], 0)
        self.assertLess(forward_of(end_frame(BY_ID["tilt_down"])["rotation"])[1], 0)


class EngineConventionTests(unittest.TestCase):
    """Synthetic presets pin the engine's sign convention, independent of the catalog.
    These follow the engine's conventions (engine/SUMMARY.md).
    """

    def test_positive_pan_angle_turns_left(self):  # pan sign
        end = camera_path.path({"type": "pan", "params": {"angle": 30}, "easing": "linear"})[-1]
        self.assertLess(forward_of(end["rotation"])[0], 0)

    def test_positive_tilt_angle_looks_up(self):  # tilt sign
        end = camera_path.path({"type": "tilt", "params": {"angle": 20}, "easing": "linear"})[-1]
        self.assertGreater(forward_of(end["rotation"])[1], 0)

    def test_positive_orbit_angle_is_counter_clockwise_from_above(self):
        end = camera_path.path(
            {"type": "orbit", "params": {"angle": 45, "radius": 5}, "easing": "linear"}
        )[-1]
        self.assertGreater(START["position"][2] * end["position"][0] - START["position"][0] * end["position"][2], 0)


if __name__ == "__main__":
    unittest.main()
