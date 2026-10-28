"""Frame interpolation: frame counts, in-betweens, the folder command and the smooth and slowmo renders."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

from stillmove import cameras
from stillmove.depth import get_backend
from stillmove.interp import LinearBackend, RifeBackend, get_interp, interp_folder, interpolate, render_interpolated

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def flat(value, height=6, width=8):
    return np.full((height, width, 3), value, dtype=np.uint8)


class InterpolationMathTests(unittest.TestCase):
    def test_frame_counts_follow_the_rule(self):
        for count in (2, 5):
            for factor in (2, 4):
                frames = [flat(i * 10) for i in range(count)]
                self.assertEqual(len(interpolate(frames, factor, LinearBackend())), (count - 1) * factor + 1)

    def test_in_betweens_lie_between_the_two_colours(self):
        out = interpolate([flat(0), flat(200)], 4, LinearBackend())
        self.assertEqual(len(out), 5)
        for k, expect in ((1, 50), (2, 100), (3, 150)):
            self.assertLessEqual(abs(int(out[k][0, 0, 0]) - expect), 1)
            self.assertTrue(0 < out[k][0, 0, 0] < 200)

    def test_the_original_frames_are_kept_at_their_ends(self):
        frames = [flat(10), flat(90), flat(250)]
        out = interpolate(frames, 2, LinearBackend())
        np.testing.assert_array_equal(out[0], frames[0])
        np.testing.assert_array_equal(out[-1], frames[-1])
        np.testing.assert_array_equal(out[2], frames[1])

    def test_the_rife_backend_refuses_fractions_other_than_the_midpoint_steps(self):
        with self.assertRaisesRegex(ValueError, "only the midpoint"):
            RifeBackend().between(flat(0), flat(255), 0.3)
        self.assertIsInstance(get_interp("rife"), RifeBackend)

    def test_an_unknown_backend_is_refused(self):
        with self.assertRaises(ValueError):
            get_interp("nope")


class InterpFolderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.frames = os.path.join(self.dir, "frames")
        os.makedirs(self.frames)
        for i in range(4):
            Image.fromarray(flat(i * 60)).save(os.path.join(self.frames, "%04d.png" % i))

    def test_the_folder_command_writes_frames_a_preview_and_a_manifest(self):
        out = os.path.join(self.dir, "out")
        manifest = interp_folder(self.frames, 2, out, fps=12)
        self.assertEqual(len(manifest["frames"]), 7)
        self.assertEqual(len(os.listdir(os.path.join(out, "frames"))), 7)
        self.assertTrue(os.path.isfile(os.path.join(out, "preview.gif")))
        info = manifest["interp"]
        self.assertEqual((info["factor"], info["mode"], info["backend"]), (2, "interp", "linear"))
        self.assertEqual((info["input_frames"], info["output_frames"]), (4, 7))
        self.assertGreaterEqual(info["timing"]["interp_s"], 0)
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["interp"]["factor"], 2)


class RenderModeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.image = os.path.join(self.dir, "pic.png")
        Image.fromarray(flat(120, height=18, width=32)).save(self.image)

    def test_smooth_keeps_the_duration(self):
        cams = cameras.from_preset("dolly_in", 1, 4, 32, 18)  # 5 frames at 4 fps
        out = os.path.join(self.dir, "smooth")
        manifest = render_interpolated(self.image, cams, get_backend("plane"), out, 2, "smooth")
        self.assertEqual(len(manifest["frames"]), 5)
        self.assertEqual(manifest["fps"], 4)
        self.assertEqual((manifest["interp"]["rendered_fps"], manifest["interp"]["output_fps"]), (2, 4))
        self.assertEqual(manifest["interp"]["mode"], "smooth")
        self.assertTrue(os.path.isfile(os.path.join(out, "preview.gif")))

    def test_slowmo_stretches_the_duration(self):
        cams = cameras.from_preset("dolly_in", 1, 4, 32, 18)
        out = os.path.join(self.dir, "slowmo")
        manifest = render_interpolated(self.image, cams, get_backend("plane"), out, 2, "slowmo")
        self.assertEqual(len(manifest["frames"]), 9)
        self.assertEqual((manifest["interp"]["rendered_fps"], manifest["interp"]["output_fps"]), (4, 4))
        self.assertEqual(manifest["interp"]["output_frames"], 9)
        self.assertGreaterEqual(manifest["interp"]["timing"]["render_s"], 0)

    def test_smooth_needs_a_fps_it_can_halve(self):
        cams = cameras.from_preset("dolly_in", 1, 3, 32, 18)
        with self.assertRaises(ValueError):
            render_interpolated(self.image, cams, get_backend("plane"), os.path.join(self.dir, "x"), 2, "smooth")

    def test_the_cli_slowmo_writes_the_mode_and_count(self):
        out = os.path.join(self.dir, "cli")
        command = [sys.executable, "-m", "stillmove.cli", "render", self.image, "--preset", "dolly_in",
                   "--seconds", "1", "--fps", "4", "--backend", "plane", "--slowmo", "2", "-o", out]
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["interp"]["mode"], "slowmo")
        self.assertEqual(len(manifest["frames"]), 9)


class InterpCliTests(unittest.TestCase):
    """The interp subcommand runs from the command line (it was not dispatched before)."""

    def test_interp_subcommand_writes_the_frames(self):
        dir = tempfile.mkdtemp()
        frames = os.path.join(dir, "frames")
        os.makedirs(frames)
        for i in range(3):
            Image.fromarray(flat(i * 80)).save(os.path.join(frames, "%04d.png" % i))
        out = os.path.join(dir, "out")
        done = subprocess.run([sys.executable, "-m", "stillmove.cli", "interp", frames, "--factor", "2",
                               "--backend", "linear", "-o", out], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(os.listdir(os.path.join(out, "frames"))), 5)


class SizesInterpTests(unittest.TestCase):
    """--sizes honours --smooth and --slowmo: each shape is interpolated and keeps its own interp block."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.image = os.path.join(self.dir, "pic.png")
        Image.fromarray(flat(120, height=18, width=32)).save(self.image)

    def _run(self, extra):
        out = os.path.join(self.dir, "out")
        command = [sys.executable, "-m", "stillmove.cli", "render", self.image, "--preset", "dolly_in",
                   "--seconds", "1", "--fps", "4", "--backend", "plane", "--sizes", "square,wide",
                   "-o", out] + extra
        return subprocess.run(command, cwd=ROOT, capture_output=True, text=True), out

    def _manifest(self, out, sid):
        with open(os.path.join(out, sid, "manifest.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def test_smooth_gives_the_same_frames_per_shape(self):
        done, out = self._run(["--smooth", "2"])
        self.assertEqual(done.returncode, 0, done.stderr)
        for sid in ("square", "wide"):
            manifest = self._manifest(out, sid)
            info = manifest["interp"]
            self.assertEqual((info["factor"], info["mode"]), (2, "smooth"))
            self.assertEqual(info["rendered_frames"], 3)
            self.assertEqual(len(manifest["frames"]), (info["rendered_frames"] - 1) * 2 + 1)
            self.assertIn("timing", info)
        self.assertTrue(os.path.isfile(os.path.join(out, "sizes.zip")))

    def test_slowmo_gives_nine_frames_per_shape(self):
        done, out = self._run(["--slowmo", "2"])
        self.assertEqual(done.returncode, 0, done.stderr)
        for sid in ("square", "wide"):
            manifest = self._manifest(out, sid)
            self.assertEqual(manifest["interp"]["mode"], "slowmo")
            self.assertEqual(len(manifest["frames"]), 9)

    def test_both_flags_are_refused_with_sizes_too(self):
        done, _out = self._run(["--smooth", "2", "--slowmo", "2"])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("not both", done.stderr)


if __name__ == "__main__":
    unittest.main()
