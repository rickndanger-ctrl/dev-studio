import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from camera_motion.engine.camera_path import path
from camera_motion.export.formats import main, parse_re10k, to_json, to_realestate10k
from camera_motion.export.trajectory import intrinsics, pose_matrix, world_to_camera

WIDTH, HEIGHT, FPS, DURATION = 640, 360, 10, 1.5
START = {"position": [1.0, 2.0, 5.0], "rotation": [20.0, -10.0, 5.0], "fov": 60.0}
PRESET = {"type": "orbit", "params": {"angle": 45, "radius": 3}, "easing": "ease_in_out"}
TOL = 1e-9


def _frames():
    return path(PRESET, duration_s=DURATION, fps=FPS, start=START)


class RealEstate10KTests(unittest.TestCase):
    def setUp(self):
        self.frames = _frames()
        self.lines = to_realestate10k(self.frames, WIDTH, HEIGHT, FPS, "scene")

    def test_frame_count_matches_engine(self):
        self.assertEqual(len(self.lines), len(self.frames) + 1)
        self.assertEqual(self.lines[0], "# scene")

    def test_timestamps_are_microseconds(self):
        for line, frame in zip(self.lines[1:], self.frames):
            self.assertEqual(int(line.split()[0]), round(frame["t"] * 1e6))
        self.assertEqual(int(self.lines[-1].split()[0]), round(DURATION * 1e6))

    def test_round_trip_matrices(self):
        parsed = parse_re10k(self.lines)
        self.assertEqual(len(parsed), len(self.frames))
        for (intr, rows), frame in zip(parsed, self.frames):
            want_intr = intrinsics(frame["fov"], WIDTH, HEIGHT)
            for got, want in zip(intr, want_intr):
                self.assertAlmostEqual(got, want, delta=TOL)
            # Undo the OpenCV flip (y and z rows) and compare to the OpenGL w2c.
            gl = [rows[0], [-v for v in rows[1]], [-v for v in rows[2]]]
            want_w2c = world_to_camera(frame)
            for i in range(3):
                for j in range(4):
                    self.assertAlmostEqual(gl[i][j], want_w2c[i][j], delta=TOL)

    def test_header_name_defaults_to_empty(self):
        lines = to_realestate10k(self.frames)
        self.assertEqual(lines[0], "# ")


class JsonTests(unittest.TestCase):
    def test_structure_and_matrices(self):
        frames = _frames()
        out = to_json(frames, WIDTH, HEIGHT, FPS)
        self.assertEqual(out["fps"], FPS)
        self.assertEqual((out["width"], out["height"]), (WIDTH, HEIGHT))
        self.assertEqual(len(out["frames"]), len(frames))
        for got, frame in zip(out["frames"], frames):
            self.assertAlmostEqual(got["t"], frame["t"])
            self.assertEqual(len(got["K"]), 3)
            self.assertEqual(len(got["c2w"]), 4)
            self.assertEqual(len(got["w2c"]), 4)
            for i in range(4):
                for j in range(4):
                    self.assertAlmostEqual(got["c2w"][i][j], pose_matrix(frame)[i][j], delta=TOL)
                    self.assertAlmostEqual(got["w2c"][i][j], world_to_camera(frame)[i][j], delta=TOL)


class CliTests(unittest.TestCase):
    def test_re10k_file_has_one_line_per_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = os.path.join(tmp, "preset.json")
            out_path = os.path.join(tmp, "out.txt")
            with open(preset_path, "w") as f:
                json.dump(PRESET, f)
            code = main([preset_path, "--format", "re10k", "-o", out_path,
                         "--fps", "10", "--seconds", "1.5", "--width", "640", "--height", "360"])
            self.assertEqual(code, 0)
            with open(out_path) as f:
                lines = f.read().splitlines()
            self.assertEqual(len(lines), round(1.5 * 10) + 1 + 1)

    def test_json_file_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = os.path.join(tmp, "preset.json")
            out_path = os.path.join(tmp, "out.json")
            with open(preset_path, "w") as f:
                json.dump(PRESET, f)
            main([preset_path, "--format", "json", "-o", out_path, "--fps", "10", "--seconds", "1.5"])
            with open(out_path) as f:
                data = json.load(f)
            self.assertEqual(len(data["frames"]), round(1.5 * 10) + 1)


class FormatsMainDelegatesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.preset_file = os.path.join(self.dir, "preset.json")
        with open(self.preset_file, "w", encoding="utf-8") as f:
            json.dump(PRESET, f)

    def test_main_calls_the_cli_export_command(self):
        out = os.path.join(self.dir, "out.json")
        with mock.patch("camera_motion.cli.main", return_value=0) as cli_main:
            self.assertEqual(main([self.preset_file, "--format", "json", "-o", out, "--fps", "10"]), 0)
        argv = cli_main.call_args[0][0]
        self.assertEqual(argv[0], "export")
        self.assertEqual(argv[1], self.preset_file)
        self.assertEqual(argv[argv.index("--format") + 1], "json")
        self.assertEqual(argv[argv.index("-o") + 1], out)
        self.assertEqual(argv[argv.index("--fps") + 1], "10")

    def test_without_out_writes_the_export_to_stdout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([self.preset_file, "--format", "json", "--fps", "10", "--seconds", "1.5"]), 0)
        self.assertEqual(len(json.loads(out.getvalue())["frames"]), 16)

    def test_trajectory_module_still_runs_as_a_script(self):
        out = os.path.join(self.dir, "traj.txt")
        result = subprocess.run(
            [sys.executable, "-m", "camera_motion.export.trajectory", self.preset_file,
             "--format", "re10k", "-o", out],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out, encoding="utf-8") as f:
            self.assertTrue(parse_re10k(f.read().splitlines()))


if __name__ == "__main__":
    unittest.main()
