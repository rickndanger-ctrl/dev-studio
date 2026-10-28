"""Tests for shotrender: frame counts, the cut boundary, crossfade blend values, the GIF,
scaling and the render command. Images are 64x36; the depth is stillmove's plane (fake)."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageSequence

import shotplan
from shotplan import shotrender

W, H, FPS = 64, 36, 24
SEQ = {"segments": [{"preset": "dolly_in", "duration_s": 1}]}


def seq(seconds):
    return {"segments": [{"preset": "dolly_in", "duration_s": seconds}]}


def shot(sid, seconds, transition="cut", image=None):
    return {"id": sid, "image": image or ("placeholder:%s" % sid), "sequence": seq(seconds),
            "duration": seconds, "transition": transition}


def plan(shots):
    return {"id": "demo", "name": "Demo", "fps": FPS, "width": W, "height": H, "shots": shots}


def load_rgb(path):
    with Image.open(path) as img:
        return np.asarray(img.convert("RGB"), dtype=np.float64)


class ShotRenderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "out")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def render(self, shots, **kw):
        p = plan(shots)
        errors, rows, sequences = shotplan.check(p, self.dir)
        self.assertEqual(errors, [])
        kw.setdefault("use_ffmpeg", False)
        return shotrender.render_plan(p, self.dir, self.out, rows, sequences, shotplan.load_json(shotplan.CATALOG),
                                      **kw)

    def frame(self, k):
        return load_rgb(os.path.join(self.out, "frames", "%04d.png" % k))

    def shot_frame(self, sid, i):
        return load_rgb(os.path.join(self.out, "shots", sid, "frames", "%04d.png" % i))

    def test_frame_count_for_two_cut_shots(self):
        m = self.render([shot("a", 1), shot("b", 1)])
        self.assertEqual(m["frames"], 48)
        self.assertEqual(len(os.listdir(os.path.join(self.out, "frames"))), 48)
        self.assertEqual([s["frames"] for s in m["shots"]], [25, 25])

    def test_frame_count_for_a_crossfade_shortens_the_timeline(self):
        m = self.render([shot("a", 1, transition={"crossfade": 0.5}), shot("b", 1)])
        self.assertEqual(m["frames"], 36)  # total 1.5 s at 24 fps
        self.assertEqual(m["shots"][1]["start"], 0.5)

    def test_cut_boundary_switches_shots_exactly(self):
        self.render([shot("a", 0.5), shot("b", 0.5)])
        # Frame 11 (t = 11/24) is still shot a's frame 11; frame 12 (t = 0.5) is shot b's frame 0.
        np.testing.assert_array_equal(self.frame(11), self.shot_frame("a", 11))
        np.testing.assert_array_equal(self.frame(12), self.shot_frame("b", 0))
        # Frame 23 is shot b's frame 12, the last one: the index is clamped to the shot.
        # shots are smoothed-depth warps now: within 2 grey levels, not byte-exact
        np.testing.assert_allclose(self.frame(23), self.shot_frame("b", 12), atol=2)

    def test_crossfade_blends_linearly_over_the_overlap(self):
        self.render([shot("a", 1, transition={"crossfade": 0.5}), shot("b", 1)])
        # The overlap is t in [0.5, 1.0), frames 12..23. alpha = (t - 0.5) / 0.5.
        np.testing.assert_array_equal(self.frame(12), self.shot_frame("a", 12))  # alpha 0
        # k = 18: t = 0.75, alpha 0.5, a frame 18, b frame round(0.25 * 24) = 6.
        expected = np.clip(np.rint(0.5 * self.shot_frame("a", 18) + 0.5 * self.shot_frame("b", 6)), 0, 255)
        np.testing.assert_array_equal(self.frame(18), expected)
        # k = 21: t = 0.875, alpha 0.75, a frame 21, b frame 9.
        expected = np.clip(np.rint(0.25 * self.shot_frame("a", 21) + 0.75 * self.shot_frame("b", 9)), 0, 255)
        np.testing.assert_array_equal(self.frame(21), expected)
        # Frame 11 is before the overlap and frame 24 (t = 1.0) is after it: both are cuts.
        np.testing.assert_array_equal(self.frame(11), self.shot_frame("a", 11))
        np.testing.assert_array_equal(self.frame(24), self.shot_frame("b", 12))

    def test_gif_has_every_frame_and_the_manifest_describes_the_run(self):
        m = self.render([shot("a", 1), shot("b", 1)])
        # Pillow merges identical neighbouring frames (a flat placeholder barely changes), and adds
        # their durations together, so check the total time rather than the frame count.
        with Image.open(os.path.join(self.out, m["gif"])) as gif:
            durations = [f.info.get("duration", 0) for f in ImageSequence.Iterator(gif)]
        self.assertLessEqual(len(durations), 48)
        self.assertAlmostEqual(sum(durations), 2000, delta=20)  # 48 frames at 24 fps
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["fps"], saved["width"], saved["height"], saved["depth"]), (FPS, W, H, "plane"))
        self.assertEqual([s["id"] for s in saved["shots"]], ["a", "b"])

    def test_scale_renders_at_a_share_of_the_plan_size(self):
        self.render([shot("a", 0.5)], scale=0.5)
        with Image.open(os.path.join(self.out, "frames", "0000.png")) as img:
            self.assertEqual(img.size, (32, 18))

    def test_unknown_depth_is_refused(self):
        with self.assertRaises(ValueError):
            self.render([shot("a", 0.5)], depth="nope")

    def test_a_file_image_is_used_directly(self):
        Image.new("RGB", (W, H), (200, 30, 30)).save(os.path.join(self.dir, "still.png"))
        m = self.render([shot("a", 0.5, image="still.png")])
        self.assertEqual(m["frames"], 12)

    def test_mp4_without_ffmpeg_is_noted_not_fatal(self):
        m = self.render([shot("a", 0.5)], mp4=True, use_ffmpeg=False)
        self.assertIsNone(m["mp4"])
        self.assertIn("ffmpeg not found", m["note"])

    def test_cli_render_checks_then_writes(self):
        plan_path = os.path.join(self.dir, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan([shot("a", 0.5), shot("b", 0.5)]), f)
        out = io.StringIO()  # keep the CLI's 'rendered …' line out of the test run's output
        with contextlib.redirect_stdout(out):
            self.assertEqual(shotplan.main(["render", plan_path, self.out, "--depth", "fake"]), 0)
        self.assertIn("rendered", out.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(self.out, "manifest.json")))

    def test_cli_render_refuses_a_bad_plan_and_writes_nothing(self):
        plan_path = os.path.join(self.dir, "plan.json")
        bad = shot("a", 0.5)
        bad["duration"] = 2
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan([bad]), f)
        err = io.StringIO()  # the CLI's error lines are expected here; keep them out of the run
        with contextlib.redirect_stderr(err):
            self.assertEqual(shotplan.main(["render", plan_path, self.out]), 1)
        self.assertIn("shots[0].duration", err.getvalue())
        self.assertFalse(os.path.exists(self.out))


class SmoothedDepthTests(unittest.TestCase):
    """shotplan renders through stillmove's shared render_frames, so its depth is smoothed too."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_each_shot_smooths_its_depth_once_before_warping(self):
        import stillmove.render as stillrender

        real = stillrender.smooth_depth
        calls = []

        def spy(depth, image, *args, **kwargs):
            calls.append(np.asarray(depth).shape)
            return real(depth, image, *args, **kwargs)

        p = plan([shot("one", 1), shot("two", 1)])
        errors, rows, sequences = shotplan.check(p, self.dir)
        self.assertEqual(errors, [])
        out = os.path.join(self.dir, "out")
        with mock.patch.object(stillrender, "smooth_depth", spy):
            shotrender.render_plan(p, self.dir, out, rows, sequences, shotplan.load_json(shotplan.CATALOG),
                                   use_ffmpeg=False)
        self.assertEqual(calls, [(H, W), (H, W)])  # one smoothing per shot, at the image size
        self.assertGreater(len(os.listdir(os.path.join(out, "shots", "one", "frames"))), 0)


if __name__ == "__main__":
    unittest.main()
