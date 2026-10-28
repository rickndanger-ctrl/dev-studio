"""Tests for stillmove.blender_preview: the script compiles, its frame count is the move plus 48, the
size rule, the command line for Blender, the lookup, and the skip path when Blender is not found.
Blender is never run: subprocess.run is replaced with a fake."""

import ast
import contextlib
import io
import os
import py_compile
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from stillmove import blender_preview as bp
from stillmove import cli


# The orbit poses for a camera that ends at (0, -5, 0) in Blender coordinates (the dolly start).
ORBIT = bp.orbit_poses((0.0, -5.0, 0.0))


def constants(script_path):
    """The top-level NAME = number assignments of a generated script, parsed (not executed)."""
    with open(script_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return values


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_preview_script_compiles(self):
        path = bp.write_script(self.dir, 73, 1254, 1254, ORBIT)
        py_compile.compile(path, doraise=True)

    def test_frame_count_is_the_move_plus_48(self):
        path = bp.write_script(self.dir, 73, 1920, 1080, ORBIT)
        values = constants(path)
        self.assertEqual(values["MOVE_FRAMES"], 73)
        self.assertEqual(values["ORBIT_FRAMES"], 48)
        self.assertEqual(values["MOVE_FRAMES"] + values["ORBIT_FRAMES"], 73 + 48)
        self.assertEqual(values["ORBIT_DEGREES"], 10.0)
        self.assertEqual((values["WIDTH"], values["HEIGHT"]), (1080, 608))

    def test_output_is_the_mesh_folder_video(self):
        path = bp.write_script(self.dir, 3, 64, 64, ORBIT)
        self.assertEqual(constants(path)["OUTPUT"], os.path.join(os.path.abspath(self.dir), bp.VIDEO_NAME))

    def test_size_keeps_the_aspect_with_the_long_side_1080(self):
        self.assertEqual(bp.preview_size(1254, 1254), (1080, 1080))
        self.assertEqual(bp.preview_size(1920, 1080), (1080, 608))
        self.assertEqual(bp.preview_size(1080, 1920), (608, 1080))
        for w, h in (bp.preview_size(1920, 1080), bp.preview_size(1080, 1920)):
            self.assertEqual(w % 2, 0)
            self.assertEqual(h % 2, 0)

    def test_a_move_needs_at_least_one_frame(self):
        with self.assertRaises(ValueError):
            bp.script_text(0, 64, 64, os.path.join(self.dir, bp.VIDEO_NAME), ORBIT)


class CommandTests(unittest.TestCase):
    def test_blender_command_runs_scene_then_preview(self):
        self.assertEqual(bp.blender_command("/usr/local/bin/blender", "/some/mesh"),
                         ["/usr/local/bin/blender", "-b", "--factory-startup",
                          "--python", "scene.py", "--python", "preview_render.py"])

    def test_find_blender_prefers_path_then_the_mac_app(self):
        with mock.patch.object(bp.shutil, "which", return_value="/usr/local/bin/blender"):
            self.assertEqual(bp.find_blender(), "/usr/local/bin/blender")
        with tempfile.TemporaryDirectory() as d:
            app = os.path.join(d, "Blender")
            open(app, "w").close()
            with mock.patch.object(bp.shutil, "which", return_value=None), mock.patch.object(bp, "MAC_BLENDER", app):
                self.assertEqual(bp.find_blender(), app)
        with mock.patch.object(bp.shutil, "which", return_value=None), \
                mock.patch.object(bp, "MAC_BLENDER", os.path.join(tempfile.gettempdir(), "no-such-blender")):
            self.assertIsNone(bp.find_blender())

    def test_run_preview_builds_the_command_and_runs_it_in_the_mesh_folder(self):
        outdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outdir, ignore_errors=True)
        calls = []

        class Done:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):  # stands in for Blender: it writes the video, nothing else runs
            calls.append((cmd, kwargs))
            with open(os.path.join(outdir, bp.VIDEO_NAME), "wb") as f:
                f.write(b"")
            return Done()

        with mock.patch.object(bp, "find_blender", return_value="/fake/blender"), \
                mock.patch.object(bp.subprocess, "run", side_effect=fake_run):
            video = bp.run_preview(outdir)
        self.assertEqual(video, os.path.join(outdir, bp.VIDEO_NAME))
        cmd, kwargs = calls[0]
        self.assertEqual(cmd, bp.blender_command("/fake/blender", outdir))
        self.assertEqual(kwargs["cwd"], outdir)

    def test_a_failing_blender_is_one_error_line(self):
        class Failed:
            returncode = 1
            stdout = ""
            stderr = "trace\nRuntimeError: no GPU\n"

        with mock.patch.object(bp, "find_blender", return_value="/fake/blender"), \
                mock.patch.object(bp.subprocess, "run", return_value=Failed()):
            with self.assertRaisesRegex(RuntimeError, "no GPU"):
                bp.run_preview("/some/mesh")


class ExitZeroIsNotSuccessTests(unittest.TestCase):
    """Blender can exit 0 after a Python error in the script; the MP4 is the real check."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_exit_zero_without_a_video_is_an_error(self):
        class Done:
            returncode = 0
            stdout = ""
            stderr = "Traceback\nTypeError: enum FFMPEG not found\n"

        with mock.patch.object(bp, "find_blender", return_value="/fake/blender"), \
                mock.patch.object(bp.subprocess, "run", return_value=Done()):
            with self.assertRaisesRegex(RuntimeError, "enum FFMPEG not found"):
                bp.run_preview(self.dir)

    def test_script_writes_an_h264_mp4_video(self):
        path = bp.write_script(self.dir, 3, 64, 64, ORBIT)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("media_type = 'VIDEO'", text)
        self.assertIn("file_format = 'FFMPEG'", text)
        self.assertIn("codec = 'H264'", text)
        self.assertIn("'BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'", text)
        self.assertIn("scene.eevee.taa_render_samples = SAMPLES", text)
        self.assertIn("SAMPLES = 16", text)


class SkipPathTests(unittest.TestCase):
    """Without Blender the mesh is still written and the command exits 0 with one stderr line."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.image = os.path.join(self.dir, "still.png")
        Image.new("RGB", (48, 27), (120, 80, 40)).save(self.image)
        self.out = os.path.join(self.dir, "mesh")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_skip_writes_the_mesh_and_exits_zero(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(bp.shutil, "which", return_value=None), \
                mock.patch.object(bp, "MAC_BLENDER", os.path.join(self.dir, "no-such-blender")), \
                mock.patch.object(bp.subprocess, "run") as run, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["mesh", self.image, "--preset", "dolly_in", "--seconds", "0.5", "--fps", "4",
                             "--grid", "8x5", "--blender-preview", "-o", self.out])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertIn("blender-preview skipped: Blender not found", err.getvalue())
        self.assertNotIn("blender-preview skipped", out.getvalue())
        for name in ("mesh.obj", "scene.py", bp.SCRIPT_NAME):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)
        self.assertFalse(os.path.exists(os.path.join(self.out, bp.VIDEO_NAME)))


class ObjectImageTests(unittest.TestCase):
    """Each mesh object gets its own picture: the backdrop maps to backdrop.png, never the texture."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_script_maps_backdrop_to_backdrop_png_and_still_to_texture(self):
        text = bp.script_text(3, 64, 64, os.path.join(self.dir, bp.VIDEO_NAME), ORBIT,
                              images={"still": "texture.png", "backdrop": "backdrop.png"})
        values = {}
        for node in ast.parse(text).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("IMAGES", "MESH_DIR"):
                    values[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(values["IMAGES"], {"backdrop": "backdrop.png", "still": "texture.png"})
        self.assertEqual(values["MESH_DIR"], os.path.abspath(self.dir))
        compile(text, bp.SCRIPT_NAME, "exec")

    def test_script_picks_the_image_by_object_name_not_the_last_one_found(self):
        text = bp.script_text(3, 64, 64, os.path.join(self.dir, bp.VIDEO_NAME), ORBIT,
                              images={"still": "texture.png", "backdrop": "backdrop.png"})
        self.assertIn("IMAGES[name] for name in names if name in IMAGES", text)
        self.assertNotIn("image = node.image", text)  # the old loop that gave every object the last image

    def test_default_images_map_only_the_still(self):
        text = bp.script_text(3, 64, 64, os.path.join(self.dir, bp.VIDEO_NAME), ORBIT)
        self.assertIn("'still': 'texture.png'", text)
        self.assertNotIn("backdrop", text.split("IMAGES = ")[1].split("\n")[0])


class LightingTests(unittest.TestCase):
    """The lights and the world are module constants, and the generated script carries them."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _values(self, text):
        values = {}
        for node in ast.parse(text).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    values[node.targets[0].id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
        return values

    def test_script_carries_the_light_constants(self):
        text = bp.script_text(3, 64, 64, os.path.join(self.dir, bp.VIDEO_NAME), ORBIT)
        values = self._values(text)
        self.assertEqual(values["LIGHTS"], [(name, cfg) for name, cfg in bp.LIGHTS])
        names = [name for name, _ in values["LIGHTS"]]
        self.assertEqual(names, ["KeyLight", "FillLight", "RimLight"])
        self.assertEqual(bp.KEY_LIGHT["location"], (-2.0, -4.0, 6.0))  # higher and more frontal than before

    def test_key_is_higher_and_more_frontal_than_the_old_position(self):
        x, y, z = bp.KEY_LIGHT["location"]
        self.assertGreater(z, 4.0)
        self.assertLess(y, -3.0)

    def test_world_is_darker_than_the_paper_by_the_fraction(self):
        text = bp.script_text(3, 64, 64, os.path.join(self.dir, bp.VIDEO_NAME), ORBIT,
                              world_rgb=(0.8, 0.9, 1.0))
        world = self._values(text)["WORLD_RGB"]
        self.assertEqual(world, tuple(round(c * bp.WORLD_FRACTION, 6) for c in (0.8, 0.9, 1.0)))
        self.assertAlmostEqual(bp.WORLD_FRACTION, 0.35)
        self.assertTrue(all(w < p for w, p in zip(world, (0.8, 0.9, 1.0))))

    def test_script_aims_the_lights_at_the_origin(self):
        text = bp.script_text(3, 64, 64, os.path.join(self.dir, bp.VIDEO_NAME), ORBIT)
        self.assertIn("to_track_quat('Z', 'Y')", text)
        compile(text, bp.SCRIPT_NAME, "exec")


if __name__ == "__main__":
    unittest.main()
