"""Review fixes for makeclip: clip folder names, the render python check, and the ffmpeg rule.

The renderer is replaced with a fake that writes a GIF, so no Blender, model or ffmpeg is needed.
"""
import io
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest

from PIL import Image

from makeclip import server


def _png():
    out = io.BytesIO()
    Image.new("RGB", (16, 12), (200, 90, 40)).save(out, "PNG")
    return out.getvalue()


class ClipNameTest(unittest.TestCase):
    def test_the_upload_time_stamp_is_dropped_before_the_clip_stamp_is_added(self):
        name = server.clip_name("My Shop-20261026-101010.jpg", "20261027-120000", lambda n: False)
        self.assertEqual(name, "my-shop-20261027-120000")

    def test_a_name_clash_gets_two_then_three(self):
        taken = {"photo-20261027-120000", "photo-20261027-120000-2"}
        name = server.clip_name("photo.png", "20261027-120000", lambda n: n in taken)
        self.assertEqual(name, "photo-20261027-120000-3")

    def test_a_clash_does_not_wait_for_a_later_time(self):
        calls = []

        def taken(name):
            calls.append(name)
            return len(calls) < 3

        started = time.monotonic()
        name = server.clip_name("photo.png", "20261027-120000", taken)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(name, "photo-20261027-120000-3")
        self.assertEqual(len(calls), 3)


class RenderPythonTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_this_python_has_what_clips_need(self):
        self.assertIsNone(server.render_python_problem(sys.executable))

    def test_a_python_without_numpy_or_pillow_gets_one_plain_line(self):
        stub = os.path.join(self.dir, "fake-python")
        with open(stub, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 1\n")
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
        problem = server.render_python_problem(stub)
        self.assertIsNotNone(problem)
        self.assertIn("lacks numpy or Pillow", problem)
        self.assertNotIn("\n", problem)


class FakeRenderer:
    """Writes a GIF into the clip folder and returns the code it was given."""

    def __init__(self, code=0, gif=True, mp4=False):
        self.code, self.gif, self.mp4 = code, gif, mp4
        self.commands = []

    def __call__(self, command, env, clip_dir, frames):
        self.commands.append(command)
        if self.gif:
            with open(os.path.join(clip_dir, "preview.gif"), "wb") as handle:
                handle.write(b"GIF89a")
        if self.mp4:
            with open(os.path.join(clip_dir, "preview.mp4"), "wb") as handle:
                handle.write(b"mp4")
        return self.code, ""


class FfmpegRuleTest(unittest.TestCase):
    BODY = {"preset": "dolly_in", "amount": 0.4, "seconds": 2, "size": "square", "layers": False,
            "depth": "simple"}

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.inputs = os.path.join(self.root, "in")
        self.outputs = os.path.join(self.root, "out")
        os.makedirs(self.inputs)
        with open(os.path.join(self.inputs, "photo.png"), "wb") as handle:
            handle.write(_png())

    def _studio(self, renderer, has_ffmpeg):
        studio = server.Studio(self.inputs, self.outputs, sys.executable, renderer=renderer,
                               page=b"<!doctype html>", plans_root=self.root, has_ffmpeg=has_ffmpeg)
        self.addCleanup(studio.close)
        return studio

    def _run_clip(self, studio):
        body = dict(self.BODY, image="photo.png")
        studio.start(body)
        deadline = time.monotonic() + 15
        while studio.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        return studio.status()

    def test_without_ffmpeg_the_render_has_no_mp4_flag_and_says_how_to_get_one(self):
        renderer = FakeRenderer(code=0, gif=True)
        status = self._run_clip(self._studio(renderer, has_ffmpeg=False))
        self.assertNotIn("--mp4", renderer.commands[0])
        self.assertEqual(status["state"], "done")
        self.assertIn("install ffmpeg to get an MP4", status["message"])

    def test_with_ffmpeg_the_render_asks_for_the_mp4(self):
        renderer = FakeRenderer(code=0, gif=True, mp4=True)
        status = self._run_clip(self._studio(renderer, has_ffmpeg=True))
        self.assertIn("--mp4", renderer.commands[0])
        self.assertEqual(status["state"], "done")

    def test_a_failed_render_never_says_exited_with_status_0_no_preview_mp4(self):
        renderer = FakeRenderer(code=0, gif=False)
        status = self._run_clip(self._studio(renderer, has_ffmpeg=False))
        self.assertEqual(status["state"], "error")
        self.assertNotIn("exited with status 0, no preview.mp4", status["message"])
        self.assertNotIn("exited with status", status["message"])
        self.assertIn("wrote no preview.mp4 or preview.gif", status["message"])

    def test_a_render_that_exits_nonzero_gives_its_status_in_plain_words(self):
        renderer = FakeRenderer(code=3, gif=False)
        status = self._run_clip(self._studio(renderer, has_ffmpeg=True))
        self.assertEqual(status["state"], "error")
        self.assertNotIn("exited with status 0, no preview.mp4", status["message"])
        self.assertIn("status 3", status["message"])

    def test_a_gif_only_clip_is_still_listed(self):
        renderer = FakeRenderer(code=0, gif=True)
        studio = self._studio(renderer, has_ffmpeg=False)
        self._run_clip(studio)
        clips = studio.clips()
        self.assertEqual(len(clips), 1)
        self.assertIsNone(clips[0]["mp4"])
        self.assertIsNotNone(clips[0]["gif"])

    def test_promo_command_asks_for_an_mp4_only_with_ffmpeg(self):
        with_mp4 = server.promo_command(sys.executable, "plan.json", "out", False, mp4=True)
        without = server.promo_command(sys.executable, "plan.json", "out", False, mp4=False)
        self.assertIn("--mp4", with_mp4)
        self.assertNotIn("--mp4", without)


if __name__ == "__main__":
    unittest.main()
