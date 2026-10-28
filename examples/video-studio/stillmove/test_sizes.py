"""stillmove render --sizes: one move, cropped to each shape, zipped; the crop follows the subject."""

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

from stillmove import _cm, cameras
from stillmove import matte as matte_module
from stillmove import sizes
from stillmove.cli import main

_cm.import_camera_motion()
SMALL = {"square": (32, 32), "wide": (48, 27), "vertical": (27, 48)}


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class _SubjectMatte:
    """Stands in for a real matte: the bright square in the test picture is the subject."""
    name = "test-subject"

    def alpha(self, rgb):
        return (rgb[..., 0] > 200).astype(np.float32)


class SizesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.image = os.path.join(self.tmp, "in.png")
        # 800x200 grey picture with a white 20x20 subject centred at (600, 100): far right of the middle (400),
        # so a middle crop misses it but a subject-centred crop does not
        pic = np.full((200, 800, 3), 60, dtype=np.uint8)
        pic[90:110, 590:610] = 255
        Image.fromarray(pic).save(self.image)
        self._saved = sizes.SIZES
        sizes.SIZES = dict(SMALL)
        self.addCleanup(setattr, sizes, "SIZES", self._saved)

    def _render_sizes(self, out, extra=()):
        return _run(["render", self.image, "--preset", "dolly_in", "--seconds", "0.5", "--fps", "4",
                     "--backend", "plane", "--sizes", "square,wide,vertical", "-o", out, *extra])

    def _camera_file(self, width, height):
        from camera_motion.engine.camera_path import path
        from camera_motion.export.formats import to_json
        preset = {"type": "dolly", "params": {"axis": "z", "distance": 1}, "easing": "linear"}
        doc = to_json(path(preset, duration_s=0.5, fps=4), width=width, height=height, fps=4)
        out = os.path.join(self.tmp, "cam.json")
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(doc, handle)
        return out

    def test_three_outputs_have_the_right_sizes(self):
        out = os.path.join(self.tmp, "run")
        code, _, err = self._render_sizes(out)
        self.assertEqual(code, 0, err)
        for sid, (w, h) in SMALL.items():
            with open(os.path.join(out, sid, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual((manifest["width"], manifest["height"]), (w, h), sid)
            with Image.open(os.path.join(out, sid, manifest["frames"][0])) as frame:
                self.assertEqual(frame.size, (w, h), sid)

    def test_zip_holds_each_preview(self):
        out = os.path.join(self.tmp, "run")
        code, _, err = self._render_sizes(out)
        self.assertEqual(code, 0, err)
        with zipfile.ZipFile(os.path.join(out, "sizes.zip")) as archive:
            self.assertEqual(sorted(archive.namelist()), ["square.gif", "vertical.gif", "wide.gif"])

    def test_subject_is_centred_in_frame_0_for_every_shape(self):
        out = os.path.join(self.tmp, "run")
        orig = matte_module.get_matte
        matte_module.get_matte = lambda name, *a, **k: _SubjectMatte()
        self.addCleanup(setattr, matte_module, "get_matte", orig)
        code, _, err = self._render_sizes(out, extra=("--layers",))
        self.assertEqual(code, 0, err)
        for sid, (w, h) in SMALL.items():
            with open(os.path.join(out, sid, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["crop_center"], [600.0, 100.0], sid)
            with Image.open(os.path.join(out, sid, manifest["frames"][0])) as frame:
                rgb = np.asarray(frame.convert("RGB"))
            ys, xs = np.nonzero(rgb[..., 0] > 150)
            self.assertTrue(len(xs) > 0, sid)
            cx, cy = xs.mean(), ys.mean()
            self.assertLessEqual(abs(cx - w / 2) / w, 0.05, (sid, cx))
            self.assertLessEqual(abs(cy - h / 2) / h, 0.05, (sid, cy))

    def test_missing_camera_file_with_sizes_is_an_error(self):
        out = os.path.join(self.tmp, "run")
        code, _, err = _run(["render", self.image, "--cam", os.path.join(self.tmp, "missing.json"),
                             "--sizes", "square", "-o", out])
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        self.assertFalse(os.path.exists(out))

    def test_cam_with_sizes_writes_three_right_sized_outputs_and_a_zip(self):
        out = os.path.join(self.tmp, "run")
        code, _, err = _run(["render", self.image, "--cam", self._camera_file(320, 180),
                             "--backend", "plane", "--sizes", "square,wide,vertical", "-o", out])
        self.assertEqual(code, 0, err)
        for sid, (w, h) in SMALL.items():
            with open(os.path.join(out, sid, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual((manifest["width"], manifest["height"]), (w, h), sid)
            with Image.open(os.path.join(out, sid, manifest["frames"][0])) as frame:
                self.assertEqual(frame.size, (w, h), sid)
        with zipfile.ZipFile(os.path.join(out, "sizes.zip")) as archive:
            self.assertEqual(sorted(archive.namelist()), ["square.gif", "vertical.gif", "wide.gif"])

    def test_unknown_size_is_an_error(self):
        code, _, err = _run(["render", self.image, "--preset", "dolly_in", "--sizes", "round", "-o",
                             os.path.join(self.tmp, "run")])
        self.assertEqual(code, 1)
        self.assertIn("--sizes takes", err)


class RetargetTest(unittest.TestCase):
    def setUp(self):
        from camera_motion.engine.camera_path import path
        from camera_motion.export.formats import to_json
        preset = {"type": "orbit", "params": {"axis": "y", "angle": 30}, "easing": "ease_in_out"}
        doc = to_json(path(preset, duration_s=1.0, fps=8), width=640, height=360, fps=8)
        self.cams = cameras._from_document(doc)

    def _fov_v(self, frame, height):
        return 2 * np.degrees(np.arctan(height / (2 * frame.K[1][1])))

    def test_square_pixels_and_centred_principal_point(self):
        for w, h in ((1920, 1080), (1080, 1920)):
            out = cameras.retarget(self.cams, w, h)
            for frame in out.frames:
                self.assertAlmostEqual(frame.K[0][0], frame.K[1][1], places=9)
                self.assertAlmostEqual(frame.K[0][2], w / 2.0, places=9)
                self.assertAlmostEqual(frame.K[1][2], h / 2.0, places=9)

    def test_vertical_fov_and_poses_are_unchanged(self):
        for w, h in ((1920, 1080), (1080, 1920)):
            out = cameras.retarget(self.cams, w, h)
            self.assertEqual((out.width, out.height), (w, h))
            for old, new in zip(self.cams.frames, out.frames):
                self.assertAlmostEqual(self._fov_v(new, h), self._fov_v(old, self.cams.height), places=9)
                np.testing.assert_array_equal(new.w2c, old.w2c)
                self.assertEqual(new.t, old.t)

    def test_non_positive_size_is_refused(self):
        with self.assertRaises(ValueError):
            cameras.retarget(self.cams, 0, 1080)


if __name__ == "__main__":
    unittest.main()
