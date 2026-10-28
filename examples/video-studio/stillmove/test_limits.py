import os
import sys
import unittest

from stillmove import _cm, limits
from stillmove.depth import get_backend

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_cm.import_camera_motion()
from camera_motion.presets import load_catalog  # noqa: E402

import measure_limits  # noqa: E402  (tools/measure_limits.py)

RECORD_KEYS = {"param", "limit", "unit", "holes_at_limit"}


def _catalog():
    return load_catalog()


class LimitsFileTests(unittest.TestCase):
    def setUp(self):
        self.limits = limits.load_limits()

    def test_covers_every_preset_type(self):
        types = {preset["type"] for preset in _catalog()}
        self.assertEqual(set(self.limits), types)

    def test_each_record_has_the_fields_and_sane_values(self):
        for kind, record in self.limits.items():
            with self.subTest(type=kind):
                self.assertEqual(set(record), RECORD_KEYS)
                self.assertEqual(record["param"], limits.PARAM[kind])
                self.assertEqual(record["unit"], limits.UNIT[record["param"]])
                self.assertGreaterEqual(record["limit"], 0)
                self.assertLessEqual(record["holes_at_limit"], limits.HOLE_LIMIT + 1e-9)


class CheckPresetTests(unittest.TestCase):
    def setUp(self):
        self.limits = limits.load_limits()
        self.presets = {preset["id"]: preset for preset in _catalog()}

    def test_orbit_left_is_past_its_safe_range(self):
        message = limits.check_preset(self.presets["orbit_left"], self.limits)
        self.assertEqual(
            message,
            f"orbit_left angle 45 exceeds the single-photo safe range "
            f"(<= {self.limits['orbit']['limit']:g}); expect torn edges",
        )

    def test_a_move_inside_its_range_has_no_warning(self):
        inside = dict(self.presets["orbit_left"], params={"angle": 0.5, "radius": 5.0})
        self.assertIsNone(limits.check_preset(inside, self.limits))

    def test_safe_range_text_names_the_limit_and_unit(self):
        text = limits.safe_range_text(self.presets["orbit_left"], self.limits)
        self.assertEqual(text, f"<= {self.limits['orbit']['limit']:g} degrees")


class RemeasureTests(unittest.TestCase):
    def test_small_size_pan_agrees_with_limits_json_within_one_step(self):
        # Pan holes depend on the field of view, not the width, so a 256x144 picture should land
        # within one 1-degree step of the 1024x576 measurement in limits.json.
        image = limits.synthetic_image(256, 144)
        depth = get_backend("gradient").depth(image)
        record = limits.measure_type("pan", image, depth, catalog_value=30)
        recorded = limits.load_limits()["pan"]["limit"]
        self.assertLessEqual(abs(record["limit"] - recorded), limits.STEP["angle"])


class MeasureToolTests(unittest.TestCase):
    def test_tool_reads_the_same_catalog_values(self):
        presets = _catalog()
        self.assertEqual(measure_limits.catalog_value("orbit", presets), 45)
        self.assertEqual(measure_limits.catalog_value("pan", presets), 30)


if __name__ == "__main__":
    unittest.main()
