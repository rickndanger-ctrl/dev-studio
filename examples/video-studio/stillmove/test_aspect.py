"""Picture shape: crop to fill (the default) or pad with borders, never a stretch; and the mp4 note."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

from stillmove.render import _load_image, load_picture

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _save(path, image):
    Image.fromarray(image).save(path)


class PictureShapeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_default_crop_keeps_a_centred_circle_round(self):
        # 160x90 into 64x64: a stretch would squash the circle; a centred crop keeps it round.
        image = np.full((90, 160, 3), 255, dtype=np.uint8)
        yy, xx = np.mgrid[0:90, 0:160]
        image[(yy - 45) ** 2 + (xx - 80) ** 2 <= 40 ** 2] = 0
        path = os.path.join(self.dir, "circle.png")
        _save(path, image)
        with contextlib.redirect_stderr(io.StringIO()):
            out = _load_image(path, 64, 64)
        self.assertEqual(out.shape, (64, 64, 3))
        dark = np.argwhere(out[..., 0] < 128)
        height = dark[:, 0].max() - dark[:, 0].min() + 1
        width = dark[:, 1].max() - dark[:, 1].min() + 1
        self.assertLessEqual(abs(width - height), 2)

    def test_crop_record_names_the_mode_and_both_shapes(self):
        path = os.path.join(self.dir, "wide.png")
        _save(path, np.zeros((90, 160, 3), dtype=np.uint8))
        with contextlib.redirect_stderr(io.StringIO()):
            out, record = load_picture(path, 64, 64, "crop")
        self.assertEqual(out.shape, (64, 64, 3))
        self.assertEqual(record, {"mode": "crop", "source": [160, 90], "target": [64, 64]})

    def test_pad_fills_the_border_with_the_edge_colour(self):
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        image[...] = (200, 30, 30)
        image[30:60, 60:100] = (30, 30, 200)
        path = os.path.join(self.dir, "pad.png")
        _save(path, image)
        with contextlib.redirect_stderr(io.StringIO()):
            out, record = load_picture(path, 64, 64, "pad")
        self.assertEqual(record["mode"], "pad")
        self.assertTrue(np.all(np.abs(out[0, :].astype(int) - [200, 30, 30]) <= 3))  # top border
        self.assertTrue(np.all(np.abs(out[32, 32].astype(int) - [30, 30, 200]) <= 40))  # the picture

    def test_a_mismatched_aspect_prints_one_warning(self):
        path = os.path.join(self.dir, "warn.png")
        _save(path, np.zeros((90, 160, 3), dtype=np.uint8))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            load_picture(path, 64, 64, "crop")
        lines = [line for line in err.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 1)
        self.assertIn("cropped to fill", lines[0])

    def test_an_unknown_mode_is_refused(self):
        path = os.path.join(self.dir, "mode.png")
        _save(path, np.zeros((9, 16, 3), dtype=np.uint8))
        with self.assertRaises(ValueError):
            load_picture(path, 8, 8, "stretch")


class RenderCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.image = os.path.join(self.dir, "pic.png")
        _save(self.image, np.full((27, 48, 3), 120, dtype=np.uint8))

    def _run(self, extra, env=None):
        out = os.path.join(self.dir, "out")
        command = [sys.executable, "-m", "stillmove.cli", "render", self.image, "--preset", "dolly_in",
                   "--seconds", "0.25", "--fps", "4", "--backend", "plane", "-o", out] + extra
        return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env), out

    def test_render_records_the_aspect_in_the_manifest(self):
        done, out = self._run(["--aspect", "pad"])
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["aspect"]["mode"], "pad")
        self.assertEqual(manifest["aspect"]["source"], [48, 27])

    def test_mp4_without_ffmpeg_prints_the_note_to_stderr(self):
        env = dict(os.environ, PATH=os.path.join(self.dir, "no-bin"))
        done, out = self._run(["--mp4"], env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("note: ffmpeg not found on PATH; mp4 skipped", done.stderr)


if __name__ == "__main__":
    unittest.main()
