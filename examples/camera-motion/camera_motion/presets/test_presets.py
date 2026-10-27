"""Tests for the camera-motion preset catalog and its validator.

Run from camera-motion/:
    python3 -W error -m unittest camera_motion.presets.test_presets
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from camera_motion.presets import validate

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA_FILE = os.path.join(HERE, "schema.json")
CATALOG_FILE = os.path.join(HERE, "catalog.json")
VALIDATE_SCRIPT = os.path.join(HERE, "validate.py")

SCHEMA = validate.load_json(SCHEMA_FILE)

GOOD_DOLLY = {
    "id": "dolly_in",
    "name": "Dolly in",
    "type": "dolly",
    "params": {"axis": "z", "distance": 10},
    "easing": "ease_in_out",
    "description": "Push toward the subject.",
}


def preset_with(**changes):
    """A copy of the valid dolly preset with top-level fields replaced or removed.

    A value of REMOVE deletes the field.
    """
    preset = copy.deepcopy(GOOD_DOLLY)
    for key, value in changes.items():
        if value is REMOVE:
            del preset[key]
        else:
            preset[key] = value
    return preset


REMOVE = object()


def errors_for(preset):
    return validate.validate_catalog({"presets": [preset]}, SCHEMA)


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, VALIDATE_SCRIPT] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def presets_in(catalog):
    """A catalog is a plain list of presets, or an object with a 'presets' list."""
    return catalog if isinstance(catalog, list) else catalog["presets"]


class RealCatalogTests(unittest.TestCase):
    def test_catalog_validates(self):
        catalog = validate.load_json(CATALOG_FILE)
        self.assertEqual(validate.validate_catalog(catalog, SCHEMA), [])

    def test_catalog_ids_are_unique(self):
        catalog = validate.load_json(CATALOG_FILE)
        ids = [preset["id"] for preset in presets_in(catalog)]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids in catalog.json")

    def test_cli_accepts_real_catalog(self):
        result = run_cli(CATALOG_FILE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK:", result.stdout)

    def test_catalog_name_falls_back_to_script_folder(self):
        # Run from an unrelated folder: "catalog.json" is not there, so it must
        # resolve beside validate.py.
        with tempfile.TemporaryDirectory() as elsewhere:
            result = run_cli("catalog.json", cwd=elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK:", result.stdout)

    def test_missing_catalog_still_fails_after_fallback(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            result = run_cli("no_such_catalog.json", cwd=elsewhere)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no_such_catalog.json", result.stderr)


class ValidPresetTests(unittest.TestCase):
    def test_boundary_values_are_accepted(self):
        presets = [
            preset_with(params={"axis": "x", "distance": 50}),
            preset_with(params={"axis": "z", "distance": -50}),
            preset_with(type="crane", params={"distance": -0.5}),
            preset_with(type="orbit", params={"angle": 360, "radius": 0.01}),
            preset_with(type="orbit", params={"angle": -360, "radius": 50}),
            preset_with(type="dolly_zoom", params={"distance": 5, "fov_end": 10}),
            preset_with(type="dolly_zoom", params={"distance": -5, "fov_end": 150}),
            preset_with(type="pan", params={"angle": 180}),
            preset_with(type="tilt", params={"angle": -90}),
        ]
        for preset in presets:
            with self.subTest(preset=preset):
                self.assertEqual(errors_for(preset), [])


class OrbitRadiusOptionalTests(unittest.TestCase):
    def test_orbit_without_radius_is_valid(self):
        self.assertEqual(errors_for(preset_with(type="orbit", params={"angle": -90})), [])

    def test_orbit_with_radius_is_still_valid(self):
        self.assertEqual(errors_for(preset_with(type="orbit", params={"angle": -90, "radius": 5})), [])

    def test_orbit_still_requires_angle(self):
        self.assertTrue(errors_for(preset_with(type="orbit", params={"radius": 5})))


class RejectionTests(unittest.TestCase):
    """Each bad preset must fail, and the message must name the field and the rule."""

    def assertRejected(self, preset, *fragments):
        errors = errors_for(preset)
        self.assertTrue(errors, "preset was accepted but should be rejected")
        for fragment in fragments:
            self.assertTrue(
                any(fragment in message for message in errors),
                "no error contains %r; got:\n%s" % (fragment, "\n".join(errors)),
            )

    def test_bad_id_pattern(self):
        self.assertRejected(preset_with(id="Dolly-In"), "presets[0].id: must match pattern")

    def test_unknown_type(self):
        self.assertRejected(
            preset_with(type="zoom"),
            "presets[0].type: must be one of",
        )

    def test_unknown_easing(self):
        self.assertRejected(preset_with(easing="bounce"), "presets[0].easing: must be one of")

    def test_empty_name(self):
        self.assertRejected(preset_with(name=""), "presets[0].name: must be at least 1 character(s) long")

    def test_missing_description(self):
        self.assertRejected(preset_with(description=REMOVE), "presets[0]: missing required field 'description'")

    def test_missing_params(self):
        self.assertRejected(preset_with(params=REMOVE), "presets[0]: missing required field 'params'")

    def test_unknown_top_level_field(self):
        self.assertRejected(preset_with(speed=3), "presets[0]: unknown field 'speed'")

    def test_dolly_zero_distance(self):
        self.assertRejected(
            preset_with(params={"axis": "z", "distance": 0}),
            "presets[0].params.distance: must not be 0",
        )

    def test_dolly_distance_too_large(self):
        self.assertRejected(
            preset_with(params={"axis": "z", "distance": 75}),
            "presets[0].params.distance: must be <= 50, got 75",
        )

    def test_dolly_bad_axis(self):
        self.assertRejected(
            preset_with(params={"axis": "y", "distance": 5}),
            "presets[0].params.axis: must be one of ['x', 'z']",
        )

    def test_dolly_missing_axis(self):
        self.assertRejected(
            preset_with(params={"distance": 5}),
            "presets[0].params: missing required field 'axis'",
        )

    def test_dolly_extra_param(self):
        self.assertRejected(
            preset_with(params={"axis": "z", "distance": 5, "speed": 1}),
            "presets[0].params: unknown field 'speed'",
        )

    def test_boolean_is_not_a_number(self):
        self.assertRejected(
            preset_with(params={"axis": "z", "distance": True}),
            "presets[0].params.distance: must be number, got boolean",
        )

    def test_crane_zero_distance(self):
        self.assertRejected(
            preset_with(type="crane", params={"distance": 0}),
            "presets[0].params.distance: must not be 0",
        )

    def test_orbit_zero_radius(self):
        self.assertRejected(
            preset_with(type="orbit", params={"angle": 45, "radius": 0}),
            "presets[0].params.radius: must be > 0, got 0",
        )

    def test_orbit_angle_out_of_range(self):
        self.assertRejected(
            preset_with(type="orbit", params={"angle": 400, "radius": 5}),
            "presets[0].params.angle: must be <= 360, got 400",
        )

    def test_dolly_zoom_fov_too_narrow(self):
        self.assertRejected(
            preset_with(type="dolly_zoom", params={"distance": 5, "fov_end": 5}),
            "presets[0].params.fov_end: must be >= 10, got 5",
        )

    def test_pan_angle_out_of_range(self):
        self.assertRejected(
            preset_with(type="pan", params={"angle": 200}),
            "presets[0].params.angle: must be <= 180, got 200",
        )

    def test_tilt_angle_out_of_range(self):
        self.assertRejected(
            preset_with(type="tilt", params={"angle": 95}),
            "presets[0].params.angle: must be <= 90, got 95",
        )

    def test_type_rules_follow_the_preset_type(self):
        # A pan preset carrying dolly params must be judged by pan's rules.
        self.assertRejected(
            preset_with(type="pan", params={"axis": "z", "distance": 5}),
            "presets[0].params: missing required field 'angle'",
            "presets[0].params: unknown field 'axis'",
        )

    def test_every_bad_field_is_reported(self):
        preset = preset_with(id="Bad", easing="bounce", params={"axis": "z", "distance": 0})
        errors = errors_for(preset)
        self.assertEqual(len(errors), 3, "\n".join(errors))


class CatalogLevelTests(unittest.TestCase):
    def test_duplicate_ids_are_rejected(self):
        second = copy.deepcopy(GOOD_DOLLY)
        second["name"] = "Another"
        errors = validate.validate_catalog({"presets": [GOOD_DOLLY, second]}, SCHEMA)
        self.assertEqual(len(errors), 1, "\n".join(errors))
        self.assertIn("presets[1].id: duplicate id 'dolly_in' (already used by presets[0])", errors[0])

    def test_empty_catalog_is_rejected(self):
        errors = validate.validate_catalog({"presets": []}, SCHEMA)
        self.assertEqual(errors, ["presets: catalog is empty"])

    def test_catalog_as_plain_list_is_accepted(self):
        self.assertEqual(validate.validate_catalog([GOOD_DOLLY], SCHEMA), [])

    def test_wrong_catalog_shape_is_rejected(self):
        errors = validate.validate_catalog("dolly_in", SCHEMA)
        self.assertEqual(len(errors), 1)
        self.assertIn("catalog: must be a list of presets", errors[0])

    def test_catalog_with_extra_key_is_rejected(self):
        errors = validate.validate_catalog({"presets": [GOOD_DOLLY], "version": 2}, SCHEMA)
        self.assertTrue(any("catalog: unknown field 'version'" in message for message in errors), errors)

    def test_unsupported_schema_keyword_stops_validation(self):
        schema = copy.deepcopy(SCHEMA)
        schema["properties"]["id"]["format"] = "slug"
        with self.assertRaises(validate.SchemaError) as ctx:
            validate.validate_catalog({"presets": [GOOD_DOLLY]}, schema)
        self.assertIn("format", str(ctx.exception))


class CommandLineTests(unittest.TestCase):
    def write_catalog(self, directory, content):
        path = os.path.join(directory, "catalog.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def test_bad_catalog_exits_1_with_field_message(self):
        bad = {"presets": [preset_with(params={"axis": "z", "distance": 0})]}
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_catalog(directory, bad)
            result = run_cli(path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("presets[0].params.distance: must not be 0", result.stderr)

    def test_nan_and_infinity_are_rejected(self):
        for bad_value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad_value):
                bad = {"presets": [preset_with(params={"axis": "z", "distance": bad_value})]}
                with tempfile.TemporaryDirectory() as directory:
                    path = self.write_catalog(directory, bad)
                    result = run_cli(path)
                self.assertEqual(result.returncode, 1)
                self.assertIn("is not allowed (numbers must be finite)", result.stderr)

    def test_load_json_refuses_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_catalog(directory, '{"x": NaN}')
            with self.assertRaises(ValueError):
                validate.load_json(path)

    def test_invalid_json_exits_1(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_catalog(directory, "{not json")
            result = run_cli(path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)

    def test_missing_file_exits_1(self):
        result = run_cli(os.path.join(HERE, "no_such_catalog.json"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
