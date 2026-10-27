"""Tests for sequences.build: joins, frame counts, continuous time, overrides and rejection rules."""

import copy
import json
import os
import unittest

from camera_motion.engine.camera_path import path
from camera_motion.presets.validate import load_json
from camera_motion.sequences.build import build
from camera_motion.sequences.validate import merge_params, merged_preset, validate_sequence

CATALOG = load_json(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "presets", "catalog.json"))
PRESETS = {p["id"]: p for p in CATALOG}
FPS = 24

# A chain with a turn in the middle: dolly_in 2 s, orbit_left 3 s, dolly_in 1 s, crane_up 1.5 s.
SHOT = {
    "id": "test_shot",
    "name": "Test shot",
    "description": "Four segments chained for the join tests.",
    "fps": FPS,
    "segments": [
        {"preset": "dolly_in", "duration_s": 2},
        {"preset": "orbit_left", "duration_s": 3},
        {"preset": "dolly_in", "duration_s": 1},
        {"preset": "crane_up", "duration_s": 1.5},
    ],
}


def _pose(frame):
    return (frame["position"], frame["rotation"], frame["fov"])


def _segment_frames(segments):
    """Regenerate each segment's frames from the previous join, the same way build() chains them.

    Returns a list of (segment_index, frames) with the duplicate first frame still present.
    """
    out = []
    start = None
    for index, seg in enumerate(segments):
        merged = merged_preset(PRESETS[seg["preset"]], seg.get("params", {}))
        frames = path(merged, duration_s=seg["duration_s"], fps=FPS, start=start)
        out.append((index, frames))
        last = frames[-1]
        start = {"position": list(last["position"]), "rotation": list(last["rotation"]), "fov": last["fov"]}
    return out


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.frames = build(SHOT, CATALOG)
        self.regen = _segment_frames(SHOT["segments"])

    def test_frame_count_is_segment_frames_minus_the_dropped_joins(self):
        expected = sum(len(frames) for _, frames in self.regen) - (len(SHOT["segments"]) - 1)
        self.assertEqual(len(self.frames), expected)

    def test_time_starts_at_zero_and_steps_one_over_fps(self):
        self.assertEqual(self.frames[0]["t"], 0)
        for prev, nxt in zip(self.frames, self.frames[1:]):
            self.assertAlmostEqual(nxt["t"] - prev["t"], 1 / FPS, places=9)

    def test_every_join_matches_exactly(self):
        # Walk the output. Segment i's first frame was dropped, so the join is the last kept
        # frame before its first kept frame. Its pose must equal the engine's frame 0 from that pose.
        index = 0
        for seg_index, frames in self.regen:
            if seg_index == 0:
                index += len(frames) - 1
                continue
            join_index = index
            join_pose = _pose(self.frames[join_index])
            self.assertEqual(_pose(frames[0]), join_pose,
                             "segment %d frame 0 does not equal the join pose" % seg_index)
            self.assertEqual(_pose(self.frames[join_index + 1]), _pose(frames[1]),
                             "segment %d's first kept frame is not its frame 1" % seg_index)
            index += len(frames) - 1
        self.assertEqual(index, len(self.frames) - 1)

    def test_segment_frames_are_the_engine_output(self):
        index = 0
        for seg_index, frames in self.regen:
            kept = frames if seg_index == 0 else frames[1:]
            for k, frame in enumerate(kept):
                self.assertEqual(_pose(self.frames[index + k]), _pose(frame))
            index += len(kept)

    def test_single_segment_equals_engine_path_from_default_start(self):
        shot = {"id": "one", "name": "One", "description": "One segment.", "fps": FPS,
                "segments": [{"preset": "dolly_in", "duration_s": 3}]}
        frames = build(shot, CATALOG)
        expected = path(PRESETS["dolly_in"], duration_s=3, fps=FPS)
        self.assertEqual([_pose(f) for f in frames], [_pose(f) for f in expected])

    def test_overrides_are_merged_over_the_catalog_params(self):
        shot = {"id": "over", "name": "Over", "description": "Override distance.", "fps": FPS,
                "segments": [{"preset": "dolly_in", "duration_s": 1, "params": {"distance": 2.0}}]}
        frames = build(shot, CATALOG)
        self.assertEqual(frames[-1]["position"], [0, 0, 3.0])

    def test_override_the_preset_type_cannot_take_is_rejected(self):
        shot = {"id": "bad", "name": "Bad", "description": "Wrong param.", "fps": FPS,
                "segments": [{"preset": "dolly_in", "duration_s": 1, "params": {"angle": 10}}]}
        with self.assertRaises(ValueError) as ctx:
            build(shot, CATALOG)
        self.assertIn("unknown field 'angle'", str(ctx.exception))

    def test_unknown_preset_is_rejected(self):
        shot = {"id": "bad", "name": "Bad", "description": "No such preset.", "fps": FPS,
                "segments": [{"preset": "dolly_sideways", "duration_s": 1}]}
        with self.assertRaises(ValueError) as ctx:
            build(shot, CATALOG)
        self.assertIn("unknown preset 'dolly_sideways'", str(ctx.exception))

    def test_segment_shorter_than_one_frame_is_rejected(self):
        shot = {"id": "tiny", "name": "Tiny", "description": "Too short.", "fps": FPS,
                "segments": [{"preset": "dolly_in", "duration_s": 0.01}]}
        with self.assertRaises(ValueError):
            build(shot, CATALOG)

    def test_missing_fields_are_rejected(self):
        shot = {"id": "nothing", "name": "No segments", "description": "x", "fps": FPS}
        with self.assertRaises(ValueError) as ctx:
            build(shot, CATALOG)
        self.assertIn("missing required field 'segments'", str(ctx.exception))

    def test_catalog_is_not_mutated(self):
        before = copy.deepcopy(CATALOG)
        build(SHOT, CATALOG)
        shot = {"id": "over", "name": "Over", "description": "Override.", "fps": FPS,
                "segments": [{"preset": "dolly_in", "duration_s": 1, "params": {"distance": 2.0}}]}
        build(shot, CATALOG)
        self.assertEqual(CATALOG, before)

    def test_frames_are_json_serialisable(self):
        json.dumps(self.frames)

    def test_null_radius_is_removed_from_the_merged_params(self):
        self.assertEqual(merge_params({"angle": -45, "radius": 5.0}, {"radius": None}), {"angle": -45})
        base = {"angle": -45, "radius": 5.0}
        merge_params(base, {"radius": None})
        self.assertEqual(base, {"angle": -45, "radius": 5.0})  # the input is not changed

        merged = merged_preset(PRESETS["orbit_left"], {"radius": None})
        self.assertNotIn("radius", merged["params"])
        self.assertIn("radius", PRESETS["orbit_left"]["params"])  # the catalog is not changed

    def test_null_radius_passes_validation(self):
        shot = {"id": "hold", "name": "Hold", "description": "Orbit keeps its distance.", "fps": FPS,
                "segments": [{"preset": "orbit_left", "duration_s": 1, "params": {"radius": None}}]}
        self.assertEqual(validate_sequence(shot, CATALOG), [])

    def test_null_radius_keeps_the_distance_through_a_chain(self):
        # dolly_in 1 s leaves the camera 4.0 from the origin; orbit_left with radius null must hold 4.0.
        shot = {"id": "hold", "name": "Hold", "description": "Orbit keeps its distance.", "fps": FPS,
                "segments": [
                    {"preset": "dolly_in", "duration_s": 1},
                    {"preset": "orbit_left", "duration_s": 2, "params": {"radius": None}},
                ]}
        frames = build(shot, CATALOG)
        join = FPS  # dolly_in's last frame, shared with orbit_left's frame 0
        self.assertEqual(frames[join]["position"][2], 4.0)
        for frame in frames[join:]:
            x, y, z = frame["position"]
            self.assertAlmostEqual((x * x + z * z) ** 0.5, 4.0, places=9)
            self.assertEqual(y, 0)

    def test_null_on_a_required_param_is_rejected(self):
        shot = {"id": "bad", "name": "Bad", "description": "Removes a required param.", "fps": FPS,
                "segments": [{"preset": "dolly_in", "duration_s": 1, "params": {"distance": None}}]}
        errors = validate_sequence(shot, CATALOG)
        self.assertTrue(any("missing required field 'distance'" in e for e in errors), errors)
        with self.assertRaises(ValueError):
            build(shot, CATALOG)


if __name__ == "__main__":
    unittest.main()
