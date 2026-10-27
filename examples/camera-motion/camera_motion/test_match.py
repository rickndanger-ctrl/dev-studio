import json
import random
import unittest

from camera_motion.engine.camera_path import path
from camera_motion.match import DEFAULT_CATALOG, match, score


def _presets():
    with open(DEFAULT_CATALOG, encoding="utf-8") as f:
        data = json.load(f)
    return data["presets"] if isinstance(data, dict) else data


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


if __name__ == "__main__":
    unittest.main()
