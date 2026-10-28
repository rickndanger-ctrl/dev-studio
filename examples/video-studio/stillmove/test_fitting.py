"""Keep-in-frame wiring on the stillmove paths: render, the layers path, mesh and the CLI flags.

The subject is synthetic and sits 14 px in from the right edge of a 96x54 frame, so dolly_in pushes it
out of frame and the fit must shorten the move. With no fit (--no-fit, or fit_margin None) the camera
must be used exactly as planned.
"""

import contextlib
import io
import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from stillmove import cameras, cli, fitting, mesh, warp
from stillmove.depth import smooth_depth
from stillmove.render import render

W, H, FPS = 96, 54, 24
MARGIN = 0.06


def subject_depth():
    depth = np.full((H, W), 8.0)
    depth[4:50, 50:82] = 2.0  # near: the subject
    return depth


def subject_alpha():
    alpha = np.zeros((H, W))
    alpha[4:50, 50:82] = 1.0
    return alpha


class SubjectDepth:
    name = "subject"

    def depth(self, image):
        return subject_depth()


class SubjectMatte:
    name = "subject"

    def alpha(self, rgb):
        return subject_alpha()


def dolly_cams():
    return cameras.from_preset("dolly_in", 1.0, FPS, W, H)


def write_image(path):
    img = np.full((H, W, 3), 120, dtype=np.uint8)
    img[4:50, 50:82] = (230, 60, 60)
    Image.fromarray(img).save(path)


def write_depth_png(path):
    """A grayscale depth map for mesh: white is near (--near 3), black is far (--far 8)."""
    img = np.zeros((H, W), dtype=np.uint8)
    img[4:50, 50:82] = 255
    Image.fromarray(img).save(path)  # a 2-D uint8 array is already an 'L' (grayscale) image


class FitTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.image = os.path.join(self.tmp, "in.png")
        write_image(self.image)
        self.out = os.path.join(self.tmp, "out")


class RenderFitTests(FitTestCase):
    def test_a_near_edge_subject_shortens_the_move(self):
        cams = dolly_cams()
        manifest = render(self.image, cams, SubjectDepth(), self.out, fit_margin=MARGIN)
        self.assertLess(manifest["fit"]["scale"], 1.0)
        self.assertEqual(manifest["fit"]["margin"], MARGIN)
        self.assertIn("shortened", manifest["fit"]["note"])
        self.assertEqual(len(manifest["frames"]), len(cams.frames))

    def test_no_fit_renders_the_camera_exactly_as_planned(self):
        cams = dolly_cams()
        manifest = render(self.image, cams, SubjectDepth(), self.out)
        self.assertIsNone(manifest["fit"])
        source = np.asarray(Image.open(self.image).convert("RGB"))
        for i, frame in enumerate(cams.frames):
            # render smooths the depth once per picture, then warps: the reference does the same.
            depth = smooth_depth(subject_depth(), source)
            expected, _filled = warp.warp(source, depth, cams.frames[0].K, frame.K, warp.relative(cams, i))
            got = np.asarray(Image.open(os.path.join(self.out, "frames", "%04d.png" % i)).convert("RGB"))
            self.assertLessEqual(int(np.abs(got.astype(int) - expected.astype(int)).max()), 1, "frame %d" % i)

    def test_the_layers_path_fits_to_the_foreground_plane(self):
        manifest = render(self.image, dolly_cams(), SubjectDepth(), self.out, matte=SubjectMatte(),
                          fit_margin=MARGIN)
        self.assertLess(manifest["fit"]["scale"], 1.0)
        self.assertIn("shortened", manifest["fit"]["note"])

    def test_a_margin_outside_the_range_is_refused(self):
        for bad in (0.0, 0.4, -0.1, 0.5):
            with self.subTest(margin=bad), self.assertRaises(ValueError):
                fitting.check_margin(bad)
        self.assertEqual(fitting.check_margin(0.06), 0.06)


class CliFitTests(FitTestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def render_argv(self, *extra):
        return ["render", self.image, "--preset", "dolly_in", "--seconds", "1", "--fps", str(FPS),
                *extra, "-o", self.out]

    def test_render_no_fit_writes_a_null_fit_record(self):
        code, _out, _err = self.run_cli(*self.render_argv("--no-fit"))
        self.assertEqual(code, 0)
        self.assertIsNone(_manifest(self.out)["fit"])

    def test_render_by_default_writes_a_fit_record_at_the_default_margin(self):
        code, _out, _err = self.run_cli(*self.render_argv())
        self.assertEqual(code, 0)
        fit = _manifest(self.out)["fit"]
        self.assertEqual(fit["margin"], fitting.DEFAULT_MARGIN)
        self.assertIn("scale", fit)

    def test_render_refuses_a_margin_outside_the_range(self):
        code, _out, err = self.run_cli(*self.render_argv("--fit-margin", "0.5"))
        self.assertEqual(code, 1)
        self.assertIn("--fit-margin", err)

    def test_mesh_no_fit_keys_the_camera_as_planned_and_fit_moves_it(self):
        depth_png = os.path.join(self.tmp, "depth.png")
        write_depth_png(depth_png)
        base = ["mesh", self.image, "--depth", depth_png, "--preset", "dolly_in", "--seconds", "1",
                "--fps", str(FPS), "--grid", "8x6"]
        planned = os.path.join(self.tmp, "planned")
        fitted = os.path.join(self.tmp, "fitted")
        code, _out, _err = self.run_cli(*base, "--no-fit", "-o", planned)
        self.assertEqual(code, 0)
        code, _out, _err = self.run_cli(*base, "-o", fitted)
        self.assertEqual(code, 0)
        planned_rows = _scene_rows(planned)
        self.assertEqual(planned_rows, mesh.scene_frames(mesh.key_script(dolly_cams(), name="still",
                                                                        mesh_path=os.path.join(planned, "mesh.obj"))))
        self.assertNotEqual(_scene_rows(fitted), planned_rows)

    def test_mesh_fit_summary_carries_the_record(self):
        depth_png = os.path.join(self.tmp, "depth.png")
        write_depth_png(depth_png)
        summary = mesh.write_mesh(self.image, dolly_cams(), depth_png, (8, 6), os.path.join(self.tmp, "m"),
                                  fit_margin=MARGIN)
        self.assertLess(summary["fit"]["scale"], 1.0)
        self.assertIn("shortened", summary["fit"]["note"])
        none = mesh.write_mesh(self.image, dolly_cams(), depth_png, (8, 6), os.path.join(self.tmp, "n"))
        self.assertIsNone(none["fit"])


def _manifest(outdir):
    import json

    with open(os.path.join(outdir, "manifest.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _scene_rows(outdir):
    with open(os.path.join(outdir, mesh.SCENE_NAME), encoding="utf-8") as handle:
        return mesh.scene_frames(handle.read())


if __name__ == "__main__":
    unittest.main()
