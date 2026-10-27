"""Tests for sequences/validate.py and the sample sequences.

Run from camera-motion/:
    python3 -W error -m unittest camera_motion.sequences.test_validate
"""

import copy
import glob
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
from camera_motion.sequences import validate as seq_validate  # noqa: E402

SAMPLES_DIR = os.path.join(HERE, "samples")
SAMPLE_NAMES = ("product_reveal", "house_walkup", "establishing")

GOOD = {
    "id": "demo_shot",
    "name": "Demo shot",
    "description": "A short test shot.",
    "fps": 24,
    "segments": [{"preset": "dolly_in", "duration_s": 2.0}],
}


def sequence_with(**changes):
    seq = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is REMOVE:
            del seq[key]
        else:
            seq[key] = value
    return seq


def segment_with(**changes):
    """A GOOD sequence whose only segment has the given fields replaced or removed."""
    segment = copy.deepcopy(GOOD["segments"][0])
    for key, value in changes.items():
        if value is REMOVE:
            del segment[key]
        else:
            segment[key] = value
    return dict(GOOD, segments=[segment])


REMOVE = object()


class SampleTests(unittest.TestCase):
    def test_three_samples_are_present(self):
        files = sorted(os.path.basename(path) for path in glob.glob(os.path.join(SAMPLES_DIR, "*.json")))
        self.assertEqual(files, sorted(name + ".json" for name in SAMPLE_NAMES))

    def test_each_sample_validates(self):
        for name in SAMPLE_NAMES:
            with self.subTest(sample=name):
                with open(os.path.join(SAMPLES_DIR, name + ".json"), encoding="utf-8") as handle:
                    seq = json.load(handle)
                self.assertEqual(seq_validate.validate_sequence(seq), [])
                self.assertEqual(seq["id"], name)

    def test_each_sample_description_is_one_line_of_at_most_120_characters(self):
        for name in SAMPLE_NAMES:
            with self.subTest(sample=name):
                with open(os.path.join(SAMPLES_DIR, name + ".json"), encoding="utf-8") as handle:
                    description = json.load(handle)["description"]
                self.assertTrue(description.strip())
                self.assertNotIn("\n", description)
                self.assertLessEqual(len(description), 120)


class ValidSequenceTests(unittest.TestCase):
    def test_good_sequence_has_no_errors(self):
        self.assertEqual(seq_validate.validate_sequence(GOOD), [])

    def test_validation_does_not_change_the_input(self):
        seq = copy.deepcopy(GOOD)
        seq_validate.validate_sequence(seq)
        self.assertEqual(seq, GOOD)

    def test_fps_boundaries_are_accepted(self):
        for fps in (1, 120):
            with self.subTest(fps=fps):
                self.assertEqual(seq_validate.validate_sequence(sequence_with(fps=fps)), [])

    def test_description_boundary_is_accepted(self):
        self.assertEqual(seq_validate.validate_sequence(sequence_with(description="x" * 120)), [])

    def test_override_within_range_is_accepted(self):
        seq = segment_with(params={"distance": 2.0})
        self.assertEqual(seq_validate.validate_sequence(seq), [])

    def test_duration_boundary_is_accepted(self):
        self.assertEqual(seq_validate.validate_sequence(segment_with(duration_s=60)), [])


class RejectionTests(unittest.TestCase):
    def assertRejected(self, seq, *fragments):
        errors = seq_validate.validate_sequence(seq)
        self.assertTrue(errors, "sequence was accepted but should be rejected")
        for fragment in fragments:
            self.assertTrue(
                any(fragment in message for message in errors),
                "no error contains %r; got:\n%s" % (fragment, "\n".join(errors)),
            )

    def test_fps_must_be_an_integer(self):
        self.assertRejected(sequence_with(fps=24.0), "fps: must be an integer, got number (24.0)")

    def test_fps_must_not_be_a_string(self):
        self.assertRejected(sequence_with(fps="24"), "fps: must be an integer")

    def test_fps_must_not_be_a_boolean(self):
        self.assertRejected(sequence_with(fps=True), "fps: must be an integer, got boolean")

    def test_fps_zero_is_rejected(self):
        self.assertRejected(sequence_with(fps=0), "fps: must be >= 1, got 0")

    def test_fps_above_120_is_rejected(self):
        self.assertRejected(sequence_with(fps=121), "fps: must be <= 120, got 121")

    def test_unknown_preset_is_rejected(self):
        self.assertRejected(segment_with(preset="dolly_sideways"), "segments[0].preset: unknown preset 'dolly_sideways'")

    def test_zero_duration_is_rejected(self):
        self.assertRejected(segment_with(duration_s=0), "segments[0].duration_s: must be > 0, got 0")

    def test_negative_duration_is_rejected(self):
        self.assertRejected(segment_with(duration_s=-1.5), "segments[0].duration_s: must be > 0, got -1.5")

    def test_duration_above_60_is_rejected(self):
        self.assertRejected(segment_with(duration_s=61), "segments[0].duration_s: must be <= 60, got 61")

    def test_duration_must_be_a_number(self):
        self.assertRejected(segment_with(duration_s="2"), "segments[0].duration_s: must be a number")

    def test_override_zero_distance_fails_the_merged_preset(self):
        # dolly_in's distance must be non-zero; the override replaces it with 0.
        self.assertRejected(segment_with(params={"distance": 0}), "segments[0].params.distance: must not be 0")

    def test_override_out_of_range_fails_the_merged_preset(self):
        self.assertRejected(segment_with(params={"distance": 75}), "segments[0].params.distance: must be <= 50, got 75")

    def test_override_cannot_add_a_param_the_type_does_not_have(self):
        self.assertRejected(segment_with(params={"speed": 3}), "segments[0].params: unknown field 'speed'")

    def test_override_must_be_an_object(self):
        self.assertRejected(segment_with(params=3), "segments[0].params: must be an object")

    def test_description_too_long_is_rejected(self):
        self.assertRejected(sequence_with(description="x" * 121), "description: must be at most 120 character(s) long, got 121")

    def test_empty_description_is_rejected(self):
        self.assertRejected(sequence_with(description=""), "description: must be at least 1 character(s) long")

    def test_bad_id_is_rejected(self):
        self.assertRejected(sequence_with(id="Demo-Shot"), "id: must match pattern")

    def test_empty_name_is_rejected(self):
        self.assertRejected(sequence_with(name=""), "name: must be at least 1 character(s) long")

    def test_no_segments_is_rejected(self):
        self.assertRejected(sequence_with(segments=[]), "segments: must have at least 1 segment(s), got 0")

    def test_segments_must_be_a_list(self):
        self.assertRejected(sequence_with(segments={"preset": "dolly_in"}), "segments: must be a list")

    def test_missing_required_fields_are_reported(self):
        self.assertRejected(sequence_with(fps=REMOVE, segments=REMOVE), "sequence: missing required field 'fps'", "sequence: missing required field 'segments'")

    def test_unknown_top_level_field_is_rejected(self):
        self.assertRejected(sequence_with(speed=2), "sequence: unknown field 'speed'")

    def test_segment_missing_duration_is_rejected(self):
        self.assertRejected(segment_with(duration_s=REMOVE), "segments[0]: missing required field 'duration_s'")

    def test_segment_unknown_field_is_rejected(self):
        self.assertRejected(segment_with(easing="linear"), "segments[0]: unknown field 'easing'")

    def test_segment_must_be_an_object(self):
        self.assertRejected(dict(GOOD, segments=["dolly_in"]), "segments[0]: must be an object")

    def test_every_bad_field_is_reported(self):
        seq = sequence_with(fps=0, description="")
        seq["segments"] = [{"preset": "nope", "duration_s": 0}]
        errors = seq_validate.validate_sequence(seq)
        self.assertGreaterEqual(len(errors), 4, "\n".join(errors))

    def test_non_object_sequence_is_rejected(self):
        self.assertEqual(seq_validate.validate_sequence([]), ["sequence: must be an object, got array"])


if __name__ == "__main__":
    unittest.main()
