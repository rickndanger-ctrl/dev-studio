"""Tests for shotplan.py: each rule in schema.json's $comment, the shape rules, and export."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import shotplan  # noqa: E402  (sets up the import path for camera_motion)
from camera_motion.export.formats import to_json  # noqa: E402
from camera_motion.presets.validate import load_json  # noqa: E402
from camera_motion.sequences.build import build  # noqa: E402

SEQ_1S = {"segments": [{"preset": "dolly_in", "duration_s": 1}]}


def quiet(argv):
    """Run shotplan's CLI with its output captured; return the exit status."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return shotplan.main(argv)


def plan(shots, **over):
    base = {"id": "demo", "name": "Demo", "fps": 24, "width": 320, "height": 180, "shots": shots}
    base.update(over)
    return base


def seq(seconds):
    return {"segments": [{"preset": "dolly_in", "duration_s": seconds}]}


def shot(sid, duration=1, sequence=None, transition="cut", image="placeholder:card"):
    """A shot whose sequence lasts exactly its duration, unless a sequence is given."""
    return {"id": sid, "image": image, "sequence": sequence or seq(duration), "duration": duration,
            "transition": transition}


class ShotPlanTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def check(self, p):
        return shotplan.check(p, self.dir)

    def assertErrorContains(self, p, text):
        errors, _, _ = self.check(p)
        self.assertTrue(any(text in e for e in errors), errors)

    def test_valid_plan_has_no_errors_and_a_timeline(self):
        errors, rows, seqs = self.check(plan([shot("a", 1), shot("b", 2)]))
        self.assertEqual(errors, [])
        self.assertEqual([r[0] for r in rows], [0.0, 1.0])
        self.assertEqual([r[1] for r in rows], [1.0, 3.0])
        self.assertEqual(set(seqs), {"a", "b"})

    def test_row_lists_each_move_with_its_duration(self):
        seq_two = {"segments": [{"preset": "dolly_in", "duration_s": 2}, {"preset": "orbit_left", "duration_s": 2}]}
        _, rows, _ = self.check(plan([shot("a", 4, sequence=seq_two)]))
        self.assertEqual(rows[0][3], "dolly_in 2s > orbit_left 2s")

    def test_crossfade_moves_the_next_shot_back(self):
        p = plan([shot("a", 2, transition={"crossfade": 0.5}), shot("b", 2)])
        errors, rows, _ = self.check(p)
        self.assertEqual(errors, [])
        self.assertEqual(rows[1][0], 1.5)

    def test_crossfade_on_the_last_shot_is_refused(self):
        self.assertErrorContains(plan([shot("a", 2), shot("b", 2, transition={"crossfade": 0.5})]),
                                 "the last shot must be a cut")

    def test_crossfade_not_shorter_than_next_shot_is_refused(self):
        p = plan([shot("a", 2, transition={"crossfade": 1.5}), shot("b", 1)])
        self.assertErrorContains(p, "crossfade 1.500 s must be shorter")

    def test_shots_overlapping_beyond_their_crossfade_are_refused(self):
        p = plan([shot("a", 2, transition={"crossfade": 1.5}), shot("b", 2, transition={"crossfade": 1.5}),
                  shot("c", 2)])
        self.assertErrorContains(p, "shots[2] overlaps shots[0]")

    def test_duplicate_shot_ids_are_refused(self):
        self.assertErrorContains(plan([shot("a"), shot("a")]), "duplicate shot id 'a'")

    def test_duration_must_match_its_sequence_within_half_a_frame(self):
        self.assertErrorContains(plan([shot("a", 2, sequence=SEQ_1S)]), "does not match its sequence's 1.000 s")
        errors, _, _ = self.check(plan([shot("a", 1 + 0.4 / 24)]))
        self.assertEqual(errors, [])

    def test_unknown_preset_in_an_inline_sequence_is_refused(self):
        bad = {"segments": [{"preset": "dolly_sideways", "duration_s": 1}]}
        self.assertErrorContains(plan([shot("a", sequence=bad)]), "unknown preset 'dolly_sideways'")

    def test_file_sequence_must_match_plan_fps(self):
        path = os.path.join(self.dir, "seq.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"id": "seq", "name": "Seq", "description": "x", "fps": 30, **SEQ_1S}, f)
        self.assertErrorContains(plan([shot("a", sequence="seq.json")]), "has fps 30 but the plan is 24")

    def test_file_sequence_is_relative_and_builds(self):
        path = os.path.join(self.dir, "seq.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"id": "seq", "name": "Seq", "description": "x", "fps": 24, **SEQ_1S}, f)
        errors, _, seqs = self.check(plan([shot("a", sequence="seq.json")]))
        self.assertEqual(errors, [])
        self.assertIn("a", seqs)

    def test_missing_image_file_is_refused_and_placeholder_needs_a_label(self):
        self.assertErrorContains(plan([shot("a", image="missing.png")]), "no file at missing.png")
        self.assertErrorContains(plan([shot("a", image="placeholder:")]), "placeholder needs a label")

    def test_existing_image_file_is_accepted(self):
        with open(os.path.join(self.dir, "still.png"), "wb") as f:
            f.write(b"not really a png")
        errors, _, _ = self.check(plan([shot("a", image="still.png")]))
        self.assertEqual(errors, [])

    def test_shape_errors_are_reported_not_raised(self):
        errors, rows, _ = self.check({"id": "x", "name": "", "fps": 0, "shots": [{"id": "A"}]})
        self.assertEqual(rows, [])
        self.assertTrue(any("missing required field 'width'" in e for e in errors), errors)
        self.assertTrue(any("plan.fps" in e for e in errors), errors)
        self.assertTrue(any("shots[0]" in e and "'A'" in e for e in errors), errors)

    def test_transition_shape_is_checked(self):
        self.assertErrorContains(plan([shot("a", transition="fade")]), "must be \"cut\"")
        self.assertErrorContains(plan([shot("a", transition={"crossfade": 0})]), "must be \"cut\"")

    def test_export_writes_one_file_per_shot_in_camera_motion_format(self):
        out = os.path.join(self.dir, "out")
        p = plan([shot("a", 1), shot("b", 1)])
        plan_path = os.path.join(self.dir, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(p, f)
        self.assertEqual(quiet(["export", plan_path, out]), 0)
        for sid in ("a", "b"):
            with open(os.path.join(out, sid + ".json"), encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual((data["fps"], data["width"], data["height"]), (24, 320, 180))
            self.assertEqual(len(data["frames"]), 25)
            self.assertEqual(set(data["frames"][0]), {"t", "K", "c2w", "w2c"})

    def test_export_matches_to_json_of_the_built_frames(self):
        out = os.path.join(self.dir, "out")
        p = plan([shot("a", 1), shot("b", 1.5, sequence={"segments": [
            {"preset": "orbit_left", "duration_s": 1}, {"preset": "crane_up", "duration_s": 0.5}]})])
        plan_path = os.path.join(self.dir, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(p, f)
        self.assertEqual(quiet(["export", plan_path, out]), 0)
        catalog = load_json(shotplan.CATALOG)
        _, _, seqs = shotplan.check(p, self.dir)
        for shot_plan in p["shots"]:
            expected = to_json(build(seqs[shot_plan["id"]], catalog), width=320, height=180, fps=24)
            with open(os.path.join(out, shot_plan["id"] + ".json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), json.loads(json.dumps(expected)))

    def test_export_refuses_a_bad_plan_and_writes_nothing(self):
        out = os.path.join(self.dir, "out")
        plan_path = os.path.join(self.dir, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan([shot("a", 2, sequence=SEQ_1S)]), f)
        self.assertEqual(quiet(["export", plan_path, out]), 1)
        self.assertFalse(os.path.exists(out))

    def test_check_exits_1_on_errors_and_0_when_clean(self):
        good = os.path.join(self.dir, "good.json")
        bad = os.path.join(self.dir, "bad.json")
        with open(good, "w", encoding="utf-8") as f:
            json.dump(plan([shot("a", 1)]), f)
        with open(bad, "w", encoding="utf-8") as f:
            json.dump(plan([shot("a", 2, sequence=SEQ_1S)]), f)
        self.assertEqual(quiet(["check", good]), 0)
        self.assertEqual(quiet(["check", bad]), 1)


if __name__ == "__main__":
    unittest.main()
