import random
import unittest

from camera_motion.engine.camera_path import path
from camera_motion.match import match, score
from camera_motion.presets import load_catalog


def _presets():
    return load_catalog()


def _frames(preset):
    return path(preset, duration_s=3.0, fps=24)


def _noisy(frames, rng):
    out = []
    for f in frames:
        out.append({
            "t": f["t"],
            "position": [p + rng.gauss(0.0, 0.02) for p in f["position"]],
            "rotation": [a + rng.gauss(0.0, 0.5) for a in f["rotation"]],
            "fov": f["fov"] + rng.gauss(0.0, 0.2),
        })
    return out


class MatchTests(unittest.TestCase):
    def test_each_preset_matches_itself_first(self):
        for preset in _presets():
            with self.subTest(id=preset["id"]):
                ranked = match(_frames(preset))
                self.assertEqual(ranked[0][0], preset["id"])
                self.assertLess(ranked[0][1], 1e-9)

    def test_noisy_copy_still_ranks_right_first_for_all_presets(self):
        rng = random.Random(7)
        for preset in _presets():
            with self.subTest(id=preset["id"]):
                ranked = match(_noisy(_frames(preset), rng))
                self.assertEqual(ranked[0][0], preset["id"])

    def test_returns_top_three_ascending(self):
        ranked = match(_frames(_presets()[0]))
        self.assertEqual(len(ranked), 3)
        scores = [s for _, s in ranked]
        self.assertEqual(scores, sorted(scores))

    def test_rotation_error_wraps_at_180(self):
        a = [{"t": 0.0, "position": [0, 0, 5], "rotation": [179.0, 0.0, 0.0], "fov": 50.0}]
        b = [{"t": 0.0, "position": [0, 0, 5], "rotation": [-179.0, 0.0, 0.0], "fov": 50.0}]
        # 2 degrees apart across the seam, not 358: the score must stay small.
        self.assertLess(score(a, b), 0.05)

    def test_rejects_single_frame(self):
        with self.assertRaises(ValueError):
            match(_frames(_presets()[0])[:1])

    def test_without_fov_track_dolly_and_dolly_zoom_tie(self):
        presets = {p["id"]: p for p in _presets()}
        frames = _frames(presets["dolly_zoom_in"])
        self.assertEqual(match(frames, top=1)[0][0], "dolly_zoom_in")
        ranked = match(frames, top=2, fov_track=False)
        self.assertEqual({pid for pid, _ in ranked}, {"dolly_in", "dolly_zoom_in"})
        self.assertAlmostEqual(ranked[0][1], ranked[1][1], delta=1e-12)


def _at_times(frames, times):
    return [dict(f, t=t) for f, t in zip(frames, times)]


class EvenSpacingTests(unittest.TestCase):
    def test_uneven_timestamps_are_rejected(self):
        frames = _frames(_presets()[0])
        times = [0.0, 1 / 24, 2 / 24, 4 / 24] + [4 / 24 + i / 24 for i in range(1, len(frames) - 3)]
        with self.assertRaisesRegex(ValueError, r"not evenly spaced \(frame 1\)"):
            match(_at_times(frames, times))

    def test_jitter_within_tolerance_is_accepted(self):
        frames = _frames(_presets()[0])
        times = [i / 24 * (1 + 1e-6 * ((-1) ** i)) for i in range(len(frames))]
        ranked = match(_at_times(frames, times))
        self.assertEqual(len(ranked), 3)


if __name__ == "__main__":
    unittest.main()
