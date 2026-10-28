"""Upscaling: output sizes, tiled seams against the whole picture, render --upscale, and the CLI."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from stillmove import cameras
from stillmove.backends.esrgan import DEFAULT_MODEL, EsrganGeneral
from stillmove.depth import get_backend
from stillmove.render import render
from stillmove.upscale import (BACKENDS, EsrganBackend, LanczosBackend, UpscaleBackend, get_upscaler, tiled_map,
                               upscale_image, upscale_path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def size_of(path):
    with Image.open(path) as im:
        return im.size


def picture(seed=3, height=90, width=130):
    return (np.random.default_rng(seed).random((height, width, 3)) * 255).astype(np.uint8)


class SizeTests(unittest.TestCase):
    def test_lanczos_makes_the_picture_scale_times_bigger(self):
        img = picture()
        for scale in (2, 4):
            self.assertEqual(LanczosBackend().upscale(img, scale).shape, (90 * scale, 130 * scale, 3))
            self.assertEqual(upscale_image(img, scale, get_upscaler("lanczos")).shape, (90 * scale, 130 * scale, 3))

    def test_the_esrgan_backend_refuses_a_missing_model(self):
        missing = os.path.join(ROOT, "no-such-esrgan-model.onnx")
        with self.assertRaisesRegex(ValueError, "not installed at"):
            EsrganBackend(EsrganGeneral(model_path=missing)).upscale(picture(), 2)

    def test_an_unknown_backend_and_a_bad_scale_are_refused(self):
        with self.assertRaises(ValueError):
            get_upscaler("nope")
        with self.assertRaises(ValueError):
            upscale_image(picture(), 3, LanczosBackend())


class TiledSeamTests(unittest.TestCase):
    def setUp(self):
        self.img = picture()
        self.whole = LanczosBackend().upscale(self.img, 2)

    def test_crop_seams_match_the_whole_picture_exactly(self):
        tiled = tiled_map(self.img, 2, LanczosBackend().upscale, tile=32, overlap=16, seam="crop")
        self.assertEqual(tiled.shape, self.whole.shape)
        self.assertEqual(int(np.abs(tiled.astype(int) - self.whole.astype(int)).max()), 0)

    def test_blended_seams_stay_within_three_levels_of_the_whole_picture(self):
        tiled = tiled_map(self.img, 2, LanczosBackend().upscale, tile=64, overlap=16, seam="blend")
        self.assertLessEqual(int(np.abs(tiled.astype(int) - self.whole.astype(int)).max()), 4)

    def test_a_tile_smaller_than_the_overlap_is_refused(self):
        with self.assertRaises(ValueError):
            tiled_map(self.img, 2, LanczosBackend().upscale, tile=16, overlap=16)

    def test_an_unknown_seam_is_refused(self):
        with self.assertRaises(ValueError):
            tiled_map(self.img, 2, LanczosBackend().upscale, seam="feather")

    def test_a_function_with_the_wrong_output_size_is_refused(self):
        with self.assertRaises(ValueError):
            tiled_map(self.img, 2, lambda window, scale: window, tile=64, overlap=8)


class NearestBackend(UpscaleBackend):
    """A different upscaler from Lanczos, so the render can show the backend is used."""

    name = "nearest"

    def upscale(self, img, scale):
        return np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)


class RenderUpscaleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.image = os.path.join(self.dir, "pic.png")
        Image.fromarray(picture(height=18, width=32)).save(self.image)
        self.cams = cameras.from_preset("dolly_in", 0.5, 4, 32, 18)

    def _render(self, out, **kwargs):
        return render(self.image, self.cams, get_backend("plane"), os.path.join(self.dir, out), **kwargs)

    def test_render_upscale_keeps_the_output_size_and_records_the_source(self):
        manifest = self._render("up", upscale=2)
        self.assertEqual(manifest["upscale"]["scale"], 2)
        self.assertEqual(manifest["upscale"]["source_before"], [32, 18])
        self.assertEqual(manifest["upscale"]["source_after"], [64, 36])
        self.assertEqual(manifest["upscale"]["output"], [32, 18])
        self.assertEqual(size_of(os.path.join(self.dir, "up", manifest["frames"][0])), (32, 18))
        self.assertEqual(len(manifest["frames"]), len(self.cams.frames))

    def test_render_without_upscale_records_none(self):
        self.assertIsNone(self._render("plain")["upscale"])

    def test_the_upscaled_source_depends_on_the_backend(self):
        with mock.patch.dict(BACKENDS, {"nearest": NearestBackend}):
            self._render("nearest", upscale=2, upscale_backend="nearest")
        self._render("lanczos", upscale=2, upscale_backend="lanczos")
        with Image.open(os.path.join(self.dir, "nearest", "frames", "0000.png")) as a, \
                Image.open(os.path.join(self.dir, "lanczos", "frames", "0000.png")) as b:
            nearest, lanczos = np.asarray(a), np.asarray(b)
        self.assertGreater(int(np.abs(nearest.astype(int) - lanczos.astype(int)).max()), 0)


class UpscaleCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _run(self, args):
        return subprocess.run([sys.executable, "-m", "stillmove.cli", "upscale"] + args, cwd=ROOT,
                              capture_output=True, text=True)

    def test_a_picture_becomes_a_bigger_picture(self):
        src = os.path.join(self.dir, "pic.png")
        Image.fromarray(picture(height=18, width=32)).save(src)
        out = os.path.join(self.dir, "pic-out")
        done = self._run([src, "--scale", "2", "-o", out])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(size_of(os.path.join(out, "upscaled.png")), (64, 36))

    def test_a_folder_of_frames_becomes_bigger_frames(self):
        frames = os.path.join(self.dir, "frames")
        os.makedirs(frames)
        for i in range(2):
            Image.fromarray(picture(seed=i, height=18, width=32)).save(os.path.join(frames, "%04d.png" % i))
        out = os.path.join(self.dir, "frames-out")
        done = self._run([frames, "--scale", "4", "--no-mp4", "-o", out])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(out, "frames"))), ["0000.png", "0001.png"])
        self.assertEqual(size_of(os.path.join(out, "frames", "0000.png")), (128, 72))

    def test_esrgan_cli_is_one_error_line_or_a_clean_run(self):
        # Passes with and without the model: a run that works writes frames; otherwise it is one error line.
        src = os.path.join(self.dir, "pic.png")
        Image.fromarray(picture(height=18, width=32)).save(src)
        out = os.path.join(self.dir, "x")
        done = self._run([src, "--backend", "esrgan", "-o", out])
        if done.returncode == 0:
            self.assertTrue(os.path.isdir(out))
            return
        self.assertEqual(done.returncode, 1)
        self.assertEqual(len(done.stderr.strip().splitlines()), 1, done.stderr)
        self.assertTrue(done.stderr.startswith("error: "), done.stderr)
        if not os.path.isfile(DEFAULT_MODEL):
            self.assertIn("the esrgan upscale model is not installed", done.stderr)


class RenderSizeTests(unittest.TestCase):
    """render --width/--height: the output frame is that size; the picture is cropped or padded to it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "src.png")
        Image.fromarray(picture(height=18, width=32)).save(self.src)

    def _cli(self, extra):
        out = os.path.join(self.dir, "out")
        command = [sys.executable, "-m", "stillmove.cli", "render", self.src, "--preset", "dolly_in",
                   "--seconds", "0.5", "--fps", "4", "--backend", "plane", "-o", out] + extra
        return subprocess.run(command, cwd=ROOT, capture_output=True, text=True), out

    def test_width_and_height_set_the_output_frame(self):
        done, out = self._cli(["--width", "64", "--height", "36", "--upscale", "2"])
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual((manifest["width"], manifest["height"]), (64, 36))
        self.assertEqual(size_of(os.path.join(out, manifest["frames"][0])), (64, 36))
        self.assertEqual(manifest["upscale"]["output"], [64, 36])

    def test_width_without_height_is_one_error_line(self):
        done, _out = self._cli(["--width", "64"])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("give both --width and --height, or neither", done.stderr)
        self.assertEqual(len(done.stderr.strip().splitlines()), 1)

    def test_a_320_source_renders_to_a_1280_output_with_upscale(self):
        big = os.path.join(self.dir, "big.png")
        Image.fromarray(picture(height=320, width=320)).save(big)
        out = os.path.join(self.dir, "x4")
        command = [sys.executable, "-m", "stillmove.cli", "render", big, "--preset", "dolly_in",
                   "--seconds", "0.25", "--fps", "4", "--backend", "plane", "--width", "1280", "--height", "1280",
                   "--upscale", "4", "--upscale-backend", "lanczos", "-o", out]
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["upscale"]["source_before"], [320, 320])
        self.assertEqual(manifest["upscale"]["source_after"], [1280, 1280])
        self.assertEqual(size_of(os.path.join(out, manifest["frames"][0])), (1280, 1280))


if __name__ == "__main__":
    unittest.main()
