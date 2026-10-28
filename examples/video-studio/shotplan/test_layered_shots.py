"""Per-shot layers: check accepts and refuses the flag, a layered shot differs from a plain one, plain shots
are byte-identical to the plain warp, the matte is built once per render, and timing is unchanged.
Fake matte, tiny images, temporary folders only."""

import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import shotplan
from shotplan import shotrender

W, H, FPS = 64, 36, 24


def textured_png(path):
    """A picture with a gradient and stripes, so a layer that moves differently shows up in the pixels."""
    ys, xs = np.mgrid[0:H, 0:W]
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[..., 0] = (xs * 3) % 256
    img[..., 1] = (ys * 7) % 256
    img[..., 2] = ((xs + ys) * 2) % 256
    Image.fromarray(img).save(path)


def shot(sid, layers=None, seconds=0.5):
    s = {"id": sid, "image": "pic.png",
         "sequence": {"segments": [{"preset": "dolly_in", "duration_s": seconds}]},
         "duration": seconds, "transition": "cut"}
    if layers is not None:
        s["layers"] = layers
    return s


def plan(shots):
    return {"id": "demo", "name": "Demo", "fps": FPS, "width": W, "height": H, "shots": shots}


class LayeredShotTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        textured_png(os.path.join(self.dir, "pic.png"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def render(self, shots, name, **kw):
        p = plan(shots)
        errors, rows, sequences = shotplan.check(p, self.dir)
        self.assertEqual(errors, [])
        out = os.path.join(self.dir, name)
        kw.setdefault("use_ffmpeg", False)
        kw.setdefault("matte", "fake")
        manifest = shotrender.render_plan(p, self.dir, out, rows, sequences, shotplan.load_json(shotplan.CATALOG),
                                          depth="fake", **kw)
        return out, manifest

    def frame_bytes(self, out, sid, i):
        with open(os.path.join(out, "shots", sid, "frames", "%04d.png" % i), "rb") as f:
            return f.read()

    def test_a_plan_with_layers_passes_check(self):
        errors, rows, _ = shotplan.check(plan([shot("a", layers=True), shot("b")]), self.dir)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2)

    def test_a_non_bool_layers_is_an_error(self):
        for bad in ("yes", 1, 0, [True]):
            with self.subTest(layers=bad):
                errors, _, _ = shotplan.check(plan([shot("a", layers=bad)]), self.dir)
                self.assertTrue(any(".layers: must be true or false" in e for e in errors), errors)

    def test_a_layered_shot_differs_from_the_plain_one(self):
        layered, _ = self.render([shot("s", layers=True)], "layered")
        plain, _ = self.render([shot("s")], "plain")
        last = 12  # the last frame of 0.5 s at 24 fps
        a = np.asarray(Image.open(io.BytesIO(self.frame_bytes(layered, "s", last))))
        b = np.asarray(Image.open(io.BytesIO(self.frame_bytes(plain, "s", last))))
        self.assertEqual(a.shape, b.shape)
        self.assertGreater(int(np.abs(a.astype(int) - b.astype(int)).sum()), 0)

    def test_a_plain_shot_matches_the_plain_warp(self):
        out, _ = self.render([shot("s")], "plain")
        # The reference: the plain warp on smoothed depth, the way stillmove renders a still (no
        # layers). Pixels within 1 level, not byte-exact (float maths differs between machines).
        from stillmove import cameras, warp
        from stillmove.depth import get_backend, smooth_depth
        from stillmove.render import _load_image

        cams = cameras.load(os.path.join(out, "shots", "s", "camera.json"))
        image = _load_image(os.path.join(self.dir, "pic.png"), cams.width, cams.height)
        depth = smooth_depth(get_backend("plane").depth(image), image)
        cam0 = cams.frames[0]
        for i, frame in enumerate(cams.frames):
            ref, _ = warp.warp(image, depth, cam0.K, frame.K, warp.relative(cams, i))
            got = np.asarray(Image.open(io.BytesIO(self.frame_bytes(out, "s", i))).convert("RGB"))
            self.assertLessEqual(int(np.abs(got.astype(int) - ref.astype(int)).max()), 1, "frame %d" % i)

    def test_the_matte_is_created_once_for_two_layered_shots(self):
        from stillmove import matte as matte_module

        with mock.patch("stillmove.matte.get_matte", wraps=matte_module.get_matte) as spy:
            self.render([shot("a", layers=True), shot("b", layers=True), shot("c")], "two")
        self.assertEqual(spy.call_count, 1)

    def test_no_layered_shot_means_no_matte_at_all(self):
        from stillmove import matte as matte_module

        with mock.patch("stillmove.matte.get_matte", wraps=matte_module.get_matte) as spy:
            self.render([shot("a"), shot("b")], "plain2")
        self.assertEqual(spy.call_count, 0)

    def test_frame_counts_and_the_timeline_are_unchanged(self):
        _, plain = self.render([shot("a", seconds=0.5), shot("b", seconds=1.0)], "p")
        _, layered = self.render([shot("a", layers=True, seconds=0.5), shot("b", seconds=1.0)], "l")
        self.assertEqual(layered["frames"], plain["frames"])
        self.assertEqual([(s["start"], s["end"], s["frames"]) for s in layered["shots"]],
                         [(s["start"], s["end"], s["frames"]) for s in plain["shots"]])


if __name__ == "__main__":
    unittest.main()
