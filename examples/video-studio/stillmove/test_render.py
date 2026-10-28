import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from stillmove import cameras
from stillmove.depth import get_backend
from stillmove.render import MANIFEST, PREVIEW, render, render_frames

W, H = 48, 27
FPS = 24
SECONDS = 1.0
TOL_MEAN_ABS = 2.0  # mean absolute difference in 0-255 levels; the frame-0 check uses a tolerance


def distinct_runs(paths):
    """How many GIF entries Pillow writes: runs of pixel-identical consecutive frames."""
    arrays = [np.asarray(Image.open(p).convert("RGB")) for p in paths]
    return 1 + sum(not np.array_equal(a, b) for a, b in zip(arrays, arrays[1:]))


def _input_image(path):
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[..., 0] = np.linspace(0, 255, W, dtype=np.uint8)[None, :]
    img[..., 1] = np.linspace(255, 0, H, dtype=np.uint8)[:, None]
    img[..., 2] = 90
    Image.fromarray(img).save(path)
    return img


class RenderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.image = os.path.join(self.tmp, "in.png")
        self.source = _input_image(self.image)
        self.cams = cameras.from_preset("dolly_in", SECONDS, FPS, W, H)
        self.out = os.path.join(self.tmp, "out")

    def test_frame_count_matches_the_camera_path(self):
        manifest = render(self.image, self.cams, get_backend("plane"), self.out)
        expected = round(SECONDS * FPS) + 1
        self.assertEqual(len(self.cams.frames), expected)
        self.assertEqual(len(manifest["frames"]), expected)

    def test_frame_files_exist(self):
        manifest = render(self.image, self.cams, get_backend("plane"), self.out)
        for name in manifest["frames"]:
            with self.subTest(name=name):
                self.assertTrue(os.path.isfile(os.path.join(self.out, name)))

    def test_preview_gif_keeps_the_frame_timing(self):
        # Pillow merges consecutive identical GIF frames and adds their durations. So the GIF has
        # one entry per run of identical frames, and its total time is still every frame.
        manifest = render(self.image, self.cams, get_backend("gradient"), self.out)
        paths = [os.path.join(self.out, name) for name in manifest["frames"]]
        with Image.open(os.path.join(self.out, PREVIEW)) as gif:
            self.assertEqual(gif.n_frames, distinct_runs(paths))
            total = 0
            for i in range(gif.n_frames):
                gif.seek(i)
                total += gif.info["duration"]
            # GIF delays are in 10 ms units; the total is the frame count over fps, within 10 ms.
            self.assertLessEqual(abs(total - len(paths) * 1000 / FPS), 10)
            self.assertEqual(gif.info.get("loop", 0), 0)

    def test_manifest_has_the_expected_fields(self):
        render(self.image, self.cams, get_backend("plane"), self.out)
        with open(os.path.join(self.out, MANIFEST), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["preview"], PREVIEW)
        self.assertEqual((manifest["fps"], manifest["width"], manifest["height"]), (FPS, W, H))
        self.assertEqual(manifest["backend"], "plane")
        self.assertIsNone(manifest["mp4"])

    def test_first_frame_is_the_input_within_tolerance(self):
        manifest = render(self.image, self.cams, get_backend("plane"), self.out)
        frame0 = np.asarray(Image.open(os.path.join(self.out, manifest["frames"][0])).convert("RGB"),
                            dtype=np.float32)
        self.assertLess(float(np.abs(frame0 - self.source.astype(np.float32)).mean()), TOL_MEAN_ABS)

    def test_mp4_is_none_without_ffmpeg_and_says_why(self):
        with mock.patch("stillmove.render.shutil.which", return_value=None):
            manifest = render(self.image, self.cams, get_backend("plane"), self.out, mp4=True)
        self.assertIsNone(manifest["mp4"])
        self.assertIn("ffmpeg", manifest["note"])

    def test_no_mp4_unless_asked(self):
        manifest = render(self.image, self.cams, get_backend("plane"), self.out)
        self.assertIsNone(manifest["mp4"])
        self.assertNotIn("note", manifest)


class RenderReportTests(unittest.TestCase):
    def test_report_has_timings_memory_and_hole_shares(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = os.path.join(tmp, "in.png")
            _input_image(image)
            cams = cameras.from_preset("dolly_in", SECONDS, FPS, W, H)
            manifest = render(image, cams, get_backend("plane"), os.path.join(tmp, "out"), report=True)
        report = manifest["report"]
        self.assertEqual(report["frames"], len(cams.frames))
        for key in ("per_frame_mean_s", "per_frame_max_s", "total_s", "depth_s", "peak_memory_mb",
                    "holes_end_pct", "holes_max_pct"):
            with self.subTest(key=key):
                self.assertGreaterEqual(report[key], 0)
        self.assertLessEqual(report["per_frame_mean_s"], report["per_frame_max_s"])
        self.assertLessEqual(report["holes_end_pct"], report["holes_max_pct"] + 1e-9)
        self.assertLessEqual(report["holes_max_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()


class RenderFramesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_smooths_depth_once_and_writes_one_png_per_frame(self):
        import stillmove.render as stillrender

        cams = cameras.from_preset("dolly_in", SECONDS, FPS, W, H)
        image = np.linspace(0, 255, W, dtype=np.uint8)[None, :, None].repeat(H, axis=0).repeat(3, axis=2)
        depth = get_backend("plane").depth(image)
        real = stillrender.smooth_depth
        with mock.patch.object(stillrender, "smooth_depth", wraps=real) as spy:
            count = render_frames(image, depth, cams, os.path.join(self.dir, "frames"))
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(count, len(cams.frames))
        self.assertEqual(sorted(os.listdir(os.path.join(self.dir, "frames"))),
                         ["%04d.png" % i for i in range(count)])
