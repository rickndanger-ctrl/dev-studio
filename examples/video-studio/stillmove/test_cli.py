import contextlib
import io
import json
import os
import tempfile
import unittest

import numpy as np
from PIL import Image

from stillmove.cli import main

W, H = 48, 27
FPS = 24
SECONDS = 1.0


def _distinct_runs(paths):
    arrays = [np.asarray(Image.open(p).convert("RGB")) for p in paths]
    return 1 + sum(not np.array_equal(a, b) for a, b in zip(arrays, arrays[1:]))


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliEndToEndTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.image = os.path.join(self.tmp, "in.png")
        img = np.zeros((H, W, 3), dtype=np.uint8)
        img[..., 0] = np.linspace(0, 255, W, dtype=np.uint8)[None, :]
        img[..., 1] = 120
        Image.fromarray(img).save(self.image)

    def test_render_preset_writes_frames_gif_and_manifest(self):
        out = os.path.join(self.tmp, "run")
        code, _, err = _run(["render", self.image, "--preset", "dolly_in",
                             "--seconds", str(SECONDS), "--fps", str(FPS), "--backend", "plane", "-o", out])
        self.assertEqual(code, 0, err)
        expected = round(SECONDS * FPS) + 1

        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(len(manifest["frames"]), expected)
        self.assertEqual(manifest["preview"], "preview.gif")
        self.assertEqual((manifest["backend"], manifest["fps"], manifest["width"], manifest["height"]),
                         ("plane", FPS, W, H))
        self.assertIsNone(manifest["mp4"])
        for name in manifest["frames"]:
            self.assertTrue(os.path.isfile(os.path.join(out, name)), name)

        # Pillow merges consecutive identical GIF frames (durations add up), so compare runs.
        paths = [os.path.join(out, name) for name in manifest["frames"]]
        with Image.open(os.path.join(out, "preview.gif")) as gif:
            self.assertEqual(gif.n_frames, _distinct_runs(paths))
            total = 0
            for i in range(gif.n_frames):
                gif.seek(i)
                total += gif.info["duration"]
            self.assertLessEqual(abs(total - expected * 1000 / FPS), 10)

    def test_presets_lists_the_catalog_ids(self):
        code, out, err = _run(["presets"])
        self.assertEqual(code, 0, err)
        ids = {line.split("\t")[0] for line in out.splitlines() if line}
        self.assertIn("dolly_in", ids)
        self.assertGreaterEqual(len(ids), 14)

    def test_doctor_has_no_fail(self):
        code, out, err = _run(["doctor"])
        self.assertEqual(code, 0, err + out)
        self.assertNotIn("FAIL", out)
        self.assertIn("PASS", out)

    def test_missing_image_is_one_error_line_and_exit_1(self):
        code, out, err = _run(["render", os.path.join(self.tmp, "nope.png"), "--preset", "dolly_in",
                               "-o", os.path.join(self.tmp, "x")])
        self.assertEqual(code, 1)
        lines = [line for line in err.splitlines() if line]
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith("error: "), lines[0])

    def test_preset_and_cam_together_is_an_error(self):
        code, _, err = _run(["render", self.image, "--preset", "dolly_in", "--cam", "cam.json",
                             "-o", os.path.join(self.tmp, "x")])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error: "), err)


class LimitsCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.image = os.path.join(self.tmp, "in.png")
        img = np.zeros((H, W, 3), dtype=np.uint8)
        img[..., 0] = np.linspace(0, 255, W, dtype=np.uint8)[None, :]
        Image.fromarray(img).save(self.image)

    def test_orbit_left_past_its_range_warns_on_stderr(self):
        out = os.path.join(self.tmp, "warn")
        code, _, err = _run(["render", self.image, "--preset", "orbit_left", "--seconds", "0.5",
                             "--fps", "24", "-o", out])
        self.assertEqual(code, 0, err)
        self.assertIn("warning: orbit_left angle 45 exceeds the single-photo safe range", err)
        self.assertIn("expect torn edges", err)

    def test_strict_refuses_orbit_left_with_exit_1(self):
        out = os.path.join(self.tmp, "strict")
        code, _, err = _run(["render", self.image, "--preset", "orbit_left", "--seconds", "0.5",
                             "--fps", "24", "--strict", "-o", out])
        self.assertEqual(code, 1)
        lines = [line for line in err.splitlines() if line]
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith("error: orbit_left angle 45 exceeds"), lines[0])
        self.assertFalse(os.path.exists(os.path.join(out, "manifest.json")))

    def test_photo_report_prints_all_five_fields(self):
        out = os.path.join(self.tmp, "report")
        code, text, err = _run(["render", self.image, "--preset", "dolly_in", "--seconds", "0.5",
                                "--fps", "24", "--photo-report", "-o", out])
        self.assertEqual(code, 0, err)
        for label in ("per-frame time: mean", "total time", "depth time", "peak memory",
                      "holes at end frame"):
            with self.subTest(label=label):
                self.assertIn(label, text)

    def test_presets_show_the_safe_range_column(self):
        code, out, err = _run(["presets"])
        self.assertEqual(code, 0, err)
        rows = [line.split("\t") for line in out.splitlines() if line]
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(id=row[0]):
                self.assertEqual(len(row), 3)
                self.assertTrue(row[2].startswith("<="), row[2])


if __name__ == "__main__":
    unittest.main()
